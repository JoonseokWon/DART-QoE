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
    ("자산 처분·폐기손익", (
        "처분이익", "처분손실", "매각이익", "매각손실", "폐기손실", "폐기손익", "자산폐기",
        "유형자산처분", "무형자산처분", "투자부동산처분", "종속기업처분", "종속기업투자처분",
        "사업부처분", "사업처분",
    )),
    ("자산 손상·환입", (
        "손상차손", "손상차손환입", "손상환입", "유형자산손상", "무형자산손상",
        "영업권손상", "사용권자산손상",
    )),
    ("구조조정·인력 감축 비용", (
        "구조조정", "인력감축", "사업장폐쇄", "공장폐쇄", "폐쇄비용", "희망퇴직",
        "명예퇴직", "특별퇴직", "퇴직위로금", "해고급여",
    )),
    ("기업결합·거래·통합 비용", (
        "기업결합관련비용", "기업결합비용", "합병관련비용", "합병비용", "인수관련비용",
        "인수비용", "거래관련비용", "거래비용", "통합비용", "PMI비용",
    )),
    ("소송·규제·재해 관련 손익", (
        "소송손실", "합의금", "배상금", "과징금", "과태료", "벌금", "제재금",
        "화재손실", "재해손실", "사고손실", "복구비", "보험보상수익", "보험보상금",
        "보험금수익", "보험차익",
    )),
    ("중단영업·사업 철수 손익", (
        "중단영업손익", "중단영업이익", "중단영업손실", "사업부매각손익",
        "사업부매각이익", "사업부매각손실", "영업중단", "사업철수", "철수손실", "철수비용",
    )),
    ("손실부담계약·계약 종료 비용", (
        "손실부담계약", "계약해지손실", "계약종료손실", "위약금",
        "계약종료충당부채", "철수관련충당부채",
    )),
]

INCOME_WORDS = (
    "환입", "처분이익", "매각이익", "이익", "수익", "보조금",
    "보험보상", "보험금", "보험차익",
)
LOSS_WORDS = (
    "처분손실", "매각손실", "폐기손실", "손실", "손상", "차손", "비용", "과징금",
    "과태료", "벌금", "제재금", "합의금", "배상금", "복구비", "해고급여", "위약금",
)
ONE_TIME_LIKELY_WORDS = tuple(dict.fromkeys(
    word for _, words in CANDIDATE_RULES for word in words
))
STRICT_ONE_TIME_CATEGORIES = {category for category, _ in CANDIDATE_RULES}
LOSS_DEFAULT_CATEGORIES = {
    "구조조정·인력 감축 비용",
    "기업결합·거래·통합 비용",
    "소송·규제·재해 관련 손익",
    "손실부담계약·계약 종료 비용",
}
UNIT_MULTIPLIERS = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000, "조원": 1_000_000_000_000}
NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])(\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?)(?![\d.])")
NOTE_ACCOUNT_EXCLUSIONS = (
    "미처분이익", "이익잉여금처분", "차기이월미처분", "손상차손누계액",
    "총장부금액", "장부금액합계", "금전적제재금액", "처벌또는조치",
)
DISCONTINUED_OPERATION_WORDS = (
    "중단영업", "사업부매각", "영업중단", "사업철수", "철수손실", "철수비용",
)


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


