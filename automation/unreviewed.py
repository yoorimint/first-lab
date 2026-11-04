"""외부 검수가 필요한 글 찾기 (감시 세션용, automation/monitor.md).

서버의 공개 대기 글·보류 초안 전체를 글 번호로 automation/reviewed.txt 와 대조한다.
시각으로 '새 글'을 찾으면 경계에 걸린 글이 빠진다 (11/4 #163 토지 실거래가가 빠졌던 일).
출력: 글 번호, 제목, 검수자에게 줄 최신 본문 파일 (보류 수정본 fixhold 가 있으면 그것).

사용: FIRSTLAB_API_TOKEN=... python3 automation/unreviewed.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "wordpress" / "queue"
REVIEWED = ROOT / "automation" / "reviewed.txt"
API = "https://first-lab.kr/wp-json/firstlab/v1/maintenance"


def reviewed_ids() -> set[int]:
    """reviewed.txt 의 글 번호 (줄 맨 앞 숫자)."""
    ids = set()
    for line in REVIEWED.read_text(encoding="utf-8").splitlines():
        head = line.split()[0] if line.strip() and not line.startswith("#") else ""
        if head.isdigit():
            ids.add(int(head))
    return ids


def body_file(post_id: int, slug: str) -> Path | None:
    """최신 본문: fixhold-<번호>-*.json (가장 최근) → 키워드가 주소와 같은 새 글 파일."""
    fixes = sorted(QUEUE.glob(f"fixhold-{post_id}-*.json"))
    if fixes:
        return fixes[-1]
    for path in QUEUE.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("type") and str(data.get("keyword", "")).strip() == slug:
            return path
    return None


def main() -> None:
    req = urllib.request.Request(API, headers={"X-Firstlab-Token": os.environ["FIRSTLAB_API_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        state = json.load(resp)
    done = reviewed_ids()
    posts = state.get("ready_posts", []) + state.get("held_drafts", [])
    todo = [p for p in posts if int(p["post_id"]) not in done]
    for p in todo:
        f = body_file(int(p["post_id"]), str(p.get("slug", "")))
        print(f"{p['post_id']}\t{p['title']}\t{f.relative_to(ROOT) if f else '본문 파일 없음(서버 본문 확인)'}")
    print(f"# 검수 필요 {len(todo)}편 / 대기·보류 {len(posts)}편")


if __name__ == "__main__":
    main()
