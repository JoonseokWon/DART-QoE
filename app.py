from __future__ import annotations

import os
import base64
import math
import re
import subprocess
import sys
import threading
import traceback
import ctypes
import zipfile
import posixpath
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from ctypes import wintypes
from collections import Counter
from copy import copy
from datetime import datetime
from pathlib import Path

# The bundled workspace Python can run Tkinter even when PyInstaller cannot
# discover its Tcl/Tk data directories automatically. The executable build
# includes those directories explicitly, so point Tk at them before import.
if getattr(sys, "frozen", False):
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # Always replace inherited values: an auto-update helper may otherwise
    # carry paths to the previous process's already-deleted _MEI directory.
    os.environ["TCL_LIBRARY"] = os.path.join(bundle_root, "_tcl_data")
    os.environ["TK_LIBRARY"] = os.path.join(bundle_root, "_tk_data")

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from qoe import analyze_dart, demo_analysis, save_json


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
CONFIG_DIR = Path(os.environ.get("APPDATA", ROOT)) / "DART-QoE"
API_KEY_FILE = CONFIG_DIR / "api-key.dat"
NODE_DEFAULT = Path(r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")
WATCHED_SOURCE_FILES = (ROOT / "app.py", ROOT / "qoe.py", ROOT / "export_workbook.mjs")

NAVY = "#112A46"
BLUE = "#2F5597"
PALE = "#EDF3F9"
INK = "#17202A"
MUTED = "#66788A"
LINE = "#D7E0EA"
GREEN = "#2E7D5B"
AMBER = "#FFF2CC"
WHITE = "#FFFFFF"
BG = "#F4F7FA"
SINGLE_INSTANCE_MUTEX = r"Local\DART-QoE-JoonseokWon"
ERROR_ALREADY_EXISTS = 183
_single_instance_handle: int | None = None


def source_snapshot(paths: tuple[Path, ...] = WATCHED_SOURCE_FILES) -> tuple[tuple[str, int, int], ...]:
    """Return a lightweight signature for development hot-reload inputs."""
    snapshot = []
    for path in paths:
        try:
            stat = path.stat()
            snapshot.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            snapshot.append((str(path), 0, 0))
    return tuple(snapshot)


def packaged_update_path(executable: Path | None = None) -> Path:
    current = executable or Path(sys.executable).resolve()
    return current.with_name(f"{current.stem}.update{current.suffix}")


def _ps_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def updater_command(current_exe: Path, update_exe: Path, process_id: int) -> list[str]:
    """Build a hidden PowerShell helper that installs and relaunches a packaged update."""
    working_dir = current_exe.parent
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$current = {_ps_literal(current_exe)}
$update = {_ps_literal(update_exe)}
$working = {_ps_literal(working_dir)}
Wait-Process -Id {process_id} -ErrorAction SilentlyContinue
# A PyInstaller one-file app has a parent bootloader process in addition to
# the Python child process above. Wait for that parent to finish deleting its
# temporary _MEI directory before replacing and relaunching the executable.
$processName = [System.IO.Path]::GetFileNameWithoutExtension($current)
for ($attempt = 0; $attempt -lt 40; $attempt++) {{
    $remaining = @(
        Get-Process -Name $processName -ErrorAction SilentlyContinue |
        Where-Object {{ $_.Path -eq $current }}
    )
    if ($remaining.Count -eq 0) {{ break }}
    Start-Sleep -Milliseconds 250
}}
$remaining = @(
    Get-Process -Name $processName -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Path -eq $current }}
)
if ($remaining.Count -gt 0) {{
    $remaining | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 1000
}}
$installed = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {{
    try {{
        Copy-Item -LiteralPath $update -Destination $current -Force -ErrorAction Stop
        Remove-Item -LiteralPath $update -Force -ErrorAction SilentlyContinue
        $installed = $true
        break
    }} catch {{
        Start-Sleep -Milliseconds 250
    }}
}}
if (Test-Path -LiteralPath $current) {{
    Start-Sleep -Milliseconds 2000
    Remove-Item Env:TCL_LIBRARY -ErrorAction SilentlyContinue
    Remove-Item Env:TK_LIBRARY -ErrorAction SilentlyContinue
    # The helper inherits PyInstaller's private environment from the old
    # one-file process. If it is passed to the replacement executable, the
    # new bootloader can reuse the deleted old _MEI directory and fail to load
    # python312.dll. Clear it and force a fresh top-level extraction.
    Get-ChildItem Env: | Where-Object {{ $_.Name -like '_PYI_*' }} | ForEach-Object {{
        Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue
    }}
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    # Delegate the launch to the Windows shell process. Explorer is already
    # running outside PyInstaller, so the replacement app cannot inherit the
    # previous one-file bootloader's private _PYI/_MEI environment.
    $shell = New-Object -ComObject Shell.Application
    $shell.ShellExecute($current, '', $working, 'open', 1)
}}
""".strip()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [
        str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
    ]


def _metric_value(metric: dict, key: str) -> float | None:
    value = metric.get(key)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _format_amount(value: float | None) -> str:
    if value is None:
        return "확인 불가"
    absolute = abs(value)
    if absolute >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:,.2f}조원"
    if absolute >= 100_000_000:
        return f"{value / 100_000_000:,.1f}억원"
    return f"{value:,.0f}원"


def _format_percent(value: float | None) -> str:
    return "확인 불가" if value is None else f"{value:.1%}"


def build_result_summary(data: dict) -> str:
    """Create a compact, decision-oriented screen summary; details stay in Excel."""
    metadata = data.get("metadata", {})
    metrics = sorted(data.get("metrics", []), key=lambda item: item.get("year", 0))
    candidates = data.get("candidates", []) or []
    automatic_candidates = [item for item in candidates if item.get("auto_extracted", True)]
    errors = data.get("errors", []) or []
    company = metadata.get("company_name", "-")
    basis = metadata.get("basis", "-")

    if not metrics:
        return (
            f"{company} · QoE 검토 요약\n\n"
            "[분석 결과]\n"
            "요약 가능한 재무지표가 없습니다. Excel의 원천 자료와 검증 시트를 확인하세요."
        )

    first, latest = metrics[0], metrics[-1]
    first_year, latest_year = first.get("year", "-"), latest.get("year", "-")
    first_revenue = _metric_value(first, "revenue")
    latest_revenue = _metric_value(latest, "revenue")
    year_gap = latest.get("year", 0) - first.get("year", 0)
    cagr = None
    if first_revenue and latest_revenue is not None and latest_revenue >= 0 and year_gap > 0:
        cagr = (latest_revenue / first_revenue) ** (1 / year_gap) - 1

    first_margin = _metric_value(first, "operating_margin")
    latest_margin = _metric_value(latest, "operating_margin")
    margin_change = latest_margin - first_margin if latest_margin is not None and first_margin is not None else None
    cash_conversion = _metric_value(latest, "cfo_to_operating_profit")
    latest_nwc_ratio = _metric_value(latest, "nwc_to_revenue")
    previous = metrics[-2] if len(metrics) > 1 else None
    previous_year = previous.get("year", "-") if previous else "-"
    previous_nwc_ratio = _metric_value(previous, "nwc_to_revenue") if previous else None
    nwc_change = latest_nwc_ratio - previous_nwc_ratio if latest_nwc_ratio is not None and previous_nwc_ratio is not None else None
    latest_net_debt = _metric_value(latest, "net_debt")
    first_net_debt = _metric_value(first, "net_debt")

    if latest_net_debt is None:
        debt_text = "확인 불가"
    elif latest_net_debt < 0:
        debt_text = f"순현금 {_format_amount(abs(latest_net_debt))}"
    elif latest_net_debt > 0:
        debt_text = (
            f"순현금 {_format_amount(-latest_net_debt)} "
            f"(순차입금 {_format_amount(latest_net_debt)})"
        )
    else:
        debt_text = "순현금 0원"

    category_counts = Counter(item.get("category", "기타") for item in automatic_candidates)
    top_categories = ", ".join(f"{name} {count}건" for name, count in category_counts.most_common(3))
    candidate_text = (
        f"자동 탐지 {len(automatic_candidates)}건" + (f" · {top_categories}" if top_categories else "")
        if automatic_candidates
        else "없음"
    )

    review_points = []
    if cash_conversion is not None and cash_conversion < 0.8:
        review_points.append(f"{latest_year}년 영업현금 전환율이 {_format_percent(cash_conversion)}로 낮아 현금화 원인을 확인하세요.")
    if nwc_change is not None and nwc_change > 0.02:
        review_points.append(
            f"매출 대비 순운전자본이 {previous_year}년에서 {latest_year}년으로 "
            f"{nwc_change * 100:.1f}%p 상승했습니다."
        )
    if latest_net_debt is not None and first_net_debt is not None and latest_net_debt > first_net_debt:
        funding_change = latest_net_debt - first_net_debt
        if first_net_debt >= 0:
            review_points.append(
                f"순차입금이 {first_year}년 {_format_amount(first_net_debt)}에서 "
                f"{latest_year}년 {_format_amount(latest_net_debt)}으로 "
                f"{_format_amount(funding_change)} 증가했습니다."
            )
        elif latest_net_debt < 0:
            review_points.append(
                f"순현금이 {first_year}년 {_format_amount(abs(first_net_debt))}에서 "
                f"{latest_year}년 {_format_amount(abs(latest_net_debt))}으로 "
                f"{_format_amount(funding_change)} 감소했습니다."
            )
        else:
            review_points.append(
                f"자금 상태가 {first_year}년 순현금 {_format_amount(abs(first_net_debt))}에서 "
                f"{latest_year}년 순차입금 {_format_amount(latest_net_debt)}으로 전환되어, "
                f"순차입금 기준 {_format_amount(funding_change)} 증가했습니다."
            )
    if automatic_candidates:
        review_points.append("조정 후보는 확정 조정이 아니므로 금액·반복 여부·원문 맥락을 확인하세요.")
    if not review_points:
        review_points.append("자동 지표에서 뚜렷한 추가 확인 신호는 없지만 원문과 계산 근거 검토는 필요합니다.")

    error_text = "없음" if not errors else f"{len(errors)}건"
    lines = [
        f"{company} · QoE 검토 요약",
        "",
        "[분석 개요]",
        f"• 기간: {first_year}~{latest_year} · {basis}",
        f"• 추출 오류 또는 제한사항: {error_text}",
        "",
        "[핵심 지표]",
        f"• 매출: {first_year}년 {_format_amount(first_revenue)} → "
        f"{latest_year}년 {_format_amount(latest_revenue)} · 연평균 성장률 {_format_percent(cagr)}",
        f"• 영업이익률: {first_year}년 {_format_percent(first_margin)} → "
        f"{latest_year}년 {_format_percent(latest_margin)}"
        + (f" · {margin_change * 100:+.1f}%p" if margin_change is not None else ""),
        f"• 영업현금 전환율: {latest_year}년 {_format_percent(cash_conversion)}",
        f"• 매출 대비 순운전자본: {latest_year}년 {_format_percent(latest_nwc_ratio)}"
        + (
            f" · {previous_year}년 → {latest_year}년 {nwc_change * 100:+.1f}%p"
            if nwc_change is not None
            else ""
        ),
        f"• 자금 상태: {latest_year}년 {debt_text}",
        f"• 정상화 조정 검토 후보: {candidate_text}",
        "• 정상화 손익: Excel에서 조정 여부와 적용 금액을 입력하면 손익 유형에 따라 자동 계산",
        "",
        "[추가 확인 포인트]",
        *[f"• {point}" for point in review_points[:3]],
        "",
        "세부 계정, 계산식, 공시 원문과 사용자 조정란은 Excel에서 확인하세요.",
    ]
    return "\n".join(lines)


def enable_dpi_awareness() -> None:
    """Use Tk-compatible native-resolution drawing before creating a window."""
    try:
        # Tk 8.6 can misplace Korean IME composition text with Per-Monitor V2.
        # System DPI awareness keeps the primary-monitor rendering sharp while
        # preserving the input method's text baseline and composition position.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_windows_data(value: bytes, decrypt: bool = False) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    source = DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    crypt32 = ctypes.windll.crypt32
    if decrypt:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
        )
    else:
        success = crypt32.CryptProtectData(
            ctypes.byref(source), ctypes.c_wchar_p("DART-QoE API key"), None, None, None, 0, ctypes.byref(target)
        )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def load_saved_api_key() -> str:
    if not API_KEY_FILE.exists():
        return ""
    try:
        return _protect_windows_data(API_KEY_FILE.read_bytes(), decrypt=True).decode("utf-8")
    except (OSError, UnicodeError):
        return ""


def save_api_key(api_key: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    API_KEY_FILE.write_bytes(_protect_windows_data(api_key.encode("utf-8")))


def delete_saved_api_key() -> None:
    API_KEY_FILE.unlink(missing_ok=True)


class NativeWindowsEntry:
    """A native Windows EDIT control embedded in Tk for reliable Korean IME input."""

    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    WS_TABSTOP = 0x00010000
    WS_BORDER = 0x00800000
    ES_CENTER = 0x0001
    ES_AUTOHSCROLL = 0x0080
    EM_SETPASSWORDCHAR = 0x00CC
    WM_SETFONT = 0x0030

    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        scale: float,
        *,
        show: str | None = None,
    ):
        self.variable = variable
        self.scale = scale
        self._destroyed = False
        self.vertical_offset = max(1, round(2 * scale))
        self.container = tk.Frame(
            parent, bg=WHITE, height=max(34, round(36 * scale)), takefocus=True,
            highlightbackground=LINE, highlightcolor=BLUE, highlightthickness=1,
        )
        self.container.pack(fill="x")
        self.container.pack_propagate(False)
        self.container.update_idletasks()

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        gdi32 = ctypes.windll.gdi32
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        )
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.SetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
        user32.SetWindowTextW.restype = wintypes.BOOL
        user32.MoveWindow.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.BOOL,
        )
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.SendMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.SendMessageW.restype = wintypes.LPARAM
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        gdi32.CreateFontW.restype = wintypes.HFONT
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteObject.restype = wintypes.BOOL

        styles = (
            self.WS_CHILD
            | self.WS_VISIBLE
            | self.WS_TABSTOP
            | self.ES_CENTER
            | self.ES_AUTOHSCROLL
        )
        self.hwnd = user32.CreateWindowExW(
            0,
            "EDIT",
            variable.get(),
            styles,
            0,
            self.vertical_offset,
            max(1, self.container.winfo_width()),
            max(1, self.container.winfo_height() - self.vertical_offset),
            self.container.winfo_id(),
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError()

        # Match the 10-point Tk controls instead of letting native entries
        # appear larger at high DPI.
        font_height = -max(14, round(14 * scale))
        self.font_handle = gdi32.CreateFontW(
            font_height,
            0,
            0,
            0,
            400,
            0,
            0,
            0,
            1,
            0,
            0,
            5,
            0,
            "Malgun Gothic",
        )
        if self.font_handle:
            user32.SendMessageW(self.hwnd, self.WM_SETFONT, self.font_handle, 1)
        if show:
            user32.SendMessageW(self.hwnd, self.EM_SETPASSWORDCHAR, ord(show[0]), 0)

        # Native EDIT controls do not participate in Tk's Tab/Return bindings.
        # Bridge those keys while leaving IME composition keys with Windows.
        self._composing = False
        self._focused = False
        self._pending_key: str | None = None
        self._wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        self._set_wndproc = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        self._set_wndproc.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
        self._set_wndproc.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = (
            ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.CallWindowProcW.restype = ctypes.c_ssize_t

        def window_proc(hwnd, message, wparam, lparam):
            if message == 0x010D:  # WM_IME_STARTCOMPOSITION
                self._composing = True
            elif message == 0x010E:  # WM_IME_ENDCOMPOSITION
                self._composing = False
            elif message == 0x0007:  # WM_SETFOCUS
                self._focused = True
            elif message == 0x0008:  # WM_KILLFOCUS
                self._focused = False
            elif message == 0x0100 and not self._composing:
                if wparam == 9:
                    backwards = bool(user32.GetKeyState(0x10) & 0x8000)
                    self._pending_key = "previous" if backwards else "next"
                    return 0
                if wparam == 13:
                    self._pending_key = "run"
                    return 0
            return user32.CallWindowProcW(self._old_wndproc, hwnd, message, wparam, lparam)

        self._wndproc = self._wndproc_type(window_proc)
        self._old_wndproc = self._set_wndproc(self.hwnd, -4, ctypes.cast(self._wndproc, ctypes.c_void_p))
        self.container.bind("<FocusIn>", lambda _event: self.focus_set(), add="+")

        self.container.bind("<Configure>", self._resize, add="+")
        self.container.bind("<Destroy>", self._destroy, add="+")
        self.container.after(80, self._poll)

    def _resize(self, _event: tk.Event | None = None) -> None:
        if self._destroyed or not ctypes.windll.user32.IsWindow(self.hwnd):
            return
        ctypes.windll.user32.MoveWindow(
            self.hwnd,
            2,
            self.vertical_offset,
            max(1, self.container.winfo_width() - 4),
            max(1, self.container.winfo_height() - self.vertical_offset - 2),
            True,
        )

    def sync_from_control(self) -> str:
        if self._destroyed or not ctypes.windll.user32.IsWindow(self.hwnd):
            return self.variable.get()
        length = ctypes.windll.user32.GetWindowTextLengthW(self.hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(self.hwnd, buffer, length + 1)
        value = buffer.value
        if self.variable.get() != value:
            self.variable.set(value)
        return value

    def focus_set(self) -> None:
        if self._destroyed or not ctypes.windll.user32.IsWindow(self.hwnd):
            return
        ctypes.windll.user32.SetFocus(self.hwnd)

    def _move_focus(self, backwards: bool) -> None:
        target = self.container.tk_focusPrev() if backwards else self.container.tk_focusNext()
        if target is not None:
            target.focus_set()

    def _poll(self) -> None:
        if self._destroyed:
            return
        self.sync_from_control()
        self.container.configure(highlightbackground=BLUE if self._focused else LINE)
        pending, self._pending_key = self._pending_key, None
        if pending == "run":
            self.container.winfo_toplevel().event_generate("<<RunAnalysis>>")
        elif pending:
            self._move_focus(pending == "previous")
        self.container.after(40, self._poll)

    def _destroy(self, event: tk.Event) -> None:
        if event.widget is not self.container or self._destroyed:
            return
        self._destroyed = True
        if ctypes.windll.user32.IsWindow(self.hwnd):
            self._set_wndproc(self.hwnd, -4, self._old_wndproc)
            ctypes.windll.user32.DestroyWindow(self.hwnd)
        if self.font_handle:
            ctypes.windll.gdi32.DeleteObject(self.font_handle)


def _chart_axis_title_xml(text: str, x_position: float) -> str:
    return (
        "<c:title><c:tx><c:rich>"
        '<a:bodyPr rot="0" vert="horz" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" />'
        '<a:lstStyle xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" />'
        '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<a:r><a:t>{text}</a:t></a:r></a:p>"
        "</c:rich></c:tx>"
        "<c:layout><c:manualLayout>"
        '<c:xMode val="edge" /><c:yMode val="edge" />'
        f'<c:x val="{x_position}" /><c:y val="0.826" />'
        "</c:manualLayout></c:layout><c:overlay val=\"1\" />"
        "</c:title>"
    )


def _populate_chart_caches(xml: str, archive: zipfile.ZipFile) -> str:
    """Keep Excel chart previews in sync with formula-cached worksheet values."""
    if "xl/workbook.xml" not in archive.namelist():
        return xml  # Minimal chart-only fixture used by the axis unit test.
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    paths = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
    book = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = {}
    for sheet in book.findall("s:sheets/s:sheet", ns):
        rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = paths[rel_id]
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        root = ET.fromstring(archive.read(path))
        sheets[sheet.attrib["name"]] = {
            cell.attrib["r"]: cell.findtext("s:v", namespaces=ns)
            for cell in root.findall(".//s:sheetData/s:row/s:c", ns)
        }

    def fill(match: re.Match) -> str:
        kind, body = match.group(1), match.group(2)
        reference = ET.fromstring(f'<root xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">{body}</root>')
        formula = reference.findtext("{http://schemas.openxmlformats.org/drawingml/2006/chart}f", default="")
        parsed = re.fullmatch(r"'(.+)'!\$?([A-Z]+)\$?(\d+):\$?\2\$?(\d+)", formula)
        if not parsed:
            return match.group(0)
        sheet_name, column, start, end = parsed.groups()
        values = [sheets[sheet_name].get(f"{column}{row}") for row in range(int(start), int(end) + 1)]
        if any(value is None for value in values):
            raise RuntimeError(f"차트 데이터 캐시가 없습니다: {formula}")
        cache = "numCache" if kind == "numRef" else "strCache"
        number_format = re.search(r"<c:formatCode>.*?</c:formatCode>", body, re.DOTALL)
        prefix = number_format.group(0) if number_format and kind == "numRef" else ""
        points = "".join(f'<c:pt idx="{i}"><c:v>{escape(value)}</c:v></c:pt>' for i, value in enumerate(values))
        replacement = f'<c:{cache}>{prefix}<c:ptCount val="{len(values)}" />{points}</c:{cache}>'
        body = re.sub(fr"<c:{cache}>.*?</c:{cache}>", replacement, body, flags=re.DOTALL)
        return f"<c:{kind}>{body}</c:{kind}>"

    return re.sub(r"<c:(numRef|strRef)>(.*?)</c:\1>", fill, xml, flags=re.DOTALL)


def _patch_qoe_chart_secondary_axis(workbook_path: Path) -> None:
    """Move the cash-conversion series to a percentage axis on the right."""
    target_name = "영업현금흐름/영업이익"
    chart_prefix = "xl/drawings/charts/chart"
    temp_path = workbook_path.with_name(f".{workbook_path.name}.secondary-axis.tmp")
    patched = False
    try:
        with zipfile.ZipFile(workbook_path, "r") as source, zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename.startswith(chart_prefix) and item.filename.endswith(".xml"):
                    xml = payload.decode("utf-8")
                    line_match = re.search(r"<c:lineChart>.*?</c:lineChart>", xml, re.DOTALL)
                    if line_match and target_name in line_match.group(0):
                        primary_chart = line_match.group(0)
                        target_series = next(
                            (
                                series
                                for series in re.findall(r"<c:ser>.*?</c:ser>", primary_chart, re.DOTALL)
                                if target_name in series
                            ),
                            None,
                        )
                        if target_series is None:
                            raise RuntimeError("현금전환율 차트 계열을 찾지 못했습니다.")
                        axis_ids = [int(value) for value in re.findall(r'<c:axId val="(\d+)"', primary_chart)]
                        if len(axis_ids) < 2:
                            raise RuntimeError("QoE 차트의 기본 축 정보를 찾지 못했습니다.")
                        secondary_category_id = max(axis_ids) + 1
                        secondary_value_id = secondary_category_id + 1
                        primary_chart = primary_chart.replace(target_series, "", 1)
                        secondary_chart = (
                            "<c:lineChart><c:grouping val=\"standard\" />"
                            f"{target_series}"
                            f'<c:axId val="{secondary_category_id}" />'
                            f'<c:axId val="{secondary_value_id}" />'
                            "</c:lineChart>"
                        )
                        secondary_axes = (
                            "<c:catAx>"
                            f'<c:axId val="{secondary_category_id}" />'
                            '<c:scaling><c:orientation val="minMax" /></c:scaling>'
                            '<c:delete val="1" /><c:axPos val="t" />'
                            '<c:numFmt formatCode="General" sourceLinked="1" />'
                            '<c:majorTickMark val="none" /><c:minorTickMark val="none" />'
                            '<c:tickLblPos val="none" />'
                            f'<c:crossAx val="{secondary_value_id}" /><c:crosses val="max" />'
                            '<c:auto val="1" /><c:lblAlgn val="ctr" /><c:lblOffset val="100" />'
                            '<c:noMultiLvlLbl val="0" />'
                            "</c:catAx>"
                            "<c:valAx>"
                            f'<c:axId val="{secondary_value_id}" />'
                            '<c:scaling><c:orientation val="minMax" /></c:scaling>'
                            '<c:delete val="0" /><c:axPos val="r" />'
                            f"{_chart_axis_title_xml('영업현금흐름/영업이익 (%)', 0.84)}"
                            '<c:numFmt formatCode="0%" sourceLinked="0" />'
                            '<c:majorTickMark val="none" /><c:minorTickMark val="none" />'
                            '<c:tickLblPos val="nextTo" />'
                            f'<c:crossAx val="{secondary_category_id}" />'
                            '<c:crosses val="max" /><c:crossBetween val="between" />'
                            "</c:valAx>"
                        )
                        xml = xml[:line_match.start()] + primary_chart + secondary_chart + xml[line_match.end():]
                        primary_axis_match = re.search(r"<c:valAx>.*?</c:valAx>", xml, re.DOTALL)
                        if primary_axis_match is None:
                            raise RuntimeError("QoE 차트의 왼쪽 금액 축을 찾지 못했습니다.")
                        primary_axis = primary_axis_match.group(0)
                        left_title = _chart_axis_title_xml("금액(원)", 0.01)
                        if re.search(r"<c:title>.*?</c:title>", primary_axis, re.DOTALL):
                            primary_axis = re.sub(
                                r"<c:title>.*?</c:title>",
                                left_title,
                                primary_axis,
                                count=1,
                                flags=re.DOTALL,
                            )
                        else:
                            primary_axis = primary_axis.replace(
                                '<c:axPos val="l" />',
                                f'<c:axPos val="l" />{left_title}',
                                1,
                            )
                        xml = (
                            xml[:primary_axis_match.start()]
                            + primary_axis
                            + xml[primary_axis_match.end():]
                        )
                        xml = xml.replace("</c:plotArea>", f"{secondary_axes}</c:plotArea>", 1)
                        payload = _populate_chart_caches(xml, source).encode("utf-8")
                        patched = True
                target.writestr(copy(item), payload)
        if not patched:
            raise RuntimeError("QoE 요약 차트의 현금전환율 계열을 찾지 못했습니다.")
        os.replace(temp_path, workbook_path)
    finally:
        temp_path.unlink(missing_ok=True)


def export_excel(data: dict, stem: str, progress_callback=None) -> Path:
    def report(percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(percent, message)

    OUTPUTS.mkdir(exist_ok=True)
    safe = "".join(ch for ch in stem if ch.isalnum() or ch in "-_가-힣") or "qoe"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUTS / f".{safe}_{stamp}.json"
    out = OUTPUTS / f"DART-QoE_{safe}_{stamp}.xlsx"
    report(93, "엑셀 원천 자료를 준비하고 있습니다")
    save_json(data, json_path)
    bundled_node = BUNDLE_ROOT / "node" / "node.exe"
    node = Path(os.environ.get("DART_QOE_NODE", bundled_node if FROZEN else NODE_DEFAULT))
    if not node.exists():
        raise RuntimeError("엑셀 생성 모듈을 찾지 못했습니다. 실행파일을 다시 내려받아 주세요.")
    env = os.environ.copy()
    default_node_path = BUNDLE_ROOT / "node_modules" if FROZEN else Path(
        r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
    )
    env.setdefault("NODE_PATH", str(default_node_path))
    try:
        report(96, "엑셀 시트와 차트를 생성하고 있습니다")
        completed = subprocess.run(
            [str(node), str(BUNDLE_ROOT / "export_workbook.mjs"), str(json_path), str(out)],
            cwd=BUNDLE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr[-2000:] or completed.stdout[-2000:])
        report(98, "차트의 금액 축과 현금전환율 보조축을 적용하고 있습니다")
        _patch_qoe_chart_secondary_axis(out)
    finally:
        json_path.unlink(missing_ok=True)
    report(99, "엑셀 파일 저장을 마무리하고 있습니다")
    return out


class DartQoeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.last_output: Path | None = None
        self.busy = False
        self.pending_api_key = ""
        self.pending_save_key = False
        self.native_entries: list[NativeWindowsEntry] = []
        self.restarting = False
        self._source_snapshot = source_snapshot() if not FROZEN else ()
        self._update_signature: tuple[int, int] | None = None
        self._update_stable_polls = 0
        self._layout_mode = ""

        root.title("DART-QoE | 정상화 이익과 운전자본 검토")
        dpi = max(96.0, float(root.winfo_fpixels("1i")))
        self.scale = min(2.0, max(1.0, dpi / 96.0))
        root.tk.call("tk", "scaling", dpi / 72.0)
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width = min(int(screen_width * 0.82), self.px(1440))
        height = min(int(screen_height * 0.86), self.px(940))
        width = max(width, min(self.px(940), int(screen_width * 0.94)))
        height = max(height, min(self.px(680), int(screen_height * 0.90)))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(min(self.px(820), int(screen_width * 0.90)),
                     min(self.px(650), int(screen_height * 0.86)))
        root.configure(bg=BG)
        root.option_add("*Font", ("맑은 고딕", 10))
        self._set_icon()
        self._configure_styles()
        self._build_ui()
        self.root.bind("<Return>", self._run_from_keyboard, add="+")
        self.root.bind("<<RunAnalysis>>", self._run_from_keyboard, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after_idle(lambda: self._layout_cards(force=True))
        # Development source files can still hot-reload. Packaged one-file
        # executables are replaced and relaunched externally to avoid
        # PyInstaller _MEI extraction races during self-update.
        if not FROZEN:
            self.root.after(1000, self._watch_for_changes)

    def px(self, value: int) -> int:
        return max(1, round(value * self.scale))

    def _set_icon(self) -> None:
        branded_icon = BUNDLE_ROOT / "assets" / "DART-QoE.png"
        if branded_icon.exists():
            try:
                icon = tk.PhotoImage(file=branded_icon)
                self._icon = icon
                self.root.iconphoto(True, icon)
                return
            except tk.TclError:
                pass
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(NAVY, to=(0, 0, 32, 32))
        icon.put(BLUE, to=(5, 5, 27, 27))
        icon.put(WHITE, to=(9, 9, 23, 13))
        icon.put(WHITE, to=(9, 16, 23, 20))
        icon.put(WHITE, to=(9, 23, 18, 26))
        self._icon = icon
        self.root.iconphoto(True, icon)

    def _watch_for_changes(self) -> None:
        if self.restarting:
            return
        current = source_snapshot()
        if current != self._source_snapshot:
            self._source_snapshot = current
            self._restart_development_app()
            return
        self.root.after(1000, self._watch_for_changes)

    def _watch_for_packaged_update(self) -> None:
        update = packaged_update_path()
        try:
            stat = update.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            self._update_signature = None
            self._update_stable_polls = 0
            return

        if signature == self._update_signature:
            self._update_stable_polls += 1
        else:
            self._update_signature = signature
            self._update_stable_polls = 0

        if self._update_stable_polls < 2 or stat.st_size < 1_000_000:
            return
        try:
            with update.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    return
        except OSError:
            return
        self._install_packaged_update(update)

    def _show_restart_status(self) -> None:
        self.restarting = True
        self.status_var.set("소스 수정 감지 · 새 버전으로 자동 재실행합니다")
        self.status_label.configure(fg=BLUE)
        self.progress.configure(mode="indeterminate")
        self.progress.grid()
        self.progress.start(12)
        self.run_button.configure(state="disabled")
        self.root.update_idletasks()

    def _restart_development_app(self) -> None:
        self._show_restart_status()
        command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]

        def relaunch() -> None:
            self.root.destroy()
            subprocess.Popen(
                command,
                cwd=ROOT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )

        self.root.after(250, relaunch)

    def _install_packaged_update(self, update: Path) -> None:
        self._show_restart_status()
        current = Path(sys.executable).resolve()
        try:
            subprocess.Popen(
                updater_command(current, update, os.getpid()),
                cwd=current.parent,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        except OSError as exc:
            self.restarting = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.progress.grid_remove()
            self.status_var.set(f"자동 업데이트를 시작하지 못했습니다 · {exc}")
            self.status_label.configure(fg="#B3261E")
            self.root.after(1000, self._watch_for_changes)
            return
        self.root.after(250, self.root.destroy)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass


        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(family="맑은 고딕", size=10)
            except tk.TclError:
                pass
        style.configure("TEntry", padding=self.px(8), fieldbackground=WHITE, bordercolor=LINE,
                        font=("맑은 고딕", 10))
        style.configure("TSpinbox", padding=self.px(8), fieldbackground=WHITE, bordercolor=LINE,
                        font=("맑은 고딕", 10))
        style.configure("TCheckbutton", background=WHITE, foreground=INK, font=("맑은 고딕", 10))
        style.map("TCheckbutton", background=[("active", WHITE)])
        style.configure("Horizontal.TProgressbar", troughcolor=PALE, background=BLUE, bordercolor=PALE,
                        thickness=self.px(6))

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=self.px(96))
        header.pack(fill="x")
        header.pack_propagate(False)

        header_content = tk.Frame(header, bg=NAVY)
        header_content.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            header_content,
            text="DART-QoE",
            bg=NAVY,
            fg=WHITE,
            font=("맑은 고딕", 23, "bold"),
        ).pack()
        tk.Label(
            header_content,
            text="정상화 이익 후보 · 운전자본 · 현금전환을 함께 보는 거래 검토 보조 도구",
            bg=NAVY,
            fg="#D9E7F3",
            font=("맑은 고딕", 10),
        ).pack(pady=(self.px(3), 0))
        viewport = tk.Frame(self.root, bg=BG)
        viewport.pack(fill="both", expand=True)
        self.body_canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        self.body_scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.body_canvas.yview)
        self.body_canvas.configure(yscrollcommand=self.body_scrollbar.set)
        self.body_canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.body_canvas, bg=BG, padx=self.px(22), pady=self.px(18))
        self.body_window = self.body_canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body_canvas.bind("<Configure>", self._resize_viewport, add="+")
        self.body.bind("<Configure>", self._update_scroll_region, add="+")

        self.form_card = self._card(self.body)
        self._build_form(self.form_card)

        self.result_card = self._card(self.body)
        self.result_card.grid_columnconfigure(0, weight=1)
        self.result_card.grid_rowconfigure(4, weight=1)
        self._build_result(self.result_card)

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._layout_cards()

    def _layout_cards(self, force: bool = False) -> None:
        mode = "stacked" if self.root.winfo_width() < self.px(1080) else "wide"
        if not force and mode == self._layout_mode:
            return
        self._layout_mode = mode
        self.form_card.grid_forget()
        self.result_card.grid_forget()
        for column in range(2):
            self.body.grid_columnconfigure(column, weight=0, minsize=0)
        for row in range(2):
            self.body.grid_rowconfigure(row, weight=0, minsize=0)
        if mode == "wide":
            self.body.grid_columnconfigure(1, weight=1)
            self.body.grid_rowconfigure(0, weight=1)
            self.form_card.configure(width=self.px(390))
            self.form_card.grid_propagate(False)
            self.form_card.grid(row=0, column=0, sticky="ns", padx=(0, self.px(18)))
            self.result_card.grid(row=0, column=1, sticky="nsew")
            self._layout_form_fields(compact=False)
        else:
            self.body.grid_columnconfigure(0, weight=1)
            self.body.grid_rowconfigure(1, weight=1)
            self.form_card.grid_propagate(True)
            self.form_card.grid(row=0, column=0, sticky="ew", pady=(0, self.px(14)))
            self.result_card.grid(row=1, column=0, sticky="nsew")
            self._layout_form_fields(compact=True)
        self.root.after_idle(self._resize_viewport)

    def _resize_viewport(self, _event: tk.Event | None = None) -> None:
        width = self.body_canvas.winfo_width()
        height = self.body_canvas.winfo_height()
        minimum_height = self.px(660 if self._layout_mode == "wide" else 900)
        self.body_canvas.itemconfigure(self.body_window, width=width, height=max(height, minimum_height))
        if height < minimum_height:
            self.body_scrollbar.pack(side="right", fill="y")
        else:
            self.body_scrollbar.pack_forget()
        self._update_scroll_region()

    def _update_scroll_region(self, _event: tk.Event | None = None) -> None:
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1,
                        padx=self.px(24), pady=self.px(21))

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=WHITE, fg=INK, font=("맑은 고딕", 10, "bold"), anchor="w").pack(
            fill="x", pady=(0, self.px(5))
        )

    def _text_entry(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> NativeWindowsEntry | tk.Entry:
        """Create a native Windows input control, with a Tk fallback elsewhere."""
        if sys.platform == "win32":
            entry = NativeWindowsEntry(parent, variable, self.scale, show=show)
            self.native_entries.append(entry)
            return entry

        options: dict[str, object] = {
            "textvariable": variable,
            "font": ("맑은 고딕", 10),
            "bg": WHITE,
            "fg": INK,
            "insertbackground": INK,
            "selectbackground": BLUE,
            "selectforeground": WHITE,
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 1,
            "highlightbackground": LINE,
            "highlightcolor": BLUE,
            "justify": "center",
        }
        if show is not None:
            options["show"] = show
        entry = tk.Entry(parent, **options)
        entry.pack(fill="x", ipady=self.px(8))
        return entry

    def _build_form(self, form: tk.Frame) -> None:
        form.grid_columnconfigure((0, 1), weight=1, uniform="form")
        self.form_title = tk.Label(
            form, text="분석 조건", bg=WHITE, fg=NAVY, font=("맑은 고딕", 18, "bold"), anchor="w"
        )
        self.form_subtitle = tk.Label(
            form,
            text="연결재무제표를 우선하고 사업보고서 주석까지 기본 분석합니다.",
            bg=WHITE,
            fg=MUTED,
            font=("맑은 고딕", 10),
            wraplength=self.px(680),
            justify="left",
            anchor="w",
        )

        saved_api_key = load_saved_api_key()
        self.api_var = tk.StringVar(value=saved_api_key)
        self.save_key_var = tk.BooleanVar(value=True)
        self.company_var = tk.StringVar(value="")
        self.begin_var = tk.StringVar(value="2021")
        self.end_var = tk.StringVar(value="2025")
        self.lease_var = tk.BooleanVar(value=True)

        self.api_block = tk.Frame(form, bg=WHITE)
        self._field_label(self.api_block, "전자공시 인증키")
        self.api_entry = self._text_entry(self.api_block, self.api_var, show="●")
        ttk.Checkbutton(
            self.api_block,
            text="이 PC에 암호화하여 저장",
            variable=self.save_key_var,
            command=self._storage_preference_changed,
        ).pack(anchor="w", pady=(self.px(7), 0))

        self.company_block = tk.Frame(form, bg=WHITE)
        self._field_label(self.company_block, "회사명 또는 종목코드")
        self.company_entry = self._text_entry(self.company_block, self.company_var)
        tk.Label(
            self.company_block, text="예: 삼성전자 또는 005930", bg=WHITE, fg=MUTED,
            font=("맑은 고딕", 9), anchor="w"
        ).pack(fill="x", pady=(self.px(5), 0))

        self.period_block = tk.Frame(form, bg=WHITE)
        self._field_label(self.period_block, "분석기간")
        years = tk.Frame(self.period_block, bg=WHITE)
        years.pack(fill="x")
        years.grid_columnconfigure((0, 2), weight=1)
        self.begin_spinbox = ttk.Spinbox(
            years, from_=2000, to=datetime.now().year, textvariable=self.begin_var, width=8, justify="center"
        )
        self.begin_spinbox.grid(row=0, column=0, sticky="ew")
        tk.Label(years, text="—", bg=WHITE, fg=MUTED).grid(row=0, column=1, padx=self.px(8))
        self.end_spinbox = ttk.Spinbox(
            years, from_=2000, to=datetime.now().year, textvariable=self.end_var, width=8, justify="center"
        )
        self.end_spinbox.grid(row=0, column=2, sticky="ew")
        tk.Label(
            self.period_block, text="필요한 사업연도를 자유롭게 설정할 수 있습니다.", bg=WHITE, fg=MUTED,
            font=("맑은 고딕", 9), anchor="w"
        ).pack(fill="x", pady=(self.px(5), 0))

        self.option_block = tk.Frame(form, bg=WHITE)
        self._field_label(self.option_block, "순차입금 기준")
        ttk.Checkbutton(self.option_block, text="리스부채 포함", variable=self.lease_var).pack(anchor="w")
        tk.Label(
            self.option_block, text="인증키는 저장을 선택한 경우에만 현재 Windows 계정으로 암호화합니다.",
            bg=AMBER, fg="#695A20", font=("맑은 고딕", 9), justify="left", anchor="w",
            wraplength=self.px(330), padx=self.px(10), pady=self.px(8),
        ).pack(fill="x", pady=(self.px(8), 0))

        self.action_block = tk.Frame(form, bg=WHITE)
        self.run_button = tk.Button(
            self.action_block,
            text="공시 분석 및 Excel 만들기",
            command=self.start_analysis,
            bg=BLUE,
            fg=WHITE,
            activebackground=NAVY,
            activeforeground=WHITE,
            relief="flat",
            cursor="hand2",
            font=("맑은 고딕", 11, "bold"),
            pady=self.px(11),
            takefocus=True,
        )
        self.run_button.pack(fill="x")

        self.form_error_var = tk.StringVar(value="")
        self.form_error = tk.Label(
            form, textvariable=self.form_error_var, bg="#FDECEA", fg="#B3261E",
            font=("맑은 고딕", 9, "bold"), justify="left", anchor="w", padx=self.px(10), pady=self.px(7),
            wraplength=self.px(680),
        )

        self.signature = tk.Frame(form, bg=WHITE)
        signature_content = tk.Frame(self.signature, bg=WHITE)
        signature_content.pack(anchor="center")

        branded_icon = BUNDLE_ROOT / "assets" / "DART-QoE.png"
        if branded_icon.exists():
            try:
                icon = tk.PhotoImage(file=branded_icon)
                target = self.px(32)
                divisor = max(1, min(icon.width(), icon.height()) // target)
                self._footer_icon = icon.subsample(divisor, divisor)
                tk.Label(signature_content, image=self._footer_icon, bg=WHITE).pack(side="left", padx=(0, self.px(9)))
            except tk.TclError:
                pass

        signature_text = tk.Frame(signature_content, bg=WHITE)
        signature_text.pack(side="left")
        tk.Label(signature_text, text="BY JOONSEOK WON", bg=WHITE, fg=NAVY,
                 font=("맑은 고딕", 9, "bold"), anchor="w").pack(fill="x")
        tk.Label(signature_text, text="DISCLOSURE-BASED QoE REVIEW TOOL", bg=WHITE, fg="#8193A6",
                 font=("맑은 고딕", 7), anchor="w").pack(fill="x", pady=(self.px(1), 0))
        self._layout_form_fields(compact=False)

    def _layout_form_fields(self, compact: bool) -> None:
        if not hasattr(self, "api_block"):
            return
        for widget in (
            self.form_title, self.form_subtitle, self.api_block, self.company_block,
            self.period_block, self.option_block, self.action_block, self.form_error, self.signature,
        ):
            widget.grid_forget()
        self.form_title.grid(row=0, column=0, columnspan=2, sticky="w")
        self.form_subtitle.configure(wraplength=self.px(680 if compact else 330))
        self.form_subtitle.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(self.px(4), self.px(16)))
        if compact:
            self.api_block.grid(row=2, column=0, sticky="nsew", padx=(0, self.px(10)))
            self.company_block.grid(row=2, column=1, sticky="nsew", padx=(self.px(10), 0))
            self.period_block.grid(row=3, column=0, sticky="nsew", padx=(0, self.px(10)), pady=(self.px(14), 0))
            self.option_block.grid(row=3, column=1, sticky="nsew", padx=(self.px(10), 0), pady=(self.px(14), 0))
            action_row = 4
        else:
            self.api_block.grid(row=2, column=0, columnspan=2, sticky="ew")
            self.company_block.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(self.px(15), 0))
            self.period_block.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(self.px(15), 0))
            self.option_block.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(self.px(15), 0))
            action_row = 6
        self.action_block.grid(row=action_row, column=0, columnspan=2, sticky="ew", pady=(self.px(18), 0))
        if self.form_error_var.get():
            self.form_error.grid(row=action_row + 1, column=0, columnspan=2, sticky="ew", pady=(self.px(8), 0))
        self.signature.grid(row=action_row + 2, column=0, columnspan=2, sticky="ew", pady=(self.px(18), 0))

    def _build_result(self, result: tk.Frame) -> None:
        tk.Label(result, text="검토 결과", bg=WHITE, fg=NAVY, font=("맑은 고딕", 18, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        self.status_var = tk.StringVar(value="준비됨 · 분석 조건을 입력하세요")
        self.status_label = tk.Label(
            result, textvariable=self.status_var, bg=WHITE, fg=GREEN, font=("맑은 고딕", 10, "bold")
        )
        self.status_label.configure(anchor="w", justify="left", wraplength=self.px(850))
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(self.px(5), self.px(11)))
        self.progress = ttk.Progressbar(result, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, self.px(17)))
        self.progress.grid_remove()

        checks = tk.Frame(result, bg=PALE, padx=self.px(12), pady=self.px(9))
        checks.grid(row=3, column=0, sticky="ew", pady=(0, self.px(15)))
        for column in range(3):
            checks.grid_columnconfigure(column, weight=1, uniform="checks")
        for index, text in enumerate(("보고이익", "현금전환", "운전자본", "순차입금", "정상화 조정 후보")):
            tk.Label(checks, text=text, bg=PALE, fg=NAVY, font=("맑은 고딕", 9, "bold"),
                     anchor="center").grid(row=index // 3, column=index % 3, sticky="ew", padx=self.px(4), pady=self.px(3))

        summary_frame = tk.Frame(result, bg=WHITE)
        summary_frame.grid(row=4, column=0, sticky="nsew")
        summary_frame.grid_rowconfigure(0, weight=1)
        summary_frame.grid_columnconfigure(0, weight=1)
        self.summary = tk.Text(
            summary_frame,
            bg="#FAFCFE",
            fg=INK,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
            padx=self.px(18),
            pady=self.px(16),
            font=("맑은 고딕", 11),
            wrap="word",
            cursor="arrow",
        )
        self.summary.grid(row=0, column=0, sticky="nsew")
        summary_scrollbar = ttk.Scrollbar(summary_frame, orient="vertical", command=self.summary.yview)
        summary_scrollbar.grid(row=0, column=1, sticky="ns")
        self.summary.configure(yscrollcommand=summary_scrollbar.set)
        self._set_summary(
            "아직 분석을 실행하지 않았습니다.\n\n"
            "[진행 순서]\n"
            "• 1. 인증키와 회사를 입력합니다.\n"
            "• 2. 분석기간과 리스부채 포함 여부를 확인합니다.\n"
            "• 3. 공시 분석을 실행하고 화면 요약과 Excel을 검토합니다.\n\n"
            "[생성 결과]\n"
            "• 원천 자료, QoE 요약, 정상화 손익, 운전자본, 순차입금, 검토 후보와 검증 시트"
        )

        actions = tk.Frame(result, bg=WHITE)
        actions.grid(row=5, column=0, sticky="ew", pady=(self.px(15), 0))
        action_group = tk.Frame(actions, bg=WHITE)
        action_group.pack(anchor="center")
        self.open_button = tk.Button(
            action_group,
            text="Excel 검토 파일 열기",
            command=self.open_excel,
            state="disabled",
            bg=GREEN,
            fg=WHITE,
            disabledforeground="#AAB4BE",
            relief="flat",
            font=("맑은 고딕", 9, "bold"),
            padx=self.px(18),
            pady=self.px(9),
        )
        self.open_button.pack(side="left")
        self.folder_button = tk.Button(
            action_group,
            text="저장 폴더 열기",
            command=self.open_folder,
            bg=PALE,
            fg=NAVY,
            relief="flat",
            font=("맑은 고딕", 9, "bold"),
            padx=self.px(18),
            pady=self.px(9),
        )
        self.folder_button.pack(side="left", padx=self.px(8))

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.tag_add("body", "1.0", "end")
        self.summary.tag_configure("body", spacing1=self.px(2), spacing3=self.px(5))
        for line_number, line in enumerate(text.splitlines(), start=1):
            start = f"{line_number}.0"
            end = f"{line_number}.end"
            if line_number == 1:
                self.summary.tag_add("summary_title", start, end)
            elif line.startswith("[") and line.endswith("]"):
                self.summary.tag_add("summary_heading", start, end)
            elif line.startswith("•"):
                self.summary.tag_add("summary_bullet", start, end)
        self.summary.tag_configure(
            "summary_title", font=("맑은 고딕", 14, "bold"), foreground=NAVY,
            spacing3=self.px(8), justify="center"
        )
        self.summary.tag_configure(
            "summary_heading", font=("맑은 고딕", 10, "bold"), foreground=BLUE,
            spacing1=self.px(7), justify="center"
        )
        self.summary.tag_configure("summary_bullet", lmargin1=self.px(4), lmargin2=self.px(16), spacing3=self.px(2))
        self.summary.configure(state="disabled")

    def start_analysis(self) -> None:
        if self.busy:
            return
        for entry in self.native_entries:
            entry.sync_from_control()
        self.pending_api_key = ""
        self.pending_save_key = False
        self._show_form_error("")
        try:
            api_key = self.api_var.get().strip()
            company = self.company_var.get().strip()
            if not api_key:
                self._focus_field("api")
                raise ValueError("전자공시 인증키를 입력하세요.")
            if not company:
                self._focus_field("company")
                raise ValueError("회사명 또는 종목코드를 입력하세요.")
            try:
                if not all(re.fullmatch(r"[0-9]{4}", value.strip()) for value in (self.begin_var.get(), self.end_var.get())):
                    raise ValueError
                begin_year = int(self.begin_var.get())
                end_year = int(self.end_var.get())
            except ValueError:
                self._focus_field("begin")
                raise ValueError("분석기간은 연도 네 자리 숫자로 입력하세요.") from None
            if begin_year > end_year:
                self._focus_field("begin")
                raise ValueError("분석 시작연도는 종료연도보다 늦을 수 없습니다.")
            request = (api_key, company, begin_year, end_year, self.lease_var.get())
            self.pending_api_key = api_key
            self.pending_save_key = self.save_key_var.get()
        except ValueError as exc:
            self._show_form_error(str(exc))
            return

        if self.pending_api_key:
            try:
                if self.pending_save_key:
                    save_api_key(self.pending_api_key)
                else:
                    delete_saved_api_key()
            except OSError as exc:
                messagebox.showwarning("인증키 저장", f"인증키를 저장하지 못했습니다.\n{exc}", parent=self.root)

        self.busy = True
        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.status_var.set("분석을 준비하고 있습니다 · 2%")
        self.status_label.configure(fg=BLUE)
        self._set_summary("DART 재무제표와 사업보고서 주석·리스부채 정보를 수집하고 계산 근거를 구성하고 있습니다.\n창을 닫지 말고 잠시 기다려 주세요.")
        self.progress.configure(value=2)
        self.progress.grid()
        threading.Thread(target=self._worker, args=(request,), daemon=True).start()

    def _queue_progress(self, percent: int, message: str) -> None:
        self.root.after(0, self._apply_progress, percent, message)

    def _apply_progress(self, percent: int, message: str) -> None:
        if not self.busy:
            return
        current = int(float(self.progress.cget("value")))
        value = max(current, min(100, int(percent)))
        self.progress.configure(value=value)
        self.status_var.set(f"{message} · {value}%")

    def _worker(self, request: tuple) -> None:
        try:
            data = analyze_dart(*request, progress_callback=self._queue_progress)
            stem = data["metadata"]["company_name"]
            output = export_excel(data, stem, progress_callback=self._queue_progress)
            self.root.after(0, self._finish_success, data, output)
        except Exception as exc:
            details = traceback.format_exc()
            try:
                (ROOT / "DART-QoE-error.log").write_text(details, encoding="utf-8")
            except OSError:
                pass
            self.root.after(0, self._finish_error, str(exc))

    def _finish_success(self, data: dict, output: Path) -> None:
        self._set_idle(keep_progress=True)
        self.last_output = output
        self.status_var.set("완료 · 엑셀 검토 파일이 생성되었습니다 · 100%")
        self.status_label.configure(fg=GREEN)
        self._set_summary(build_result_summary(data))
        self.open_button.configure(state="normal", cursor="hand2")

    def _finish_error(self, message: str) -> None:
        self._set_idle()
        self.status_var.set("오류 · 입력 또는 공시 추출 결과를 확인하세요")
        self.status_label.configure(fg="#B3261E")
        self._set_summary(
            "분석을 완료하지 못했습니다.\n\n"
            "[확인할 항목]\n"
            "• 전자공시 인증키가 유효한지 확인하세요.\n"
            "• 회사명 또는 종목코드와 분석기간을 다시 확인하세요.\n"
            f"• 오류 내용: {message}\n\n"
            "입력값은 유지됩니다. 수정한 뒤 다시 실행하세요. 상세 기록은 DART-QoE-error.log에 저장됩니다."
        )

    def _set_idle(self, keep_progress: bool = False) -> None:
        self.busy = False
        if keep_progress:
            self.progress.configure(value=100)
            self.progress.grid()
        else:
            self.progress.configure(value=0)
            self.progress.grid_remove()
        self.run_button.configure(state="normal")

    def open_excel(self) -> None:
        if self.last_output and self.last_output.exists():
            os.startfile(self.last_output)
        else:
            messagebox.showinfo("파일 확인", "생성된 엑셀 파일을 찾지 못했습니다.", parent=self.root)

    def open_folder(self) -> None:
        OUTPUTS.mkdir(exist_ok=True)
        os.startfile(OUTPUTS)

    def _storage_preference_changed(self) -> None:
        if not self.save_key_var.get():
            delete_saved_api_key()

    def _show_form_error(self, message: str) -> None:
        self.form_error_var.set(message)
        self._layout_form_fields(compact=self._layout_mode == "stacked")

    def _focus_field(self, field: str) -> None:
        target = {
            "api": self.api_entry,
            "company": self.company_entry,
            "begin": self.begin_spinbox,
            "end": self.end_spinbox,
        }.get(field)
        if target is not None:
            self.root.after_idle(target.focus_set)

    def _run_from_keyboard(self, _event: tk.Event) -> str:
        if not self.busy:
            self.start_analysis()
        return "break"


def acquire_single_instance() -> bool:
    """Allow only one interactive DART-QoE instance per Windows session."""
    global _single_instance_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if not handle:
        return True
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _single_instance_handle = int(handle)
    return True


def smoke_test() -> int:
    output = export_excel(demo_analysis(), "desktop_smoke")
    (ROOT / "DART-QoE-smoke.txt").write_text(str(output), encoding="utf-8")
    return 0


def main() -> int:
    if "--smoke-test" in sys.argv:
        return smoke_test()
    if not acquire_single_instance():
        return 0
    enable_dpi_awareness()
    root = tk.Tk()
    DartQoeApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        details = traceback.format_exc()
        try:
            (ROOT / "DART-QoE-error.log").write_text(details, encoding="utf-8")
        finally:
            try:
                messagebox.showerror("DART-QoE 실행 오류", details)
            except Exception:
                pass
        raise
