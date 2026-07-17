from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET


API_BASE = "https://opendart.fss.or.kr/api"
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


ACCOUNT_RULES = {
    "revenue": (["ifrs-full_Revenue"], ["매출액", "영업수익", "수익(매출액)"]),
    "operating_profit": (["dart_OperatingIncomeLoss"], ["영업이익", "영업이익(손실)"]),
    "net_income": (["ifrs-full_ProfitLoss"], ["당기순이익", "당기순이익(손실)"]),
    "cfo": (["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름"]),
    "ar": (["ifrs-full_TradeAndOtherCurrentReceivables"], ["매출채권", "매출채권 및 기타유동채권"]),
    "inventory": (["ifrs-full_Inventories"], ["재고자산"]),
    "ap": (["ifrs-full_TradeAndOtherCurrentPayables"], ["매입채무", "매입채무 및 기타유동채무"]),
    "cash": (["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
    "cogs": (["ifrs-full_CostOfSales"], ["매출원가"]),
}

DEBT_WORDS = ("단기차입금", "장기차입금", "유동성장기", "사채", "전환사채", "차입부채")
LEASE_WORDS = ("리스부채",)

CANDIDATE_RULES = [
    ("유형자산 처분손익", ("처분이익", "처분손실", "유형자산처분", "매각이익", "매각손실")),
    ("손상·충당부채", ("손상차손", "손상환입", "충당부채전입", "충당부채환입", "대손상각",
                    "대손충당금전입", "재고자산평가손실", "재고자산평가충당금환입",
                    "품질보증충당부채", "판매보증충당부채")),
    ("소송·재해 등 사건", ("소송손실", "화재손실", "재해손실", "사고손실", "복구비", "합의금", "과징금")),
    ("구조조정·거래 관련 비용", ("구조조정", "희망퇴직", "명예퇴직", "퇴직위로금", "기업결합관련비용",
                              "합병관련비용", "인수관련비용", "거래관련비용", "통합비용")),
    ("정부보조금", ("정부보조금", "국고보조금", "보조금수익")),
    ("비영업·중단영업·대규모 기타손익", ("관계기업투자처분이익", "관계기업투자처분손실",
                                      "지분법이익", "지분법손실", "중단영업",
                                      "기타수익", "기타비용", "잡이익", "잡손실")),
]

INCOME_WORDS = ("환입", "처분이익", "매각이익", "이익", "수익", "보조금")
LOSS_WORDS = ("처분손실", "매각손실", "손실", "차손", "비용", "과징금", "합의금", "복구비")
RECURRING_LIKELY_WORDS = (
    "재고자산평가손실", "재고자산평가충당금환입", "대손상각", "대손충당금전입",
    "품질보증충당부채", "판매보증충당부채", "지분법이익", "지분법손실",
)
ONE_TIME_LIKELY_WORDS = (
    "중단영업", "소송손실", "화재손실", "재해손실", "사고손실", "합의금", "과징금",
    "구조조정", "희망퇴직", "명예퇴직", "퇴직위로금", "기업결합관련비용",
    "합병관련비용", "인수관련비용", "거래관련비용", "통합비용",
)
OPERATING_SCOPE_WORDS = (
    "재고자산평가손실", "재고자산평가충당금환입", "대손상각", "대손충당금전입",
    "품질보증충당부채", "판매보증충당부채",
)
NET_INCOME_SCOPE_WORDS = (
    "관계기업", "지분법", "중단영업", "금융수익", "금융비용", "기타수익", "기타비용",
)
UNIT_MULTIPLIERS = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000, "조원": 1_000_000_000_000}
NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])(\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?)(?![\d.])")
NOTE_CANDIDATE_EXCLUSIONS = ("미처분이익", "이익잉여금처분", "차기이월미처분")


class DartError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        result = float(text)
        return -result if negative else result
    except ValueError:
        return None


