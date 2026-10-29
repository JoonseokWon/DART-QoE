import base64
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from app import DartQoeApp, _populate_chart_caches, _patch_qoe_chart_secondary_axis, build_result_summary, packaged_update_path, updater_command
from qoe import demo_analysis


class AutomaticRestartTests(unittest.TestCase):
    def test_packaged_update_uses_side_by_side_executable(self):
        current = Path(r"C:\Tools\DART-QoE.exe")
        self.assertEqual(packaged_update_path(current), Path(r"C:\Tools\DART-QoE.update.exe"))

    def test_updater_waits_replaces_and_relaunches(self):
        current = Path(r"C:\Tools\DART-QoE.exe")
        update = Path(r"C:\Tools\DART-QoE.update.exe")
        command = updater_command(current, update, 1234)
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("Wait-Process -Id 1234", script)
        self.assertIn("Get-Process -Name $processName", script)
        self.assertIn("Where-Object { $_.Path -eq $current }", script)
        self.assertIn("$remaining.Count -eq 0", script)
        self.assertIn("Stop-Process -Force", script)
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn("Start-Sleep -Milliseconds 2000", script)
        self.assertIn("Remove-Item Env:TCL_LIBRARY", script)
        self.assertIn("Remove-Item Env:TK_LIBRARY", script)
        self.assertIn("$_.Name -like '_PYI_*'", script)
        self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", script)
        self.assertIn("New-Object -ComObject Shell.Application", script)
        self.assertIn("$shell.ShellExecute($current, '', $working, 'open', 1)", script)
        self.assertIn(str(update), script)

    def test_packaged_app_does_not_start_self_update_watcher(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("if not FROZEN:", source)
        self.assertNotIn("if FROZEN:\n            self._watch_for_packaged_update()", source)


class InputFlowTests(unittest.TestCase):
    def make_ui(self, api="", company="삼성전자", begin="2021", end="2025"):
        ui = object.__new__(DartQoeApp)
        ui.busy = False
        ui.native_entries = []
        for name, value in (("api", api), ("company", company), ("begin", begin), ("end", end)):
            setattr(ui, name + "_var", Mock(get=Mock(return_value=value)))
        ui._show_form_error = Mock()
        ui._focus_field = Mock()
        return ui

    def test_missing_company_keeps_entered_period_and_focuses_company(self):
        ui = self.make_ui(api="test", company="")
        ui.start_analysis()
        ui._show_form_error.assert_called_with("회사명 또는 종목코드를 입력하세요.")
        ui._focus_field.assert_called_with("company")
        ui.begin_var.set.assert_not_called()
        self.assertFalse(ui.busy)

    def test_invalid_period_reports_input_error_before_network(self):
        ui = self.make_ui(api="test", begin="abc")
        ui.start_analysis()
        ui._show_form_error.assert_called_with("분석기간은 연도 네 자리 숫자로 입력하세요.")
        ui._focus_field.assert_called_with("begin")
        self.assertFalse(ui.busy)

    def test_reversed_period_is_not_started(self):
        ui = self.make_ui(api="test", begin="2025", end="2021")
        ui.start_analysis()
        ui._show_form_error.assert_called_with("분석 시작연도는 종료연도보다 늦을 수 없습니다.")
        self.assertFalse(ui.busy)


class WorkbookChartTests(unittest.TestCase):
    def test_preview_cache_uses_actual_worksheet_values(self):
        chart = '<c:numRef><c:f>\'QoE 요약\'!$B$24:$B$25</c:f><c:numCache><c:formatCode>0</c:formatCode><c:ptCount val="0" /></c:numCache></c:numRef>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.xlsx'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="QoE 요약" r:id="r1" /></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml" /></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="24"><c r="B24"><v>100</v></c></row><row r="25"><c r="B25"><v>-25</v></c></row></sheetData></worksheet>')
            with zipfile.ZipFile(path) as z:
                patched = _populate_chart_caches(chart, z)
        self.assertIn('<c:ptCount val="2" />', patched)
        self.assertIn('<c:pt idx="0"><c:v>100</c:v></c:pt>', patched)
        self.assertIn('<c:pt idx="1"><c:v>-25</c:v></c:pt>', patched)

    def test_cash_conversion_series_moves_to_right_percentage_axis(self):
        chart_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            "<c:chart><c:plotArea><c:lineChart><c:grouping val=\"standard\" />"
            '<c:ser><c:idx val="0" /><c:order val="0" /><c:tx><c:v>보고 영업이익</c:v></c:tx></c:ser>'
            '<c:ser><c:idx val="1" /><c:order val="1" /><c:tx><c:v>정상화 영업이익</c:v></c:tx></c:ser>'
            '<c:ser><c:idx val="2" /><c:order val="2" /><c:tx><c:v>영업현금흐름/영업이익</c:v></c:tx></c:ser>'
            '<c:axId val="100" /><c:axId val="200" /></c:lineChart>'
            '<c:catAx><c:axId val="100" /></c:catAx>'
            '<c:valAx><c:axId val="200" /><c:axPos val="l" /></c:valAx>'
            "</c:plotArea></c:chart></c:chartSpace>"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "chart.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("xl/drawings/charts/chart1.xml", chart_xml)
            _patch_qoe_chart_secondary_axis(workbook)
            with zipfile.ZipFile(workbook) as archive:
                patched = archive.read("xl/drawings/charts/chart1.xml").decode("utf-8")
        self.assertEqual(patched.count("<c:lineChart>"), 2)
        self.assertEqual(patched.count("영업현금흐름/영업이익</c:v>"), 1)
        self.assertIn('<c:axPos val="r" />', patched)
        self.assertIn('<c:numFmt formatCode="0%" sourceLinked="0" />', patched)
        self.assertIn("영업현금흐름/영업이익 (%)", patched)
        self.assertEqual(patched.count('rot="0" vert="horz"'), 2)
        self.assertIn('<c:x val="0.01" /><c:y val="0.826" />', patched)
        self.assertIn('<c:x val="0.84" /><c:y val="0.826" />', patched)


class ResultSummaryTests(unittest.TestCase):
    def test_original_filing_candidate_search_option_is_removed(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertNotIn("사업보고서 원문에서 후보 탐색", source)
        self.assertNotIn("notes_var", source)

    def test_summary_prioritizes_key_metrics_and_review_points(self):
        summary = build_result_summary(demo_analysis())
        self.assertIn("[핵심 지표]", summary)
        self.assertIn("매출: 2022년", summary)
        self.assertIn("→ 2024년", summary)
        self.assertIn("영업이익률: 2022년", summary)
        self.assertIn("영업현금 전환율: 2024년", summary)
        self.assertIn("매출 대비 순운전자본: 2024년", summary)
        self.assertIn("자금 상태: 2024년 순현금 -330.0억원 (순차입금 330.0억원)", summary)
        self.assertIn("정상화 조정 검토 후보: 자동 탐지 2건", summary)
        self.assertIn("정상화 손익: Excel에서 조정 여부와 적용 금액을 입력하면 손익 유형에 따라 자동 계산", summary)
        self.assertIn("[추가 확인 포인트]", summary)
        self.assertIn(
            "순차입금이 2022년 150.0억원에서 2024년 330.0억원으로 180.0억원 증가했습니다.",
            summary,
        )
        self.assertNotIn("저장 위치", summary)


if __name__ == "__main__":
    unittest.main()
