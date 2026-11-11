"""+@ 보강 차례 (11/11 운영자 '쉬운 말 작업 다 하면 바로 초안 전부 +@ 작업해라').

공개 순서 앞에서부터, 쉬운 말로 다듬었고(한 줄 정리 있음) 아직 보강 안 한(/post 의 plus_at 비어 있음) 대기 글 n 편을 골라
원본을 /tmp/firstlab-review/plus-<번호>.json, 네이버 상위 글을 /tmp/firstlab-review/naver-<번호>.txt 에 받아 둔다.
그다음은 automation/plus-prompt.md 순서 (보강 → 다른 쪽 사실 검수 → 감시 세션이 끝까지 읽기 → plus_check --write).

사용: python3 automation/plus_queue.py [n]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import naver_top  # noqa: E402
import plain_queue  # noqa: E402
import tick  # noqa: E402

OUT = plain_queue.OUT
QUEUE = plain_queue.QUEUE


def pending(pid: int, now: float | None = None) -> bool:
    now = now or time.time()
    return any(now - f.stat().st_mtime < plain_queue.PENDING_HOURS * 3600 for f in QUEUE.glob(f"refresh-plus-{pid}-*.json"))


def wanted(post: dict) -> bool:
    return plain_queue.is_done(post.get("content_html", "")) and not post.get("plus_at")


def main(n: int = 4) -> int:
    OUT.mkdir(exist_ok=True)
    picked, not_plain = [], 0
    for pid in plain_queue.order():
        if len(picked) >= n:
            break
        if pending(pid):
            continue
        post = tick.get(f"{tick.SITE}/wp-json/firstlab/v1/post/{pid}", token=True)
        if not plain_queue.is_done(post.get("content_html", "")):
            not_plain += 1
            continue
        if not wanted(post):
            continue
        (OUT / f"plus-{pid}.json").write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
        keyword = post.get("keyword") or post.get("title", "").split(":")[0]
        (OUT / f"naver-{pid}.txt").write_text(naver_top.report(keyword), encoding="utf-8")
        picked.append(pid)
    print("\n".join(map(str, picked)))
    print(f"# 이번 {len(picked)}편 (아직 쉬운 말 안 된 글 {not_plain}편은 건너뜀)")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 4))
