#!/usr/bin/env python3
"""
Accommodation QA MBR Data Collection & Visualization Script

Collects data from Lark Base and generates an interactive HTML trend visualization.

Usage:
    python3 accom_qa_mbr_report.py [--domain Accommodation] [--output report.html]

Requirements:
    - lark-cli installed and authenticated (run: lark-cli auth login --domain base)
"""

import argparse
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# Configuration
BASE_TOKEN = "LTgsbKdaIa65kdsGQPIl1wUggqc"
TABLE_ID = "tblqAZmTWnHRwDb2"
VIEW_ID = "vewhlHhNSt"
IDENTITY = "user"

# LiteLLM config — set these before running
LITELLM_API_KEY  = ""   # your company LiteLLM API key
LITELLM_BASE_URL = ""   # e.g. https://litellm.yourcompany.com
LITELLM_MODEL    = "claude-sonnet-4.6"

# Child table IDs
TABLE_AMS                = "tblPLbLZqbJoSXS4"
TABLE_BACKEND_COVERAGE   = "tbl3ffKbMhKz3Bs6"
TABLE_MOBILE_COVERAGE    = "tblqA5KCHCsUsvLR"
TABLE_WEB_COVERAGE       = "tbltpndvAxB7wSBe"
TABLE_AUTO_EFFECTIVENESS = "tblp9diZhqNOTz2I"
TABLE_BASELINE           = "tblIHopqxv7vvPGP"

METRICS = [
    "Manual Hours",
    "Production Incidents",
    "SEV0-2 due to QA Miss",
    "Automation Effectiveness",
    "QA Validation Coverage",
    "Automation Maturity Score",
    "Unit Test Coverage (Backend)",
    "Unit Test Coverage (Mobile)",
    "Unit Test Coverage (Web)",
    "Contract Test Coverage",
    "Inter Service API Test Coverage",
    "Intra Service API Test Coverage",
    "E2E Test Coverage (Backend)",
    "E2E Test Coverage (Mobile)",
    "E2E Test Coverage (Web)",
    "Production Bugs",
    "Avg E2E Test Coverage",
    "Avg Unit Test Coverage",
]

# Column indices in the table output (0-based)
COL_MAP = {
    "Domain": 2,
    "Start Date": 3,
    "End Date": 4,
    "Manual Hours": 5,
    "Production Incidents": 6,
    "SEV0-2 due to QA Miss": 7,
    "Automation Effectiveness": 8,
    "QA Validation Coverage": 9,
    "Automation Maturity Score": 10,
    "Unit Test Coverage (Backend)": 22,
    "Unit Test Coverage (Mobile)": 23,
    "Unit Test Coverage (Web)": 24,
    "Contract Test Coverage": 25,
    "Inter Service API Test Coverage": 26,
    "Intra Service API Test Coverage": 27,
    "E2E Test Coverage (Backend)": 28,
    "E2E Test Coverage (Mobile)": 29,
    "E2E Test Coverage (Web)": 30,
    "Production Bugs (Critical)": 31,
    "Production Bugs (Major)": 32,
    "Production Bugs (Minor)": 33,
    "Avg Unit Test Coverage": 39,
    "Avg E2E Test Coverage": 40,
}


