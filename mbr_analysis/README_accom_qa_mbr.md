# Accommodation QA MBR Report

Automated data collection and interactive visualization for Accommodation QA Monthly Business Review metrics from Lark Base.

## Prerequisites

1. **Python 3.8+** installed
2. **lark-cli** installed and authenticated

### Install lark-cli

```bash
npm install -g @anthropic-ai/lark-cli
```

### Authenticate lark-cli

```bash
lark-cli auth login
```

You need the following permission scopes granted to your Lark app/user:
- `base:field:read`
- `base:record:read`

## Configuration

The script connects to a specific Lark Base table. These values are hardcoded at the top of `accom_qa_mbr_report.py`:

| Variable | Description | Current Value |
|----------|-------------|---------------|
| `BASE_TOKEN` | Lark Base app token | `LTgsbKdaIa65kdsGQPIl1wUggqc` |
| `TABLE_ID` | Data table ID | `tblqAZmTWnHRwDb2` |
| `VIEW_ID` | View ID | `vewhlHhNSt` |
| `IDENTITY` | Auth identity (`user` or `bot`) | `user` |

To point at a different Lark Base, update these constants in the script.

### LiteLLM Configuration (AI Narrative)

To enable the AI narrative panel, set these values in `accom_qa_mbr_report.py`:

| Variable | Description |
|----------|-------------|
| `LITELLM_API_KEY` | Your company LiteLLM API key |
| `LITELLM_BASE_URL` | Your LiteLLM endpoint (e.g. `https://litellm.yourcompany.com`) |
| `LITELLM_MODEL` | Model to use (default: `claude-sonnet-4.6`) |

The proxy server starts automatically every time the script runs and listens on a random local port. The port is printed to the terminal.

If `LITELLM_API_KEY` or `LITELLM_BASE_URL` are empty, the proxy still starts but the "Generate Analysis" button will show a configuration error.

### Customizing the AI Prompt

Edit `prompts/ams_system_prompt.txt` to change the system prompt without touching Python code.

Add `.md` files to `prompts/knowledge/` to inject additional context into every AI analysis call. Files are loaded alphabetically. Example: `prompts/knowledge/team_context.md`.

### AMS Child Tables

The script fetches these additional tables to power the AMS breakdown section:

| Table | ID | Purpose |
|-------|----|---------|
| AMS | `tblPLbLZqbJoSXS4` | Final score + pillar scores |
| Backend Coverage | `tbl3ffKbMhKz3Bs6` | Unit/Contract/Intra/Inter/E2E |
| Mobile Coverage | `tblqA5KCHCsUsvLR` | Unit/Integration/E2E |
| Web Coverage | `tbltpndvAxB7wSBe` | Unit/Component/E2E |
| Automation Effectiveness | `tblp9diZhqNOTz2I` | Per-platform AE |
| Baseline | `tblIHopqxv7vvPGP` | Manual effort baseline |

## Usage

```bash
# Default: filter "Accommodation" domain, output to accommodation_qa_mbr_trends.html
python3 accom_qa_mbr_report.py

# Open in browser automatically after generation
python3 accom_qa_mbr_report.py --open

# Filter a different domain
python3 accom_qa_mbr_report.py --domain "Flight"

# Custom output path
python3 accom_qa_mbr_report.py --output my_report.html
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--domain` | `Accommodation` | Domain name to filter records by |
| `--output` | `<domain>_qa_mbr_trends.html` | Output HTML file path |
| `--open` | off | Open the generated report in default browser |

## Output

The script generates a self-contained HTML file with:

- **From/To date range dropdowns** — filter charts and table to a specific period
- **Interactive trend charts** (powered by Chart.js) for all key metrics
- **Raw data table** with hyperlinks — click any value to open the source record in Lark Base

### Metrics Tracked

- Manual Hours
- Production Incidents
- SEV0-2 due to QA Miss
- Automation Effectiveness (%)
- QA Validation Coverage (%)
- Automation Maturity Score
- Unit Test Coverage (Backend / Mobile / Web)
- Contract Test Coverage
- Inter/Intra Service API Test Coverage
- E2E Test Coverage (Backend / Mobile / Web)
- Average Unit Test / E2E Test Coverage
- Production Bugs (Critical + Major + Minor)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `lark-cli: command not found` | Install with `npm install -g @anthropic-ai/lark-cli` |
| Permission denied / 403 | Re-run `lark-cli auth login` and ensure scopes are granted |
| TLS timeout / network error | Script auto-retries 3 times with exponential backoff |
| No records found | Verify the domain name matches exactly (case-sensitive) |
