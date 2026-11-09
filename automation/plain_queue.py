"""쉬운 말 다듬기 차례 (11/10 운영자 '초안 저장해 놓은 글들도 전부 수정하자').

공개 순서(/maintenance publish_order_all, 봇이 한 시간마다 기록) 앞에서부터, 아직 안 다듬은 글(본문에 '한 줄 정리:' 없음) n 편을
골라 원본을 /tmp/firstlab-review/post-<번호>.json 에 받아 둔다. 그다음은 automation/plain-prompt.md 순서
(다듬기 → 다른 쪽 뜻 검수 → 확인 → plain_check --write).

사용: python3 automation/plain_queue.py [n]   → 글 번호를 한 줄에 하나씩 출력 (끝에 남은 편수)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tick  # noqa: E402

OUT = Path("/tmp/firstlab-review")
DONE_MARK = "<strong>한 줄 정리"


def is_done(content_html: str) -> bool:
    return DONE_MARK in (content_html or "")


QUEUE = Path(__file__).resolve().parent.parent / "wordpress" / "queue"
PENDING_HOURS = 2  # 갱신 파일을 올린 지 2시간 안이면 서버 반영 대기 중으로 보고 건너뜀 (그 뒤에도 표시가 없으면 반영 실패 → 다시 뽑힘)


def pending(pid: int, now: float | None = None) -> bool:
    import time
    now = now or time.time()
    return any(now - f.stat().st_mtime < PENDING_HOURS * 3600 for f in QUEUE.glob(f"refresh-plain-{pid}-*.json"))


def order() -> list[int]:
    m = tick.get(f"{tick.SITE}/wp-json/firstlab/v1/maintenance", token=True)
    ids = list(m.get("publish_order_all") or [])
    ids += [p["post_id"] for p in m.get("ready_posts") or [] if p["post_id"] not in ids]  # 기록 전이면 대기 글 날짜순
    return ids


def main(n: int = 6) -> int:
    OUT.mkdir(exist_ok=True)
    picked, left = [], 0
    for pid in order():
        if pending(pid):
            continue
        post = tick.get(f"{tick.SITE}/wp-json/firstlab/v1/post/{pid}", token=True) if len(picked) < n else None
        if post is None:
            left += 1
            continue
        if is_done(post.get("content_html", "")):
            continue
        (OUT / f"post-{pid}.json").write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
        picked.append(pid)
    print("\n".join(map(str, picked)))
    print(f"# 이번 {len(picked)}편, 그 뒤 확인 안 한 대기 글 {left}편 (다듬은 글은 건너뜀)")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
