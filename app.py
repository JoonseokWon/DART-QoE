from __future__ import annotations

import os
import base64
import subprocess
import sys
import threading
import traceback
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
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
    Start-Process -FilePath $current -WorkingDirectory $working
}}
""".strip()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [
        str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
    ]


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
        self.container = tk.Frame(parent, bg=WHITE, height=max(34, round(36 * scale)))
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

        styles = self.WS_CHILD | self.WS_VISIBLE | self.WS_TABSTOP | self.WS_BORDER | self.ES_AUTOHSCROLL
        self.hwnd = user32.CreateWindowExW(
            0,
            "EDIT",
            variable.get(),
            styles,
            0,
            0,
            max(1, self.container.winfo_width()),
            max(1, self.container.winfo_height()),
            self.container.winfo_id(),
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError()

        font_height = -max(16, round(16 * scale))
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

        self.container.bind("<Configure>", self._resize, add="+")
        self.container.bind("<Destroy>", self._destroy, add="+")
        self.container.after(80, self._poll)

    def _resize(self, _event: tk.Event | None = None) -> None:
        if self._destroyed or not ctypes.windll.user32.IsWindow(self.hwnd):
            return
        ctypes.windll.user32.MoveWindow(
            self.hwnd,
            0,
            0,
            max(1, self.container.winfo_width()),
            max(1, self.container.winfo_height()),
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

    def _poll(self) -> None:
        if self._destroyed:
            return
        self.sync_from_control()
        self.container.after(80, self._poll)

    def _destroy(self, event: tk.Event) -> None:
        if event.widget is not self.container or self._destroyed:
            return
        self._destroyed = True
        if ctypes.windll.user32.IsWindow(self.hwnd):
            ctypes.windll.user32.DestroyWindow(self.hwnd)
        if self.font_handle:
            ctypes.windll.gdi32.DeleteObject(self.font_handle)


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
        root.minsize(min(self.px(900), int(screen_width * 0.90)),
                     min(self.px(650), int(screen_height * 0.86)))
        root.configure(bg=BG)
        root.option_add("*Font", ("맑은 고딕", 10))
        self._set_icon()
        self._configure_styles()
        self._build_ui()
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
        if FROZEN:
            self._watch_for_packaged_update()
        else:
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
        self.demo_button.configure(state="disabled")
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
        header = tk.Frame(self.root, bg=NAVY, height=self.px(148))
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="DART-QoE", bg=NAVY, fg=WHITE, font=("맑은 고딕", 28, "bold")).pack(
            anchor="w", padx=self.px(38), pady=(self.px(20), self.px(2))
        )
        tk.Label(
            header,
            text="정상화 이익 후보 · 운전자본 · 현금전환을 함께 보는 거래 검토 보조 도구",
            bg=NAVY,
            fg="#D9E7F3",
            font=("맑은 고딕", 11),
        ).pack(anchor="w", padx=self.px(40))
        tk.Label(
            header,
            text="자동 결론이 아니라 원문 확인 항목과 계산 근거를 제시합니다.",
            bg=NAVY,
            fg="#AFC5D8",
            font=("맑은 고딕", 10),
        ).pack(anchor="w", padx=self.px(40), pady=(self.px(3), 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=self.px(28), pady=self.px(22))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        form = self._card(body)
        form.grid(row=0, column=0, sticky="ns", padx=(0, self.px(18)))
        form.configure(width=self.px(390))
        form.grid_propagate(False)
        self._build_form(form)

        result = self._card(body)
        result.grid(row=0, column=1, sticky="nsew")
        result.grid_columnconfigure(0, weight=1)
        result.grid_rowconfigure(4, weight=1)
        self._build_result(result)

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1,
                        padx=self.px(24), pady=self.px(21))

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=WHITE, fg=INK, font=("맑은 고딕", 10, "bold")).pack(
            anchor="w", pady=(self.px(12), self.px(5))
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
        }
        if show is not None:
            options["show"] = show
        entry = tk.Entry(parent, **options)
        entry.pack(fill="x", ipady=self.px(8))
        return entry

    def _build_form(self, form: tk.Frame) -> None:
        tk.Label(form, text="분석 조건", bg=WHITE, fg=NAVY, font=("맑은 고딕", 18, "bold")).pack(anchor="w")
        tk.Label(
            form,
            text="연결재무제표를 우선하여 최근 사업연도를 분석합니다.",
            bg=WHITE,
            fg=MUTED,
            font=("맑은 고딕", 10),
            wraplength=self.px(330),
            justify="left",
        ).pack(anchor="w", pady=(self.px(4), self.px(7)))

        saved_api_key = load_saved_api_key()
        self.api_var = tk.StringVar(value=saved_api_key)
        self.save_key_var = tk.BooleanVar(value=True)
        self.company_var = tk.StringVar(value="")
        self.begin_var = tk.StringVar(value="2021")
        self.end_var = tk.StringVar(value="2025")
        self.lease_var = tk.BooleanVar(value=True)
        self.notes_var = tk.BooleanVar(value=True)

        self._field_label(form, "전자공시 인증키")
        self._text_entry(form, self.api_var, show="●")
        ttk.Checkbutton(
            form,
            text="이 PC에 암호화하여 저장",
            variable=self.save_key_var,
            command=self._storage_preference_changed,
        ).pack(anchor="w", pady=(self.px(7), 0))
        self._field_label(form, "회사명 또는 종목코드")
        self._text_entry(form, self.company_var)
        self._field_label(form, "분석기간")
        years = tk.Frame(form, bg=WHITE)
        years.pack(fill="x")
        years.grid_columnconfigure((0, 2), weight=1)
        ttk.Spinbox(years, from_=2015, to=2030, textvariable=self.begin_var, width=8).grid(row=0, column=0, sticky="ew")
        tk.Label(years, text="—", bg=WHITE, fg=MUTED).grid(row=0, column=1, padx=self.px(8))
        ttk.Spinbox(years, from_=2015, to=2030, textvariable=self.end_var, width=8).grid(row=0, column=2, sticky="ew")

        ttk.Checkbutton(form, text="순차입금에 리스부채 포함", variable=self.lease_var).pack(
            anchor="w", pady=(self.px(16), self.px(4)))
        ttk.Checkbutton(form, text="사업보고서 원문에서 후보 탐색", variable=self.notes_var).pack(
            anchor="w", pady=self.px(4))

        self.run_button = tk.Button(
            form,
            text="분석 및 엑셀 생성",
            command=lambda: self.start_analysis(False),
            bg=BLUE,
            fg=WHITE,
            activebackground=NAVY,
            activeforeground=WHITE,
            relief="flat",
            cursor="hand2",
            font=("맑은 고딕", 11, "bold"),
            pady=self.px(11),
        )
        self.run_button.pack(fill="x", pady=(self.px(21), self.px(8)))
        self.demo_button = tk.Button(
            form,
            text="인증키 없이 데모 생성",
            command=lambda: self.start_analysis(True),
            bg=PALE,
            fg=NAVY,
            activebackground="#DDE8F3",
            relief="flat",
            cursor="hand2",
            font=("맑은 고딕", 10, "bold"),
            pady=self.px(9),
        )
        self.demo_button.pack(fill="x")

        notice = tk.Frame(form, bg=AMBER, padx=self.px(12), pady=self.px(10))
        notice.pack(fill="x", side="bottom")
        notice.grid_columnconfigure(1, weight=1)
        for row, (label, value) in enumerate((
            ("인증키", "윈도우 계정으로 암호화 저장"),
            ("데모 데이터", "기능 설명용 샘플"),
        )):
            tk.Label(notice, text=label, bg=AMBER, fg="#695A20", font=("맑은 고딕", 9, "bold")).grid(
                row=row, column=0, sticky="nw", padx=(0, self.px(12)), pady=self.px(2))
            tk.Label(notice, text=value, bg=AMBER, fg="#695A20", font=("맑은 고딕", 9)).grid(
                row=row, column=1, sticky="nw", pady=self.px(2))

    def _build_result(self, result: tk.Frame) -> None:
        tk.Label(result, text="검토 결과", bg=WHITE, fg=NAVY, font=("맑은 고딕", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.status_var = tk.StringVar(value="준비됨")
        self.status_label = tk.Label(
            result, textvariable=self.status_var, bg=WHITE, fg=GREEN, font=("맑은 고딕", 10, "bold")
        )
        self.status_label.configure(anchor="w", justify="left", wraplength=self.px(650))
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
                     anchor="w").grid(row=index // 3, column=index % 3, sticky="ew", padx=self.px(4), pady=self.px(3))

        self.summary = tk.Text(
            result,
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
        self.summary.grid(row=4, column=0, sticky="nsew")
        self._set_summary(
            "분석을 실행하면 이곳에 결과 요약이 표시됩니다.\n\n"
            "엑셀에는 원천 자료, QoE 요약, 운전자본, 검토 후보, 검토 흔적, "
            "검증 시트가 생성됩니다."
        )

        actions = tk.Frame(result, bg=WHITE)
        actions.grid(row=5, column=0, sticky="ew", pady=(self.px(15), 0))
        self.open_button = tk.Button(
            actions,
            text="엑셀 열기",
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
            actions,
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
        self.summary.configure(state="disabled")

    def start_analysis(self, demo: bool) -> None:
        if self.busy:
            return
        for entry in self.native_entries:
            entry.sync_from_control()
        self.pending_api_key = ""
        self.pending_save_key = False
        try:
            if demo:
                request = None
            else:
                api_key = self.api_var.get().strip()
                company = self.company_var.get().strip()
                begin_year = int(self.begin_var.get())
                end_year = int(self.end_var.get())
                if not api_key or not company:
                    raise ValueError("전자공시 인증키와 회사명을 입력하세요.")
                if begin_year > end_year or end_year - begin_year > 4:
                    raise ValueError("분석기간은 순서대로 최대 5개년까지 입력하세요.")
                request = (api_key, company, begin_year, end_year, self.lease_var.get(), self.notes_var.get())
                self.pending_api_key = api_key
                self.pending_save_key = self.save_key_var.get()
        except ValueError as exc:
            messagebox.showwarning("입력 확인", str(exc), parent=self.root)
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
        self.demo_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.status_var.set("분석을 준비하고 있습니다 · 2%")
        self.status_label.configure(fg=BLUE)
        self._set_summary("DART 공시와 원문 주석을 수집하고 계산 근거를 구성하고 있습니다.\n창을 닫지 말고 잠시 기다려 주세요.")
        self.progress.configure(value=2)
        self.progress.grid()
        threading.Thread(target=self._worker, args=(demo, request), daemon=True).start()

    def _queue_progress(self, percent: int, message: str) -> None:
        self.root.after(0, self._apply_progress, percent, message)

    def _apply_progress(self, percent: int, message: str) -> None:
        if not self.busy:
            return
        current = int(float(self.progress.cget("value")))
        value = max(current, min(100, int(percent)))
        self.progress.configure(value=value)
        self.status_var.set(f"{message} · {value}%")

    def _worker(self, demo: bool, request: tuple | None) -> None:
        try:
            if demo:
                self._queue_progress(20, "데모 자료를 준비하고 있습니다")
                data = demo_analysis()
                stem = "demo"
                self._queue_progress(91, "데모 분석 계산을 완료했습니다")
            else:
                assert request is not None
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
        metadata = data.get("metadata", {})
        years = data.get("years", [])
        candidates = data.get("candidates", [])
        errors = data.get("errors", [])
        self.status_var.set("완료 · 엑셀 검토 파일이 생성되었습니다 · 100%")
        self.status_label.configure(fg=GREEN)
        self._set_summary(
            f"회사\n{metadata.get('company_name', '-')}\n\n"
            f"분석기간\n{', '.join(map(str, years)) or '-'}\n\n"
            f"재무제표 기준\n{metadata.get('basis', '-')}\n\n"
            f"정상화 조정 검토 후보\n{len(candidates)}건\n\n"
            f"추출 오류 또는 제한사항\n{len(errors)}건\n\n"
            f"저장 위치\n{output}"
        )
        self.open_button.configure(state="normal", cursor="hand2")

    def _finish_error(self, message: str) -> None:
        self._set_idle()
        self.status_var.set("오류 · 입력 또는 공시 추출 결과를 확인하세요")
        self.status_label.configure(fg="#B3261E")
        self._set_summary(f"분석을 완료하지 못했습니다.\n\n{message}\n\n상세 내용은 DART-QoE-error.log에 기록됩니다.")
        messagebox.showerror("DART-QoE 오류", message, parent=self.root)

    def _set_idle(self, keep_progress: bool = False) -> None:
        self.busy = False
        if keep_progress:
            self.progress.configure(value=100)
            self.progress.grid()
        else:
            self.progress.configure(value=0)
            self.progress.grid_remove()
        self.run_button.configure(state="normal")
        self.demo_button.configure(state="normal")

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


def smoke_test() -> int:
    output = export_excel(demo_analysis(), "desktop_smoke")
    (ROOT / "DART-QoE-smoke.txt").write_text(str(output), encoding="utf-8")
    return 0


def main() -> int:
    if "--smoke-test" in sys.argv:
        return smoke_test()
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