def _extract_lease_liability(text: str, year: int) -> int | float | None:
    """Extract the current-year current/non-current lease liability total.

    DART's full-account API often embeds lease liabilities in long-term debt
    rather than exposing a separate lease account. In that case, use only table
    rows whose first cell is explicitly labelled 리스부채. Duplicate report
    sections are collapsed, and the largest two distinct balances represent
    consolidated current and non-current portions when both are disclosed.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    values: set[int | float] = set()
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        if "\t" not in line or not compact.startswith("리스부채"):
            continue
        unit_context = _nearest_note_unit(lines, index)
        if not unit_context:
            continue
        _, multiplier = unit_context
        tokens = [match.group(1) for match in NUMBER_TOKEN_RE.finditer(line)]
        amount_token = next((token for token in tokens if "," in token), None)
        if amount_token is None:
            amount_token = next(
                (
                    token
                    for token in tokens
                    if token.strip("()-").isdigit()
                    and int(token.strip("()-")) not in {year - 1, year, year + 1}
                    and int(token.strip("()-")) >= 1_000
                ),
                None,
            )
        if amount_token is None:
            continue
        amount = _token_amount(amount_token, multiplier)
        if amount is not None and amount > 0:
            values.add(amount)
    if not values:
        return None
    balances = sorted(values, reverse=True)
    if len(balances) >= 3 and abs(balances[0] - balances[1] - balances[2]) <= balances[0] * 0.01:
        return balances[0]
    return sum(balances[:2])


def _decode_filing_documents(raw: bytes) -> list[tuple[str, str]]:
    documents = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith((".xml", ".html", ".htm")):
                continue
            blob = zf.read(name)
            decoded = None
            for encoding in ("utf-8", "euc-kr", "cp949"):
                try:
                    decoded = blob.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is not None:
                documents.append((name, _clean_html(decoded)))
    return documents


def _select_financial_document(
    documents: list[tuple[str, str]],
    basis: str,
) -> tuple[str, str]:
    if not documents:
        raise DartError("사업보고서 원문에 읽을 수 있는 문서가 없습니다.")

    def compact_title(text: str) -> str:
        return re.sub(r"\s+", "", " ".join(text.splitlines()[:12]))

    if basis == "CFS":
        matches = [
            item for item in documents
            if "연결감사보고서" in compact_title(item[1])
        ]
    else:
        matches = [
            item for item in documents
            if "감사보고서" in compact_title(item[1])
            and "연결감사보고서" not in compact_title(item[1])
        ]
    if matches:
        return max(matches, key=lambda item: len(item[1]))

    # Some issuers embed the notes only in the main annual-report document.
    report_matches = [
        item for item in documents
        if "사업보고서" in compact_title(item[1])
    ]
    return max(report_matches or documents, key=lambda item: len(item[1]))


def _note_row_amount(
    line: str,
    year: int,
    multiplier: int,
    lines: list[str] | None = None,
    line_index: int | None = None,
) -> tuple[int | float, bool, str] | None:
    cells = [cell.strip() for cell in line.split("\t")]
    if len(cells) < 2:
        return None
    value_cells = cells[1:]
    if len(value_cells) > 3:
        if lines is None or line_index is None:
            return None
        compact_context = [
            re.sub(r"\s+", "", context)
            for context in lines[max(0, line_index - 80):line_index]
        ]
        period = None
        for context in reversed(compact_context):
            if "(2)전기" in context or "⑵전기" in context:
                period = "prior"
                break
            if "(1)당기" in context or "⑴당기" in context:
                period = "current"
                break
        if period != "current":
            return None
        header_cells = None
        for context in reversed(lines[max(0, line_index - 12):line_index]):
            if "\t" not in context:
                continue
            candidate_cells = [cell.strip() for cell in context.split("\t")]
            if (
                len(candidate_cells) == len(cells)
                and re.sub(r"\s+", "", candidate_cells[-1]) in {"계", "합계"}
            ):
                header_cells = candidate_cells
                break
        if not header_cells:
            return None

        parsed_values = []
        displayed_values = []
        for cell in value_cells:
            compact = re.sub(r"\s+", "", cell)
            if compact in {"", "-", "–", "—"}:
                parsed_values.append(0)
                displayed_values.append("-")
                continue
            match = NUMBER_TOKEN_RE.search(compact)
            if match is None:
                return None
            token = match.group(1)
            amount = _token_amount(token, multiplier)
            if amount is None:
                return None
            parsed_values.append(amount)
            displayed_values.append(token)
        total = parsed_values[-1]
        component_sum = sum(parsed_values[:-1])
        tolerance = max(multiplier, abs(total) * 0.000001)
        if total == 0 or abs(component_sum - total) > tolerance:
            return None
        return abs(total), total < 0, displayed_values[-1]
    for index, cell in enumerate(value_cells):
        compact = re.sub(r"\s+", "", cell)
        if compact in {"-", "–", "—"}:
            return None
        match = NUMBER_TOKEN_RE.search(compact)
        if match is None:
            continue
        token = match.group(1)
        plain = token.strip("()-").replace(",", "")
        if plain.isdigit() and int(plain) in {year - 1, year, year + 1}:
            continue
        if (
            index + 1 < len(value_cells)
            and plain.isdigit()
            and int(plain) <= 300
            and "," not in token
            and "," in value_cells[index + 1]
        ):
            continue
        amount = _token_amount(token, multiplier)
        if amount is None or amount == 0:
            return None
        return abs(amount), amount < 0, token
    return None


def _note_candidates(
    text: str,
    year: int,
    rcept_no: str,
    basis: str = "CFS",
) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found = []
    seen = set()
    for index, line in enumerate(lines):
        if "\t" not in line:
            continue
        account = line.split("\t", 1)[0].strip()
        compact_account = re.sub(r"\s+", "", account)
        normalized_account = re.sub(r"[\s()（）·ㆍ\-]", "", account)
        if not compact_account or any(word in compact_account for word in NOTE_ACCOUNT_EXCLUSIONS):
            continue
        rules = CANDIDATE_RULES
        if any(word in compact_account for word in DISCONTINUED_OPERATION_WORDS):
            rules = sorted(
                CANDIDATE_RULES,
                key=lambda item: item[0] != "중단영업·사업 철수 손익",
            )
        category_match = next(
            (
                (category, word)
                for category, words in rules
                for word in words
                if re.sub(r"[\s()（）·ㆍ\-]", "", word) in normalized_account
            ),
            None,
        )
        if category_match is None:
            continue
        category, keyword = category_match
        unit_context = _nearest_note_unit(lines, index)
        if unit_context is None:
            continue
        unit, multiplier = unit_context
        amount_result = _note_row_amount(line, year, multiplier, lines, index)
        if amount_result is None:
            continue
        amount, negative, displayed_amount = amount_result
        section = ""
        for context in reversed(lines[max(0, index - 120):index]):
            match = re.match(r"^\s*\d+\.\s*([^:&\r\n]+)", context)
            if match:
                section = re.sub(r"\s+", " ", match.group(1)).strip(" .")
                break
        if (
            category == "자산 손상·환입"
            and normalized_account == "손상환입"
            and not any(
                word in re.sub(r"\s+", "", section)
                for word in ("유형자산", "무형자산", "영업권", "사용권자산", "투자부동산")
            )
        ):
            continue
        key = (category, compact_account, section, amount)
        if key in seen:
            continue
        seen.add(key)
        profit_loss_type = classify_candidate_profit_loss(account, category=category)
        if "손상" in normalized_account and "환입" in normalized_account:
            profit_loss_type = "손실" if negative else "이익"
        elif profit_loss_type == "확인 필요":
            profit_loss_type = "손실" if negative else "이익"
        found.append({
            "year": year,
            "category": category,
            "account": account,
            "amount": amount,
            "excerpt": "",
            "rcept_no": rcept_no,
            "source": (
                f"사업보고서 {'연결' if basis == 'CFS' else '별도'}재무제표 주석"
                f"{f'·{section}' if section else ''}·{unit} 환산"
            ),
            "status": "확인 필요",
            "profit_loss_type": profit_loss_type,
            "recurrence_hint": classify_recurrence_hint(account, category=category),
            "normalization_scope": classify_normalization_scope(account, category),
            "user_recurrence": "일회성",
            "user_adjustment": "예",
            "auto_extracted": True,
            "displayed_amount": displayed_amount,
            "matched_keyword": keyword,
        })
        if len(found) >= 100:
            break
    return found


def _merge_candidates(*candidate_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    positions = {}
    for group in candidate_groups:
        for candidate in group:
            key = (
                candidate.get("year"),
                candidate.get("category"),
                re.sub(r"\s+", "", candidate.get("account", "")),
                candidate.get("amount"),
            )
            existing_index = positions.get(key)
            if existing_index is None:
                positions[key] = len(merged)
                merged.append(candidate)
                continue
            if (
                merged[existing_index].get("source") == "재무제표 API"
                and str(candidate.get("source", "")).startswith("사업보고서")
            ):
                merged[existing_index] = candidate
    return merged


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
    if category in LOSS_DEFAULT_CATEGORIES:
        return "손실"
    return "확인 필요"


def classify_recurrence_hint(account: str, excerpt: str = "", category: str = "") -> str:
    """Provide a conservative recurrence hint without deciding the user's adjustment."""
    account_text = re.sub(r"\s+", "", account or "")
    excerpt_text = re.sub(r"\s+", "", excerpt or "")
    if category in STRICT_ONE_TIME_CATEGORIES or any(word in account_text for word in ONE_TIME_LIKELY_WORDS):
        return "일회성 가능"
    if category in LOSS_DEFAULT_CATEGORIES and any(
        word in excerpt_text for word in ONE_TIME_LIKELY_WORDS
    ):
        return "일회성 가능"
    return "확인 필요"


