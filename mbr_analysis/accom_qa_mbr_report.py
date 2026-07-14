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
import time
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
LITELLM_API_KEY  = "sk-fxYlNgQYTiGTg8ylEHbHIg"   # your company LiteLLM API key
LITELLM_BASE_URL = "https://litellm.tvlk.cloud"   # e.g. https://litellm.yourcompany.com
LITELLM_MODEL    = "claude-sonnet-4.6"

# Proxy port — fixed so the HTML can always reach the proxy regardless of when it was generated.
# Change this if the port is already in use on your machine.
PROXY_PORT_FIXED = 18234

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


def fetch_table_raw(table_id, label, results, errors, max_retries=3, view_id=None):
    """Fetch all records from a Lark Base table into results[label]. Non-fatal on error."""
    cmd = [
        "lark-cli", "base", "+record-list",
        "--as", IDENTITY,
        "--base-token", BASE_TOKEN,
        "--table-id", table_id,
        "--limit", "200",
    ]
    if view_id:
        cmd += ["--view-id", view_id]
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
            if not output.strip():
                raise RuntimeError("Empty output from lark-cli")
            if "TLS handshake timeout" in output or "connection reset" in output.lower():
                if attempt < max_retries:
                    time.sleep(attempt * 5)
                    continue
                raise RuntimeError("Network error after retries")
            if '"ok": false' in output and "missing_scope" in output:
                raise RuntimeError("Missing lark-cli scope — run: lark-cli auth login --domain base")
            results[label] = output
            return
        except Exception as e:
            if attempt < max_retries:
                print(f"  Warning: {label} fetch attempt {attempt} failed ({e}), retrying...")
            else:
                errors[label] = str(e)
                results[label] = None
                print(f"  Warning: failed to fetch {label} table after {max_retries} attempts ({e})")


