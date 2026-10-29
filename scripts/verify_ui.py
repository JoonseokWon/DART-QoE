"""Exercise desktop layout and states without network calls or saved credentials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--state", choices=["ready", "error", "success", "loading"], default="ready")
    args = parser.parse_args()
    app.enable_dpi_awareness()
    root = app.tk.Tk()
    root.withdraw()
    with patch.object(app, "load_saved_api_key", return_value=""), patch.object(app, "FROZEN", True):
        ui = app.DartQoeApp(root)
    root.geometry(f"{args.width}x{args.height}")
    if not args.show:
        root.attributes("-alpha", 0)
    root.deiconify()
    root.update()
    if isinstance(ui.api_entry, app.NativeWindowsEntry):
        # Native-key unit check: use the control's message handler directly.
        app.ctypes.windll.user32.SendMessageW(ui.api_entry.hwnd, 0x0100, 9, 0)
        assert ui.api_entry._pending_key == "next"
        ui.api_entry._poll()
        app.ctypes.windll.user32.SendMessageW(ui.company_entry.hwnd, 0x0100, 13, 0)
        assert ui.company_entry._pending_key == "run"
        ui.company_entry._poll()
        assert ui.form_error_var.get() == "전자공시 인증키를 입력하세요."
        ui._show_form_error("")
    ui._layout_cards(force=True)
    root.update()
    if args.state == "error":
        ui.start_analysis()
        assert ui.form_error_var.get() == "전자공시 인증키를 입력하세요."
        assert not ui.busy
    elif args.state == "success":
        ui._finish_success(app.demo_analysis(), ROOT / "outputs" / "validation.xlsx")
        assert ui.open_button.cget("state") == "normal"
    elif args.state == "loading":
        ui.busy = True
        ui.run_button.configure(state="disabled")
        ui.progress.grid()
        ui._apply_progress(55, "2024년 사업보고서 주석을 수집하고 있습니다")
        ui._apply_progress(25, "이전 단계")
        assert int(ui.progress.cget("value")) == 55
    root.update()
    assert ui.form_card.winfo_width() > 300
    assert ui.result_card.winfo_width() > 300
    assert ui.run_button.winfo_y() + ui.run_button.winfo_height() <= ui.action_block.winfo_height()
    summary = {
        "size": [root.winfo_width(), root.winfo_height()], "layout": ui._layout_mode,
        "state": args.state, "scrollbar": bool(ui.body_scrollbar.winfo_ismapped()),
        "form": [ui.form_card.winfo_width(), ui.form_card.winfo_height()],
        "result": [ui.result_card.winfo_width(), ui.result_card.winfo_height()],
        "error": ui.form_error_var.get(),
        "native_keyboard": "passed",
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.show:
        root.title(f"DART-QoE 검증 {args.state} {args.width}x{args.height}")
        root.after(120000, root.destroy)
        root.mainloop()
    else:
        root.destroy()


if __name__ == "__main__":
    run()
