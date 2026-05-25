#!/usr/bin/env python3
"""
BiomAPI CLI - Zero-dependency client for BiomAPI biometry extraction.

Processes biometry PDFs/images through the BiomAPI service and retrieves
results via BiomPIN secure sharing codes. Also works as a Claude Code plugin
and Codex skill.

Usage:
    biomapi.py configure [--key <key>] [--gemini-key <key>] [--show] [--clear]
    biomapi.py process <file_path> [<file_path2> ...] [--no-pin] [--key <key>] [--gemini-key <key>]
    biomapi.py retrieve <biompin_code>
    biomapi.py csv <file.json> [<file2.json> ...] [--output <dir>]
    biomapi.py usage
    biomapi.py status
    biomapi.py --help

Stdout (process/retrieve):
    One JSON object per file, in input order:
      {"patient_id": ..., "patient_name": ..., "device": ..., "biompin": ..., "saved_json": "/abs/path.json"}
    biompin is extracted from the API response at biompin.pin.
    The full raw API response is always saved to disk at saved_json.
    On error: {"error": true, "detail": "...", ...}

Stdout (csv):
    {"byeye": "/abs/path/biomapi_byeye.csv"}

Environment:
    BIOMAPI_KEY      - Optional API key for higher rate limits
    GEMINI_API_KEY   - Optional: your own Gemini key (BYOK, uses biomai_byok limits)

Config file (~/.config/biomapi/config):
    Simple KEY=VALUE format. Keys: BIOMAPI_KEY, GEMINI_API_KEY, BIOMAPI_URL
    Priority: CLI flags > environment variables > config file
    Run `biomapi.py configure` to set up the config file interactively.
"""

import json
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    """Write config dict to ~/.config/biomapi/config. Creates dirs if needed. Returns path."""
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in config.items():
            f.write(f"{k}={v}\n")
    return path


def _mask_key(value: str) -> str:
    """Mask a secret value for display: show first 10 chars + '...'."""
    if len(value) <= 10:
        return value
    return value[:10] + "..."


_config = _load_config()
API_KEY = os.environ.get("BIOMAPI_KEY") or _config.get("BIOMAPI_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or _config.get("GEMINI_API_KEY", "")
BASE_URL = os.environ.get("BIOMAPI_URL") or _config.get("BIOMAPI_URL", "https://biomapi.com")

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".json"}


def _build_multipart(file_path: str, generate_pin: bool) -> tuple[bytes, str]:
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

    parts = []

    # File part
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )
    parts.append(file_data)
    parts.append(b"\r\n")

    # BiomPIN part
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="biompin"\r\n\r\n'
        f"{'true' if generate_pin else 'false'}\r\n"
    )

    # Closing boundary
    parts.append(f"--{boundary}--\r\n")

    # Combine parts (mix of str and bytes)
    body = b""
    for part in parts:
        body += part.encode("utf-8") if isinstance(part, str) else part

    return body, f"multipart/form-data; boundary={boundary}"


def _request(method: str, url: str, body: bytes | None = None, content_type: str | None = None, raw: bool = False) -> dict | str:
    """Make an HTTP request. Returns parsed JSON dict, or raw string if raw=True. Returns error dict on failure."""
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    if GEMINI_API_KEY:
        headers["X-Gemini-API-Key"] = GEMINI_API_KEY
    if content_type:
        headers["Content-Type"] = content_type

    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=120) as resp:
            response_body = resp.read().decode("utf-8")
            if raw:
                return response_body
            return json.loads(response_body)
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            error_json = json.loads(error_body)
            error = error_json.get("error", {})
            detail = error.get("message") or error_json.get("detail", error_body)
        except json.JSONDecodeError:
            detail = error_body
        return {"error": True, "status": e.code, "detail": detail}
    except URLError as e:
        return {"error": True, "detail": f"Connection failed: {e.reason}"}


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


def _save_result(result: dict, source_path: str) -> str:
    """Save full API response JSON next to source_path. Returns absolute save path."""
    filename = _generate_filename(result)
    save_dir = os.path.dirname(source_path) or os.getcwd()
    try:
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return os.path.abspath(save_path)
    except OSError:
        # Fallback to cwd if source dir is not writable
        save_path = os.path.join(os.getcwd(), filename)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return os.path.abspath(save_path)


def _process_one(file_path: str, generate_pin: bool) -> dict:
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

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if file_size_mb > 20:
        return {"error": True, "file": file_path, "detail": f"File too large ({file_size_mb:.1f}MB). Maximum: 20MB"}

    body, content_type = _build_multipart(file_path, generate_pin)
    result = _request("POST", f"{BASE_URL}/api/v1/biom/process", body=body, content_type=content_type)

    if result.get("error"):
        return result

    # Save full response to disk before returning — metadata is preserved here
    saved_path = _save_result(result, file_path)

    patient = (result.get("data") or {}).get("patient", {})
    biompin_info = result.get("biompin") or {}
    return {
        "patient_id": patient.get("id"),
        "patient_name": patient.get("name"),
        "device": (result.get("data") or {}).get("biometer", {}).get("device_name"),
        "biompin": biompin_info.get("pin") if isinstance(biompin_info, dict) else None,
        "saved_json": saved_path,
    }