def fetch_all_tables():
    """Fetch all 7 tables in parallel. Returns dict of raw outputs keyed by label."""
    tables = {
        "main":               TABLE_ID,
        "ams":                TABLE_AMS,
        "backend_coverage":   TABLE_BACKEND_COVERAGE,
        "mobile_coverage":    TABLE_MOBILE_COVERAGE,
        "web_coverage":       TABLE_WEB_COVERAGE,
        "auto_effectiveness": TABLE_AUTO_EFFECTIVENESS,
        "baseline":           TABLE_BASELINE,
    }
    results = {}
    errors = {}
    threads = []
    for label, table_id in tables.items():
        print(f"  Fetching {label} table ({table_id})...")
        view_id = VIEW_ID if label == "main" else None
        t = threading.Thread(
            target=fetch_table_raw,
            args=(table_id, label, results, errors),
            kwargs={"view_id": view_id},
            daemon=True,
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    if results.get("main") is None:
        print("Error: Failed to fetch main MBR table. Cannot continue.")
        sys.exit(1)
    return results


def parse_child_table(raw_output, domain_filter):
    """Parse a child table's markdown output into a list of row dicts for the domain.
    Each row dict has {column_name: value_string} plus '_start' and '_end' (YYYY-MM-DD).
    Returns empty list if raw_output is None or unparseable.

    Child tables use monthly periods, so rows are matched to weekly main-table records
    by range: start_date <= weekly_end_date <= end_date (see join_enriched_records).
    """
    if not raw_output:
        return []
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
        return []

    headers = [h.strip() for h in header_line.split("|")[1:-1]]
    result = []
    for row in data_lines:
        cols = [c.strip() for c in row.split("|")[1:-1]]
        if len(cols) < len(headers):
            cols += [""] * (len(headers) - len(cols))
        row_dict = dict(zip(headers, cols))

        domain_val = row_dict.get("Domain", "")
        if domain_filter not in domain_val:
            continue

        end_raw   = row_dict.get("End Date", "").strip()
        start_raw = row_dict.get("Start Date", "").strip()
        if not end_raw:
            continue
        row_dict["_end"]   = end_raw[:10]
        row_dict["_start"] = start_raw[:10] if start_raw else ""
        result.append(row_dict)
    return result


def _find_child_row(rows, weekly_end_date):
    """Find the child table row whose period contains weekly_end_date.
    Falls back to the row with the closest end date not before weekly_end_date."""
    if not rows:
        return {}
    # Exact range match: _start <= weekly_end_date <= _end
    for row in rows:
        if row.get("_start") and row.get("_end"):
            if row["_start"] <= weekly_end_date <= row["_end"]:
                return row
    # Fallback: latest row whose end date <= weekly_end_date
    candidates = [r for r in rows if r.get("_end", "") <= weekly_end_date]
    if candidates:
        return max(candidates, key=lambda r: r["_end"])
    # Last resort: earliest row
    return min(rows, key=lambda r: r.get("_end", ""))


def parse_baseline_table(raw_output, domain_filter):
    """Parse the Baseline table into a single dict {field: value} for the domain."""
    if not raw_output:
        return {}
    lines = raw_output.splitlines()
    header_line = None
    for line in lines:
        if line.startswith("| _record_id"):
            header_line = line
        elif line.startswith("| ---"):
            continue
        elif line.startswith("| rec") and header_line:
            cols = [c.strip() for c in line.split("|")[1:-1]]
            headers = [h.strip() for h in header_line.split("|")[1:-1]]
            if len(cols) < len(headers):
                cols += [""] * (len(headers) - len(cols))
            row_dict = dict(zip(headers, cols))
            domain_val = row_dict.get("Domain", "")
            if domain_filter in domain_val:
                return row_dict
    return {}


def join_enriched_records(main_records, child_data, baseline):
    """Join main records with child table data by date range.
    Child tables are monthly; main table is weekly — each weekly record is matched
    to the child row whose Start Date <= weekly End Date <= End Date.
    Returns list of enriched dicts, one per period, in main_records order."""
    enriched = []
    for rec in main_records:
        end_key = rec[COL_MAP["End Date"]][:10]
        row = {"_end_date": end_key, "_record": rec}
        for table_label, table_rows in child_data.items():
            row[table_label] = _find_child_row(table_rows, end_key)
        row["baseline"] = baseline
        enriched.append(row)
    return enriched


def extract_ams_data(enriched_records):
    """Extract AMS pillar and sub-component data from enriched records.
    Returns list of dicts, one per period, with all AMS fields as floats (None if missing)."""
    result = []
    for row in enriched_records:
        ams = row.get("ams", {})
        be  = row.get("backend_coverage", {})
        mob = row.get("mobile_coverage", {})
        web = row.get("web_coverage", {})
        ae  = row.get("auto_effectiveness", {})
        bl  = row.get("baseline", {})
        rec = row["_record"]

        def n(d, k):
            """Parse float from dict value, return None if missing/unparseable."""
            v = d.get(k, "")
            if not v or not str(v).strip():
                return None
            s = str(v).strip().replace("%", "").replace(",", "")
            try:
                return float(s)
            except ValueError:
                return None

        entry = {
            "end_date": row["_end_date"],
            # AMS score comes from the main MBR table (updated weekly, always current).
            # The AMS child table is monthly and may lag — main table is the source of truth.
            "ams_score":          parse_num(rec[COL_MAP["Automation Maturity Score"]]),
            # Pillar scores come from the AMS child table (monthly breakdown)
            "coverage_score":     n(ams, "Coverage"),
            "reliability_score":  n(ams, "Reliability"),
            "efficiency_score":   n(ams, "Efficiency"),
            "backend_stability":  n(ams, "Backend Stability"),
            "mobile_stability":   n(ams, "Mobile Stability"),
            "web_stability":      n(ams, "Web Stability"),
            # Coverage scores from child tables (not AMS table — those are comma-separated lists)
            "backend_coverage":   n(be,  "Backend Coverage"),
            "mobile_coverage_s":  n(mob, "Mobile Coverage"),
            "web_coverage_s":     n(web, "Web Coverage"),
            # Backend Coverage sub-components — field names as truncated by lark-cli
            "be_unit":            n(be, "Backend Unit Test"),
            "be_contract":        n(be, "Backend Contract Tes..."),
            "be_intra":           n(be, "Backend Intra-Servic..."),
            "be_inter":           n(be, "Backend Inter-Servic..."),
            "be_api_e2e":         n(be, "Backend API E2E"),
            # Mobile Coverage sub-components
            "mob_unit":           n(mob, "Unit Tests"),
            "mob_integration":    n(mob, "Integration Tests"),
            "mob_e2e":            n(mob, "E2E Tests"),
            # Web Coverage sub-components
            "web_unit":           n(web, "Unit Tests"),
            "web_component":      n(web, "Component Tests"),
            "web_e2e":            n(web, "E2E Tests"),
            # Automation Effectiveness per platform (0-1)
            "ae_backend":         n(ae, "Backend Automation E..."),
            "ae_mobile":          n(ae, "Mobile Automation Ef..."),
            "ae_web":             n(ae, "Web Automation Effec..."),
            # Manual hours (from main record)
            "manual_hours":       parse_num(rec[COL_MAP["Manual Hours"]]),
            # Baseline
            "baseline_hours":     n(bl, "Manual Effort Baseline"),
            # Full baseline dict for AI prompt context
            "baseline_dict":      bl,
        }
        result.append(entry)
    return result


def ams_maturity_label(score):
    """Return maturity level label for a given AMS score."""
    if score is None:
        return "N/A"
    if score >= 81:
        return "Level 5: Optimizing"
    if score >= 61:
        return "Level 4: Measured"
    if score >= 41:
        return "Level 3: Defined"
    if score >= 21:
        return "Level 2: Emerging"
    return "Level 1: Initial"


def efficiency_tier_label(manual_hours):
    """Return efficiency tier label based on manual hours."""
    if manual_hours is None:
        return "N/A"
    if manual_hours <= 50:
        return "Optimized"
    if manual_hours <= 100:
        return "Advanced"
    if manual_hours <= 150:
        return "Developing"
    return "Initial"

DEFAULT_SYSTEM_PROMPT = (
    "You are a QA metrics analyst explaining Automation Maturity Score (AMS) results "
    "to engineering leadership. Be concise, factual, and specific. Use plain English, "
    "not math notation. Structure your response in exactly four sections with these "
    'headers: "How AMS is Calculated", "This Period\'s Breakdown", "Key Movers", '
    '"Action Items". Each section should be 3-6 sentences. Do not use bullet points — '
    "write in paragraphs."
)


def load_system_prompt():
    """Load system prompt from prompts/ams_system_prompt.txt (fallback to default).
    Appends all *.md files from prompts/knowledge/ as a knowledge base section."""
    script_dir = Path(__file__).parent
    prompt_file = script_dir / "prompts" / "ams_system_prompt.txt"

    if prompt_file.exists():
        prompt = prompt_file.read_text(encoding="utf-8").strip()
    else:
        prompt = DEFAULT_SYSTEM_PROMPT

    knowledge_dir = script_dir / "prompts" / "knowledge"
    if knowledge_dir.exists():
        kb_files = sorted(knowledge_dir.glob("*.md"))
        if kb_files:
            kb_sections = []
            for f in kb_files:
                kb_sections.append(f"### {f.stem}\n{f.read_text(encoding='utf-8').strip()}")
            prompt += "\n\n## Knowledge Base\n\n" + "\n\n".join(kb_sections)
    return prompt


def _pct(val):
    """Format a 0-1 decimal as a percentage string, or return 'N/A'."""
    if val is None:
        return "N/A"
    return f"{val * 100:.1f}%"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Local proxy that forwards /analyze requests to LiteLLM."""

    system_prompt = ""   # set by start_proxy_server() before first request

    def log_message(self, fmt, *args):
        pass  # suppress default access log noise

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/analyze":
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            return

        # Check config
        if not LITELLM_API_KEY or not LITELLM_BASE_URL:
            self.send_response(503)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "LiteLLM not configured. Set LITELLM_API_KEY and LITELLM_BASE_URL in the script."
            }).encode())
            return

        # Read request body
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self.send_response(400)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing or empty request body"}).encode())
            return

        current = body.get("current", {})
        previous = body.get("previous")
        baseline = body.get("baseline", {})
        domain = body.get("domain", "")
        period = body.get("period", "")

        # Build user message
        user_msg = (
            f"Domain: {domain}\n"
            f"Period end date: {period}\n"
            f"Final AMS score: {current.get('ams_score')}\n"
            f"Maturity level: {ams_maturity_label(current.get('ams_score'))}\n\n"
            f"Pillar scores:\n"
            f"  Coverage ({current.get('coverage_score')} / 100, weight 40%)\n"
            f"    Backend Coverage ({current.get('backend_coverage')} / 100, weight 60% of Coverage)\n"
            f"      Unit Test: {_pct(current.get('be_unit'))}  [weight 35%]\n"
            f"      Contract Test: {_pct(current.get('be_contract'))}  [weight 35%]\n"
            f"      Intra-Service: {_pct(current.get('be_intra'))}  [weight 10%]\n"
            f"      Inter-Service: {_pct(current.get('be_inter'))}  [weight 15%]\n"
            f"      API E2E: {_pct(current.get('be_api_e2e'))}  [weight 5%]\n"
            f"    Mobile Coverage ({current.get('mobile_coverage_s')} / 100, weight 20% of Coverage)\n"
            f"      Unit Test: {_pct(current.get('mob_unit'))}  [weight 30%]\n"
            f"      Integration: {_pct(current.get('mob_integration'))}  [weight 20%]\n"
            f"      E2E: {_pct(current.get('mob_e2e'))}  [weight 50%]\n"
            f"    Web Coverage ({current.get('web_coverage_s')} / 100, weight 20% of Coverage)\n"
            f"      Unit Test: {_pct(current.get('web_unit'))}  [weight 30%]\n"
            f"      Component: {_pct(current.get('web_component'))}  [weight 20%]\n"
            f"      E2E: {_pct(current.get('web_e2e'))}  [weight 50%]\n"
            f"  Reliability ({current.get('reliability_score')} / 100, weight 30%)\n"
            f"    Backend Stability: {_pct(current.get('backend_stability'))}  [weight 50%]\n"
            f"    Mobile Stability: {_pct(current.get('mobile_stability'))}  [weight 25%]\n"
            f"    Web Stability: {_pct(current.get('web_stability'))}  [weight 25%]\n"
            f"  Efficiency ({current.get('efficiency_score')} / 100, weight 30%)\n"
            f"    Manual Hours: {current.get('manual_hours')} (baseline: {baseline.get('Manual Effort Baseline', 'N/A')})\n"
            f"    Tier: {efficiency_tier_label(current.get('manual_hours'))}\n"
        )

        if previous:
            user_msg += (
                f"\nPrevious period ({previous.get('end_date')}) for comparison:\n"
                f"  AMS: {previous.get('ams_score')}\n"
                f"  Coverage: {previous.get('coverage_score')}, "
                f"Reliability: {previous.get('reliability_score')}, "
                f"Efficiency: {previous.get('efficiency_score')}\n"
                f"  BE Unit: {_pct(previous.get('be_unit'))}, "
                f"BE Contract: {_pct(previous.get('be_contract'))}, "
                f"BE Inter: {_pct(previous.get('be_inter'))}, "
                f"BE Intra: {_pct(previous.get('be_intra'))}\n"
                f"  Manual Hours: {previous.get('manual_hours')}\n"
            )
        else:
            user_msg += "\nNo previous period available (this is the earliest record).\n"

        payload = json.dumps({
            "model": LITELLM_MODEL,
            "messages": [
                {"role": "system", "content": ProxyHandler.system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            "stream": True,
        }).encode()

        req = urllib.request.Request(
            f"{LITELLM_BASE_URL.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for line in resp:
                    self.wfile.write(line)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            body_err = e.read().decode(errors="replace")
            self.send_response(e.code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"LiteLLM HTTP {e.code}: {body_err[:300]}"}).encode())
        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


PROXY_IDLE_TIMEOUT = 7200  # seconds — proxy auto-shuts down after 2h of inactivity


def _kill_previous_proxy(port):
    """If a previous instance of this script is holding the fixed proxy port, kill it.
    Only kills processes whose command line contains this script's filename.
    Does nothing if the port is held by an unrelated process (leaves it alone)."""
    import signal
    script_name = Path(__file__).name  # e.g. "accom_qa_mbr_report.py"
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().splitlines()
    except Exception:
        return  # lsof not available — skip silently

    for pid_str in pids:
        try:
            pid = int(pid_str.strip())
            if pid == os.getpid():
                continue  # skip self (shouldn't happen but be safe)
            # Check if this PID belongs to the same script
            cmd_result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True
            )
            if script_name in cmd_result.stdout:
                os.kill(pid, signal.SIGTERM)
                print(f"  Closed previous proxy session (PID {pid}) on port {port}.")
                time.sleep(0.3)  # brief pause to let the port be released
            else:
                print(f"  Note: port {port} is held by a different process (PID {pid}, not this script). Using fallback port.")
        except (ValueError, ProcessLookupError):
            pass


def start_proxy_server():
    """Start the LiteLLM proxy on a random available port. Returns the port number.

    If a previous instance of this script is holding the fixed port, it is
    terminated automatically for a clean session. The server shuts itself down
    after PROXY_IDLE_TIMEOUT seconds of inactivity, freeing the port.
    """
    ProxyHandler.system_prompt = load_system_prompt()

    class _ReuseAddrServer(socketserver.TCPServer):
        allow_reuse_address = True

    # Kill any previous session of this script on the fixed port for a clean start.
    _kill_previous_proxy(PROXY_PORT_FIXED)

    # Try fixed port. Fall back to random only if held by a non-script process.
    try:
        server = _ReuseAddrServer(("localhost", PROXY_PORT_FIXED), ProxyHandler)
        port = PROXY_PORT_FIXED
    except OSError:
        server = _ReuseAddrServer(("localhost", 0), ProxyHandler)
        port = server.server_address[1]
        print(f"  Note: fixed port {PROXY_PORT_FIXED} still busy (held by another process), using port {port} instead.")
        print(f"  To always use the fixed port, free port {PROXY_PORT_FIXED} or change PROXY_PORT_FIXED in the script.")

    def _serve_with_timeout():
        server.timeout = PROXY_IDLE_TIMEOUT
        while True:
            # handle_request returns False (via select timeout) after server.timeout
            # seconds with no incoming connection — we use that as our idle signal.
            ready = server.socket.fileno() != -1  # sanity check
            if not ready:
                break
            import select
            r, _, _ = select.select([server.socket], [], [], PROXY_IDLE_TIMEOUT)
            if not r:
                # No activity for PROXY_IDLE_TIMEOUT seconds — shut down cleanly
                print(f"\nProxy server idle for {PROXY_IDLE_TIMEOUT // 60}min, shutting down and freeing port {port}.")
                server.server_close()
                break
            server.handle_request()

    t = threading.Thread(target=_serve_with_timeout, daemon=True)
    t.start()
    print(f"Proxy server running at http://localhost:{port} "
          f"(auto-closes after {PROXY_IDLE_TIMEOUT // 60}min idle, or Ctrl+C)")
    return port


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


def generate_html(labels, datasets, record_ids, domain, output_path, ams_data=None, proxy_port=None):
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

    # Serialize AMS data for JS
    ams_data_js = json.dumps(ams_data if ams_data else [])

    # Build AMS Overview HTML (placeholder divs — JS fills content)
    ams_overview_html = """
<div class="ams-overview" id="amsOverview">
  <h2 style="margin-top:0">Automation Maturity Score — Overview</h2>
  <div class="ams-score-row">
    <span class="ams-score-big" id="amsScoreBig">—</span>
    <span class="ams-level" id="amsLevelLabel"></span>
    <span id="amsDeltaBadge"></span>
  </div>
  <div style="margin-bottom:8px;font-size:13px;color:#555;">Pillar contributions to final score:</div>
  <div class="pillar-bar-wrap">
    <div class="pillar-bar-label">Coverage (40%): <span id="covContrib">—</span></div>
    <div class="pillar-bar-track"><div class="pillar-bar-fill" id="covBar" style="background:#4CAF50;width:0%"></div></div>
  </div>
  <div class="pillar-bar-wrap">
    <div class="pillar-bar-label">Reliability (30%): <span id="relContrib">—</span></div>
    <div class="pillar-bar-track"><div class="pillar-bar-fill" id="relBar" style="background:#2196F3;width:0%"></div></div>
  </div>
  <div class="pillar-bar-wrap">
    <div class="pillar-bar-label">Efficiency (30%): <span id="effContrib">—</span></div>
    <div class="pillar-bar-track"><div class="pillar-bar-fill" id="effBar" style="background:#FF9800;width:0%"></div></div>
  </div>
</div>

<div class="pillar-cards" id="pillarCards">
  <div class="pillar-card">
    <h3>Coverage <span style="font-weight:normal;color:#888">(40% weight)</span></h3>
    <div class="pillar-score" style="color:#4CAF50" id="covScore">—</div>
    <div class="pillar-contrib" id="covContribDetail"></div>
    <div id="covSubBars"></div>
  </div>
  <div class="pillar-card">
    <h3>Reliability <span style="font-weight:normal;color:#888">(30% weight)</span></h3>
    <div class="pillar-score" style="color:#2196F3" id="relScore">—</div>
    <div class="pillar-contrib" id="relContribDetail"></div>
    <div id="relSubBars"></div>
  </div>
  <div class="pillar-card">
    <h3>Efficiency <span style="font-weight:normal;color:#888">(30% weight)</span></h3>
    <div class="pillar-score" style="color:#FF9800" id="effScore">—</div>
    <div class="pillar-contrib" id="effContribDetail"></div>
    <div id="effSubBars"></div>
  </div>
</div>
"""
    narrative_html = """
<div class="ai-narrative" id="aiNarrative">
  <h2>AI Analysis <span id="aiPeriodLabel" style="font-weight:normal;font-size:0.8em;color:#888"></span></h2>
  <div class="ai-narrative-text" id="aiNarrativeText"></div>
  <div class="ai-error" id="aiError"></div>
  <button class="ai-btn" id="aiBtn" onclick="generateAnalysis()">Generate Analysis</button>
  <span class="ai-spinner" id="aiSpinner">Generating...</span>
</div>
"""
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
.ams-overview {{ background: white; border-radius: 8px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.ams-score-row {{ display: flex; align-items: center; gap: 24px; flex-wrap: wrap; margin-bottom: 16px; }}
.ams-score-big {{ font-size: 3em; font-weight: bold; color: #9C27B0; }}
.ams-level {{ font-size: 1.1em; color: #555; }}
.ams-delta-pos {{ background: #e8f5e9; color: #2e7d32; padding: 4px 12px; border-radius: 12px; font-weight: bold; }}
.ams-delta-neg {{ background: #ffebee; color: #c62828; padding: 4px 12px; border-radius: 12px; font-weight: bold; }}
.ams-delta-neu {{ background: #f5f5f5; color: #555; padding: 4px 12px; border-radius: 12px; }}
.pillar-bar-wrap {{ margin: 8px 0; }}
.pillar-bar-label {{ font-size: 12px; color: #666; margin-bottom: 2px; }}
.pillar-bar-track {{ background: #eee; border-radius: 4px; height: 18px; position: relative; }}
.pillar-bar-fill {{ height: 18px; border-radius: 4px; display: inline-block; transition: width 0.4s; }}
.pillar-cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }}
.pillar-card {{ flex: 1; min-width: 260px; background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.pillar-card h3 {{ margin: 0 0 4px 0; font-size: 1em; color: #333; }}
.pillar-score {{ font-size: 2em; font-weight: bold; margin: 8px 0; }}
.pillar-contrib {{ font-size: 0.85em; color: #888; margin-bottom: 12px; }}
.sub-bar-row {{ margin: 6px 0; }}
.sub-bar-name {{ font-size: 11px; color: #555; display: flex; justify-content: space-between; }}
.sub-bar-track {{ background: #eee; border-radius: 3px; height: 10px; margin-top: 2px; }}
.sub-bar-fill {{ height: 10px; border-radius: 3px; }}
.efficiency-tier {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: bold; background: #e3f2fd; color: #1565c0; margin-top: 8px; }}
.unavailable {{ color: #aaa; font-style: italic; font-size: 0.9em; }}
.ai-narrative {{ background: white; border-radius: 8px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.ai-narrative h2 {{ margin-top: 0; }}
.ai-narrative-text {{ line-height: 1.7; color: #333; white-space: pre-wrap; font-size: 14px; }}
.ai-narrative-text h3 {{ color: #9C27B0; margin: 16px 0 6px; font-size: 1em; }}
.ai-btn {{ background: #9C27B0; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; margin-top: 12px; }}
.ai-btn:hover {{ background: #7B1FA2; }}
.ai-btn:disabled {{ background: #ccc; cursor: default; }}
.ai-spinner {{ display:none; margin-left: 10px; color: #9C27B0; font-size: 13px; }}
.ai-error {{ color: #c62828; font-size: 13px; margin-top: 8px; }}
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

{ams_overview_html}

{narrative_html}

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

  updateAmsOverview(end);
  updateNarrativePanel(end);
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

const amsData = {ams_data_js};
const PROXY_PORT = {proxy_port if proxy_port else 'null'};
const DOMAIN = "{domain}";

function subBar(name, val, weight, color) {{
  const pct = val !== null ? (val * 100).toFixed(1) : null;
  const w = val !== null ? Math.min(100, val * 100) : 0;
  return `<div class="sub-bar-row">
    <div class="sub-bar-name"><span>${{name}} <span style="color:#aaa">[wt ${{weight}}]</span></span><span>${{pct !== null ? pct + '%' : '—'}}</span></div>
    <div class="sub-bar-track"><div class="sub-bar-fill" style="background:${{color}};width:${{w}}%"></div></div>
  </div>`;
}}

function updateAmsOverview(toIdx) {{
  if (!amsData || amsData.length === 0) return;
  const toLabel = allLabels[toIdx];
  const entry = amsData.find(d => d.end_date === toLabel);
  if (!entry) return;
  const prevIdx = amsData.findIndex(d => d.end_date === toLabel) - 1;
  const prev = prevIdx >= 0 ? amsData[prevIdx] : null;

  const score = entry.ams_score;
  document.getElementById('amsScoreBig').textContent = score !== null ? score.toFixed(1) : '—';
  document.getElementById('amsLevelLabel').textContent = score !== null ? amsLevel(score) : '';

  const badge = document.getElementById('amsDeltaBadge');
  if (prev && prev.ams_score !== null && score !== null) {{
    const delta = score - prev.ams_score;
    const sign = delta >= 0 ? '+' : '';
    badge.textContent = sign + delta.toFixed(1) + ' vs prev';
    badge.className = delta > 0 ? 'ams-delta-pos' : (delta < 0 ? 'ams-delta-neg' : 'ams-delta-neu');
  }} else {{
    badge.textContent = 'First period';
    badge.className = 'ams-delta-neu';
  }}

  const cov = entry.coverage_score, rel = entry.reliability_score, eff = entry.efficiency_score;
  // Pillar scores are stored as 0-1 fractions; multiply by 100 for display
  const cov100 = cov !== null ? cov * 100 : null;
  const rel100 = rel !== null ? rel * 100 : null;
  const eff100 = eff !== null ? eff * 100 : null;
  const covC = cov100 !== null ? (cov100 * 0.40).toFixed(1) : '—';
  const relC = rel100 !== null ? (rel100 * 0.30).toFixed(1) : '—';
  const effC = eff100 !== null ? (eff100 * 0.30).toFixed(1) : '—';
  document.getElementById('covContrib').textContent = covC + ' pts';
  document.getElementById('relContrib').textContent = relC + ' pts';
  document.getElementById('effContrib').textContent = effC + ' pts';
  document.getElementById('covBar').style.width = (cov100 !== null ? Math.min(100, cov100) : 0) + '%';
  document.getElementById('relBar').style.width = (rel100 !== null ? Math.min(100, rel100) : 0) + '%';
  document.getElementById('effBar').style.width = (eff100 !== null ? Math.min(100, eff100) : 0) + '%';

  document.getElementById('covScore').textContent = cov100 !== null ? cov100.toFixed(1) : '—';
  document.getElementById('covContribDetail').textContent = `Contributes ${{covC}} pts to AMS`;
  document.getElementById('covSubBars').innerHTML =
    '<b style="font-size:11px;color:#888">Backend (60%)</b>' +
    subBar('Unit Test', entry.be_unit, '35%', '#4CAF50') +
    subBar('Contract', entry.be_contract, '35%', '#4CAF50') +
    subBar('Intra-Service', entry.be_intra, '10%', '#4CAF50') +
    subBar('Inter-Service', entry.be_inter, '15%', '#4CAF50') +
    subBar('API E2E', entry.be_api_e2e, '5%', '#4CAF50') +
    '<b style="font-size:11px;color:#888;display:block;margin-top:8px">Mobile (20%)</b>' +
    subBar('Unit Test', entry.mob_unit, '30%', '#8BC34A') +
    subBar('Integration', entry.mob_integration, '20%', '#8BC34A') +
    subBar('E2E', entry.mob_e2e, '50%', '#8BC34A') +
    '<b style="font-size:11px;color:#888;display:block;margin-top:8px">Web (20%)</b>' +
    subBar('Unit Test', entry.web_unit, '30%', '#CDDC39') +
    subBar('Component', entry.web_component, '20%', '#CDDC39') +
    subBar('E2E', entry.web_e2e, '50%', '#CDDC39');

  document.getElementById('relScore').textContent = rel100 !== null ? rel100.toFixed(1) : '—';
  document.getElementById('relContribDetail').textContent = `Contributes ${{relC}} pts to AMS`;
  document.getElementById('relSubBars').innerHTML =
    subBar('Backend Stability', entry.backend_stability, '50%', '#2196F3') +
    subBar('Mobile Stability',  entry.mobile_stability,  '25%', '#2196F3') +
    subBar('Web Stability',     entry.web_stability,     '25%', '#2196F3');

  document.getElementById('effScore').textContent = eff100 !== null ? eff100.toFixed(1) : '—';
  document.getElementById('effContribDetail').textContent = `Contributes ${{effC}} pts to AMS`;
  const bl = entry.baseline_hours;
  const mh = entry.manual_hours;
  const blStr = bl !== null ? bl + 'h baseline' : '';
  const mhStr = mh !== null ? mh + 'h manual' : '—';
  const barPct = (bl && mh) ? Math.min(100, (mh / bl) * 100) : 0;
  document.getElementById('effSubBars').innerHTML =
    `<div style="font-size:12px;color:#555;margin-bottom:6px">${{mhStr}} / ${{blStr}}</div>
     <div class="sub-bar-track" style="height:14px"><div class="sub-bar-fill" style="background:#FF9800;width:${{barPct}}%;height:14px"></div></div>
     <div class="efficiency-tier">${{efficiencyTier(mh)}}</div>`;
}}

function amsLevel(score) {{
  if (score >= 81) return 'Level 5: Optimizing';
  if (score >= 61) return 'Level 4: Measured';
  if (score >= 41) return 'Level 3: Defined';
  if (score >= 21) return 'Level 2: Emerging';
  return 'Level 1: Initial';
}}

function efficiencyTier(h) {{
  if (h === null || h === undefined) return 'N/A';
  if (h <= 50)  return 'Optimized';
  if (h <= 100) return 'Advanced';
  if (h <= 150) return 'Developing';
  return 'Initial';
}}

let currentNarrativePeriod = null;

function narrativeCacheKey(period) {{
  return 'ams_narrative_' + DOMAIN + '_' + period;
}}

function updateNarrativePanel(toIdx) {{
  const toLabel = allLabels[toIdx];
  currentNarrativePeriod = toLabel;
  document.getElementById('aiPeriodLabel').textContent = '— ' + toLabel;
  document.getElementById('aiError').textContent = '';

  const cached = localStorage.getItem(narrativeCacheKey(toLabel));
  if (cached) {{
    renderNarrative(cached);
    document.getElementById('aiBtn').textContent = 'Regenerate';
    return;
  }}
  document.getElementById('aiNarrativeText').textContent = '';
  document.getElementById('aiBtn').textContent = 'Generate Analysis';

  if (toLabel === allLabels[allLabels.length - 1] && !cached) {{
    generateAnalysis();
  }}
}}

function renderNarrative(text) {{
  const formatted = text.replace(/^(How AMS is Calculated|This Period's Breakdown|Key Movers|Action Items)$/gm, '<h3>$1</h3>');
  document.getElementById('aiNarrativeText').innerHTML = formatted;
}}

async function generateAnalysis() {{
  if (!PROXY_PORT) {{
    document.getElementById('aiError').textContent = 'Proxy not running. Start the script to enable AI analysis.';
    return;
  }}
  const toLabel = currentNarrativePeriod;
  const entry = amsData.find(d => d.end_date === toLabel);
  if (!entry) return;
  const prevIdx = amsData.findIndex(d => d.end_date === toLabel) - 1;
  const prev = prevIdx >= 0 ? amsData[prevIdx] : null;

  document.getElementById('aiBtn').disabled = true;
  document.getElementById('aiSpinner').style.display = 'inline';
  document.getElementById('aiError').textContent = '';
  document.getElementById('aiNarrativeText').textContent = '';

  const body = {{
    period: toLabel,
    domain: DOMAIN,
    current: entry,
    previous: prev,
    baseline: entry.baseline_dict || {{}},
  }};

  try {{
    const resp = await fetch(`http://localhost:${{PROXY_PORT}}/analyze`, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
    }});
    if (!resp.ok) {{
      const err = await resp.json().catch(() => ({{error: 'Unknown error'}}));
      throw new Error(err.error || `HTTP ${{resp.status}}`);
    }}

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    while (true) {{
      const {{ done, value }} = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, {{ stream: true }});
      for (const line of chunk.split('\\n')) {{
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') continue;
        try {{
          const parsed = JSON.parse(data);
          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {{
            fullText += delta;
            renderNarrative(fullText);
          }}
        }} catch (_) {{}}
      }}
    }}
    localStorage.setItem(narrativeCacheKey(toLabel), fullText);
    document.getElementById('aiBtn').textContent = 'Regenerate';
  }} catch (e) {{
    const msg = e.message.includes('Failed to fetch') || e.message.includes('NetworkError')
      ? 'Proxy not reachable — the report HTML must be opened from the same terminal session that generated it. Re-run the script and open the new HTML file.'
      : 'Error: ' + e.message;
    document.getElementById('aiError').textContent = msg;
  }} finally {{
    document.getElementById('aiBtn').disabled = false;
    document.getElementById('aiSpinner').style.display = 'none';
  }}
}}

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

    # Step 1: Start proxy server (always, regardless of --open)
    proxy_port = start_proxy_server()

    # Step 2: Fetch all tables in parallel
    print("Fetching data from Lark Base...")
    raw_tables = fetch_all_tables()

    # Step 3: Parse main table
    records = parse_records(raw_tables["main"], domain_filter=args.domain)
    if not records:
        print(f"No records found for domain: {args.domain}")
        sys.exit(1)

    # Step 4: Parse child tables
    child_data = {
        "ams":                parse_child_table(raw_tables.get("ams"), args.domain),
        "backend_coverage":   parse_child_table(raw_tables.get("backend_coverage"), args.domain),
        "mobile_coverage":    parse_child_table(raw_tables.get("mobile_coverage"), args.domain),
        "web_coverage":       parse_child_table(raw_tables.get("web_coverage"), args.domain),
        "auto_effectiveness": parse_child_table(raw_tables.get("auto_effectiveness"), args.domain),
    }
    baseline = parse_baseline_table(raw_tables.get("baseline"), args.domain)

    # Step 5: Join enriched records
    enriched = join_enriched_records(records, child_data, baseline)

    # Step 6: Extract existing metrics (unchanged)
    labels, datasets, record_ids = extract_metrics(records)

    # Step 7: Extract AMS pillar data
    ams_data = extract_ams_data(enriched)

    # Step 8: Generate visualization
    generate_html(labels, datasets, record_ids, args.domain, args.output, ams_data, proxy_port)

    # Step 9: Optionally open
    if args.open:
        if sys.platform == "darwin":
            subprocess.run(["open", args.output])
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", args.output])
        else:
            print(f"Open {args.output} in your browser.")


if __name__ == "__main__":
    main()
