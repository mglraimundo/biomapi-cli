---
name: biomapi
description: Process optical biometry reports (PDF/images/JSON), retrieve BiomPIN results, export BiomAPI CSV files, check usage or service status, and create prefilled ESCRS IOL Calculator links through biomapi.com. Use when the user provides a biometry report, mentions a BiomPIN code (word-word-123456), asks to use BiomAPI, or wants an ESCRS IOL Calculator link from a report.
---

# BiomAPI

Extract structured biometry data from optical biometry device reports (PDF/images/JSON) using the BiomAPI service at `https://biomapi.com`.

Introduce the skill as **BiomAPI**. The installation ID is `biomapi-cli`; the bundled CLI is an implementation detail. Start with the requested workflow, not key setup: public BiomAI access includes 25 credits per rolling 24 hours per IP (one credit per Standard PDF/image extraction). Check `usage` for the caller's actual allowance when needed. Keys are optional advanced configuration.

## Data Reference

See [reference.md](reference.md) for the response schema, field units and validation ranges, supported devices, enum values, posterior keratometry, and metadata. Load it only when rendering a biometry table, checking schema constraints, or explaining a response field — not for standard extractions or clinical interpretation.

## Client Script

The API client is `scripts/biomapi.py` relative to this `SKILL.md`. It requires Python 3.11+ and has zero external dependencies.

Resolve the script to an absolute path before invoking it; do not assume the current working directory is the skill directory. In the commands below, `<biomapi-script>` means that absolute path.

```bash
python3 <biomapi-script> <command> [args]
```

- Claude Code exposes the skill directory as `${CLAUDE_SKILL_DIR}`.
- Codex exposes the selected `SKILL.md` path; resolve `scripts/biomapi.py` from its parent directory.
- On Windows, use an available Python 3.11+ launcher and forward-slash paths.

## Privacy and Safety

- Pass source file paths directly to the script. Do not read the PDF, image, or JSON into the host assistant's context.
- The host assistant does not inspect the source file, but the script uploads it to BiomAPI. BiomAPI and, for PDF/image extraction, Google Gemini process the report.
- BiomAPI is not a medical device. Do not diagnose, recommend treatment, calculate clinical decisions, or interpret measurements unless the user separately requests general information. Tell users to verify extracted values against the original report.
- The `#biomctx=...` URL fragment is reversible, not encrypted. It can contain the patient acronym and patient ID. It is not sent in a normal HTTP request, but anyone receiving the complete URL can decode it; handle that URL as patient-identifying data.
- Never ask users to paste API keys into the conversation. Direct them to configure keys locally or use environment variables.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BIOMAPI_URL` | No | `https://biomapi.com` | API base URL |
| `ESCRS_IOL_CALCULATOR_URL` | No | `https://iolcalculator.escrs.org` | ESCRS calculator base URL for generated links |
| `BIOMAPI_KEY` | No | *(none)* | API key for higher rate limits |
| `GEMINI_API_KEY` | No | *(none)* | Your own Gemini API key (BYOK — uses the separate `biomai_byok` bucket) |

Public access without a key uses the deployment-configured per-IP quotas. Standard PDF/image processing costs 1 credit and Slow costs 0.5. Use the `usage` command for the limits that apply to the caller.

**Access tiers:**

| `BIOMAPI_KEY` | `GEMINI_API_KEY` | `process` limit | `retrieve` limit |
|---|---|---|---|
| — | — | Public `biomai` quota | Public `retrieve` quota |
| ✓ | — | Custom quota (per user) | Custom quota (per user) |
| — | ✓ | `biomai_byok` bucket (your Gemini quota) | Public `retrieve` quota |
| ✓ | ✓ | `biomai_byok` bucket (your Gemini quota) | Custom quota (per user) |

### Advanced: optional key setup

Do not prompt for keys during ordinary use. If the user asks for setup, first run `configure --show`. This reports whether keys are configured and their source without printing any part of a key. Do not read the config file or print environment variables. For a 429, check `usage` and explain when credits return; discuss keys only as an optional alternative when relevant to the limit.

1. **BIOMAPI_KEY** — for higher daily limits: obtained from the BiomAPI operator.
2. **GEMINI_API_KEY** — for BYOK processing in the separate `biomai_byok` bucket using the user's own Gemini API key from [aistudio.google.com](https://aistudio.google.com).