def fetch_records(max_retries=3):
    """Fetch records from Lark Base using lark-cli with retry on transient errors."""
    cmd = [
        "lark-cli", "base", "+record-list",
        "--as", IDENTITY,
        "--base-token", BASE_TOKEN,
        "--table-id", TABLE_ID,
        "--view-id", VIEW_ID,
        "--limit", "200",
    ]

    import time
    for attempt in range(1, max_retries + 1):
        print(f"Fetching records from Lark Base (attempt {attempt}/{max_retries})...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr

        if not output.strip():
            print("Error: No output from lark-cli. Ensure you are authenticated.")
            print("Run: lark-cli auth login --domain base")
            sys.exit(1)

        # Check for transient errors (TLS timeout, network issues)
        if "TLS handshake timeout" in output or "connection reset" in output.lower():
            if attempt < max_retries:
                wait = attempt * 5
                print(f"  Network error, retrying in {wait}s...")
                time.sleep(wait)
                continue
            else:
                print("Error: Network request failed after all retries.")
                print(output[:500])
                sys.exit(1)

        # Check for auth errors
        if '"ok": false' in output and "missing_scope" in output:
            print("Error: Missing permissions. Run: lark-cli auth login --domain base")
            sys.exit(1)

        return output

    return output


def parse_records(raw_output, domain_filter="Accommodation"):
    """Parse markdown table output into structured data."""
    lines = raw_output.splitlines()
    header_line = None
    data_lines = []

    for line in lines:
        if line.startswith("| _record_id"):
            header_line = line
        elif line.startswith("| ---"):
            continue
        elif line.startswith("| rec") and header_line:
            data_lines.append(line)

    if not header_line:
        print("Error: Could not find table header in output.")
        print("Raw output (first 500 chars):", raw_output[:500])
        sys.exit(1)

    # Filter by domain
    filtered = []
    for row in data_lines:
        cols = [c.strip() for c in row.split("|")[1:-1]]
        domain_col = cols[COL_MAP["Domain"]] if len(cols) > COL_MAP["Domain"] else ""
        if domain_filter in domain_col:
            filtered.append(cols)

    # Sort by End Date and deduplicate (keep last per End Date)
    filtered.sort(key=lambda c: c[COL_MAP["End Date"]])
    seen = {}
    for cols in filtered:
        end_date = cols[COL_MAP["End Date"]]
        if end_date and end_date.strip():
            seen[end_date] = cols

    unique = sorted(seen.values(), key=lambda c: c[COL_MAP["End Date"]])
    print(f"Found {len(unique)} unique {domain_filter} records.")
    return unique


def get_record_ids(records):
    """Extract record IDs (column 0) for each record."""
    return [r[0] for r in records]


def record_url(record_id):
    """Build Lark Base record URL."""
    return f"https://bytedance.larkoffice.com/base/{BASE_TOKEN}?table={TABLE_ID}&view={VIEW_ID}&record={record_id}"


def parse_pct(val):
    """Parse percentage string to float."""
    if not val or not val.strip():
        return None
    val = val.strip().replace("%", "")
    try:
        return float(val)
    except ValueError:
        return None


def parse_num(val):
    """Parse numeric string to float."""
    if not val or not val.strip():
        return None
    try:
        return float(val)
    except ValueError:
        return None


def extract_metrics(records):
    """Extract all metrics from parsed records."""
    labels = [r[COL_MAP["End Date"]][:10] for r in records]
    record_ids = get_record_ids(records)

    datasets = {}
    for metric in METRICS:
        if metric == "Production Bugs":
            # Sum of critical + major + minor
            values = []
            for r in records:
                c = parse_num(r[COL_MAP["Production Bugs (Critical)"]]) or 0
                m = parse_num(r[COL_MAP["Production Bugs (Major)"]]) or 0
                n = parse_num(r[COL_MAP["Production Bugs (Minor)"]]) or 0
                values.append(c + m + n)
            datasets[metric] = values
        elif metric in ("Automation Effectiveness", "QA Validation Coverage"):
            # Stored as decimal (0-1), convert to percentage
            raw = [parse_num(r[COL_MAP[metric]]) for r in records]
            datasets[metric] = [v * 100 if v is not None and v < 2 else v for v in raw]
        elif metric in ("Manual Hours", "Production Incidents", "SEV0-2 due to QA Miss",
                        "Automation Maturity Score"):
            datasets[metric] = [parse_num(r[COL_MAP[metric]]) for r in records]
        else:
            datasets[metric] = [parse_pct(r[COL_MAP[metric]]) for r in records]

    return labels, datasets, record_ids


def generate_html(labels, datasets, record_ids, domain, output_path):
    """Generate interactive HTML visualization."""
    # Serialize data for JS
    def to_js_array(arr):
        return json.dumps([v if v is not None else "null" for v in arr]).replace('"null"', 'null')

    # Build record URLs
    record_urls = [record_url(rid) for rid in record_ids]

    chart_groups = [
        {
            "title": "Manual Hours",
            "id": "chart1",
            "y_title": "Hours",
            "series": [("Manual Hours", "#2196F3")],
        },
        {
            "title": "Automation Effectiveness",
            "id": "chart2",
            "y_title": "%",
            "series": [("Automation Effectiveness", "#2196F3")],
        },
        {
            "title": "QA Validation Coverage",
            "id": "chart2b",
            "y_title": "%",
            "series": [("QA Validation Coverage", "#FF5722")],
        },
        {
            "title": "Automation Maturity Score",
            "id": "chart3",
            "y_title": "Score",
            "series": [("Automation Maturity Score", "#9C27B0")],
        },
        {
            "title": "Unit Test Coverage",
            "id": "chart4",
            "y_title": "%",
            "series": [
                ("Unit Test Coverage (Backend)", "#2196F3"),
                ("Unit Test Coverage (Mobile)", "#FF5722"),
                ("Unit Test Coverage (Web)", "#4CAF50"),
            ],
        },
        {
            "title": "API Test Coverage",
            "id": "chart5",
            "y_title": "%",
            "series": [
                ("Contract Test Coverage", "#2196F3"),
                ("Inter Service API Test Coverage", "#FF5722"),
                ("Intra Service API Test Coverage", "#4CAF50"),
            ],
        },
        {
            "title": "E2E Test Coverage",
            "id": "chart6",
            "y_title": "%",
            "series": [
                ("E2E Test Coverage (Backend)", "#2196F3"),
                ("E2E Test Coverage (Mobile)", "#FF5722"),
                ("E2E Test Coverage (Web)", "#4CAF50"),
            ],
        },
        {
            "title": "Average Coverage",
            "id": "chart7",
            "y_title": "%",
            "series": [
                ("Avg Unit Test Coverage", "#2196F3"),
                ("Avg E2E Test Coverage", "#FF5722"),
            ],
        },
        {
            "title": "Production Bugs & Incidents",
            "id": "chart8",
            "y_title": "Count",
            "series": [
                ("Production Bugs", "#FF5722"),
                ("Production Incidents", "#2196F3"),
                ("SEV0-2 due to QA Miss", "#4CAF50"),
            ],
        },
    ]

    # Build chart containers
    chart_divs = "\n".join(
        f'<div class="chart-container"><h2>{g["title"]}</h2><canvas id="{g["id"]}"></canvas></div>'
        for g in chart_groups
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build data table with hyperlinks
    table_headers = ["Week"] + [m for m in METRICS]
    table_rows_js = []
    for i, label in enumerate(labels):
        row = {"week": label, "url": record_urls[i]}
        for m in METRICS:
            row[m] = datasets[m][i]
        table_rows_js.append(row)

    html = f"""<!DOCTYPE html>
<html>
<head>
<title>{domain} QA MBR Trends</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; background: #f5f5f5; }}
h1 {{ text-align: center; color: #333; }}
h2 {{ color: #555; margin-top: 10px; font-size: 1.1em; }}
.meta {{ text-align: center; color: #666; margin-bottom: 20px; }}
.controls {{ text-align: center; margin-bottom: 20px; }}
.controls select {{ padding: 8px 16px; font-size: 14px; border-radius: 4px; border: 1px solid #ccc; }}
.chart-container {{ background: white; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
canvas {{ max-height: 320px; }}
.data-table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden; margin-top: 20px; }}
.data-table th {{ background: #333; color: white; padding: 10px 8px; font-size: 12px; text-align: center; }}
.data-table td {{ padding: 8px; text-align: center; border-bottom: 1px solid #eee; font-size: 13px; }}
.data-table td a {{ color: #1976D2; text-decoration: none; }}
.data-table td a:hover {{ text-decoration: underline; }}
.data-table tr:hover {{ background: #f0f7ff; }}
.data-table tr.hidden {{ display: none; }}
</style>
</head>
<body>
<h1>{domain} QA MBR - Trend Analysis</h1>
<p class="meta">Generated: {now} | Data points: {len(labels)} | Period: {labels[0]} to {labels[-1]}</p>

<div class="controls">
  <label for="weekFrom"><strong>From:</strong></label>
  <select id="weekFrom" onchange="filterRange()">
    {"".join(f'<option value="{i}">{l}</option>' for i, l in enumerate(labels))}
  </select>
  <label for="weekTo" style="margin-left:16px;"><strong>To:</strong></label>
  <select id="weekTo" onchange="filterRange()">
    {"".join(f'<option value="{i}"{" selected" if i == len(labels)-1 else ""}>{l}</option>' for i, l in enumerate(labels))}
  </select>
</div>

{chart_divs}

<h2 style="margin-top:30px;">Raw Data (click values to view in Lark Base)</h2>
<table class="data-table" id="dataTable">
<thead><tr>
  <th>Week</th>
  {"".join(f"<th>{m}</th>" for m in METRICS)}
</tr></thead>
<tbody>
{"".join(
    '<tr data-week="' + labels[i] + '"><td>' + labels[i] + '</td>' +
    "".join(
        f'<td><a href="{record_urls[i]}" target="_blank">{datasets[m][i] if datasets[m][i] is not None else "-"}</a></td>'
        for m in METRICS
    ) + '</tr>'
    for i in range(len(labels))
)}
</tbody>
</table>

<script>
const allLabels = {json.dumps(labels)};
const recordUrls = {json.dumps(record_urls)};
const allDatasets = {json.dumps({m: datasets[m] for m in METRICS})};
const chartConfigs = {json.dumps([{"id": g["id"], "yTitle": g["y_title"], "series": g["series"]} for g in chart_groups])};

let chartInstances = {{}};

function makeChart(id, datasets, yTitle, sliceLabels) {{
  if (chartInstances[id]) chartInstances[id].destroy();
  chartInstances[id] = new Chart(document.getElementById(id), {{
    type: 'line',
    data: {{ labels: sliceLabels, datasets }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{ y: {{ title: {{ display: true, text: yTitle }} }} }},
      plugins: {{ legend: {{ position: 'bottom' }} }}
    }}
  }});
}}

function filterRange() {{
  const from = parseInt(document.getElementById('weekFrom').value);
  const to = parseInt(document.getElementById('weekTo').value);
  const start = Math.min(from, to);
  const end = Math.max(from, to);
  const sliceLabels = allLabels.slice(start, end + 1);

  // Rebuild charts
  chartConfigs.forEach(cfg => {{
    const ds = cfg.series.map(([name, color]) => ({{
      label: name,
      data: allDatasets[name].slice(start, end + 1),
      borderColor: color,
      backgroundColor: color + '20',
      fill: false,
      tension: 0.3,
      spanGaps: true
    }}));
    makeChart(cfg.id, ds, cfg.yTitle, sliceLabels);
  }});

  // Attach click handlers: Production Bugs → JIRA, Production Incidents → Datadog
  addProductionClickHandler();

  // Filter table rows
  const rows = document.querySelectorAll('#dataTable tbody tr');
  rows.forEach((r, i) => {{
    if (i >= start && i <= end) {{
      r.classList.remove('hidden');
    }} else {{
      r.classList.add('hidden');
    }}
  }});
}}

function addProductionClickHandler() {{
  const chart = chartInstances['chart8'];
  if (!chart) {{
    console.warn('Production chart (chart8) not found.');
    return;
  }}
  chart.canvas.style.cursor = 'pointer';
  chart.canvas.removeEventListener('click', chart._clickHandler);
  chart._clickHandler = function(evt) {{
    const points = chart.getElementsAtEventForMode(evt, 'nearest', {{ intersect: true }}, true);
    if (!points.length) return;
    const idx = points[0].index;
    const datasetIdx = points[0].datasetIndex;
    const sliceLabels = chart.data.labels;
    const endDateStr = sliceLabels[idx];
    if (!endDateStr) return;
    const endDate = new Date(endDateStr);
    const startDate = new Date(endDate);
    startDate.setDate(startDate.getDate() - 30);
    const fmt = d => d.toISOString().slice(0, 10);
    const seriesName = chart.data.datasets[datasetIdx].label;
    if (seriesName === 'Production Bugs') {{
      const url = "https://29022131.atlassian.net/issues?jql=project%20%3D%20ACT%0AAND%20created%20%3E%3D%20%22" + fmt(startDate) + "%22%0AAND%20created%20%3C%3D%20%22" + fmt(endDate) + "%22%0AAND%20issueType%20IN%20%28bug%29%0AAND%20%22Environment%5BDropdown%5D%22%20%3D%20production%0AAND%20component%20%3D%20DEMAND%0AAND%20status%20%21%3D%20CANCELED%0AAND%20%22Severity%5BDropdown%5D%22%20IN%20%28Major%2C%20Minor%29";
      window.open(url, '_blank');
    }} else if (seriesName === 'Production Incidents') {{
      const from_ts = startDate.getTime();
      const to_ts = endDate.getTime() + 86400000 - 1;
      const url = "https://app.datadoghq.com/incidents?query=Domain%3A%28ast%20OR%20acd%20OR%20asi%29%20-incident_closure%3A%22False%20Positive%22%20incident_cause%3A%28Bug%20OR%20%22Configuration%20Issue%22%20OR%20%22Load%2FCapacity%20Issue%22%29%20-severity%3ASEV-3&from_ts=" + from_ts + "&to_ts=" + to_ts;
      window.open(url, '_blank');
    }}
  }};
  chart.canvas.addEventListener('click', chart._clickHandler);
}}

function filterWeek(week) {{}}

// Initial render (filterRange already calls addProductionClickHandler internally)
filterRange();
</script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
    print(f"Visualization saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Accommodation QA MBR Data Collection & Visualization")
    parser.add_argument("--domain", default="Accommodation", help="Domain to filter (default: Accommodation)")
    parser.add_argument("--output", default=None, help="Output HTML file path")
    parser.add_argument("--open", action="store_true", help="Open the report in browser after generation")
    args = parser.parse_args()

    if args.output is None:
        args.output = f"{args.domain.lower().replace(' ', '_')}_qa_mbr_trends.html"

    # Step 1: Fetch data
    raw_output = fetch_records()

    # Step 2: Parse and filter
    records = parse_records(raw_output, domain_filter=args.domain)
    if not records:
        print(f"No records found for domain: {args.domain}")
        sys.exit(1)

    # Step 3: Extract metrics
    labels, datasets, record_ids = extract_metrics(records)

    # Step 4: Generate visualization
    generate_html(labels, datasets, record_ids, args.domain, args.output)

    # Step 5: Optionally open
    if args.open:
        if sys.platform == "darwin":
            subprocess.run(["open", args.output])
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", args.output])
        else:
            print(f"Open {args.output} in your browser.")


if __name__ == "__main__":
    main()