def _clean_html(text: str) -> str:
    row_marker = "\u241eDART_QOE_ROW\u241e"
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)</tr>", f"\n{row_marker}\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</title>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    # DART XML frequently inserts source-code line breaks between adjacent table
    # cells. Join those cells before restoring the explicit table-row marker.
    text = re.sub(r"[ \r\f\v\n]*\t[ \r\f\v\n]*", "\t", text)
    text = text.replace(row_marker, "\n")
    text = re.sub(r"[ \r\f\v]+", " ", text)
    text = re.sub(r" *\t+ *", "\t", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _nearest_note_unit(lines: list[str], index: int, lookback: int = 30) -> tuple[str, int] | None:
    for line in reversed(lines[max(0, index - lookback): index + 1]):
        compact = re.sub(r"\s+", "", line)
        match = re.search(r"단위[:：]?\(?\s*(조원|억원|백만원|천원|원)\)?", compact)
        if match:
            unit = match.group(1)
            return unit, UNIT_MULTIPLIERS[unit]
    return None


def _token_amount(token: str, multiplier: int) -> int | float | None:
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace(",", "")
    try:
        value = Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return int(value) if value == value.to_integral_value() else float(value)


def _keyword_original_position(line: str, keyword: str) -> int:
    compact_chars = []
    positions = []
    for position, char in enumerate(line):
        if not char.isspace():
            compact_chars.append(char)
            positions.append(position)
    compact = "".join(compact_chars)
    compact_index = compact.find(keyword)
    return positions[compact_index] if compact_index >= 0 else -1


def _extract_note_amount(line: str, keyword: str, year: int, multiplier: int) -> tuple[int | float, str] | None:
    if "\t" not in line:
        return None
    keyword_position = _keyword_original_position(line, keyword)
    tokens = []
    for match in NUMBER_TOKEN_RE.finditer(line):
        token = match.group(1)
        value = _token_amount(token, multiplier)
        if value is None or value == 0:
            continue
        unscaled = abs(float(value)) / multiplier
        plain_integer = token.strip("()-").isdigit()
        if plain_integer and int(unscaled) in {year - 1, year, year + 1}:
            continue
        tokens.append((match.start(), value, token))
    if not tokens:
        return None
    after_keyword = [item for item in tokens if keyword_position >= 0 and item[0] > keyword_position]
    if not after_keyword:
        return None
    selected_index = 0
    # Full financial-statement rows often have the layout
    # "account | note number | current year | prior year". A small, unformatted
    # first integer followed by another amount is a note reference, not money.
    first_token = after_keyword[0][2].strip("()-")
    next_token = after_keyword[1][2] if len(after_keyword) >= 2 else ""
    if (
        len(after_keyword) >= 2
        and first_token.isdigit()
        and int(first_token) <= 300
        and "," not in first_token
        and "," in next_token
    ):
        selected_index = 1
    selected = after_keyword[selected_index]
    return selected[1], selected[2]


def classify_candidate_profit_loss(account: str, excerpt: str = "", category: str = "") -> str:
    """Classify only the P&L direction; recurrence is evaluated separately."""
    account_text = re.sub(r"\s+", "", account or "")
    excerpt_text = re.sub(r"\s+", "", excerpt or "")
    for text in (account_text, excerpt_text):
        if any(word in text for word in INCOME_WORDS):
            return "이익"
        if any(word in text for word in LOSS_WORDS):
            return "손실"
    if category == "정부보조금":
        return "이익"
    if category in {"소송·재해 등 사건", "구조조정·거래 관련 비용"}:
        return "손실"
    return "확인 필요"


def classify_recurrence_hint(account: str, excerpt: str = "", category: str = "") -> str:
    """Provide a conservative recurrence hint without deciding the user's adjustment."""
    account_text = re.sub(r"\s+", "", account or "")
    excerpt_text = re.sub(r"\s+", "", excerpt or "")
    if any(word in account_text for word in RECURRING_LIKELY_WORDS):
        return "반복 가능"
    if any(word in account_text for word in ONE_TIME_LIKELY_WORDS):
        return "일회성 가능"
    if category == "정부보조금":
        return "반복 가능"
    if category in {"소송·재해 등 사건", "구조조정·거래 관련 비용"} and any(
        word in excerpt_text for word in ONE_TIME_LIKELY_WORDS
    ):
        return "일회성 가능"
    return "확인 필요"


def classify_normalization_scope(account: str, category: str = "") -> str:
    """Suggest which normalized metric could use the item; the user may override it."""
    account_text = re.sub(r"\s+", "", account or "")
    if any(word in account_text for word in OPERATING_SCOPE_WORDS):
        return "영업이익"
    if any(word in account_text for word in NET_INCOME_SCOPE_WORDS):
        return "순이익"
    if category == "비영업·중단영업·대규모 기타손익":
        return "순이익"
    return "미결정"


@dataclass
class DartClient:
    api_key: str
    timeout: int = 30

    def _request(self, endpoint: str, params: dict[str, Any]) -> bytes:
        query = urllib.parse.urlencode({"crtfc_key": self.api_key, **params})
        req = urllib.request.Request(f"{API_BASE}/{endpoint}?{query}", headers={"User-Agent": "DART-QoE/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.read()
        except Exception as exc:
            raise DartError(f"DART 요청 실패 ({endpoint}): {exc}") from exc

    def json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        raw = self._request(endpoint, params)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise DartError(f"DART JSON 해석 실패 ({endpoint})") from exc
        if data.get("status") != "000":
            raise DartError(f"DART {data.get('status')}: {data.get('message', '알 수 없는 오류')}")
        return data

    def resolve_company(self, query: str) -> dict[str, str]:
        raw = self._request("corpCode.xml", {})
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            root = ET.fromstring(zf.read(zf.namelist()[0]))
        q = query.strip().lower()
        matches = []
        for node in root.findall("list"):
            item = {child.tag: (child.text or "").strip() for child in node}
            if q in {item.get("corp_code", "").lower(), item.get("stock_code", "").lower()} or q in item.get("corp_name", "").lower():
                matches.append(item)
        if not matches:
            raise DartError(f"회사 '{query}'를 찾지 못했습니다.")
        exact = [m for m in matches if q in (m.get("corp_name", "").lower(), m.get("stock_code", "").lower(), m.get("corp_code", "").lower())]
        return (exact or matches)[0]

    def annual_accounts(self, corp_code: str, year: int) -> tuple[list[dict[str, Any]], str]:
        params = {"corp_code": corp_code, "bsns_year": year, "reprt_code": "11011"}
        try:
            data = self.json("fnlttSinglAcntAll.json", {**params, "fs_div": "CFS"})
            rows = data.get("list", [])
            if rows:
                return rows, "CFS"
        except DartError as exc:
            # 013 means that the requested company/year/basis has no data.
            # Authentication, rate-limit and network errors must remain visible.
            if not str(exc).startswith("DART 013:"):
                raise

        data = self.json("fnlttSinglAcntAll.json", {**params, "fs_div": "OFS"})
        rows = data.get("list", [])
        if not rows:
            raise DartError(f"{year}년 연결·별도 재무제표 조회 결과가 없습니다.")
        return rows, "OFS"

    def annual_filing(self, corp_code: str, year: int) -> dict[str, Any] | None:
        data = self.json("list.json", {
            "corp_code": corp_code, "bgn_de": f"{year + 1}0101", "end_de": f"{year + 1}1231",
            "pblntf_ty": "A", "last_reprt_at": "Y", "page_count": 100,
        })
        items = [x for x in data.get("list", []) if "사업보고서" in x.get("report_nm", "") and f"{year}.12" in x.get("report_nm", "")]
        return items[0] if items else None

    def filing_text(self, rcept_no: str) -> str:
        raw = self._request("document.xml", {"rcept_no": rcept_no})
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            chunks = []
            for name in zf.namelist():
                if name.lower().endswith((".xml", ".html", ".htm")):
                    blob = zf.read(name)
                    for enc in ("utf-8", "euc-kr", "cp949"):
                        try:
                            chunks.append(blob.decode(enc)); break
                        except UnicodeDecodeError:
                            pass
        return _clean_html("\n".join(chunks))


def _match_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    ids, names = ACCOUNT_RULES[key]
    candidates = []
    for row in rows:
        account_id = row.get("account_id", "")
        account_nm = re.sub(r"\s+", "", row.get("account_nm", ""))
        if account_id in ids or any(n.replace(" ", "") == account_nm for n in names):
            value = _number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
            if value is not None:
                candidates.append((0 if account_id in ids else 1, value))
    return sorted(candidates)[0][1] if candidates else None


def _sum_accounts(rows: list[dict[str, Any]], words: tuple[str, ...]) -> float | None:
    values = []
    for row in rows:
        name = re.sub(r"\s+", "", row.get("account_nm", ""))
        if any(word in name for word in words) and not any(x in name for x in ("합계", "총계", "이자", "상환")):
            value = _number(row.get("thstrm_amount"))
            if value is not None:
                values.append((row.get("account_id", ""), name, value))
    unique = {(a, n, v): v for a, n, v in values}
    return sum(unique.values()) if unique else None


def _candidate_rows(rows: list[dict[str, Any]], year: int, rcept_no: str | None) -> list[dict[str, Any]]:
    found = []
    seen = set()
    for row in rows:
        name = row.get("account_nm", "")
        statement = row.get("sj_div", "")
        if statement and statement not in {"IS", "CIS"}:
            continue
        if "기타포괄손익" in name:
            continue
        raw_amount = _number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
        if raw_amount is None:
            continue
        amount = abs(raw_amount)
        for category, words in CANDIDATE_RULES:
            if any(word in name for word in words):
                key = (category, re.sub(r"\s+", "", name), amount)
                if key in seen:
                    break
                seen.add(key)
                found.append({
                    "year": year, "category": category, "account": name,
                    "amount": amount,
                    "excerpt": "재무제표 계정명 기준 자동 탐지 — 일회성 여부는 원문 주석 확인 필요",
                    "rcept_no": rcept_no or "", "source": "재무제표 API", "status": "확인 필요",
                    "profit_loss_type": classify_candidate_profit_loss(name, category=category),
                    "recurrence_hint": classify_recurrence_hint(name, category=category),
                    "normalization_scope": classify_normalization_scope(name, category),
                })
                break
    return found


def _note_candidates(text: str, year: int, rcept_no: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found = []
    seen = set()
    for i, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if any(excluded in compact for excluded in NOTE_CANDIDATE_EXCLUSIONS):
            continue
        for category, words in CANDIDATE_RULES:
            hit = next((word for word in words if word in compact), None)
            if not hit:
                continue
            unit_context = _nearest_note_unit(lines, i)
            if not unit_context:
                continue
            unit, multiplier = unit_context
            amount_result = _extract_note_amount(line, hit, year, multiplier)
            if not amount_result:
                continue
            raw_amount, displayed_amount = amount_result
            amount = abs(raw_amount)
            excerpt_body = " | ".join(lines[max(0, i - 1): min(len(lines), i + 2)])
            excerpt = f"[표시단위: {unit}, 선택 금액: {displayed_amount}] {excerpt_body}"[:600]
            key = (category, hit, amount)
            if key in seen:
                continue
            seen.add(key)
            found.append({"year": year, "category": category, "account": hit, "amount": amount,
                          "excerpt": excerpt, "rcept_no": rcept_no, "source": f"사업보고서 원문·{unit} 환산", "status": "확인 필요",
                          "profit_loss_type": classify_candidate_profit_loss(hit, excerpt, category),
                          "recurrence_hint": classify_recurrence_hint(hit, excerpt, category),
                          "normalization_scope": classify_normalization_scope(hit, category)})
            if len(found) >= 60:
                return found
    return found


def _deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    positions = {}
    for candidate in candidates:
        key = (
            candidate.get("year"),
            candidate.get("category"),
            re.sub(r"\s+", "", candidate.get("account", "")),
            candidate.get("amount"),
        )
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(result)
            result.append(candidate)
            continue
        existing = result[existing_index]
        if existing.get("source") == "재무제표 API" and candidate.get("source", "").startswith("사업보고서 원문"):
            result[existing_index] = candidate
    return result


def build_analysis(company: dict[str, str], rows_by_year: dict[int, list[dict[str, Any]]], filings: list[dict[str, Any]], note_texts: dict[int, str], include_lease: bool, basis_by_year: dict[int, str] | None = None) -> dict[str, Any]:
    filing_by_year = {x["year"]: x for x in filings}
    years = sorted(rows_by_year)
    raw = {}
    candidates = []
    for year in years:
        rows = rows_by_year[year]
        values = {key: _match_metric(rows, key) for key in ACCOUNT_RULES}
        values["debt"] = _sum_accounts(rows, DEBT_WORDS)
        values["lease"] = _sum_accounts(rows, LEASE_WORDS)
        raw[year] = values
        rno = filing_by_year.get(year, {}).get("rcept_no", "")
        candidates += _candidate_rows(rows, year, rno)
        if note_texts.get(year):
            candidates += _note_candidates(note_texts[year], year, rno)
    candidates = _deduplicate_candidates(candidates)

    metrics = []
    for i, year in enumerate(years):
        v = raw[year]
        prev = raw.get(years[i - 1]) if i else None
        def avg(key: str) -> float | None:
            if v.get(key) is None: return None
            return (v[key] + prev[key]) / 2 if prev and prev.get(key) is not None else v[key]
        revenue, cogs = v.get("revenue"), v.get("cogs")
        debt = v.get("debt") or 0
        lease = (v.get("lease") or 0) if include_lease else 0
        metrics.append({
            "year": year, **v,
            "revenue_growth": (revenue / prev["revenue"] - 1) if prev and revenue is not None and prev.get("revenue") else None,
            "operating_margin": v["operating_profit"] / revenue if revenue and v.get("operating_profit") is not None else None,
            "cfo_to_operating_profit": v["cfo"] / v["operating_profit"] if v.get("operating_profit") else None,
            "ni_cfo_gap": (v["net_income"] - v["cfo"]) if v.get("net_income") is not None and v.get("cfo") is not None else None,
            "ar_days": avg("ar") / revenue * 365 if revenue and avg("ar") is not None else None,
            "inventory_days": avg("inventory") / cogs * 365 if cogs and avg("inventory") is not None else None,
            "ap_days": avg("ap") / cogs * 365 if cogs and avg("ap") is not None else None,
            "nwc": (v.get("ar") or 0) + (v.get("inventory") or 0) - (v.get("ap") or 0),
            "nwc_to_revenue": ((v.get("ar") or 0) + (v.get("inventory") or 0) - (v.get("ap") or 0)) / revenue if revenue else None,
            "net_debt": debt + lease - (v.get("cash") or 0),
        })
    basis_by_year = basis_by_year or {year: "CFS" for year in years}
    basis_values = set(basis_by_year.values())
    if basis_values == {"CFS"}:
        basis_label = "연결재무제표"
    elif basis_values == {"OFS"}:
        basis_label = "별도재무제표 (연결 자료 미제공)"
    else:
        basis_label = "연도별 연결·별도 기준 혼합"
    return {
        "metadata": {"company_name": company.get("corp_name", ""), "corp_code": company.get("corp_code", ""),
                     "stock_code": company.get("stock_code", ""), "basis": basis_label,
                     "basis_by_year": {str(year): basis_by_year[year] for year in years}, "unit": "원",
                     "include_lease": include_lease, "created": date.today().isoformat()},
        "years": years, "metrics": metrics, "candidates": candidates,
        "filings": filings,
        "limitations": ["자동 탐지 결과는 정상화 조정 확정값이 아닙니다.", "반복성 힌트와 정상화 대상은 계정명 기준 초기값이며 사용자가 원문과 발생 원인을 확인해야 합니다.",
                        "정상화 영업이익은 사용자가 일회성·영업이익·조정 예로 판단한 항목만 반영합니다.",
                        "원문 주석 후보는 표시단위와 금액을 같은 표 행에서 확인한 경우에만 제시합니다.",
                        "계정명·키워드 후보는 원문 주석 및 경영진 설명과 대조해야 합니다.",
                        "회전일수는 기초 비교값이 없을 때 기말잔액을 사용합니다.", "상각전영업이익 배수는 감가상각비·무형자산상각비의 신뢰성 확보 전까지 개념검증 범위에서 제외했습니다."],
    }


def demo_analysis() -> dict[str, Any]:
    years = [2022, 2023, 2024]
    values = {
        2022: dict(revenue=120_000_000_000, operating_profit=9_600_000_000, net_income=7_200_000_000, cfo=5_400_000_000,
                   ar=19_000_000_000, inventory=15_000_000_000, ap=11_000_000_000, cash=12_000_000_000, cogs=78_000_000_000, debt=25_000_000_000, lease=2_000_000_000),
        2023: dict(revenue=145_000_000_000, operating_profit=13_050_000_000, net_income=10_200_000_000, cfo=7_000_000_000,
                   ar=27_000_000_000, inventory=23_000_000_000, ap=13_000_000_000, cash=10_000_000_000, cogs=91_000_000_000, debt=31_000_000_000, lease=2_400_000_000),
        2024: dict(revenue=168_000_000_000, operating_profit=17_640_000_000, net_income=14_000_000_000, cfo=8_100_000_000,
                   ar=38_000_000_000, inventory=31_000_000_000, ap=15_000_000_000, cash=9_000_000_000, cogs=103_000_000_000, debt=39_000_000_000, lease=3_000_000_000),
    }
    filings = [{"year": y, "report_nm": f"사업보고서 ({y}.12)", "rcept_no": f"20250{y}000001", "rcept_dt": f"{y+1}0315",
                "url": DART_VIEWER + f"20250{y}000001"} for y in years]
    data = build_analysis({"corp_name": "샘플 제조", "corp_code": "00000000", "stock_code": "000000"},
                          {y: [] for y in years}, filings, {}, True)
    data["metrics"] = []
    for i, y in enumerate(years):
        v, p = values[y], values.get(years[i-1]) if i else None
        avg = lambda k: (v[k] + p[k]) / 2 if p else v[k]
        data["metrics"].append({"year": y, **v,
            "revenue_growth": v["revenue"] / p["revenue"] - 1 if p else None,
            "operating_margin": v["operating_profit"] / v["revenue"], "cfo_to_operating_profit": v["cfo"] / v["operating_profit"],
            "ni_cfo_gap": v["net_income"] - v["cfo"], "ar_days": avg("ar") / v["revenue"] * 365,
            "inventory_days": avg("inventory") / v["cogs"] * 365, "ap_days": avg("ap") / v["cogs"] * 365,
            "nwc": v["ar"] + v["inventory"] - v["ap"], "nwc_to_revenue": (v["ar"] + v["inventory"] - v["ap"]) / v["revenue"],
            "net_debt": v["debt"] + v["lease"] - v["cash"]})
    data["candidates"] = [
        {"year": 2024, "category": "유형자산 처분손익", "account": "유형자산처분이익", "amount": 2_100_000_000,
         "excerpt": "유휴 생산설비 매각으로 유형자산처분이익을 인식함. 반복성 및 매각 배경 확인 필요.", "rcept_no": filings[-1]["rcept_no"], "source": "샘플 원문", "status": "확인 필요",
         "profit_loss_type": "이익", "recurrence_hint": "확인 필요", "normalization_scope": "미결정"},
        {"year": 2024, "category": "정부보조금", "account": "정부보조금수익", "amount": 850_000_000,
         "excerpt": "설비투자 지원 관련 정부보조금이 기타수익에 포함됨. 지속 조건과 표시 분류 확인 필요.", "rcept_no": filings[-1]["rcept_no"], "source": "샘플 원문", "status": "확인 필요",
         "profit_loss_type": "이익", "recurrence_hint": "반복 가능", "normalization_scope": "미결정"},
    ]
    return data


def analyze_dart(
    api_key: str,
    company_query: str,
    begin_year: int,
    end_year: int,
    include_lease: bool = True,
    fetch_notes: bool = True,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    def report(percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(percent, message)

    if begin_year > end_year:
        raise ValueError("분석 시작연도는 종료연도보다 늦을 수 없습니다.")
    report(4, "회사 정보를 확인하고 있습니다")
    client = DartClient(api_key.strip())
    company = client.resolve_company(company_query)
    report(10, f"회사 확인 완료 · {company['corp_name']}")
    rows_by_year, basis_by_year, filings, note_texts, errors = {}, {}, [], {}, []
    years = list(range(begin_year, end_year + 1))
    year_span = 72 / len(years)
    for index, year in enumerate(years):
        year_start = 10 + year_span * index
        report(round(year_start + year_span * 0.10), f"{year}년 재무제표를 조회하고 있습니다")
        try:
            rows, basis = client.annual_accounts(company["corp_code"], year)
            if rows:
                rows_by_year[year] = rows
                basis_by_year[year] = basis
        except Exception as exc:
            errors.append({"year": year, "stage": "재무제표", "message": str(exc)})
            report(round(year_start + year_span), f"{year}년 재무제표 조회를 마쳤습니다")
            continue
        report(round(year_start + year_span * 0.45), f"{year}년 재무제표 확인 완료")
        try:
            report(round(year_start + year_span * 0.55), f"{year}년 사업보고서와 주석을 조회하고 있습니다")
            filing = client.annual_filing(company["corp_code"], year)
            if filing:
                filing = {**filing, "year": year, "url": DART_VIEWER + filing["rcept_no"]}
                filings.append(filing)
                if fetch_notes:
                    note_texts[year] = client.filing_text(filing["rcept_no"])
        except Exception as exc:
            errors.append({"year": year, "stage": "공시 원문", "message": str(exc)})
        report(round(year_start + year_span), f"{year}년 공시 자료 확인 완료")
    if not rows_by_year:
        detail = " / ".join(f"{item['year']}년: {item['message']}" for item in errors if item["stage"] == "재무제표")
        raise DartError(f"분석 가능한 연결·별도 재무제표를 찾지 못했습니다. {detail}".strip())
    report(86, "재무지표와 검토 후보를 계산하고 있습니다")
    result = build_analysis(company, rows_by_year, filings, note_texts, include_lease, basis_by_year)
    result["errors"] = errors
    report(91, "분석 계산을 완료했습니다")
    return result


def save_json(data: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
