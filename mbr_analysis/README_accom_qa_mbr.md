# Accommodation QA MBR Report

Automated data collection and interactive visualization for Accommodation QA Monthly Business Review metrics from Lark Base.

## Prerequisites

1. **Python 3.8+** installed. The script only uses Python standard-library modules, including `sqlite3` for note storage.
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

### LiteLLM Configuration (AI Analysis for AMS)

The AI analysis panel uses a local Python proxy that forwards requests to LiteLLM. Configure it with environment variables before running the script:

| Environment Variable | Required | Description |
|----------------------|----------|-------------|
| `TVLK_LITELLM_KEY` | Yes | Traveloka LiteLLM API key |
| `LITELLM_BASE_URL` | No | LiteLLM endpoint. Defaults to `https://litellm.tvlk.cloud` |

Example:

```bash
export TVLK_LITELLM_KEY="<your-litellm-key>"
export LITELLM_BASE_URL="https://litellm.tvlk.cloud"
```

`LITELLM_MODEL` is configured in `accom_qa_mbr_report.py` and currently defaults to `gpt-5.5`.

The proxy server starts automatically every time the script runs. Keep the terminal process alive while using AI analysis. If `TVLK_LITELLM_KEY` is not set, the proxy still starts but the "Generate Analysis" button will show a configuration error.

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

# Recommended interactive mode: serve the report locally so notes persist to SQLite
python3 accom_qa_mbr_report.py --serve

# Filter a different domain
python3 accom_qa_mbr_report.py --domain "Flight"

# Custom output path
python3 accom_qa_mbr_report.py --output my_report.html

# Custom SQLite note database path
python3 accom_qa_mbr_report.py --serve --notes mbr_analysis/accommodation_notes.db

# Use a specific local server port
python3 accom_qa_mbr_report.py --serve --port 8088

# Run with AI analysis enabled
TVLK_LITELLM_KEY="<your-litellm-key>" python3 accom_qa_mbr_report.py --serve --open
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--domain` | `Accommodation` | Domain name to filter records by |
| `--output` | `<domain>_qa_mbr_trends.html` | Output HTML file path |
| `--notes` | `<output_stem>_notes.db` | SQLite database file for metric notes |
| `--open` | off | Open the generated report in default browser |
| `--serve` | off | Start a local Python HTTP service and open the report through it. Required for browser edits to persist directly to SQLite |
| `--port` | `0` | Port for `--serve`; `0` uses a random available local port |

## Tooling Flow

The script uses the following tools and storage surfaces:

- `lark-cli base +record-list` fetches records from Lark Base using the configured `BASE_TOKEN`, `TABLE_ID`, `VIEW_ID`, and `IDENTITY`.
- Python fetches the main MBR table plus AMS/coverage child tables in parallel, parses Lark Base markdown output, normalizes domain names, computes metrics, and builds weekly domain-comparison data.
- Chart.js is loaded from CDN in the generated HTML to render line and bar charts.
- SQLite stores metric notes in the `metric_notes` table when `--serve` is used.
- The local Python service exposes `GET /notes` and `POST /save-notes` so the browser can dynamically read and write notes.
- The local LiteLLM proxy powers the **AI Analysis for AMS** panel and streams the response into the generated HTML.

## Interactive Features

### Domain Performance

The report includes a **Domain Performance** section with three compact bar charts:

- Manual Hours
- Automation Maturity
- QA Validation Coverage

Use **Week Picker** to choose which week the domain bar charts show. Use the **Domains** dropdown to choose which domains are included in the graph.

By default, these domains are checked:

- Accommodation
- Travel Activities and Ground Transport
- Transport

Click **All** to show every included domain, **None** to clear the chart, or **Default** to restore the default selection. To avoid label overlap, non-default selections use compact labels such as `D1`, `D2`, etc., and the domain key below the charts maps each code to a readable label. Hover the chart or domain key to see the full domain name.

Domain labels are normalized for readability. For example, `['Accommodation']`-style values are shown without brackets or quotes, and escaped Lark values such as `Travel Activities \u0026 Ground Transport` or `Travel Activities \- Ground Transport` are shown as `Travel Activities and Ground Transport`.

Domain Performance excludes domains whose normalized names start with `TA` and excludes `Overall`.

### Metric Notes

Notes can be added to datapoints in these trend charts:

- Manual Hours
- Automation Effectiveness
- QA Validation Coverage

Hover a datapoint to view existing notes in the tooltip. The standard tooltip already shows the metric value; if notes exist, it also shows:

- Reason
- Mitigation Actions

Single-click a datapoint to pin the metric value on the chart. If that datapoint has notes, the pinned glass-style textbox also includes the reason and mitigation. Double-click a datapoint to open the note editor with fields for:

- Explanation / reason
- Mitigation actions

When the report is opened through `--serve`, notes are saved immediately to the SQLite database and are available after reopening the report. If the HTML is opened directly as a static file, notes loaded at generation time are visible, but browser edits cannot be written back to SQLite.

### AI Analysis for AMS

The **AI Analysis for AMS** panel summarizes how the Automation Maturity Score is calculated, the current period breakdown, key movers, and action items. It requires the local LiteLLM proxy started by the Python script and `TVLK_LITELLM_KEY` in the environment.

Generated analysis is cached in browser `localStorage` per domain and period. Use **Regenerate** to refresh the cached content.

## Output

The script generates a self-contained HTML file with:

- **From/To date range dropdowns** — filter charts and table to a specific period
- **Domain Performance date selector** — choose the week used for domain comparison bar charts
- **Domain Performance domain selector** — choose which domains appear in comparison bar charts
- **Interactive trend charts** (powered by Chart.js) for all key metrics
- **Datapoint notes** for selected metrics when served through the local Python service
- **AI Analysis for AMS** with LiteLLM-backed narrative and browser-side cache
- **Raw data table** with hyperlinks — click any value to open the source record in Lark Base

The generated HTML can still be opened directly, but use `--serve` for full note read/write behavior.

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
| Notes do not persist after reopening HTML | Run the report with `--serve`; direct `file://` HTML cannot write to SQLite |
| Port already in use | Omit `--port` to use a random available port, or choose another port |
| AI analysis says LiteLLM is not configured | Export `TVLK_LITELLM_KEY` before running the script |
| AI analysis says proxy is not reachable | Re-run the Python script and open the newly generated report from that same session. Do not use a stale HTML file from a previous run |
| Domain labels overlap | Use the default domain selection or rely on compact `D1`, `D2` labels plus the domain key for all-domain views |

## Run Tips

- Prefer `--serve` for day-to-day use because it enables SQLite note persistence from the browser.
- Keep the terminal process running while using AI analysis; the LiteLLM proxy lives inside that Python process.
- Use `--open` together with `--serve` if you want the browser to open automatically.
- Generated HTML and SQLite note files are local artifacts. The repository `.gitignore` excludes generated report HTML, but still check `git status` before committing.
- If the report looks stale, regenerate it from the latest Lark Base data instead of refreshing an old HTML file.

## Verification

Run the lightweight unit tests and syntax check after changing the script:

```bash
python3 -m unittest mbr_analysis/test_accom_qa_mbr_report.py
python3 -m py_compile mbr_analysis/accom_qa_mbr_report.py mbr_analysis/test_accom_qa_mbr_report.py
```
