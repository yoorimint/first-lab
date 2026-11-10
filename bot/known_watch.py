"""확인된 틀린 문구 감시 (코드만, 하루 한 번) — 11/10 쉬운 말 다듬기 뜻 검수에서 나온 법 원문 확인 오류가 여러 글에 되풀이됨.

condition_gate.known_wrong(시행일이 지나 틀리게 된 말 포함, 예: 2026-12-08 근로감독관→노동감독관)에 걸린 글에
'바뀐 근거' 표시(bot/changed.py)를 단다 → 공개 글은 refresh.pick_targets 가 먼저 재검증, 대기 초안은 공개 직전 보류.
같은 표시는 다시 안 달고(changed.merged), 고쳐지면 다음 날부터 안 걸림 — 평생 반복 없음.
"""
from __future__ import annotations

import datetime as dt

from . import changed
from .condition_gate import known_wrong


def targets(posts: dict[str, dict], today: dt.date) -> dict[int, str]:
    """글 번호 → 첫 번째 걸린 문구 (공개·예약·초안만)."""
    out = {}
    for pid, p in posts.items():
        if p.get("status") not in changed.LIVE:
            continue
        found = known_wrong(p.get("content", ""), today)
        if found:
            out[int(pid)] = found[0]
    return out


def run(today: dt.date | None = None, posts: dict | None = None, flag=changed.flag) -> str:
    today = today or dt.date.today()
    if posts is None:
        from . import wpbulk
        posts = wpbulk.posts()
    hits = targets(posts, today)
    marked = []
    for pid, why in hits.items():
        marked += flag([pid], f"{today:%Y-%m-%d} {why[:120]}")
    return f"확인된 틀린 문구 감시: 걸린 글 {len(hits)}편, 새로 표시 {len(marked)}편" + (f" ({', '.join(map(str, list(hits)[:8]))})" if hits else "")
