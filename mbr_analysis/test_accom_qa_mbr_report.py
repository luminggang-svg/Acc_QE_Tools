import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import accom_qa_mbr_report as report


def row(record_id, domain, end_date, manual_hours, qa_validation, maturity):
    cols = [""] * 41
    cols[0] = record_id
    cols[report.COL_MAP["Domain"]] = domain
    cols[report.COL_MAP["End Date"]] = end_date
    cols[report.COL_MAP["Manual Hours"]] = str(manual_hours)
    cols[report.COL_MAP["QA Validation Coverage"]] = str(qa_validation)
    cols[report.COL_MAP["Automation Maturity Score"]] = str(maturity)
    return cols


class TestAccomQaMbrReport(unittest.TestCase):
    def test_litellm_config_uses_environment_with_default_base_url(self):
        with mock.patch.dict(report.os.environ, {"TVLK_LITELLM_KEY": "test-key"}, clear=True):
            self.assertEqual(report.litellm_api_key(), "test-key")
            self.assertEqual(report.litellm_base_url(), "https://litellm.tvlk.cloud")

        with mock.patch.dict(
            report.os.environ,
            {"TVLK_LITELLM_KEY": "test-key", "LITELLM_BASE_URL": "https://custom.example"},
            clear=True,
        ):
            self.assertEqual(report.litellm_base_url(), "https://custom.example")

    def test_build_domain_comparison_uses_latest_record_per_domain(self):
        records = [
            row("rec1", "Accommodation", "2026-01-31", 12, 0.7, 2),
            row("rec2", "Accommodation", "2026-02-28", 8, 0.85, 3),
            row("rec3", "Flight", "2026-02-15", 14, 90, 4),
        ]

        comparison = report.build_domain_comparison(records)

        self.assertEqual(comparison["domains"], ["Accommodation", "Flight"])
        self.assertEqual(comparison["endDates"], ["2026-02-28", "2026-02-15"])
        self.assertEqual(comparison["metrics"]["Manual Hours"], [8.0, 14.0])
        self.assertEqual(comparison["metrics"]["QA Validation Coverage"], [85.0, 90.0])
        self.assertEqual(comparison["metrics"]["Automation Maturity Score"], [3.0, 4.0])

    def test_clean_domain_name_removes_wrapping_punctuation_and_replaces_ampersand_label(self):
        self.assertEqual(report.clean_domain_name('["Accommodation"]'), "Accommodation")
        self.assertEqual(
            report.clean_domain_name('Travel Activities \\u0026 Grand Transport'),
            "Travel Activities Ground Transport",
        )
        self.assertEqual(
            report.domain_performance_name('["Travel Activities \\\\u0026 Ground Transport"]'),
            "Travel Activities and Ground Transport",
        )

    def test_build_weekly_domain_comparison_defaults_to_last_complete_week(self):
        records = [
            row("rec1", '["Accommodation"]', "2026-06-15", 10, 0.75, 2),
            row("rec2", '["Accommodation"]', "2026-06-26", 8, 0.85, 3),
            row("rec3", "Platform", "2026-06-26", 14, 0.80, 4),
            row("rec4", "Payment", "2026-06-26", 12, 0.90, 5),
            row("rec5", "Travel Activities \\u0026 Grand Transport", "2026-06-26", 6, 0.70, 2),
            row("rec6", "Transprt", "2026-06-26", 9, 0.72, 3),
            row("rec7", "Flight", "2026-06-26", 20, 0.95, 5),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-26")

        self.assertEqual(comparison["defaultWeek"], "2026-06-26")
        self.assertEqual(comparison["weeks"], ["2026-06-15", "2026-06-26"])
        self.assertEqual(
            comparison["byWeek"]["2026-06-26"]["domains"],
            ["Accommodation", "Flight", "Payment", "Platform", "Transprt", "Travel Activities and Ground Transport"],
        )

    def test_domain_performance_includes_all_domains_except_ta_and_overall(self):
        records = [
            row("rec1", "Accommodation", "2026-06-26", 8, 0.85, 3),
            row("rec2", "Travel Activities \\u0026 Grand Transport", "2026-06-26", 6, 0.70, 2),
            row("rec3", "Transport", "2026-06-26", 99, 0.99, 9),
            row("rec4", "Transprt", "2026-06-26", 9, 0.72, 3),
            row("rec5", "Platform", "2026-06-26", 3, 0.80, 4),
            row("rec6", "Payment", "2026-06-26", 2, 0.90, 5),
            row("rec7", "Flight", "2026-06-26", 20, 0.95, 5),
            row("rec8", "TA Activities", "2026-06-26", 30, 0.60, 2),
            row("rec9", "Overall", "2026-06-26", 100, 0.60, 2),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-26")
        snapshot = comparison["byWeek"]["2026-06-26"]

        self.assertEqual(
            comparison["defaultSelectedDomains"],
            ["Accommodation", "Travel Activities and Ground Transport", "Transport"],
        )
        self.assertEqual(
            snapshot["domains"],
            ["Accommodation", "Flight", "Payment", "Platform", "Transport", "Transprt", "Travel Activities and Ground Transport"],
        )
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Transport"], [0, 0, 0, 0, 99.0, 0, 0])
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Transprt"], [0, 0, 0, 0, 0, 9.0, 0])
        self.assertNotIn("TA Activities", snapshot["layers"])
        self.assertNotIn("Overall", snapshot["layers"])

    def test_domain_performance_maps_escaped_ground_transport_and_keeps_metric_value(self):
        records = [
            row("rec1", "Travel Activities \\- Ground Transport", "2026-06-15", 1, 0.5, 37.2),
            row("rec2", "Travel Activities \\\\- Ground Transport", "2026-06-26", 2, 0.6, 38.4),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-15")
        snapshot = comparison["byWeek"]["2026-06-15"]
        later_snapshot = comparison["byWeek"]["2026-06-26"]

        self.assertEqual(snapshot["domains"], ["Travel Activities and Ground Transport"])
        self.assertEqual(
            snapshot["metrics"]["Automation Maturity Score"]["Travel Activities and Ground Transport"],
            [37.2],
        )
        self.assertEqual(
            later_snapshot["metrics"]["Automation Maturity Score"]["Travel Activities and Ground Transport"],
            [38.4],
        )

    def test_generated_html_uses_normalized_ground_transport_label(self):
        labels = ["2026-06-15"]
        datasets = {metric: [0] for metric in report.METRICS}
        comparison = {
            "defaultWeek": "2026-06-15",
            "latestWeek": "2026-06-15",
            "weeks": ["2026-06-15"],
            "byWeek": {
                "2026-06-15": {
                    "domains": ["Travel Activities and Ground Transport"],
                    "endDates": ["2026-06-15"],
                    "metrics": {
                        metric: {"Travel Activities and Ground Transport": [1]}
                        for metric in report.DOMAIN_COMPARISON_METRICS
                    },
                    "layers": ["Travel Activities and Ground Transport"],
                }
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "report.html"
            report.generate_html(
                labels,
                labels,
                datasets,
                ["rec1"],
                "Accommodation",
                output_path,
                domain_comparison=comparison,
                notes={},
                notes_path="notes.db",
            )

            html = output_path.read_text()
            self.assertIn('<meta charset="utf-8">', html)
            self.assertIn('&mdash;', html)
            self.assertIn('&rarr;', html)
            self.assertIn("Travel Activities and Ground Transport", html)
            self.assertIn("domainCheckboxes", html)
            self.assertIn("selectedDomains", html)
            self.assertIn("shortDomainLabel", html)
            self.assertIn("isDefaultDomainSelection", html)
            self.assertIn("friendlyDomainLabel", html)
            self.assertIn("usesDomainCodes", html)
            self.assertIn("domainValueLabelsPlugin", html)
            self.assertIn("formatBarValue", html)
            self.assertIn("maxDomainTotal", html)
            self.assertIn("suggestedMax", html)
            self.assertIn("chart.chartArea.top", html)
            self.assertIn("tooltipNoteLines", html)
            self.assertIn("pinnedMetricTextLines", html)
            self.assertNotIn("Metric Value", html)
            self.assertNotIn("Explanation & Reason", html)
            self.assertIn("pinnedMetricValues", html)
            self.assertIn("pinnedMetricValueLabelsPlugin", html)
            self.assertIn("pinnedMetricGlassTheme", html)
            self.assertIn("createLinearGradient", html)
            self.assertIn("shadowBlur", html)
            self.assertIn("textAlign = 'left'", html)
            self.assertIn("pinnedMetricLineStyle", html)
            self.assertIn("theme.valueText", html)
            self.assertIn("theme.noteText", html)
            self.assertIn("font-size: 11px", html)
            self.assertIn("font-size: 10.5px", html)
            self.assertIn("letter-spacing: -0.01em", html)
            self.assertIn("line-height: 1.45", html)
            self.assertIn("margin: 10px 0 2px", html)
            self.assertIn("addMetricValuePinClickHandlers", html)
            self.assertIn("addMetricNoteDoubleClickHandlers", html)
            self.assertIn("Travel + Ground", html)
            self.assertIn("domainSelectionKey", html)
            self.assertIn("domainCodeMap", html)
            self.assertIn("'D' + (index + 1)", html)
            self.assertIn("-apple-system", html)
            self.assertIn("text-align: center", html)
            self.assertNotIn("Travel Activities \\\\- Ground Transport", html)

    def test_domain_performance_weeks_only_include_dates_with_all_domains(self):
        records = [
            row("rec1", "Accommodation", "2026-06-15", 10, 0.75, 2),
            row("rec2", "Platform", "2026-06-15", 14, 0.80, 4),
            row("rec3", "Accommodation", "2026-06-26", 8, 0.85, 3),
            row("rec4", "Travel Activities \\u0026 Grand Transport", "2026-06-26", 6, 0.70, 2),
            row("rec5", "Transprt", "2026-06-26", 12, 0.90, 5),
            row("rec6", "Platform", "2026-06-26", 4, 0.82, 4),
            row("rec7", "Payment", "2026-06-26", 5, 0.83, 4),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-15")

        self.assertEqual(comparison["weeks"], ["2026-06-15", "2026-06-26"])
        self.assertEqual(comparison["defaultWeek"], "2026-06-15")

    def test_domain_performance_includes_latest_week_even_when_incomplete(self):
        records = [
            row("rec1", "Accommodation", "2026-06-26", 8, 0.85, 3),
            row("rec2", "Travel Activities \\u0026 Grand Transport", "2026-06-26", 12, 0.90, 5),
            row("rec3", "Platform", "2026-06-26", 6, 0.70, 2),
            row("rec5", "Payment", "2026-06-26", 5, 0.80, 4),
            row("rec6", "Transprt", "2026-06-26", 4, 0.75, 3),
            row("rec4", "Accommodation", "2026-07-03", 7, 0.88, 4),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-26")
        snapshot = comparison["byWeek"]["2026-07-03"]

        self.assertEqual(comparison["weeks"], ["2026-06-26", "2026-07-03"])
        self.assertEqual(comparison["latestWeek"], "2026-07-03")
        self.assertEqual(
            snapshot["domains"],
            ["Accommodation", "Payment", "Platform", "Transprt", "Travel Activities and Ground Transport"],
        )
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Accommodation"], [7.0, 0, 0, 0, 0])
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Payment"], [0, 0, 0, 0, 0])

    def test_ta_and_overall_domains_are_excluded_from_domain_performance(self):
        records = [
            row("rec1", "Accommodation", "2026-06-26", 8, 0.85, 3),
            row("rec2", "Flight", "2026-06-26", 12, 0.90, 5),
            row("rec3", "TA Activities", "2026-06-26", 6, 0.70, 2),
            row("rec4", "TA Ground Transport", "2026-06-26", 4, 0.80, 3),
            row("rec5", "Accommodation", "2026-07-03", 7, 0.88, 4),
            row("rec6", "Payment", "2026-07-03", 11, 0.91, 5),
            row("rec7", "TA Activities", "2026-07-03", 5, 0.76, 3),
            row("rec8", "TA Ground Transport", "2026-07-03", 3, 0.84, 4),
            row("rec9", "Overall", "2026-07-03", 99, 0.84, 4),
        ]

        comparison = report.build_weekly_domain_comparison(records, default_week="2026-06-26")
        snapshot = comparison["byWeek"]["2026-07-03"]

        self.assertEqual(comparison["weeks"], ["2026-06-26", "2026-07-03"])
        self.assertEqual(comparison["latestWeek"], "2026-07-03")
        self.assertEqual(
            snapshot["domains"],
            ["Accommodation", "Flight", "Payment"],
        )
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Accommodation"], [7.0, 0, 0])
        self.assertEqual(snapshot["metrics"]["Manual Hours"]["Payment"], [0, 0, 11.0])
        self.assertNotIn("TA Activities", snapshot["layers"])
        self.assertNotIn("Overall", snapshot["layers"])

    def test_sqlite_notes_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            notes_path = Path(temp_dir) / "notes.db"
            notes = {
                "Accommodation|Manual Hours|2026-02-28": {
                    "domain": "Accommodation",
                    "metric": "Manual Hours",
                    "date": "2026-02-28",
                    "value": 8,
                    "reason": "Regression from manual release checks",
                    "mitigation": "Automate smoke test pack",
                    "updatedAt": "2026-07-08T10:00:00",
                }
            }

            report.save_notes(notes_path, notes)

            with sqlite3.connect(notes_path) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='metric_notes'"
                ).fetchall()
            self.assertEqual(tables, [("metric_notes",)])
            self.assertEqual(report.load_notes(notes_path), notes)

    def test_report_request_handler_reads_and_writes_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            notes_path = Path(temp_dir) / "notes.db"
            report.save_notes(notes_path, {"old": {"reason": "existing"}})
            handler = object.__new__(report.ReportRequestHandler)
            handler.notes_path = notes_path

            self.assertEqual(handler.read_notes(), {"old": {"reason": "existing"}})

            handler.write_notes({"new": {"mitigation": "saved dynamically"}})

            self.assertEqual(report.load_notes(notes_path), {"new": {"mitigation": "saved dynamically"}})


if __name__ == "__main__":
    unittest.main()