Resolve the installed script and an available Python 3.11+ interpreter, then give the user one copyable `configure` command with the real absolute paths, correctly quoted for their terminal. Never leave `<biomapi-script>` or a guessed plugin cache path in user-facing instructions. The user runs this command in their own terminal, where key entry is hidden. Do not start an interactive key prompt in an agent tool session, ask for a key in chat, or populate a command with a secret supplied in chat.

Enter keeps an existing key or skips an optional key. Settings in `~/.config/biomapi/config` are shared by Codex, Claude Code, and the standalone CLI under the same user account on the same machine, and survive plugin updates. Website and remote settings are separate.

After the user completes setup, run `configure --show`. Run `usage` too if they ask to verify service access or limits; do not upload a report just to test setup. Environment variables override saved settings, and the configuration check identifies that source. Saving a key does not validate it with the service.

Keys can also be provided through environment variables. Command-line `--key` and `--gemini-key` flags remain available for controlled automation, but can be exposed through shell history or process listings.

### Updating BiomAPI

When the user explicitly asks to update BiomAPI, use the host's plugin manager. Do not check for updates on every extraction, silently update, or overwrite files in the installed plugin cache.

For the GitHub marketplace installation documented by BiomAPI:

- Codex: run `codex plugin marketplace upgrade biomapi`, then `codex plugin add biomapi-cli@biomapi` only if the refresh succeeded.
- Claude Code: run `claude plugin marketplace update biomapi-plugins`, then `claude plugin update biomapi-cli@biomapi-plugins` only if the refresh succeeded. Preserve the installed scope; inspect `claude plugin list` and use the matching `--scope` when needed.

First verify that the installed source matches `mglraimundo/biomapi-cli` and these marketplace names using the host's marketplace list command. For a different or local source, explain what is installed and use its update mechanism instead of adding or switching marketplaces. If the host CLI is unavailable, provide the manual steps from the [AI skill guide](https://biomapi.com/docs/guides/ai-skill/#update-biomapi).

Report the command outcome accurately, including when no new version was available. After a successful update, ask the user to start a new Codex task or restart Claude Code so the refreshed skill is loaded. Saved keys remain separate from the plugin and do not need to be entered again.

## Commands

### Configure API keys

```bash
python3 <biomapi-script> configure
python3 <biomapi-script> configure --show
```

### Process a biometry file

```bash
python3 <biomapi-script> process /path/to/report.pdf
```

Multiple files in one call (processed concurrently inside the script):

```bash
python3 <biomapi-script> process file1.pdf file2.pdf file3.pdf
python3 <biomapi-script> process file1.pdf file2.pdf --service-tier slow
```

- Accepts: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.json`; the server enforces its configured per-file upload limit
- BiomPIN is generated **by default** — use `--no-pin` only if the user explicitly asks not to share
- `--service-tier standard|slow` applies to PDF/images. Standard costs 1 BiomAI credit; Slow costs 0.5 and may take substantially longer. Use Standard unless the user requests Slow or prioritizes quota efficiency over latency.

### Retrieve via BiomPIN

```bash
python3 <biomapi-script> retrieve word-word-123456
```

The retrieve command also accepts a full BiomAPI or ESCRS URL containing `#biomctx=...`; use that form when available so patient name/ID can be restored locally after BiomPIN retrieval.

### Generate export archive

```bash
python3 <biomapi-script> export file1.json [file2.json ...] [--output /path/to/dir]
```

- Input: one or more `saved_json` paths (from `process` or `retrieve` output)
- `--output`: directory for the ZIP file (default: current working directory)
- Output: `{"export": "/abs/path/biomapi_export.zip"}`
- The ZIP contains combined CSV and XLSX spreadsheets plus one JSON file per response
- **Requires network access** — the archive is generated server-side

### Check rate limit usage

```bash
python3 <biomapi-script> usage
```

### Check API status

```bash
python3 <biomapi-script> status
```

Returns lightweight API status and deployment metadata, including `db_id`.


## Output Behavior

**Be concise.** The user is a clinical expert. Do NOT comment on, interpret, or analyze biometric values. Provide commentary or recommendations only if the user **explicitly asks**.

## Script Output Format

The script always outputs a **compact summary** JSON (not the full API response) to stdout — one object per file in input order:

```json
{
  "patient_id": "12345",
  "patient_name": "JD",
  "device": "IOLMaster 700",
  "biompin": "lunar-rocket-731904",
  "biomapi_url": "https://biomapi.com/pin/lunar-rocket-731904#biomctx=...",
  "escrs_url": "https://iolcalculator.escrs.org?biompin=lunar-rocket-731904#biomctx=...",
  "saved_json": "/abs/path/to/biomapi-12345-iolmaster700.json"
}
```

