import unittest
from unittest.mock import patch

from qoe import (
    DartClient,
    DartError,
    _candidate_rows,
    _clean_html,
    _deduplicate_candidates,
    _note_candidates,
    analyze_dart,
    classify_normalization_scope,
    classify_candidate_profit_loss,
    classify_recurrence_hint,
    demo_analysis,
)


class DemoAnalysisTests(unittest.TestCase):
    def test_candidate_profit_loss_classification(self):
        self.assertEqual(classify_candidate_profit_loss("유형자산처분이익"), "이익")
        self.assertEqual(classify_candidate_profit_loss("손상차손"), "손실")
        self.assertEqual(classify_candidate_profit_loss("손상환입"), "이익")
        self.assertEqual(classify_candidate_profit_loss("관계기업투자"), "확인 필요")

    def test_strict_one_time_candidates_default_to_operating_profit(self):
        self.assertEqual(classify_recurrence_hint("유형자산처분이익", category="유형자산 처분손익"), "일회성 가능")
        self.assertEqual(classify_recurrence_hint("소송손실", category="소송·재해 등 사건"), "일회성 가능")
        self.assertEqual(classify_normalization_scope("손상차손", category="자산 손상"), "영업이익")
        self.assertEqual(classify_normalization_scope("지분법이익"), "미결정")

    def test_note_candidates_require_and_convert_disclosed_amounts(self):
        note = """
        23. 기타수익 및 기타비용
        (단위: 백만원)
        구분\t2024\t2023
        유형자산처분이익\t1,234\t500
        손상차손\t(250)\t100
        기타수익은 당기손익으로 표시합니다.
        """
        candidates = _note_candidates(note, 2024, "202500000001")
        amounts = {item["account"]: item["amount"] for item in candidates}
        self.assertEqual(amounts["처분이익"], 1_234_000_000)
        self.assertEqual(amounts["손상차손"], 250_000_000)
        self.assertTrue(all(item["amount"] is not None for item in candidates))
        self.assertFalse(any("기타수익" == item["account"] for item in candidates))

    def test_note_candidates_skip_keywords_without_amount_or_unit(self):
        note = "회사는 관계기업 투자와 충당부채 및 기타수익에 관한 회계정책을 적용합니다."
        self.assertEqual(_note_candidates(note, 2024, "202500000001"), [])

    def test_recurring_and_non_operating_items_are_not_extracted(self):
        note = """
        (단위: 백만원)
        재고자산평가손실\t1,200
        대손상각비\t300
        정부보조금수익\t500
        지분법이익\t900
        기타수익\t700
        """
        self.assertEqual(_note_candidates(note, 2024, "202500000001"), [])

    def test_candidate_rows_use_profit_and_loss_statements_and_deduplicate(self):
        rows = [
            {"sj_div": "BS", "account_nm": "충당부채", "thstrm_amount": "1000"},
            {"sj_div": "CF", "account_nm": "관계기업 투자 취득", "thstrm_amount": "2000"},
            {"sj_div": "IS", "account_nm": "유형자산처분이익", "thstrm_amount": "3000"},
            {"sj_div": "IS", "account_nm": "유형자산처분이익", "thstrm_amount": "3000"},
            {"sj_div": "IS", "account_nm": "손상차손", "thstrm_amount": None},
            {"sj_div": "IS", "account_nm": "기업결합관련비용", "thstrm_amount": "5000"},
        ]
        candidates = _candidate_rows(rows, 2024, "202500000001")
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["account"], "유형자산처분이익")
        self.assertEqual(candidates[0]["amount"], 3000)
        self.assertEqual(candidates[1]["recurrence_hint"], "일회성 가능")

    def test_note_candidate_replaces_duplicate_api_candidate(self):
        base = {"year": 2024, "category": "기타손익", "account": "기타수익", "amount": 1_000}
        candidates = _deduplicate_candidates([
            {**base, "source": "재무제표 API"},
            {**base, "source": "사업보고서 원문·백만원 환산"},
        ])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "사업보고서 원문·백만원 환산")

    def test_clean_html_preserves_table_cell_separators(self):
        cleaned = _clean_html(
            "<table><tr>\n<td>유형자산처분이익</td>\n<td>1,234</td>\n</tr>"
            "<tr><td>손상차손</td><td>(250)</td></tr></table>"
        )
        self.assertIn("유형자산처분이익\t1,234", cleaned)
        self.assertIn("손상차손\t(250)", cleaned)
        self.assertNotIn("1,234\t손상차손", cleaned)

    def test_note_candidates_ignore_amount_like_numbers_in_prose(self):
        note = """
        (단위: 백만원)
        회사는 2024년에 기타수익 1,234와 관련한 회계정책을 검토했습니다.
        """
        self.assertEqual(_note_candidates(note, 2024, "202500000001"), [])

    def test_note_candidates_skip_note_number_and_false_substring(self):
        note = """
        (단위: 백만원)
        기타수익\t23\t7,359,004\t797,494
        미처분이익잉여금\t23,614,523\t8,401,233
        """
        candidates = _note_candidates(note, 2021, "20220308000798")
        self.assertEqual(candidates, [])

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

    def test_strict_candidates_are_initially_reflected_and_remain_reviewable(self):
        self.assertGreaterEqual(len(self.data["candidates"]), 2)
        self.assertTrue(all(x["status"] == "확인 필요" for x in self.data["candidates"]))
        self.assertTrue(all(x["user_recurrence"] == "일회성" for x in self.data["candidates"]))
        self.assertTrue(all(x["normalization_scope"] == "영업이익" for x in self.data["candidates"]))
        self.assertTrue(all(x["user_adjustment"] == "예" for x in self.data["candidates"]))

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
