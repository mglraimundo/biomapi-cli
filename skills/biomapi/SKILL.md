---
name: biomapi
description: Process optical biometry reports (PDF/images/JSON), retrieve BiomPIN results, export BiomAPI CSV files, check usage or service status, and create prefilled ESCRS IOL Calculator links through biomapi.com. Use when the user provides a biometry report, mentions a BiomPIN code (word-word-123456), asks to use BiomAPI, or wants an ESCRS IOL Calculator link from a report.
---

# BiomAPI - AI Biometry Extraction

Extract structured biometry data from optical biometry device reports (PDF/images/JSON) using the BiomAPI service at `https://biomapi.com`.

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

Public access (no key) is rate-limited to 15 extractions/day per IP.

**Access tiers:**

| `BIOMAPI_KEY` | `GEMINI_API_KEY` | `process` limit | `retrieve` limit |
|---|---|---|---|
| — | — | 15/day per IP | 1000/day per IP |
| ✓ | — | Custom quota (per user) | Custom quota (per user) |
| — | ✓ | `biomai_byok` bucket (your Gemini quota) | 1000/day per IP |
| ✓ | ✓ | `biomai_byok` bucket (your Gemini quota) | Custom quota (per user) |

### Helping Users Configure Keys

If a user reports a 429 rate limit error or asks how to get higher limits, help them set up keys:

1. **BIOMAPI_KEY** — for higher daily limits: obtained from the BiomAPI operator.
2. **GEMINI_API_KEY** — for BYOK processing in the separate `biomai_byok` bucket using the user's own Gemini API key from [aistudio.google.com](https://aistudio.google.com).

Tell the user to run the interactive configuration locally. Do not run it with a secret supplied in chat:

```bash
python3 <biomapi-script> configure
python3 <biomapi-script> configure --show
```

Keys can also be provided through environment variables. Command-line `--key` and `--gemini-key` flags remain available for controlled automation, but can be exposed through shell history or process listings.

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
```

- Accepts: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.json` (max 15 MiB each)
- BiomPIN is generated **by default** — use `--no-pin` only if the user explicitly asks not to share

### Retrieve via BiomPIN

```bash
python3 <biomapi-script> retrieve word-word-123456
```

The retrieve command also accepts a full BiomAPI or ESCRS URL containing `#biomctx=...`; use that form when available so patient name/ID can be restored locally after BiomPIN retrieval.

### Generate CSV export

```bash
python3 <biomapi-script> csv file1.json [file2.json ...] [--output /path/to/dir]
```

- Input: one or more `saved_json` paths (from `process` or `retrieve` output)
- `--output`: directory for the CSV file (default: current working directory)
- Output: `{"byeye": "/abs/path/biomapi_byeye.csv"}`
- **Requires network access** — CSV is generated server-side

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

After processing all files, **automatically** run the `csv` command with all `saved_json` paths:

```bash
python3 <biomapi-script> csv file1.json file2.json ... --output /path/to/dir
```

Then return the generated `byeye` CSV as a clickable file link or attachment supported by the host. Do not return individual JSON files unless requested.

Show a compact summary listing all patients processed, then the CSV file.

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
- **429**: Rate limited (15/day public). Suggest setting `BIOMAPI_KEY` or `GEMINI_API_KEY` for higher limits.
- **Connection failed**: Service may be temporarily unavailable.
- **Unsupported file type**: Only `.pdf`, `.png`, `.jpg`, `.jpeg`, `.json` supported.
- **File too large**: Max 15 MiB.

## File Handling

Pass source file paths directly to `biomapi.py process` — never use a file-reading tool on them. The script handles local I/O and sends the source to BiomAPI for processing. Do not read `saved_json` by default; display its path. Read it only when the user explicitly asks for the biometry table or a response-field explanation.

For CSV export, use the `csv` command with the `saved_json` paths — never build CSV manually.
