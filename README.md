# BiomAPI

![BiomAPI](skills/biomapi/assets/icon.png)

Extract optical biometry reports, retrieve BiomPINs, and export spreadsheets from Codex or Claude Code. The plugin bundles a zero-dependency Python client, which also runs as a standalone CLI.

**No API key needed:** public access includes 25 BiomAI credits per rolling 24 hours per IP, enough for 25 Standard PDF/image extractions. See [credits and limits](https://biomapi.com/docs/api/rate-limits/).

## Install the plugin

Requires Python 3.11+ and internet access.

### Codex

Run in your terminal:

```bash
codex plugin marketplace add mglraimundo/biomapi-cli
codex plugin add biomapi-cli@biomapi
```

Start a new task and select **BiomAPI** or mention it in your prompt.

### Claude Code

Run inside Claude Code:

```text
/plugin marketplace add mglraimundo/biomapi-cli
/plugin install biomapi-cli@biomapi-plugins
```

Start a new conversation. Ask to use BiomAPI, or invoke `/biomapi-cli:biomapi` explicitly. The installation ID remains `biomapi-cli`; clients that support display names show **BiomAPI**.

## Try it

Select a report or provide its local path, then ask:

> Use BiomAPI to process this report and give me the ESCRS calculator link.

> Show the measurements for both eyes from that result.

> Process these three reports with BiomAPI and give me the combined spreadsheet export.

For a first test without patient data, save the [synthetic JSON report](https://biomapi.com/docs/examples/report.json) and ask BiomAPI to process it.

The default result is a compact summary with a BiomPIN, sharing links, and a saved JSON path. Ask for a table to see the measurements, or specify “without a BiomPIN” to skip sharing. Multiple reports produce an export ZIP with CSV, XLSX, and JSON files.

See the [AI skill guide](https://biomapi.com/docs/guides/ai-skill/) for more prompts, setup, and troubleshooting, and [supported devices](https://biomapi.com/docs/guides/supported-devices/) for biometer coverage.

## Updates

Ask “Update the BiomAPI plugin to the latest version,” or follow the [manual update instructions](https://biomapi.com/docs/guides/ai-skill/#update-biomapi). Start a new Codex task or restart Claude Code afterward. Updates include the bundled client and preserve saved keys. Claude Code also offers marketplace auto-updates; BiomAPI itself does not silently update during extraction.

## Advanced: optional keys

You can skip key setup and use the public allowance. If you have an assigned quota, ask “Help me set up my BiomAPI key.” To use your own Google quota, ask “Help me use my own Gemini key with BiomAPI.”

The assistant checks existing settings and gives you the installed client's setup command to run in your own terminal. Key entry is hidden; keep keys out of the conversation. Settings are shared across Codex, Claude Code, and the standalone CLI under the same user account on the same machine. See [optional key setup](https://biomapi.com/docs/guides/ai-skill/#advanced-optional-api-keys).

## Standalone CLI

Download and unzip [biomapi-cli.zip](https://github.com/mglraimundo/biomapi-cli/raw/main/biomapi-cli.zip), then run:

```bash
python3 biomapi.py process report.pdf
```

See the [CLI reference](cli/README.md) for all commands and configuration options.

## Data handling

The client uploads reports to BiomAPI. PDF and image extraction also sends the source to Google Gemini. The assistant can pass a local path without reading the original report into its own context; the host's attachment handling is separate.

Sharing is enabled by default. Returned URLs may contain a reversible `#biomctx=...` fragment with patient context; treat the full URL as patient-identifying data. Verify extracted measurements against the source. See the [Privacy Policy](https://biomapi.com/docs/privacy/).

## License

MIT