def classify_normalization_scope(account: str, category: str = "") -> str:
    """Suggest which normalized metric could use the item; the user may override it."""
    if category == "중단영업·사업 철수 손익":
        return "순이익"
    if category in STRICT_ONE_TIME_CATEGORIES:
        return "영업이익"
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
        return "\n".join(text for _, text in _decode_filing_documents(raw))

    def filing_financial_text(self, rcept_no: str, basis: str) -> str:
        raw = self._request("document.xml", {"rcept_no": rcept_no})
        _, text = _select_financial_document(_decode_filing_documents(raw), basis)
        return text


def _match_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    ids, names = ACCOUNT_RULES[key]
    statement_preferences = {
        "revenue": ("IS", "CIS"),
        "operating_profit": ("IS", "CIS"),
        "net_income": ("IS", "CIS"),
        "cfo": ("CF",),
        "ar": ("BS",),
        "inventory": ("BS",),
        "ap": ("BS",),
        "cash": ("BS",),
        "cogs": ("IS", "CIS"),
    }
    preferred_statements = statement_preferences[key]
    candidates = []
    for row in rows:
        account_id = row.get("account_id", "")
        account_nm = re.sub(r"\s+", "", row.get("account_nm", ""))
        if account_id in ids or any(n.replace(" ", "") == account_nm for n in names):
            statement = row.get("sj_div", "")
            if statement and statement not in preferred_statements:
                continue
            value = _number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
            if value is not None:
                statement_priority = (
                    preferred_statements.index(statement)
                    if statement in preferred_statements
                    else len(preferred_statements)
                )
                try:
                    order = int(row.get("ord", 9999))
                except (TypeError, ValueError):
                    order = 9999
                candidates.append((
                    statement_priority,
                    0 if account_id in ids else 1,
                    order,
                    value,
                ))
    return sorted(candidates)[0][3] if candidates else None


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
        compact_name = re.sub(r"\s+", "", name)
        statement = row.get("sj_div", "")
        if statement and statement not in {"IS", "CIS"}:
            continue
        if "기타포괄손익" in name:
            continue
        raw_amount = _number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
        if raw_amount is None or raw_amount == 0:
            continue
        amount = abs(raw_amount)
        rules = CANDIDATE_RULES
        if any(word in compact_name for word in DISCONTINUED_OPERATION_WORDS):
            rules = sorted(
                CANDIDATE_RULES,
                key=lambda item: item[0] != "중단영업·사업 철수 손익",
            )
        for category, words in rules:
            if any(word in compact_name for word in words):
                key = (category, compact_name, amount)
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
                    "user_recurrence": "일회성",
                    "user_adjustment": "예",
                })
                break
    return found


