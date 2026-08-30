#!/usr/bin/env python3
"""
BiomAPI CLI - Zero-dependency client for BiomAPI biometry extraction.

Processes biometry PDFs/images through the BiomAPI service and retrieves
results via BiomPIN secure sharing codes. Also works through Claude Code and
Codex plugin marketplaces.

Usage:
    biomapi.py configure [--key <key>] [--gemini-key <key>] [--url <url>] [--escrs-url <url>] [--show] [--clear]
    biomapi.py process <file_path> [<file_path2> ...] [--service-tier standard|slow] [--no-pin] [--key <key>] [--gemini-key <key>] [--escrs-url <url>]
    biomapi.py retrieve <biompin_code_or_url>
    biomapi.py export <file.json> [<file2.json> ...] [--output <dir>]
    biomapi.py usage
    biomapi.py status
    biomapi.py --help

Stdout (process/retrieve):
    One JSON object per file, in input order:
      {"patient_id": ..., "patient_name": ..., "device": ..., "biompin": ..., "biomapi_url": ..., "escrs_url": ..., "saved_json": "/abs/path.json"}
    biompin is extracted from the API response at biompin.pin.
    The full raw API response is always saved to disk at saved_json.
    On error: {"error": true, "detail": "...", ...}

Stdout (export):
    {"export": "/abs/path/biomapi_export.zip"}

Environment:
    BIOMAPI_KEY      - Optional API key for higher rate limits
    GEMINI_API_KEY   - Optional: your own Gemini key (BYOK, uses biomai_byok limits)
    ESCRS_IOL_CALCULATOR_URL - Optional: calculator base URL for generated links

Config file (~/.config/biomapi/config):
    Simple KEY=VALUE format. Keys: BIOMAPI_KEY, GEMINI_API_KEY, BIOMAPI_URL, ESCRS_IOL_CALCULATOR_URL
    Priority: CLI flags > environment variables > config file
    Run `biomapi.py configure` to set up the config file interactively.
"""

import base64
import binascii
import json
import os
import re
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qs,
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urlsplit,
    urlunsplit,
)
from urllib.request import Request, urlopen

MIN_PYTHON = (3, 11)
if sys.version_info < MIN_PYTHON:
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    print(
        f"BiomAPI CLI requires Python {required} or newer; found Python {current}.",
        file=sys.stderr,
    )
    raise SystemExit(1)


MAX_CONCURRENT_FILES = 4
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".json"}


def _config_path() -> str:
    """Return path to ~/.config/biomapi/config (cross-platform)."""
    return os.path.join(os.path.expanduser("~"), ".config", "biomapi", "config")


def _load_config() -> dict:
    """Read ~/.config/biomapi/config if it exists. Simple KEY=VALUE format."""
    config = {}
    try:
        with open(_config_path()) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return config


