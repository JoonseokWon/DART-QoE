from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from qoe import analyze_dart, demo_analysis, save_json

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
NODE_DEFAULT = Path(r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")


def debug_log(message: str) -> None:
    if not os.environ.get("DART_QOE_DEBUG"):
        return
    with (ROOT / "DART-QoE-startup.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat()} {message}\n")

HTML = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DART-QoE</title><style>
:root{--navy:#112a46;--blue:#2f5597;--pale:#edf3f9;--ink:#17202a;--amber:#fff2cc;--line:#d7e0ea}*{box-sizing:border-box}body{margin:0;font-family:Arial,"Malgun Gothic",sans-serif;color:var(--ink);background:#f4f7fa}header{background:var(--navy);color:white;padding:28px 5vw}header h1{margin:0 0 8px;font-size:30px}header p{margin:0;opacity:.88;max-width:900px}.wrap{max-width:1180px;margin:26px auto;padding:0 18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:0 5px 18px #112a4612}.card h2{margin-top:0;color:var(--navy);font-size:20px}.field{margin:13px 0}.field label{display:block;font-weight:700;margin-bottom:6px}.field input{width:100%;padding:11px;border:1px solid #b9c8d8;border-radius:8px}.years{display:grid;grid-template-columns:1fr 1fr;gap:10px}.check{display:flex;align-items:center;gap:8px;margin:12px 0}.check input{width:auto}button{border:0;border-radius:8px;padding:12px 18px;font-weight:700;cursor:pointer;background:var(--blue);color:white;margin-right:7px}button.secondary{background:#e7eef6;color:var(--navy)}button:disabled{opacity:.5}.notice{background:var(--amber);padding:13px;border-radius:8px;line-height:1.55}.story{grid-column:1/-1}.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.step{background:var(--pale);padding:15px;border-radius:9px}.status{margin-top:15px;white-space:pre-wrap;padding:14px;border-radius:8px;background:#0c1724;color:#cfe3f5;min-height:64px}.result{margin-top:14px}.result a{display:inline-block;padding:11px 15px;background:#e2f0d9;color:#215e21;text-decoration:none;border-radius:8px;font-weight:700}@media(max-width:760px){.grid,.steps{grid-template-columns:1fr}.story{grid-column:auto}}
</style></head><body><header><h1>DART-QoE</h1><p>정상화 이익 후보와 운전자본·현금전환을 함께 보는 거래 검토 보조 PoC — 자동 결론이 아니라 원문 확인 항목과 계산 근거를 만듭니다.</p></header>
<main class="wrap"><div class="grid"><section class="card"><h2>실제 DART 분석</h2><div class="field"><label>OpenDART API 키</label><input id="api" type="password" placeholder="40자리 인증키"></div><div class="field"><label>회사명 / 종목코드</label><input id="company" value="휴메딕스"></div><div class="years"><div class="field"><label>시작연도</label><input id="begin" type="number" value="2022"></div><div class="field"><label>종료연도</label><input id="end" type="number" value="2024"></div></div><label class="check"><input id="lease" type="checkbox" checked>순차입금에 리스부채 포함</label><label class="check"><input id="notes" type="checkbox" checked>사업보고서 원문에서 후보 탐색</label><button onclick="run(false)">분석 및 Excel 생성</button></section>
<section class="card"><h2>API 키 없이 검증</h2><p>샘플 제조기업 3개년 자료로 보고이익, 현금전환, 운전자본, 순차입금과 정상화 조정 후보가 포함된 Excel을 즉시 생성합니다.</p><div class="notice">샘플 숫자는 기능 설명용이며 실제 기업 분석 결과가 아닙니다. 파란 입력셀에서 사용자 조정 여부와 사유를 기록할 수 있습니다.</div><p><button class="secondary" onclick="run(true)">데모 Excel 생성</button></p></section>
<section class="card story"><h2>프로젝트 서사</h2><div class="steps"><div class="step"><b>1. M&A 사례</b><br>인수 배경·구조·성과 해석</div><div class="step"><b>2. DART-QoE</b><br>이익 지속성·운전자금 직접 검토</div><div class="step"><b>3. DART-OT</b><br>차입금 이자비용 감사 관점 검토</div><div class="step"><b>4. 검토 흔적</b><br>원문·계정·산식·조정 사유 보존</div></div><div id="status" class="status">준비됨</div><div id="result" class="result"></div></section></div></main>
<script>async function run(demo){const s=document.getElementById('status'),r=document.getElementById('result');s.textContent='처리 중… 실제 원문 수집은 수 분 걸릴 수 있습니다.';r.innerHTML='';const body=demo?{demo:true}:{api_key:api.value,company:company.value,begin_year:+begin.value,end_year:+end.value,include_lease:lease.checked,fetch_notes:notes.checked};try{const res=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await res.json();if(!res.ok)throw Error(data.error||'오류');s.textContent=`완료: ${data.company} / ${data.years.join(', ')}\n정상화 조정 후보 ${data.candidate_count}건 · 추출 오류 ${data.error_count}건`;r.innerHTML=`<a href="${data.download}">Excel 열기</a>`}catch(e){s.textContent='오류: '+e.message}}</script></body></html>'''


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
        raise RuntimeError("Node.js 실행 파일을 찾지 못했습니다. DART_QOE_NODE 환경변수를 설정하세요.")
    env = os.environ.copy()
    default_node_path = BUNDLE_ROOT / "node_modules" if FROZEN else Path(
        r"C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
    )
    env.setdefault("NODE_PATH", str(default_node_path))
    try:
        cp = subprocess.run(
            [str(node), str(BUNDLE_ROOT / "export_workbook.mjs"), str(json_path), str(out)],
            cwd=BUNDLE_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode:
            raise RuntimeError(cp.stderr[-2000:] or cp.stdout[-2000:])
    finally:
        json_path.unlink(missing_ok=True)
    return out


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/":
            raw = HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if self.path.startswith("/outputs/"):
            name = Path(self.path).name; target = OUTPUTS / name
            if target.exists() and target.suffix == ".xlsx":
                raw = target.read_bytes(); self.send_response(200); self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"'); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/run": self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length", "0")); req = json.loads(self.rfile.read(length) or b"{}")
            if req.get("demo"):
                data = demo_analysis(); stem = "demo"
            else:
                if not req.get("api_key") or not req.get("company"): raise ValueError("API 키와 회사명을 입력하세요.")
                data = analyze_dart(req["api_key"], req["company"], int(req["begin_year"]), int(req["end_year"]),
                                    bool(req.get("include_lease", True)), bool(req.get("fetch_notes", True)))
                stem = data["metadata"]["company_name"]
            out = export_excel(data, stem)
            self._json(200, {"company": data["metadata"]["company_name"], "years": data["years"],
                             "candidate_count": len(data.get("candidates", [])), "error_count": len(data.get("errors", [])),
                             "download": "/outputs/" + out.name})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, fmt, *args):
        if sys.stdout:
            print(f"[{self.log_date_time_string()}] {fmt % args}")


def main():
    host = "127.0.0.1"
    configured_port = os.environ.get("DART_QOE_PORT")
    # Keep a dedicated default port so other portfolio tools (for example,
    # Food-Fee on 8765) cannot be mistaken for DART-QoE.
    requested_port = int(configured_port or "18765")
    debug_log("main entered")
    try:
        server = ThreadingHTTPServer((host, requested_port), Handler)
    except OSError:
        if configured_port:
            raise
        # The default port may be occupied by another local program. Let the
        # operating system allocate a free port so the desktop app still opens.
        server = ThreadingHTTPServer((host, 0), Handler)
    port = server.server_address[1]
    debug_log(f"server created on port {port}")
    url = f"http://{host}:{port}"
    if sys.stdout:
        print(f"DART-QoE 실행: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.7, lambda: webbrowser.open_new_tab(url)).start()
    debug_log("serve_forever starting")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            (ROOT / "DART-QoE-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            raise