The **full raw API response** (including all metadata, LLM metrics, timings) is always saved to disk at `saved_json` automatically.

**Patient name**: ALWAYS use `patient_name` exactly as returned in the **compact stdout summary** (e.g., `"patient_name": "MAG"`). This is often an acronym or abbreviation — that is intentional. Do NOT replace it with the full name from the saved JSON, the PDF, the filename, or any other source. Even if the full JSON contains a longer or different name string, the compact summary's `patient_name` is the canonical display name. Never expand, reconstruct, or infer the name.

## Presenting Results

### Single file

**Default output** — show the compact info block only. Do not read, attach, or transform `saved_json`.

```
Patient: JD (ID: 12345)

BiomPIN: lunar-rocket-731904

BiomAPI: <biomapi_url from stdout>

ESCRS IOL Calculator: <escrs_url from stdout>

Saved: /abs/path/to/biomapi-jd-iolmaster700.json
```

Each line MUST be separated by a blank line for readability. Do not collapse them into a single block.

- BiomPIN is generated **by default**; use `--no-pin` only if the user explicitly requests no BiomPIN
- Use the `biomapi_url` and `escrs_url` fields exactly as returned. When they contain `#biomctx=...`, treat the complete URL as patient-identifying data.
- If no BiomPIN: show patient line and Saved path only (no URLs)
- No biometry table unless the user explicitly asks

### Multiple files

After processing all files, **automatically** run the `export` command with all `saved_json` paths:

```bash
python3 <biomapi-script> export file1.json file2.json ... --output /path/to/dir
```

Then return the generated export ZIP as a clickable file link or attachment supported by the host. Do not return separate files unless requested.

Show a compact summary listing all patients processed, then the export ZIP.

### Biometry table (on request)

**If the user asks for the biometry table**, use the `Read` tool on `saved_json` and render a compact table with device name as header, both eyes side by side, measurements in this exact order:

| {Device Name} | Right (OD) | Left (OS) |
|---|---|---|
| Lens Status | Phakic | Phakic |
| Post Refractive | None | None |
| AL (mm) | 23.45 | 23.52 |
| ACD (mm) | 3.12 | 3.08 |
| LT (mm) | 4.52 | 4.48 |
| WTW (mm) | 11.80 | 11.90 |
| CCT (μm) | 545 | 542 |
| n | 1.3375 | 1.3375 |
| K1 (D) | 43.25 | 43.00 |
| K1 Axis (°) | 5 | 175 |
| K2 (D) | 44.50 | 44.25 |
| K2 Axis (°) | 95 | 85 |

If `extra_data.posterior_keratometry` exists, add a **Posterior Keratometry** table:

| {PK Device Name} | Right (OD) | Left (OS) |
|---|---|---|
| PK1 (D) | 6.12 | 6.08 |
| PK1 Axis (°) | 8 | 172 |
| PK2 (D) | 6.45 | 6.38 |
| PK2 Axis (°) | 98 | 82 |

Table formatting rules:
- Show `null` values as `—`
- K1/K2 magnitude and axis are always separate rows (not combined)
- AL, ACD, LT, WTW: 2 decimal places. CCT: 0 decimals. n: 4 decimals. K1/K2/PK: 2 decimals. Axes: 0 decimals.


## ESCRS IOL Calculation Shortcut

When the user asks for an **ESCRS IOL calculation** (or similar phrasing like "calculate the IOL", "run this through ESCRS", etc.) given a biometry PDF or image, the default output already includes the ESCRS link — no special handling needed. Just process normally (BiomPIN is on by default).


## Error Handling

The script outputs JSON with `"error": true` on failure. Keep error messages brief:
- **429**: Use `usage` to inspect the applicable quota and explain when credits return. Mention Slow if a half credit is available; keys are an optional advanced alternative when relevant.
- **Connection failed**: Service may be temporarily unavailable.
- **Unsupported file type**: Only `.pdf`, `.png`, `.jpg`, `.jpeg`, `.json` supported.
- **File too large**: The server response reports the deployment's configured maximum.

## File Handling

Pass source file paths directly to `biomapi.py process` — never use a file-reading tool on them. The script handles local I/O and sends the source to BiomAPI for processing. Do not read `saved_json` by default; display its path. Read it only when the user explicitly asks for the biometry table or a response-field explanation.

For export, use the `export` command with the `saved_json` paths. Do not build the CSV or XLSX manually.