def cmd_configure(args: list[str]) -> None:
    """Set up API keys in the config file. Works on Windows, macOS, and Linux."""
    show = "--show" in args
    clear = "--clear" in args
    clear_key = "--clear-key" in args
    clear_gemini_key = "--clear-gemini-key" in args

    key_value = None
    gemini_key_value = None
    url_value = None

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
        else:
            i += 1

    path = _config_path()

    if show:
        cfg = _load_config()
        print(f"Config file: {path}", file=sys.stderr)
        print(f"File exists: {'yes' if os.path.isfile(path) else 'no'}", file=sys.stderr)
        print(file=sys.stderr)
        for name, default in [("BIOMAPI_KEY", None), ("GEMINI_API_KEY", None), ("BIOMAPI_URL", "https://biomapi.com")]:
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
                _save_config(cfg)
            else:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            print(f"Removed: {', '.join(removed)}", file=sys.stderr)
        else:
            print("Nothing to remove.", file=sys.stderr)
        return

    # Flag-based (non-interactive): at least one value flag provided
    if key_value is not None or gemini_key_value is not None or url_value is not None:
        cfg = _load_config()
        if key_value is not None:
            cfg["BIOMAPI_KEY"] = key_value
        if gemini_key_value is not None:
            cfg["GEMINI_API_KEY"] = gemini_key_value
        if url_value is not None:
            cfg["BIOMAPI_URL"] = url_value
        saved = _save_config(cfg)
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

    saved = _save_config(cfg)
    print(f"\nSaved to {saved}", file=sys.stderr)