def build_analysis(
    company: dict[str, str],
    rows_by_year: dict[int, list[dict[str, Any]]],
    filings: list[dict[str, Any]],
    include_lease: bool,
    basis_by_year: dict[int, str] | None = None,
    lease_by_year: dict[int, int | float] | None = None,
    note_texts: dict[int, str] | None = None,
) -> dict[str, Any]:
    filing_by_year = {x["year"]: x for x in filings}
    years = sorted(rows_by_year)
    lease_by_year = lease_by_year or {}
    note_texts = note_texts or {}
    raw = {}
    candidates = []
    for year in years:
        rows = rows_by_year[year]
        values = {key: _match_metric(rows, key) for key in ACCOUNT_RULES}
        reported_debt = _sum_accounts(rows, DEBT_WORDS) or 0
        direct_lease = _sum_accounts(rows, LEASE_WORDS)
        lease = direct_lease if direct_lease is not None else lease_by_year.get(year)
        values["lease"] = lease or 0
        # A separate API lease account is not part of DEBT_WORDS. When the API
        # has no separate lease row but the filing discloses one, it is normally
        # embedded in 유동성장기부채/장기차입금 and must be removed once here so
        # the workbook can add it back only when the user selects inclusion.
        values["debt"] = (
            reported_debt
            if direct_lease is not None or not lease
            else max(reported_debt - lease, 0)
        )
        raw[year] = values
        rno = filing_by_year.get(year, {}).get("rcept_no", "")
        api_detected = _candidate_rows(rows, year, rno)
        note_detected = _note_candidates(
            note_texts.get(year, ""),
            year,
            rno,
            basis_by_year.get(year, "CFS") if basis_by_year else "CFS",
        )
        detected = _merge_candidates(api_detected, note_detected)
        candidates += detected

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
        "limitations": ["자동 탐지 결과는 정상화 조정 확정값이 아닙니다.",
                        "정상화 후보는 자산 처분·폐기, 자산 손상·환입, 구조조정·인력 감축, 기업결합·거래·통합, 소송·규제·재해, 중단영업·사업 철수, 손실부담계약·계약 종료의 7개 유형으로 제한합니다.",
                        "재고자산평가손실·대손·보증충당부채·정부보조금·관계기업·일반 기타손익은 정상화 후보로 추출하지 않습니다.",
                        "강한 일회성 후보는 일회성·영업이익·조정 예로 초기 반영되며 사용자가 원문과 발생 원인을 확인해 제외할 수 있습니다.",
                        "검토 후보는 재무제표 API와 사업보고서의 연결·별도 재무제표 주석에서 7개 유형의 금액을 기본 추출합니다.",
                        "리스부채는 재무제표 별도 계정 또는 사업보고서의 리스부채 표에서 금액을 확인합니다.",
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
                          {y: [] for y in years}, filings, True)
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
        {"year": 2024, "category": "자산 처분·폐기손익", "account": "유형자산처분이익", "amount": 2_100_000_000,
         "excerpt": "유휴 생산설비 매각으로 유형자산처분이익을 인식함. 반복성 및 매각 배경 확인 필요.", "rcept_no": filings[-1]["rcept_no"], "source": "샘플 원문", "status": "확인 필요",
         "profit_loss_type": "이익", "recurrence_hint": "일회성 가능", "normalization_scope": "영업이익",
         "user_recurrence": "일회성", "user_adjustment": "예"},
        {"year": 2024, "category": "소송·규제·재해 관련 손익", "account": "소송손실", "amount": 850_000_000,
         "excerpt": "단일 소송 합의에 따른 손실을 인식함. 사건의 종결 여부와 금액을 원문에서 확인.", "rcept_no": filings[-1]["rcept_no"], "source": "샘플 원문", "status": "확인 필요",
         "profit_loss_type": "손실", "recurrence_hint": "일회성 가능", "normalization_scope": "영업이익",
         "user_recurrence": "일회성", "user_adjustment": "예"},
    ]
    return data


