from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
import ctypes
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from qoe import analyze_dart, demo_analysis, save_json


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
CONFIG_DIR = Path(os.environ.get("APPDATA", ROOT)) / "DART-QoE"
API_KEY_FILE = CONFIG_DIR / "api-key.dat"
NODE_DEFAULT = Path(r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")

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


def export_excel(data: dict, stem: str) -> Path:
    OUTPUTS.mkdir(exist_ok=True)
    safe = "".join(ch for ch in stem if ch.isalnum() or ch in "-_가-힣") or "qoe"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUTS / f".{safe}_{stamp}.json"
    out = OUTPUTS / f"DART-QoE_{safe}_{stamp}.xlsx"
    save_json(data, json_path)
    bundled_node = BUNDLE_ROOT / "node" / "node.exe"
    node = Path(os.environ.get("DART_QOE_NODE", bundled_node if FROZEN else NODE_DEFAULT))
    if not node.exists():
        raise RuntimeError("Excel 생성 모듈을 찾지 못했습니다. 실행파일을 다시 내려받아 주세요.")
    env = os.environ.copy()
    default_node_path = BUNDLE_ROOT / "node_modules" if FROZEN else Path(
        r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
    )
    env.setdefault("NODE_PATH", str(default_node_path))
    try:
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
    return out


class DartQoeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.last_output: Path | None = None
        self.busy = False
        self.pending_api_key = ""
        self.pending_save_key = False

        root.title("DART-QoE | 정상화 이익과 운전자본 검토")
        root.geometry("1080x760")
        root.minsize(940, 680)
        root.configure(bg=BG)
        self._set_icon()
        self._configure_styles()
        self._build_ui()

    def _set_icon(self) -> None:
        icon = tk.PhotoImage(width=32, height=32)
        icon.put(NAVY, to=(0, 0, 32, 32))
        icon.put(BLUE, to=(5, 5, 27, 27))
        icon.put(WHITE, to=(9, 9, 23, 13))
        icon.put(WHITE, to=(9, 16, 23, 20))
        icon.put(WHITE, to=(9, 23, 18, 26))
        self._icon = icon
        self.root.iconphoto(True, icon)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TEntry", padding=8, fieldbackground=WHITE, bordercolor=LINE)
        style.configure("TSpinbox", padding=8, fieldbackground=WHITE, bordercolor=LINE)
        style.configure("TCheckbutton", background=WHITE, foreground=INK, font=("맑은 고딕", 9))
        style.map("TCheckbutton", background=[("active", WHITE)])
        style.configure("Horizontal.TProgressbar", troughcolor=PALE, background=BLUE, bordercolor=PALE)

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=126)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="DART-QoE", bg=NAVY, fg=WHITE, font=("맑은 고딕", 25, "bold")).pack(
            anchor="w", padx=38, pady=(22, 1)
        )
        tk.Label(
            header,
            text="정상화 이익 후보 · 운전자본 · 현금전환을 함께 보는 거래 검토 보조 도구",
            bg=NAVY,
            fg="#D9E7F3",
            font=("맑은 고딕", 10),
        ).pack(anchor="w", padx=40)
        tk.Label(
            header,
            text="자동 결론이 아니라 원문 확인 항목과 계산 근거를 제시합니다.",
            bg=NAVY,
            fg="#AFC5D8",
            font=("맑은 고딕", 9),
        ).pack(anchor="w", padx=40, pady=(3, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=22)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        form = self._card(body)
        form.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        form.configure(width=350)
        form.grid_propagate(False)
        self._build_form(form)

        result = self._card(body)
        result.grid(row=0, column=1, sticky="nsew")
        result.grid_columnconfigure(0, weight=1)
        result.grid_rowconfigure(4, weight=1)
        self._build_result(result)

    @staticmethod
    def _card(parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1, padx=24, pady=21)

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=WHITE, fg=INK, font=("맑은 고딕", 9, "bold")).pack(
            anchor="w", pady=(12, 5)
        )

    def _build_form(self, form: tk.Frame) -> None:
        tk.Label(form, text="분석 조건", bg=WHITE, fg=NAVY, font=("맑은 고딕", 16, "bold")).pack(anchor="w")
        tk.Label(
            form,
            text="연결재무제표를 우선하여 최근 사업연도를 분석합니다.",
            bg=WHITE,
            fg=MUTED,
            font=("맑은 고딕", 9),
            wraplength=295,
            justify="left",
        ).pack(anchor="w", pady=(4, 7))

        saved_api_key = load_saved_api_key()
        self.api_var = tk.StringVar(value=saved_api_key)
        self.save_key_var = tk.BooleanVar(value=True)
        self.company_var = tk.StringVar(value="휴메딕스")
        self.begin_var = tk.StringVar(value="2022")
        self.end_var = tk.StringVar(value="2024")
        self.lease_var = tk.BooleanVar(value=True)
        self.notes_var = tk.BooleanVar(value=True)

        self._field_label(form, "OpenDART API 키")
        ttk.Entry(form, textvariable=self.api_var, show="●").pack(fill="x")
        ttk.Checkbutton(
            form,
            text="이 PC에 암호화하여 저장",
            variable=self.save_key_var,
            command=self._storage_preference_changed,
        ).pack(anchor="w", pady=(7, 0))
        self._field_label(form, "회사명 또는 종목코드")
        ttk.Entry(form, textvariable=self.company_var).pack(fill="x")
        self._field_label(form, "분석기간")
        years = tk.Frame(form, bg=WHITE)
        years.pack(fill="x")
        years.grid_columnconfigure((0, 2), weight=1)
        ttk.Spinbox(years, from_=2015, to=2030, textvariable=self.begin_var, width=8).grid(row=0, column=0, sticky="ew")
        tk.Label(years, text="—", bg=WHITE, fg=MUTED).grid(row=0, column=1, padx=8)
        ttk.Spinbox(years, from_=2015, to=2030, textvariable=self.end_var, width=8).grid(row=0, column=2, sticky="ew")

        ttk.Checkbutton(form, text="순차입금에 리스부채 포함", variable=self.lease_var).pack(anchor="w", pady=(16, 4))
        ttk.Checkbutton(form, text="사업보고서 원문에서 후보 탐색", variable=self.notes_var).pack(anchor="w", pady=4)

        self.run_button = tk.Button(
            form,
            text="분석 및 Excel 생성",
            command=lambda: self.start_analysis(False),
            bg=BLUE,
            fg=WHITE,
            activebackground=NAVY,
            activeforeground=WHITE,
            relief="flat",
            cursor="hand2",
            font=("맑은 고딕", 10, "bold"),
            pady=11,
        )
        self.run_button.pack(fill="x", pady=(21, 8))
        self.demo_button = tk.Button(
            form,
            text="API 키 없이 데모 생성",
            command=lambda: self.start_analysis(True),
            bg=PALE,
            fg=NAVY,
            activebackground="#DDE8F3",
            relief="flat",
            cursor="hand2",
            font=("맑은 고딕", 9, "bold"),
            pady=9,
        )
        self.demo_button.pack(fill="x")

        notice = tk.Frame(form, bg=AMBER, padx=12, pady=10)
        notice.pack(fill="x", side="bottom")
        tk.Label(
            notice,
            text="API 키는 Windows 계정으로 암호화됩니다.\n데모 숫자는 기능 설명용입니다.",
            bg=AMBER,
            fg="#695A20",
            font=("맑은 고딕", 8),
            justify="left",
        ).pack(anchor="w")

    def _build_result(self, result: tk.Frame) -> None:
        tk.Label(result, text="검토 결과", bg=WHITE, fg=NAVY, font=("맑은 고딕", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.status_var = tk.StringVar(value="준비됨")
        self.status_label = tk.Label(
            result, textvariable=self.status_var, bg=WHITE, fg=GREEN, font=("맑은 고딕", 9, "bold")
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(5, 11))
        self.progress = ttk.Progressbar(result, mode="indeterminate")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 17))

        checks = tk.Frame(result, bg=PALE, padx=16, pady=13)
        checks.grid(row=3, column=0, sticky="ew", pady=(0, 15))
        tk.Label(
            checks,
            text="보고이익  ·  현금전환  ·  운전자본  ·  순차입금  ·  정상화 조정 후보",
            bg=PALE,
            fg=NAVY,
            font=("맑은 고딕", 9, "bold"),
        ).pack(anchor="w")

        self.summary = tk.Text(
            result,
            bg="#FAFCFE",
            fg=INK,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
            padx=18,
            pady=16,
            font=("맑은 고딕", 10),
            wrap="word",
            cursor="arrow",
        )
        self.summary.grid(row=4, column=0, sticky="nsew")
        self._set_summary(
            "분석을 실행하면 이곳에 결과 요약이 표시됩니다.\n\n"
            "Excel에는 Source Data, QoE Summary, Working Capital, Review Candidates, "
            "Audit Trail, Checks 시트가 생성됩니다."
        )

        actions = tk.Frame(result, bg=WHITE)
        actions.grid(row=5, column=0, sticky="ew", pady=(15, 0))
        self.open_button = tk.Button(
            actions,
            text="Excel 열기",
            command=self.open_excel,
            state="disabled",
            bg=GREEN,
            fg=WHITE,
            disabledforeground="#AAB4BE",
            relief="flat",
            font=("맑은 고딕", 9, "bold"),
            padx=18,
            pady=9,
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
            padx=18,
            pady=9,
        )
        self.folder_button.pack(side="left", padx=8)

    def _set_summary(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def start_analysis(self, demo: bool) -> None:
        if self.busy:
            return
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
                    raise ValueError("OpenDART API 키와 회사명을 입력하세요.")
                if begin_year > end_year or end_year - begin_year > 4:
                    raise ValueError("분석기간은 순서대로 최대 5개년까지 입력하세요.")
                request = (api_key, company, begin_year, end_year, self.lease_var.get(), self.notes_var.get())
                self.pending_api_key = api_key
                self.pending_save_key = self.save_key_var.get()
        except ValueError as exc:
            messagebox.showwarning("입력 확인", str(exc), parent=self.root)
            return

        self.busy = True
        self.run_button.configure(state="disabled")
        self.demo_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.status_var.set("분석 중 · 실제 공시 원문 수집은 수 분 걸릴 수 있습니다")
        self.status_label.configure(fg=BLUE)
        self._set_summary("DART 공시와 원문 주석을 수집하고 계산 근거를 구성하고 있습니다.\n창을 닫지 말고 잠시 기다려 주세요.")
        self.progress.start(10)
        threading.Thread(target=self._worker, args=(demo, request), daemon=True).start()

    def _worker(self, demo: bool, request: tuple | None) -> None:
        try:
            if demo:
                data = demo_analysis()
                stem = "demo"
            else:
                assert request is not None
                data = analyze_dart(*request)
                stem = data["metadata"]["company_name"]
            output = export_excel(data, stem)
            self.root.after(0, self._finish_success, data, output)
        except Exception as exc:
            details = traceback.format_exc()
            try:
                (ROOT / "DART-QoE-error.log").write_text(details, encoding="utf-8")
            except OSError:
                pass
            self.root.after(0, self._finish_error, str(exc))

    def _finish_success(self, data: dict, output: Path) -> None:
        self._set_idle()
        if self.pending_api_key:
            if self.pending_save_key:
                try:
                    save_api_key(self.pending_api_key)
                except OSError as exc:
                    messagebox.showwarning("API 키 저장", f"API 키를 저장하지 못했습니다.\n{exc}", parent=self.root)
            else:
                delete_saved_api_key()
        self.last_output = output
        metadata = data.get("metadata", {})
        years = data.get("years", [])
        candidates = data.get("candidates", [])
        errors = data.get("errors", [])
        self.status_var.set("완료 · Excel 검토 파일이 생성되었습니다")
        self.status_label.configure(fg=GREEN)
        self._set_summary(
            f"회사\n{metadata.get('company_name', '-')}\n\n"
            f"분석기간\n{', '.join(map(str, years)) or '-'}\n\n"
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

    def _set_idle(self) -> None:
        self.busy = False
        self.progress.stop()
        self.run_button.configure(state="normal")
        self.demo_button.configure(state="normal")

    def open_excel(self) -> None:
        if self.last_output and self.last_output.exists():
            os.startfile(self.last_output)
        else:
            messagebox.showinfo("파일 확인", "생성된 Excel 파일을 찾지 못했습니다.", parent=self.root)

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
