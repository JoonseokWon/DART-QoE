import base64
import unittest
from pathlib import Path

from app import build_result_summary, packaged_update_path, updater_command
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
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn("Start-Sleep -Milliseconds 1000", script)
        self.assertIn("Start-Process -FilePath $current", script)
        self.assertIn(str(update), script)


class ResultSummaryTests(unittest.TestCase):
    def test_summary_prioritizes_key_metrics_and_review_points(self):
        summary = build_result_summary(demo_analysis())
        self.assertIn("[핵심 지표]", summary)
        self.assertIn("매출:", summary)
        self.assertIn("영업이익률:", summary)
        self.assertIn("영업현금 전환율:", summary)
        self.assertIn("정상화 조정 검토 후보: 2건", summary)
        self.assertIn("[추가 확인 포인트]", summary)
        self.assertNotIn("저장 위치", summary)


if __name__ == "__main__":
    unittest.main()
