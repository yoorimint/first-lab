"""쉬운 말 기준(기준 글 #1419) 검사를 서버 쪽 관문에서 쓰기 — 잣대는 automation/plain_check.style_problems 하나.

11/10 운영자 '이제 글을 새로 적거나 수정하면 새로운 방식대로 가는 거지? 안 그러면 그러게 시스템 고치고':
새 글(bot/review_gate) · 보류 초안 수정(bot/hold) · 글 갱신(bot/refresh.apply_direct) · 공개 직전(bot/freshness) 모두 같은 잣대.
"""
from __future__ import annotations

import sys
from pathlib import Path

_AUTOMATION = Path(__file__).resolve().parent.parent / "automation"


def problems(body_html: str) -> list[str]:
    if str(_AUTOMATION) not in sys.path:
        sys.path.insert(0, str(_AUTOMATION))
    import plain_check  # noqa: E402 — automation/ 은 서버에도 같이 배포됨 (bot/patch_check 가 verify_patches 를 쓰듯)
    return [f"쉬운 말 기준: {x}" for x in plain_check.style_problems(body_html)]
