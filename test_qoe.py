import unittest
from unittest.mock import patch

from qoe import (
    DartClient,
    DartError,
    _candidate_rows,
    _clean_html,
    _extract_lease_liability,
    _merge_candidates,
    _note_candidates,
    _select_financial_document,
    analyze_dart,
    build_analysis,
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
        self.assertEqual(classify_recurrence_hint("유형자산처분이익", category="자산 처분·폐기손익"), "일회성 가능")
        self.assertEqual(classify_recurrence_hint("소송손실", category="소송·규제·재해 관련 손익"), "일회성 가능")
        self.assertEqual(classify_normalization_scope("손상차손", category="자산 손상·환입"), "영업이익")
        self.assertEqual(
            classify_normalization_scope("중단영업손실", category="중단영업·사업 철수 손익"),
            "순이익",
        )
        self.assertEqual(classify_normalization_scope("지분법이익"), "미결정")

    def test_lease_liability_uses_current_and_noncurrent_disclosures(self):
        note = """
        10. 유형자산
        (단위: 백만원)
        리스부채(*2)\tCSSD 등\t3.3\t811,902\t710,781
        리스부채(*2)\tCSSD 등\t3.3\t2,864,656\t1,999,716
        리스부채(*2)\tCSSD 등\t3.3\t811,902\t710,781
        """
        self.assertEqual(_extract_lease_liability(note, 2021), 3_676_558_000_000)

    def test_lease_option_changes_net_debt_by_disclosed_lease_amount(self):
        company = {"corp_name": "테스트", "corp_code": "00123456", "stock_code": "123456"}
        rows = {
            2024: [
                {"account_nm": "장기차입금", "thstrm_amount": "1500"},
                {"account_nm": "현금및현금성자산", "account_id": "ifrs-full_CashAndCashEquivalents", "thstrm_amount": "500"},
            ]
        }
        lease_by_year = {2024: 300}
        included = build_analysis(company, rows, [], True, lease_by_year=lease_by_year)
        excluded = build_analysis(company, rows, [], False, lease_by_year=lease_by_year)
        self.assertEqual(included["metrics"][0]["debt"], 1200)
        self.assertEqual(included["metrics"][0]["lease"], 300)
        self.assertEqual(included["metrics"][0]["net_debt"], 1000)
        self.assertEqual(excluded["metrics"][0]["net_debt"], 700)

    def test_metric_uses_primary_statement_instead_of_equity_duplicate(self):
        company = {"corp_name": "테스트", "corp_code": "00123456", "stock_code": "123456"}
        rows = {
            2024: [
                {
                    "sj_div": "IS",
                    "account_id": "ifrs-full_ProfitLoss",
                    "account_nm": "당기순이익",
                    "thstrm_amount": "34451351000000",
                    "ord": "18",
                },
                {
                    "sj_div": "SCE",
                    "account_id": "ifrs-full_ProfitLoss",
                    "account_nm": "당기순이익",
                    "thstrm_amount": "0",
                    "ord": "13",
                },
                {
                    "sj_div": "SCE",
                    "account_id": "ifrs-full_ProfitLoss",
                    "account_nm": "당기순이익",
                    "thstrm_amount": "829988000000",
                    "ord": "13",
                },
            ]
        }
        result = build_analysis(company, rows, [], True)
        self.assertEqual(result["metrics"][0]["net_income"], 34_451_351_000_000)

    def test_candidate_rows_use_profit_and_loss_statements_and_deduplicate(self):
        rows = [
            {"sj_div": "BS", "account_nm": "충당부채", "thstrm_amount": "1000"},
            {"sj_div": "CF", "account_nm": "관계기업 투자 취득", "thstrm_amount": "2000"},
            {"sj_div": "IS", "account_nm": "유형자산처분이익", "thstrm_amount": "3000"},
            {"sj_div": "IS", "account_nm": "유형자산처분이익", "thstrm_amount": "3000"},
            {"sj_div": "IS", "account_nm": "손상차손", "thstrm_amount": None},
            {"sj_div": "IS", "account_nm": "소송손실", "thstrm_amount": "0"},
            {"sj_div": "IS", "account_nm": "기업결합관련비용", "thstrm_amount": "5000"},
        ]
        candidates = _candidate_rows(rows, 2024, "202500000001")
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["account"], "유형자산처분이익")
        self.assertEqual(candidates[0]["amount"], 3000)
        self.assertEqual(candidates[1]["recurrence_hint"], "일회성 가능")

    def test_candidate_rows_cover_all_seven_one_time_categories(self):
        accounts = [
            "유형자산 처분이익",
            "영업권 손상차손",
            "특별퇴직 비용",
            "기업결합 관련 비용",
            "과징금",
            "사업부 매각이익",
            "손실부담계약손실",
        ]
        rows = [
            {"sj_div": "IS", "account_nm": account, "thstrm_amount": str(index * 1000)}
            for index, account in enumerate(accounts, start=1)
        ]
        candidates = _candidate_rows(rows, 2024, "202500000001")
        self.assertEqual(
            [item["category"] for item in candidates],
            [
                "자산 처분·폐기손익",
                "자산 손상·환입",
                "구조조정·인력 감축 비용",
                "기업결합·거래·통합 비용",
                "소송·규제·재해 관련 손익",
                "중단영업·사업 철수 손익",
                "손실부담계약·계약 종료 비용",
            ],
        )
        self.assertEqual(candidates[5]["normalization_scope"], "순이익")
        self.assertEqual(candidates[5]["profit_loss_type"], "이익")
        self.assertTrue(all(item["recurrence_hint"] == "일회성 가능" for item in candidates))

    def test_missing_categories_are_not_added_as_blank_review_rows(self):
        company = {"corp_name": "테스트", "corp_code": "00123456", "stock_code": "123456"}
        result = build_analysis(
            company,
            {2024: [{"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1000"}]},
            [],
            True,
        )
        self.assertEqual(result["candidates"], [])

    def test_clean_html_preserves_table_cell_separators(self):
        cleaned = _clean_html(
            "<table><tr>\n<td>유형자산처분이익</td>\n<td>1,234</td>\n</tr>"
            "<tr><td>손상차손</td><td>(250)</td></tr></table>"
        )
        self.assertIn("유형자산처분이익\t1,234", cleaned)
        self.assertIn("손상차손\t(250)", cleaned)
        self.assertNotIn("1,234\t손상차손", cleaned)

    def test_financial_document_selection_follows_statement_basis(self):
        documents = [
            ("main.xml", "사업보고서\n본문"),
            ("separate.xml", "감사보고서\n재무제표 주석\n" + "별도" * 20),
            ("consolidated.xml", "연결감사보고서\n연결재무제표 주석\n" + "연결" * 20),
        ]
        self.assertEqual(_select_financial_document(documents, "CFS")[0], "consolidated.xml")
        self.assertEqual(_select_financial_document(documents, "OFS")[0], "separate.xml")

    def test_note_candidates_extract_current_year_amounts_and_exclude_false_positives(self):
        note = """
        10. 유형자산
        (단위: 백만원)
        연결미처분이익잉여금\t100,000\t90,000
        유형자산처분이익\t81,647\t104,663
        유형자산처분손실\t124,018\t88,000
        손상환입\t13,416\t12,000
        손상차손누계액\t1,000,000\t900,000
        세후중단영업손익\t(26,900)\t38,265
        사업부매각이익\t38,265\t-
        세후중단영업손익(*1)\t(1,242)\t-\t170,022\t-\t289,965\t-
        """
        candidates = _note_candidates(note, 2024, "202500000001", "CFS")
        self.assertEqual(
            [(item["account"], item["amount"]) for item in candidates],
            [
                ("유형자산처분이익", 81_647_000_000),
                ("유형자산처분손실", 124_018_000_000),
                ("손상환입", 13_416_000_000),
                ("세후중단영업손익", 26_900_000_000),
                ("사업부매각이익", 38_265_000_000),
            ],
        )
        self.assertTrue(all(item["auto_extracted"] for item in candidates))
        self.assertEqual(candidates[3]["category"], "중단영업·사업 철수 손익")
        self.assertEqual(candidates[3]["profit_loss_type"], "손실")
        self.assertNotIn("연결미처분이익잉여금", [item["account"] for item in candidates])
        self.assertNotIn("손상차손누계액", [item["account"] for item in candidates])

    def test_note_candidates_use_verified_current_period_totals_from_multi_column_tables(self):
        note = """
        10. 유형자산
        가. 당기 및 전기 중 유형자산의 변동 내역은 다음과 같습니다.
        (1) 당기
        (단위: 백만원)
        구 분\t토지\t건물\t기계장치\t기타\t계
        손상(환입)\t-\t(2,255)\t(11,815)\t(12,323)\t(26,393)
        (2) 전기
        (단위: 백만원)
        구 분\t토지\t건물\t기계장치\t기타\t계
        손상(환입)\t-\t(12,135)\t(131,985)\t(7,082)\t(151,202)
        20. 금융상품
        (1) 당기
        (단위: 백만원)
        금융자산\t상각후원가\t공정가치\t기타\t파생상품\t계
        손상(환입)\t(19,124)\t-\t-\t-\t(19,124)
        """
        candidates = _note_candidates(note, 2022, "202300000001", "CFS")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["account"], "손상(환입)")
        self.assertEqual(candidates[0]["amount"], 26_393_000_000)
        self.assertEqual(candidates[0]["profit_loss_type"], "손실")
        self.assertIn("유형자산", candidates[0]["source"])

    def test_note_candidate_replaces_same_api_candidate(self):
        api_candidate = {
            "year": 2024,
            "category": "자산 처분·폐기손익",
            "account": "유형자산처분이익",
            "amount": 81_647_000_000,
            "source": "재무제표 API",
        }
        note_candidate = {
            **api_candidate,
            "source": "사업보고서 연결재무제표 주석·백만원 환산",
            "auto_extracted": True,
        }
        merged = _merge_candidates([api_candidate], [note_candidate])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["source"].startswith("사업보고서"))

    def test_analysis_always_reads_financial_statement_notes(self):
        company = {"corp_code": "00123456", "corp_name": "테스트", "stock_code": "123456"}
        filing = {"rcept_no": "202500000001", "report_nm": "사업보고서 (2024.12)", "rcept_dt": "20250331"}
        note = "(단위: 백만원)\n유형자산처분이익\t81,647\t104,663"
        with patch.object(DartClient, "resolve_company", return_value=company), patch.object(
            DartClient,
            "annual_accounts",
            return_value=([{"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "1000000"}], "CFS"),
        ), patch.object(DartClient, "annual_filing", return_value=filing), patch.object(
            DartClient,
            "filing_financial_text",
            return_value=note,
        ) as note_fetch:
            result = analyze_dart("test", "테스트", 2024, 2024)
        note_fetch.assert_called_once_with("202500000001", "CFS")
        extracted = [item for item in result["candidates"] if item.get("auto_extracted")]
        self.assertEqual(extracted[0]["amount"], 81_647_000_000)

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
            result = analyze_dart("test", "테스트", 2016, 2025)
        self.assertEqual(result["years"], list(range(2016, 2026)))

    def test_analysis_period_must_be_chronological(self):
        with self.assertRaisesRegex(ValueError, "시작연도"):
            analyze_dart("test", "테스트", 2025, 2021)


if __name__ == "__main__":
    unittest.main()