def cmd_process(file_paths: list[str], generate_pin: bool = True) -> None:
    """Upload one or more biometry files for AI extraction, concurrently."""
    if len(file_paths) == 1:
        result = _process_one(file_paths[0], generate_pin)
        print(json.dumps(result))
        if result.get("error"):
            sys.exit(1)
        return

    results = [None] * len(file_paths)
    with ThreadPoolExecutor(max_workers=len(file_paths)) as executor:
        futures = {executor.submit(_process_one, fp, generate_pin): i for i, fp in enumerate(file_paths)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    for result in results:
        print(json.dumps(result))

    if any(r.get("error") for r in results if r):
        sys.exit(1)


def cmd_retrieve(biompin_code: str) -> None:
    """Retrieve biometry data using a BiomPIN code."""
    from urllib.parse import quote

    url = f"{BASE_URL}/api/v1/biom/retrieve?biom_pin={quote(biompin_code)}"
    result = _request("GET", url)

    if result.get("error"):
        print(json.dumps(result))
        sys.exit(1)

    saved_path = _save_result(result, os.path.join(os.getcwd(), "_retrieve"))
    patient = (result.get("data") or {}).get("patient", {})
    biompin_info = result.get("biompin") or {}
    biompin = biompin_info.get("pin") if isinstance(biompin_info, dict) else None
    print(json.dumps({
        "patient_id": patient.get("id"),
        "patient_name": patient.get("name"),
        "device": (result.get("data") or {}).get("biometer", {}).get("device_name"),
        "biompin": biompin or biompin_code,
        "saved_json": saved_path,
    }))


def cmd_csv(json_paths: list[str], output_dir: str) -> None:
    """Generate a byeye CSV by sending JSON files to the API's /biom/csv endpoint."""
    os.makedirs(output_dir, exist_ok=True)

    responses = []
    for p in json_paths:
        p = os.path.abspath(p)
        try:
            with open(p, encoding="utf-8") as f:
                responses.append({"json_data": json.load(f), "filename": os.path.basename(p)})
        except Exception as e:
            print(json.dumps({"error": True, "detail": f"Could not read {p}: {e}"}))
            sys.exit(1)

    payload = json.dumps({"responses": responses}).encode("utf-8")
    result = _request("POST", f"{BASE_URL}/api/v1/biom/csv", body=payload, content_type="application/json", raw=True)

    if isinstance(result, dict) and result.get("error"):
        print(json.dumps(result))
        sys.exit(1)

    out_path = os.path.join(output_dir, "biomapi_byeye.csv")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(result)

    print(json.dumps({"byeye": os.path.abspath(out_path)}))


def cmd_usage() -> None:
    """Check current rate limit usage for the authenticated key or public IP."""
    result = _request("GET", f"{BASE_URL}/api/v1/biom/usage")
    print(json.dumps(result))
    if isinstance(result, dict) and result.get("error"):
        sys.exit(1)


def cmd_status() -> None:
    """Check lightweight API status."""
    result = _request("GET", f"{BASE_URL}/api/v1/status")
    print(json.dumps(result))
    if result.get("error"):
        sys.exit(1)


_HELP = """\
BiomAPI CLI — zero-dependency Python client for BiomAPI biometry extraction.

Usage:
  biomapi.py configure                     [--key <key>] [--gemini-key <key>] [--show] [--clear]
  biomapi.py process <file> [<file2> ...]  [--no-pin] [--key <key>] [--gemini-key <key>]
  biomapi.py retrieve <biompin_code>       [--key <key>]
  biomapi.py csv <file.json> [<file2.json> ...] [--output <dir>]
  biomapi.py usage                         [--key <key>]
  biomapi.py status
  biomapi.py --help

Commands:
  configure    Save API keys to the config file (works on Windows, macOS, Linux).
               Run with no flags for interactive setup, or use flags directly:
                 --key <key>         Set BIOMAPI_KEY
                 --gemini-key <key>  Set GEMINI_API_KEY
                 --url <url>         Set BIOMAPI_URL (for self-hosted instances)
                 --show              Display current configuration
                 --clear             Remove the config file
                 --clear-key         Remove only BIOMAPI_KEY from config
                 --clear-gemini-key  Remove only GEMINI_API_KEY from config

  process      Extract biometry from PDF/PNG/JPG/JPEG/JSON (max 20MB each).
               Saves full JSON response next to each source file.
               BiomPIN is generated by default; use --no-pin to skip.

  retrieve     Fetch previously extracted data using a BiomPIN code.
               Saves full JSON to the current directory.

  csv          Export saved JSON results to a by-eye CSV via the API.
               Requires network access. Output: biomapi_byeye.csv.

  usage        Show current rate-limit usage for your key or public IP.

  status       Lightweight API status — returns deployment metadata and db_id.

Options:
  --no-pin          Skip BiomPIN generation on process (default: generate)
  --key <key>       Override BIOMAPI_KEY (higher daily limits)
  --gemini-key <k>  Override GEMINI_API_KEY (BYOK — separate biomai_byok bucket)
  -h, --help        Show this help message

Environment variables:
  BIOMAPI_KEY      BiomAPI key for higher rate limits on all endpoints
  GEMINI_API_KEY   Your own Gemini API key (BYOK — separate biomai_byok bucket)
  BIOMAPI_URL      API base URL (default: https://biomapi.com)

Config file (~/.config/biomapi/config):
  Simple KEY=VALUE pairs. Same keys as environment variables.
  Run `python biomapi.py configure` to create/update it automatically.
  Priority: CLI flags > env vars > config file

Access tiers:
  No key             30 process / 1000 retrieve per day (per IP)
  BIOMAPI_KEY        Custom quota per user
  GEMINI_API_KEY     biomai_byok bucket (uses your Gemini quota)

Examples:
  python biomapi.py configure                     # interactive setup
  python biomapi.py configure --key biom_abc123   # set key directly
  python biomapi.py configure --show              # view current config
  python biomapi.py process report.pdf
  python biomapi.py process *.pdf --no-pin
  python biomapi.py retrieve lunar-rocket-731904
  python biomapi.py csv biomapi-*.json --output ./exports
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

    # Extract global flags: --key and --gemini-key (override module-level vars)
    global API_KEY, GEMINI_API_KEY
    filtered = []
    i = 0
    while i < len(args):
        if args[i] == "--key" and i + 1 < len(args):
            API_KEY = args[i + 1]
            i += 2
        elif args[i] == "--gemini-key" and i + 1 < len(args):
            GEMINI_API_KEY = args[i + 1]
            i += 2
        else:
            filtered.append(args[i])
            i += 1
    args = filtered

    if not args:
        print("Usage: biomapi.py <configure|process|retrieve|csv|usage|status> [args]", file=sys.stderr)
        sys.exit(1)

    command = args[0]

    if command == "process":
        if len(args) < 2:
            print("Usage: biomapi.py process <file_path> [<file_path2> ...] [--no-pin]", file=sys.stderr)
            sys.exit(1)
        rest = args[1:]
        generate_pin = "--no-pin" not in rest
        file_paths = [a for a in rest if a != "--no-pin"]
        cmd_process(file_paths, generate_pin)

    elif command == "retrieve":
        if len(args) < 2:
            print("Usage: biomapi.py retrieve <biompin_code>", file=sys.stderr)
            sys.exit(1)
        cmd_retrieve(args[1])

    elif command == "csv":
        if len(args) < 2:
            print("Usage: biomapi.py csv <file.json> [<file2.json> ...] [--output <dir>]", file=sys.stderr)
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
        cmd_csv(json_paths, output_dir)

    elif command == "usage":
        cmd_usage()

    elif command == "status":
        cmd_status()

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Commands: configure, process, retrieve, csv, usage, status", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