def _save_config(config: dict) -> str:
    """Atomically write the config with private POSIX permissions."""
    path = _config_path()
    config_dir = os.path.dirname(path)
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(config_dir, 0o700)

    fd, temporary_path = tempfile.mkstemp(prefix=".config-", dir=config_dir, text=True)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            for key, value in config.items():
                f.write(f"{key}={value}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
    return path


def _mask_key(value: str) -> str:
    """Mask a secret value for display: show first 10 chars + '...'."""
    if len(value) <= 10:
        return value
    return value[:10] + "..."


try:
    _config = _load_config()
except OSError as e:
    print(json.dumps({"error": True, "detail": f"Could not read config file: {e}"}))
    raise SystemExit(1) from None
API_KEY = os.environ.get("BIOMAPI_KEY") or _config.get("BIOMAPI_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or _config.get("GEMINI_API_KEY", "")
BASE_URL = os.environ.get("BIOMAPI_URL") or _config.get("BIOMAPI_URL", "https://biomapi.com")
ESCRS_IOL_CALCULATOR_URL = (
    os.environ.get("ESCRS_IOL_CALCULATOR_URL")
    or _config.get("ESCRS_IOL_CALCULATOR_URL", "https://iolcalculator.escrs.org")
)


def _identity_context(patient_name: str | None, patient_id: str | None) -> dict | None:
    """Return the BiomPIN browser SDK v1 identity context payload."""
    if not patient_name and not patient_id:
        return None

    return {
        "v": 1,
        "patient_name": patient_name or None,
        "patient_id": patient_id or None,
    }


def _encode_identity_context(patient_name: str | None, patient_id: str | None) -> str | None:
    """Encode patient identifiers for the reversible #biomctx URL fragment."""
    context = _identity_context(patient_name, patient_id)
    if not context:
        return None

    raw = json.dumps(context, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_identity_context(encoded: str | None) -> dict | None:
    """Decode a #biomctx value into patient identifiers."""
    if not encoded:
        return None

    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(parsed, dict):
        return None

    if parsed.get("v") not in (None, 1):
        return None

    patient_name = parsed.get("patient_name") or None
    patient_id = parsed.get("patient_id") or None
    if not patient_name and not patient_id:
        return None

    return {"patient_name": patient_name, "patient_id": patient_id}


def _append_identity_fragment(url: str, patient_name: str | None, patient_id: str | None) -> str:
    """Append #biomctx to a URL when identifiers are available."""
    encoded = _encode_identity_context(patient_name, patient_id)
    if not encoded:
        return url

    split = urlsplit(url)
    url_without_fragment = urlunsplit((split.scheme, split.netloc, split.path, split.query, ""))
    return f"{url_without_fragment}#biomctx={encoded}"


def _build_biomapi_url(pin: str, patient_name: str | None = None, patient_id: str | None = None) -> str:
    """Build a BiomAPI direct access URL, including private context when available."""
    url = f"{BASE_URL.rstrip('/')}/pin/{quote(pin)}"
    return _append_identity_fragment(url, patient_name, patient_id)


def _build_escrs_url(pin: str, patient_name: str | None = None, patient_id: str | None = None) -> str:
    """Build an ESCRS calculator URL with BiomPIN and optional private context."""
    split = urlsplit(ESCRS_IOL_CALCULATOR_URL)
    query = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True) if key != "biompin"]
    query.append(("biompin", pin))
    url = urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))
    return _append_identity_fragment(url, patient_name, patient_id)


def _decode_identity_context_from_fragment(fragment: str) -> dict | None:
    """Extract and decode biomctx from a URL fragment."""
    params = parse_qs(fragment or "", keep_blank_values=True)
    values = params.get("biomctx")
    return _decode_identity_context(values[0]) if values else None


def _parse_biompin_input(value: str) -> tuple[str, dict | None]:
    """Accept either a raw BiomPIN or a BiomAPI/ESCRS URL containing one."""
    raw = value.strip()
    split = urlsplit(raw)
    context = _decode_identity_context_from_fragment(split.fragment)

    query_pin = parse_qs(split.query).get("biompin")
    if query_pin:
        return query_pin[0], context

    path_parts = [unquote(part) for part in split.path.split("/") if part]
    if "pin" in path_parts:
        pin_index = path_parts.index("pin") + 1
        if pin_index < len(path_parts):
            return path_parts[pin_index], context

    return raw.split("#", 1)[0], context


def _merge_identity_context(result: dict, context: dict | None) -> None:
    """Restore identifiers into a retrieved response when supplied in #biomctx."""
    if not context:
        return

    patient = (result.get("data") or {}).get("patient")
    if not isinstance(patient, dict):
        return

    patient["name"] = patient.get("name") or context.get("patient_name")
    patient["id"] = patient.get("id") or context.get("patient_id")


def _compact_summary(result: dict, saved_path: str, biompin_fallback: str | None = None) -> dict:
    """Return the compact stdout summary for process/retrieve commands."""
    patient = (result.get("data") or {}).get("patient") or {}
    if not isinstance(patient, dict):
        patient = {}
    biompin_info = result.get("biompin") or {}
    biompin = biompin_info.get("pin") if isinstance(biompin_info, dict) else None
    biompin = biompin or biompin_fallback
    patient_name = patient.get("name")
    patient_id = patient.get("id")

    summary = {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "device": (result.get("data") or {}).get("biometer", {}).get("device_name"),
        "biompin": biompin,
    }
    if biompin:
        summary["biomapi_url"] = _build_biomapi_url(biompin, patient_name, patient_id)
        summary["escrs_url"] = _build_escrs_url(biompin, patient_name, patient_id)
    summary["saved_json"] = saved_path
    return summary


