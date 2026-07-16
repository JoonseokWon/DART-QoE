import unittest
from unittest.mock import patch

from qoe import DartClient, DartError, analyze_dart, demo_analysis


class DemoAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = demo_analysis()

    def test_demo_has_three_years_and_audit_metadata(self):
        self.assertEqual(self.data["years"], [2022, 2023, 2024])
        self.assertEqual(self.data["metadata"]["basis"], "연결재무제표")
        self.assertEqual(len(self.data["filings"]), 3)

    def test_2024_working_capital_and_net_debt(self):
        current = self.data["metrics"][-1]
        self.assertEqual(current["nwc"], 54_000_000_000)
        self.assertEqual(current["net_debt"], 33_000_000_000)
        self.assertAlmostEqual(current["operating_margin"], 0.105)

    def test_candidates_are_review_flags_not_adjustments(self):
        self.assertGreaterEqual(len(self.data["candidates"]), 2)
        self.assertTrue(all(x["status"] == "확인 필요" for x in self.data["candidates"]))

    def test_accounts_fall_back_to_separate_statements(self):
        client = DartClient("test")
        with patch.object(client, "json", side_effect=[DartError("DART 013: 조회된 데이타가 없습니다."), {"list": [{"account_nm": "매출액"}]}]) as mocked:
            rows, basis = client.annual_accounts("00123456", 2024)
        self.assertEqual(basis, "OFS")
        self.assertEqual(rows[0]["account_nm"], "매출액")
        self.assertEqual(mocked.call_args_list[1].args[1]["fs_div"], "OFS")

    def test_analysis_period_is_not_limited_to_five_years(self):
        with patch.object(DartClient, "resolve_company", return_value={
            "corp_code": "00123456", "corp_name": "테스트", "stock_code": "123456"
        }), patch.object(DartClient, "annual_accounts", return_value=([
            {"account_nm": "매출액", "thstrm_amount": "1000000"}
        ], "CFS")), patch.object(DartClient, "annual_filing", return_value=None):
            result = analyze_dart("test", "테스트", 2016, 2025, fetch_notes=False)
        self.assertEqual(result["years"], list(range(2016, 2026)))

    def test_analysis_period_must_be_chronological(self):
        with self.assertRaisesRegex(ValueError, "시작연도"):
            analyze_dart("test", "테스트", 2025, 2021, fetch_notes=False)


if __name__ == "__main__":
    unittest.main()
