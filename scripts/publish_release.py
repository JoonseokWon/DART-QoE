"""Publish verified local assets to the project's GitHub Release using Git credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "JoonseokWon/DART-QoE"
GIT = "git"


def credential() -> str:
    result = subprocess.run(
        [GIT, "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        text=True, capture_output=True, cwd=ROOT, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not fields.get("password"):
        raise RuntimeError("GitHub Git 인증정보를 찾지 못했습니다.")
    return fields["password"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--tag")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--asset", type=Path, action="append", default=[])
    args = parser.parse_args()
    token = credential()

    def request(url, method="GET", body=None, content_type="application/json"):
        payload = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(url, data=payload, method=method, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10", "Content-Type": content_type,
            "User-Agent": "DART-QoE-release",
        })
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)

    base = f"https://api.github.com/repos/{REPO}"
    if args.check:
        releases = request(base + "/releases?per_page=10")
        print(json.dumps({"releases": [{"tag": r["tag_name"], "url": r["html_url"]} for r in releases]}, ensure_ascii=False))
        return
    if not args.tag or not args.notes or not args.asset:
        parser.error("--tag, --notes, --asset are required")
    commit = subprocess.check_output([GIT, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    try:
        release = request(base + "/releases/tags/" + urllib.parse.quote(args.tag))
        if release["target_commitish"] != commit:
            raise RuntimeError("같은 태그의 다른 릴리스가 이미 있습니다. 덮어쓰지 않습니다.")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = request(base + "/releases", "POST", {
            "tag_name": args.tag, "target_commitish": commit, "name": f"DART-QoE {args.tag}",
            "body": args.notes.read_text(encoding="utf-8"), "draft": True, "prerelease": False,
        })
    existing = {a["name"]: a for a in request(base + f'/releases/{release["id"]}/assets')}
    for asset in args.asset:
        data = asset.read_bytes()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if asset.name in existing:
            if existing[asset.name].get("digest") != digest:
                raise RuntimeError(f"다른 내용의 자산이 이미 있습니다: {asset.name}")
            continue
        upload = release["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(asset.name)
        uploaded = request(upload, "POST", data, "application/octet-stream")
        if uploaded["size"] != len(data) or uploaded.get("digest") != digest:
            raise RuntimeError(f"업로드 크기 또는 해시 불일치: {asset.name}")
        print(f"Uploaded: {asset.name} ({len(data)} bytes)")
    release = request(base + f'/releases/{release["id"]}', "PATCH", {"draft": False})
    print(release["html_url"])


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"GitHub API 오류: HTTP {error.code}") from None