def analyze_dart(
    api_key: str,
    company_query: str,
    begin_year: int,
    end_year: int,
    include_lease: bool = True,
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
    rows_by_year, basis_by_year, filings, lease_by_year, note_texts, errors = {}, {}, [], {}, {}, []
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
            report(round(year_start + year_span * 0.55), f"{year}년 사업보고서 원문과 주석을 조회하고 있습니다")
            filing = client.annual_filing(company["corp_code"], year)
            if filing:
                filing = {**filing, "year": year, "url": DART_VIEWER + filing["rcept_no"]}
                filings.append(filing)
                financial_text = client.filing_financial_text(filing["rcept_no"], basis)
                note_texts[year] = financial_text
                if _sum_accounts(rows, LEASE_WORDS) is None:
                    lease_amount = _extract_lease_liability(financial_text, year)
                    if lease_amount is not None:
                        lease_by_year[year] = lease_amount
        except Exception as exc:
            errors.append({"year": year, "stage": "사업보고서 원문·주석·리스부채", "message": str(exc)})
        report(round(year_start + year_span), f"{year}년 공시 자료 확인 완료")
    if not rows_by_year:
        detail = " / ".join(f"{item['year']}년: {item['message']}" for item in errors if item["stage"] == "재무제표")
        raise DartError(f"분석 가능한 연결·별도 재무제표를 찾지 못했습니다. {detail}".strip())
    report(86, "재무지표와 검토 후보를 계산하고 있습니다")
    result = build_analysis(
        company, rows_by_year, filings, include_lease, basis_by_year, lease_by_year, note_texts
    )
    result["errors"] = errors
    report(91, "분석 계산을 완료했습니다")
    return result


def save_json(data: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
