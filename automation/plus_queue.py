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
HINTS = json.loads((Path(__file__).resolve().parent / "plus-hints.json").read_text(encoding="utf-8"))


def pending(pid: int, now: float | None = None, out: Path | None = None) -> bool:
    """서버 반영 대기 중이거나, 2시간 안에 뽑아 아직 보강·검수 중인 글 (같은 글을 두 번 뽑지 않게. 2시간 지나면 다시 뽑힘)."""
    now = now or time.time()
    recent = [*QUEUE.glob(f"refresh-plus-{pid}-*.json"), *(out or OUT).glob(f"plus-{pid}.json")]
    return any(now - f.stat().st_mtime < plain_queue.PENDING_HOURS * 3600 for f in recent)


def patch_pending(post: dict, patches: list[dict] | None = None, last_change: float | None = None, now: float | None = None) -> bool:
    """검수 패치(wordpress/patches.json)가 아직 이 글에 반영 안 됐으면 True (11/11 #3017: 패치 반영 전 본문으로 보강을 시작하면
    보강본이 패치를 덮거나 '그 사이 수정됨'으로 거절됨). patches.json 을 올린 지 2시간이 지나면 기다리지 않음 (평생 보류 금지)."""
    import re
    import subprocess
    now = now or time.time()
    if last_change is None:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", "wordpress/patches.json"], cwd=QUEUE.parent.parent,
                             capture_output=True, text=True).stdout.strip()
        last_change = float(out) if out else 0.0
    if now - last_change > plain_queue.PENDING_HOURS * 3600:
        return False
    if patches is None:
        patches = json.loads((QUEUE.parent / "patches.json").read_text(encoding="utf-8"))
    text = re.sub(r"<[^>]+>", "", post.get("content_html", ""))
    return any(p.get("post") == post.get("post_id") and p.get("find") and p["find"] in text for p in patches)


def wanted(post: dict) -> bool:
    return plain_queue.is_done(post.get("content_html", "")) and not post.get("plus_at")


def main(n: int = 4) -> int:
    OUT.mkdir(exist_ok=True)
    picked, not_plain = [], 0
    from unreviewed import reviewed_ids
    done_review = reviewed_ids()
    for pid in plain_queue.order():
        if len(picked) >= n:
            break
        # 외부 검수 전인 새 초안은 건너뜀 (11/11 #3140: 검수 중인 글이 보강에 뽑혀 검수 패치와 부딪칠 뻔함). 검수 끝나면 reviewed.txt 에 들어가 다음 차례에 뽑힘
        if pending(pid) or pid not in done_review:
            continue
        post = tick.get(f"{tick.SITE}/wp-json/firstlab/v1/post/{pid}", token=True)
        if not plain_queue.is_done(post.get("content_html", "")):
            not_plain += 1
            continue
        if not wanted(post) or patch_pending(post):
            continue
        (OUT / f"plus-{pid}.json").write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
        keyword = post.get("keyword") or post.get("title", "").split(":")[0]
        hint = HINTS.get(str(pid), "")   # 다듬기·검수 중에 찾은 '보강 때 고칠 것' (automation/plus-hints.json)
        (OUT / f"naver-{pid}.txt").write_text((f"# 감시 세션 메모 (꼭 반영): {hint}\n" if hint else "") + naver_top.report(keyword), encoding="utf-8")
        picked.append(pid)
    print("\n".join(map(str, picked)))
    print(f"# 이번 {len(picked)}편 (아직 쉬운 말 안 된 글 {not_plain}편은 건너뜀)")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 4))
