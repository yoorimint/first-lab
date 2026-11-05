"""한 번만: 11/5 패치 버그로 사라진 다음 문단 되살리기 (server/bot-setup.sh 가 표시 파일로 한 번만 실행).

무슨 일: 찾을 문구(find)가 '</p>'·'</li>' 로 끝난 패치 4개(#51 2개·#59·#70)를 적용할 때 정규식이 다음 문단까지 삼켜,
고친 문단 바로 뒤 문단 1개가 지워졌다 (bot/fix_posts.patch_content 는 고침, apply_review 가 이런 패치를 막음).
되살리는 법: 그 글의 수정 기록(워드프레스 revision) 중 패치 전 판에서 '찾을 문구가 든 요소 끝 ~ 다음 요소 끝'을 꺼내
지금 글의 고친 문단 바로 뒤에 다시 넣는다. 확실하지 않으면(이전 판이 없음·그 문단이 이미 있음·고친 문단을 못 찾음) 아무것도 안 바꾸고 기록만.
"""
from __future__ import annotations

import json
import re

from .fix_posts import inner
from .settings import wp_cli

BROKEN = [  # (글, tag, 찾을 문구(닫는 태그 포함), 고친 문구 앞부분)
    (59, "li", "<strong>총액 견적</strong>을 받아요.</li>", "집에서 다닐 수 있는 병원 2~3곳에 체중과 성별을 알려 주고"),
    (70, "p", "상자 여러 개를 보낼 때는 방문접수가 훨씬 편해요.</p>", "둘 다 온라인으로 미리 정보를 입력하는 건 같아요."),
    (51, "li", "응시 지역을 고르고 결제해요.</li>", "<strong>필기 접수</strong>"),
    (51, "p", "그대로 준비하면 돼요.</p>", "2026년 실기 공개 문제는"),
]


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def lost_segment(old: str, tag: str, find: str) -> str | None:
    """이전 판에서 find 가 끝나는 요소 다음의 요소까지 (사이의 블록 표시 포함)."""
    i = old.find(find)
    if i < 0:
        return None
    end_first = i + len(find)
    nxt = re.search(rf"<{tag}(?:\s[^>]*)?>.*?</{tag}>", old[end_first:], flags=re.S)
    return old[end_first: end_first + nxt.end()] if nxt else None


def restore_one(post_id: int, tag: str, find: str, fixed_head: str, wp=wp_cli) -> str:
    current = wp("post", "get", str(post_id), "--field=post_content")
    revs = json.loads(wp("post", "list", "--post_type=revision", f"--post_parent={post_id}", "--post_status=inherit",
                         "--orderby=date", "--order=DESC", "--fields=ID", "--format=json", "--posts_per_page=50") or "[]")
    for rev in revs:
        old = wp("post", "get", str(rev["ID"]), "--field=post_content")
        seg = lost_segment(old, tag, find)
        if not seg:
            continue
        lost_text = _plain(seg)
        if lost_text and lost_text in _plain(current):
            return f"글 {post_id}: 다음 문단이 이미 있음 → 그대로"
        m = re.search(rf"<{tag}(?:\s[^>]*)?>(?:(?!</{tag}>).)*?{re.escape(fixed_head)}(?:(?!</{tag}>).)*?</{tag}>", current, flags=re.S)
        if not m:
            return f"글 {post_id}: 고친 문단을 못 찾음 → 그대로 (확인 필요)"
        updated = current[: m.end()] + seg + current[m.end():]
        wp("post", "update", str(post_id), f"--post_content={updated}")
        return f"글 {post_id}: 사라진 문단 되살림 — {lost_text[:50]}"
    return f"글 {post_id}: 패치 전 수정 기록 없음 → 그대로 (확인 필요)"


def run() -> str:
    from .state import add_log, load_state, save_state
    out = []
    for post_id, tag, find, head in BROKEN:
        try:
            out.append(restore_one(post_id, tag, find, head))
        except Exception as e:  # noqa: BLE001
            out.append(f"글 {post_id}: 실패 {str(e)[:80]}")
    msg = "패치 버그로 사라진 문단 되살리기: " + " / ".join(out)
    state = load_state()
    add_log(state, all("되살림" in x or "이미 있음" in x for x in out), msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
