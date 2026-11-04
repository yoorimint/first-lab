"""외부 검수가 필요한 글 찾기 (감시 세션용, automation/monitor.md).

서버의 공개 대기 글·보류 초안 전체를 글 번호로 automation/reviewed.txt 와 대조한다.
시각으로 '새 글'을 찾으면 경계에 걸린 글이 빠진다 (11/4 #163 토지 실거래가가 빠졌던 일).
출력: 글 번호, 제목, 검수자에게 줄 본문 파일 — 서버의 지금 본문(patches·보류 수정 반영)을 받아 OUT_DIR 에 저장.

사용: FIRSTLAB_API_TOKEN=... python3 automation/unreviewed.py [글번호 ...]
  글 번호를 주면 reviewed.txt 와 상관없이 그 글들의 본문만 받는다 (다시 검수할 때).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEWED = ROOT / "automation" / "reviewed.txt"
BASE = "https://first-lab.kr/wp-json/firstlab/v1"
OUT_DIR = Path(tempfile.gettempdir()) / "firstlab-review"  # 검수자에게 줄 본문 (저장소 밖)


def reviewed_ids() -> set[int]:
    """reviewed.txt 의 글 번호 (줄 맨 앞 숫자)."""
    ids = set()
    for line in REVIEWED.read_text(encoding="utf-8").splitlines():
        head = line.split()[0] if line.strip() and not line.startswith("#") else ""
        if head.isdigit():
            ids.add(int(head))
    return ids


def _get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={"X-Firstlab-Token": os.environ["FIRSTLAB_API_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def save_body(post_id: int) -> Path:
    """서버의 지금 본문을 JSON 파일로 (title, excerpt, content_html)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"post-{post_id}.json"
    path.write_text(json.dumps(_get(f"/post/{post_id}"), ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    if len(sys.argv) > 1:
        todo = [{"post_id": int(x), "title": ""} for x in sys.argv[1:]]
    else:
        state = _get("/maintenance")
        done = reviewed_ids()
        posts = state.get("ready_posts", []) + state.get("held_drafts", [])
        todo = [p for p in posts if int(p["post_id"]) not in done]
        print(f"# 검수 필요 {len(todo)}편 / 대기·보류 {len(posts)}편")
    for p in todo:
        path = save_body(int(p["post_id"]))
        title = p.get("title") or json.loads(path.read_text(encoding="utf-8"))["title"]
        print(f"{p['post_id']}\t{title}\t{path}")


if __name__ == "__main__":
    main()