def _build_multipart(file_path: str, generate_pin: bool, service_tier: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body using only stdlib."""
    boundary = f"----BiomAPI{uuid.uuid4().hex}"
    filename = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        file_data = f.read()

    # Determine content type from extension
    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".json": "application/json",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    biompin_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="biompin"\r\n\r\n'
        f"{'true' if generate_pin else 'false'}\r\n"
    ).encode()
    service_tier_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="service_tier"\r\n\r\n'
        f"{service_tier}\r\n"
    ).encode()
    closing_boundary = f"--{boundary}--\r\n".encode()

    return (
        b"".join((file_header, file_data, b"\r\n", biompin_part, service_tier_part, closing_boundary)),
        f"multipart/form-data; boundary={boundary}",
    )


def _request(
    method: str,
    url: str,
    body: bytes | None = None,
    content_type: str | None = None,
    binary: bool = False,
    timeout_seconds: float | None = 120,
) -> dict | bytes:
    """Make an HTTP request. Returns parsed JSON or raw bytes for binary downloads."""
    headers = {"X-BiomAPI-Integrator-ID": "biomapi.cli"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    if GEMINI_API_KEY:
        headers["X-Gemini-API-Key"] = GEMINI_API_KEY
    if content_type:
        headers["Content-Type"] = content_type

    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            response_body = resp.read()
            if binary:
                return response_body
            response_text = response_body.decode("utf-8")
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                return {"error": True, "detail": "Invalid response from BiomAPI: expected a JSON object."}
            return parsed
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            error_json = json.loads(error_body)
            error = error_json.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or error_json.get("detail", error_body)
            elif isinstance(error, str):
                detail = error
            else:
                detail = error_json.get("detail", error_body)
            if not isinstance(detail, str):
                detail = json.dumps(detail, separators=(",", ":"))
        except (json.JSONDecodeError, AttributeError):
            detail = error_body
        return {"error": True, "status": e.code, "detail": detail}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": True, "detail": "Invalid response from BiomAPI: expected a UTF-8 JSON object."}
    except URLError as e:
        return {"error": True, "detail": f"Connection failed: {e.reason}"}
    except TimeoutError:
        return {"error": True, "detail": "Connection failed: request timed out."}
    except OSError as e:
        return {"error": True, "detail": f"Connection failed: {e}"}


def _generate_filename(result: dict) -> str:
    """Mirror generateSmartFilename from downloadManager.js.

    Format: biomapi-{patient_id}-{device}.json
    - patient_id: lowercase, [^a-z0-9-] → '-', collapse runs, max 50 chars
    - device: lowercase, only [a-z0-9], max 30 chars
    - Fallbacks: YYYY-MM-DD for missing patient_id, 'unknown' for missing device
    """
    patient_id = (result.get("data") or {}).get("patient", {}).get("id")
    device_name = (result.get("data") or {}).get("biometer", {}).get("device_name")

    if patient_id:
        id_part = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", str(patient_id).lower()))[:50]
    else:
        id_part = date.today().isoformat()

    if device_name:
        device_part = re.sub(r"[^a-z0-9]", "", str(device_name).lower())[:30]
    else:
        device_part = "unknown"

    return f"biomapi-{id_part}-{device_part}.json"


def _write_unique_json(result: dict, save_dir: str, filename: str) -> str:
    """Write JSON without replacing an existing file."""
    stem, extension = os.path.splitext(filename)
    suffix = 1
    while True:
        candidate = filename if suffix == 1 else f"{stem}-{suffix}{extension}"
        save_path = os.path.join(save_dir, candidate)
        try:
            with open(save_path, "x", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            return os.path.abspath(save_path)
        except FileExistsError:
            suffix += 1


def _save_result(result: dict, source_path: str) -> str:
    """Save the full response beside the source, falling back to the current directory."""
    filename = _generate_filename(result)
    preferred_dir = os.path.dirname(source_path) or os.getcwd()
    save_dirs = [preferred_dir]
    current_dir = os.getcwd()
    if os.path.abspath(preferred_dir) != os.path.abspath(current_dir):
        save_dirs.append(current_dir)

    errors = []
    for save_dir in save_dirs:
        try:
            os.makedirs(save_dir, exist_ok=True)
            return _write_unique_json(result, save_dir, filename)
        except OSError as e:
            errors.append(f"{save_dir}: {e}")

    raise OSError("Could not save result JSON (" + "; ".join(errors) + ")")


def _process_one(file_path: str, generate_pin: bool, service_tier: str) -> dict:
    """Validate and upload a single biometry file.

    Returns a compact summary dict (not the full API response) so the LLM
    context stays small. The full raw response is always saved to disk first.
    """
    file_path = os.path.abspath(file_path)

    if not os.path.isfile(file_path):
        return {"error": True, "file": file_path, "detail": f"File not found: {file_path}"}

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "error": True,
            "file": file_path,
            "detail": f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        }

    try:
        body, content_type = _build_multipart(file_path, generate_pin, service_tier)
    except OSError as e:
        return {"error": True, "file": file_path, "detail": f"Could not read file: {e}"}
    result = _request(
        "POST",
        f"{BASE_URL.rstrip('/')}/api/v1/biom/process",
        body=body,
        content_type=content_type,
        timeout_seconds=None,
    )

    if result.get("error"):
        return result

    # Save full response to disk before returning — metadata is preserved here
    try:
        saved_path = _save_result(result, file_path)
    except OSError as e:
        return {"error": True, "file": file_path, "detail": str(e)}

    return _compact_summary(result, saved_path)


def cmd_configure(args: list[str]) -> None:
    """Set up API keys in the config file. Works on Windows, macOS, and Linux."""
    show = "--show" in args
    clear = "--clear" in args
    clear_key = "--clear-key" in args
    clear_gemini_key = "--clear-gemini-key" in args

    key_value = None
    gemini_key_value = None
    url_value = None
    escrs_url_value = None

    i = 0
    while i < len(args):
        if args[i] == "--key" and i + 1 < len(args):
            key_value = args[i + 1]
            i += 2
        elif args[i] == "--gemini-key" and i + 1 < len(args):
            gemini_key_value = args[i + 1]
            i += 2
        elif args[i] == "--url" and i + 1 < len(args):
            url_value = args[i + 1]
            i += 2
        elif args[i] == "--escrs-url" and i + 1 < len(args):
            escrs_url_value = args[i + 1]
            i += 2
        else:
            i += 1

    path = _config_path()

    if show:
        cfg = _load_config()
        print(f"Config file: {path}", file=sys.stderr)
        print(f"File exists: {'yes' if os.path.isfile(path) else 'no'}", file=sys.stderr)
        print(file=sys.stderr)
        for name, default in [
            ("BIOMAPI_KEY", None),
            ("GEMINI_API_KEY", None),
            ("BIOMAPI_URL", "https://biomapi.com"),
            ("ESCRS_IOL_CALCULATOR_URL", "https://iolcalculator.escrs.org"),
        ]:
            env_val = os.environ.get(name)
            cfg_val = cfg.get(name)
            if env_val:
                print(f"  {name} = {_mask_key(env_val)} (from environment)", file=sys.stderr)
            elif cfg_val:
                print(f"  {name} = {_mask_key(cfg_val)} (from config file)", file=sys.stderr)
            elif default:
                print(f"  {name} = {default} (default)", file=sys.stderr)
            else:
                print(f"  {name} = (not set)", file=sys.stderr)
        return

    if clear:
        try:
            os.remove(path)
            print(f"Removed {path}", file=sys.stderr)
        except FileNotFoundError:
            print(f"No config file to remove ({path})", file=sys.stderr)
        except OSError as e:
            print(json.dumps({"error": True, "detail": f"Could not remove config file: {e}"}))
            sys.exit(1)
        return

    if clear_key or clear_gemini_key:
        cfg = _load_config()
        removed = []
        if clear_key and "BIOMAPI_KEY" in cfg:
            del cfg["BIOMAPI_KEY"]
            removed.append("BIOMAPI_KEY")
        if clear_gemini_key and "GEMINI_API_KEY" in cfg:
            del cfg["GEMINI_API_KEY"]
            removed.append("GEMINI_API_KEY")
        if removed:
            if cfg:
                try:
                    _save_config(cfg)
                except OSError as e:
                    print(json.dumps({"error": True, "detail": f"Could not save config file: {e}"}))
                    sys.exit(1)
            else:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    print(json.dumps({"error": True, "detail": f"Could not remove config file: {e}"}))
                    sys.exit(1)
            print(f"Removed: {', '.join(removed)}", file=sys.stderr)
        else:
            print("Nothing to remove.", file=sys.stderr)
        return

    # Flag-based (non-interactive): at least one value flag provided
    if key_value is not None or gemini_key_value is not None or url_value is not None or escrs_url_value is not None:
        cfg = _load_config()
        if key_value is not None:
            cfg["BIOMAPI_KEY"] = key_value
        if gemini_key_value is not None:
            cfg["GEMINI_API_KEY"] = gemini_key_value
        if url_value is not None:
            cfg["BIOMAPI_URL"] = url_value
        if escrs_url_value is not None:
            cfg["ESCRS_IOL_CALCULATOR_URL"] = escrs_url_value
        try:
            saved = _save_config(cfg)
        except OSError as e:
            print(json.dumps({"error": True, "detail": f"Could not save config file: {e}"}))
            sys.exit(1)
        print(f"Saved to {saved}", file=sys.stderr)
        return

    # Interactive mode
    print("BiomAPI CLI Configuration", file=sys.stderr)
    print(f"Config file: {path}", file=sys.stderr)
    print(file=sys.stderr)

    cfg = _load_config()

    for name, label in [("BIOMAPI_KEY", "BIOMAPI_KEY"), ("GEMINI_API_KEY", "GEMINI_API_KEY")]:
        current = cfg.get(name, "")
        if current:
            prompt = f"  {label} [{_mask_key(current)}]: "
        else:
            prompt = f"  {label}: "
        try:
            val = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return
        if val:
            cfg[name] = val

    if not cfg.get("BIOMAPI_KEY") and not cfg.get("GEMINI_API_KEY"):
        print("\nNo keys configured. Skipping save.", file=sys.stderr)
        return

    try:
        saved = _save_config(cfg)
    except OSError as e:
        print(json.dumps({"error": True, "detail": f"Could not save config file: {e}"}))
        sys.exit(1)
    print(f"\nSaved to {saved}", file=sys.stderr)


def cmd_process(
    file_paths: list[str],
    generate_pin: bool = True,
    service_tier: str = "standard",
) -> None:
    """Upload one or more biometry files for AI extraction, concurrently."""
    if len(file_paths) == 1:
        result = _process_one(file_paths[0], generate_pin, service_tier)
        print(json.dumps(result))
        if result.get("error"):
            sys.exit(1)
        return

    results = [None] * len(file_paths)
    with ThreadPoolExecutor(max_workers=min(len(file_paths), MAX_CONCURRENT_FILES)) as executor:
        futures = {
            executor.submit(_process_one, fp, generate_pin, service_tier): i
            for i, fp in enumerate(file_paths)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as e:
                results[index] = {
                    "error": True,
                    "file": os.path.abspath(file_paths[index]),
                    "detail": f"Processing failed locally: {e}",
                }

    for result in results:
        print(json.dumps(result))

    if any(r.get("error") for r in results if r):
        sys.exit(1)


def cmd_retrieve(biompin_input: str) -> None:
    """Retrieve biometry data using a BiomPIN code."""
    biompin_code, identity_context = _parse_biompin_input(biompin_input)
    url = f"{BASE_URL.rstrip('/')}/api/v1/biom/retrieve?biom_pin={quote(biompin_code)}"
    result = _request("GET", url)

    if result.get("error"):
        print(json.dumps(result))
        sys.exit(1)

    _merge_identity_context(result, identity_context)
    try:
        saved_path = _save_result(result, os.path.join(os.getcwd(), "_retrieve"))
    except OSError as e:
        print(json.dumps({"error": True, "detail": str(e)}))
        sys.exit(1)
    print(json.dumps(_compact_summary(result, saved_path, biompin_fallback=biompin_code)))


def cmd_export(json_paths: list[str], output_dir: str) -> None:
    """Generate a CSV/XLSX/JSON ZIP by sending saved responses to /biom/export."""
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        print(json.dumps({"error": True, "detail": f"Could not create output directory: {e}"}))
        sys.exit(1)

    responses = []
    for p in json_paths:
        p = os.path.abspath(p)
        try:
            with open(p, encoding="utf-8") as f:
                responses.append({"json_data": json.load(f), "filename": os.path.basename(p)})
        except Exception as e:
            print(json.dumps({"error": True, "detail": f"Could not read {p}: {e}"}))
            sys.exit(1)

    payload = json.dumps({
        "responses": responses,
        "formats": ["csv", "xlsx", "json"],
    }).encode("utf-8")
    result = _request(
        "POST",
        f"{BASE_URL.rstrip('/')}/api/v1/biom/export",
        body=payload,
        content_type="application/json",
        binary=True,
    )

    if isinstance(result, dict) and result.get("error"):
        print(json.dumps(result))
        sys.exit(1)

    out_path = os.path.join(output_dir, "biomapi_export.zip")
    try:
        with open(out_path, "wb") as f:
            f.write(result)
    except OSError as e:
        print(json.dumps({"error": True, "detail": f"Could not save export: {e}"}))
        sys.exit(1)

    print(json.dumps({"export": os.path.abspath(out_path)}))


def cmd_usage() -> None:
    """Check current rate limit usage for the authenticated key or public IP."""
    result = _request("GET", f"{BASE_URL.rstrip('/')}/api/v1/biom/usage")
    print(json.dumps(result))
    if isinstance(result, dict) and result.get("error"):
        sys.exit(1)


def cmd_status() -> None:
    """Check lightweight API status."""
    result = _request("GET", f"{BASE_URL.rstrip('/')}/api/v1/status")
    print(json.dumps(result))
    if result.get("error"):
        sys.exit(1)


_HELP = """\
BiomAPI CLI — zero-dependency Python client for BiomAPI biometry extraction.

Usage:
  biomapi.py configure                     [--key <key>] [--gemini-key <key>] [--url <url>] [--escrs-url <url>] [--show] [--clear]
  biomapi.py process <file> [<file2> ...]  [--service-tier standard|slow] [--no-pin] [--key <key>] [--gemini-key <key>] [--escrs-url <url>]
  biomapi.py retrieve <biompin_or_url>     [--key <key>] [--escrs-url <url>]
  biomapi.py export <file.json> [<file2.json> ...] [--output <dir>]
  biomapi.py usage                         [--key <key>]
  biomapi.py status
  biomapi.py --help

Commands:
  configure    Save API keys to the config file (works on Windows, macOS, Linux).
               Run with no flags for interactive setup, or use flags directly:
                 --key <key>         Set BIOMAPI_KEY
                 --gemini-key <key>  Set GEMINI_API_KEY
                 --url <url>         Set BIOMAPI_URL (for self-hosted instances)
                 --escrs-url <url>   Set ESCRS_IOL_CALCULATOR_URL
                 --show              Display current configuration
                 --clear             Remove the config file
                 --clear-key         Remove only BIOMAPI_KEY from config
                 --clear-gemini-key  Remove only GEMINI_API_KEY from config

  process      Extract biometry from PDF/PNG/JPG/JPEG/JSON.
               The server enforces its configured per-file upload limit.
               Saves full JSON response next to each source file.
               BiomPIN is generated by default; use --no-pin to skip.
               Standard costs 1 BiomAI credit; Slow costs 0.5 and may take longer.

  retrieve     Fetch previously extracted data using a BiomPIN code.
               Saves full JSON to the current directory.

  export       Export saved JSON results to CSV, XLSX, and JSON files via the API.
               Requires network access. Output: biomapi_export.zip.

  usage        Show current rate-limit usage for your key or public IP.

  status       Lightweight API status — returns deployment metadata and db_id.

Options:
  --no-pin          Skip BiomPIN generation on process (default: generate)
  --service-tier    BiomAPI tier for PDF/images: standard (default) or slow
  --key <key>       Override BIOMAPI_KEY (higher daily limits)
  --gemini-key <k>  Override GEMINI_API_KEY (BYOK — separate biomai_byok bucket)
  --escrs-url <url> Override ESCRS_IOL_CALCULATOR_URL for generated links
  -h, --help        Show this help message

Environment variables:
  BIOMAPI_KEY      BiomAPI key for higher rate limits on all endpoints
  GEMINI_API_KEY   Your own Gemini API key (BYOK — separate biomai_byok bucket)
  BIOMAPI_URL      API base URL (default: https://biomapi.com)
  ESCRS_IOL_CALCULATOR_URL
                  ESCRS calculator base URL (default: https://iolcalculator.escrs.org)

Config file (~/.config/biomapi/config):
  Simple KEY=VALUE pairs. Same keys as environment variables.
  Run `python biomapi.py configure` to create/update it automatically.
  Prefer interactive configuration or environment variables so secrets do not
  appear in shell history. POSIX config files are restricted to the current user.
  Priority: CLI flags > env vars > config file

Access tiers:
  No key             Deployment-configured public quotas (per IP)
  BIOMAPI_KEY        Custom quota per user
  GEMINI_API_KEY     biomai_byok bucket (uses your Gemini quota)

Examples:
  python biomapi.py configure                     # interactive setup
  python biomapi.py configure --key biom_abc123   # set key directly
  python biomapi.py configure --show              # view current config
  python biomapi.py process report.pdf
  python biomapi.py process reports/*.pdf --service-tier slow
  python biomapi.py process *.pdf --no-pin
  python biomapi.py retrieve lunar-rocket-731904
  python biomapi.py export biomapi-*.json --output ./exports
  python biomapi.py usage
  python biomapi.py status
"""


def main() -> None:
    args = sys.argv[1:]

    # Help
    if not args or args[0] in ("-h", "--help", "help"):
        print(_HELP)
        sys.exit(0)

    # Handle configure before global flag extraction (--key/--gemini-key mean "save" here, not "use")
    if args[0] == "configure":
        cmd_configure(args[1:])
        return

    # Extract global flags: --key/--gemini-key/--escrs-url (override module-level vars)
    global API_KEY, GEMINI_API_KEY, ESCRS_IOL_CALCULATOR_URL
    filtered = []
    i = 0
    while i < len(args):
        if args[i] == "--key" and i + 1 < len(args):
            API_KEY = args[i + 1]
            i += 2
        elif args[i] == "--gemini-key" and i + 1 < len(args):
            GEMINI_API_KEY = args[i + 1]
            i += 2
        elif args[i] == "--escrs-url" and i + 1 < len(args):
            ESCRS_IOL_CALCULATOR_URL = args[i + 1]
            i += 2
        else:
            filtered.append(args[i])
            i += 1
    args = filtered

    if not args:
        print("Usage: biomapi.py <configure|process|retrieve|export|usage|status> [args]", file=sys.stderr)
        sys.exit(1)

    command = args[0]

    if command == "process":
        if len(args) < 2:
            print("Usage: biomapi.py process <file_path> [<file_path2> ...] [--service-tier standard|slow] [--no-pin]", file=sys.stderr)
            sys.exit(1)
        rest = args[1:]
        generate_pin = "--no-pin" not in rest
        service_tier = "standard"
        if "--service-tier" in rest:
            idx = rest.index("--service-tier")
            if idx + 1 >= len(rest):
                print("Error: --service-tier requires standard or slow", file=sys.stderr)
                sys.exit(1)
            service_tier = rest[idx + 1].lower()
            if service_tier not in {"standard", "slow"}:
                print("Error: --service-tier must be standard or slow", file=sys.stderr)
                sys.exit(1)
            rest = rest[:idx] + rest[idx + 2:]
        file_paths = [a for a in rest if a != "--no-pin"]
        if not file_paths:
            print("Error: no files specified", file=sys.stderr)
            sys.exit(1)
        cmd_process(file_paths, generate_pin, service_tier)

    elif command == "retrieve":
        if len(args) < 2:
            print("Usage: biomapi.py retrieve <biompin_code_or_url>", file=sys.stderr)
            sys.exit(1)
        cmd_retrieve(args[1])

    elif command == "export":
        if len(args) < 2:
            print("Usage: biomapi.py export <file.json> [<file2.json> ...] [--output <dir>]", file=sys.stderr)
            sys.exit(1)
        rest = args[1:]
        output_dir = os.getcwd()
        if "--output" in rest:
            idx = rest.index("--output")
            if idx + 1 >= len(rest):
                print("Error: --output requires a directory argument", file=sys.stderr)
                sys.exit(1)
            output_dir = rest[idx + 1]
            rest = rest[:idx] + rest[idx + 2:]
        json_paths = [a for a in rest if not a.startswith("--")]
        if not json_paths:
            print("Error: no JSON files specified", file=sys.stderr)
            sys.exit(1)
        cmd_export(json_paths, output_dir)

    elif command == "usage":
        cmd_usage()

    elif command == "status":
        cmd_status()

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Commands: configure, process, retrieve, export, usage, status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
