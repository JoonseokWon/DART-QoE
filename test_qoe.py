import unittest

from qoe import demo_analysis


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


if __name__ == "__main__":
    unittest.main()
