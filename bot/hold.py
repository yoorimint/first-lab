"""검수 보류 초안 고치기: 관문(review_gate)에서 막힌 초안을 글쓰기 세션이 고쳐 올리면, 다시 관문 검사를 해서
통과하면 공개 대기열에 넣고, 또 막히면 새 이유를 남긴다. 운영자 손 없이 보류 → 수정 → 재검사 → 대기열이 돈다.

파일: {"type": "fix_hold", "post_id", "base_modified", "content_html", "title"(선택), "excerpt"(선택), "review", "changes"}
  고칠 수 없으면(공식 근거를 못 찾음 등) {"type": "fix_hold", "post_id", "give_up": true, "why"} → 그 글은 목록에서 빠짐.
보류 초안 목록은 읽기 API 의 held_drafts (wordpress/mu-plugins/first-lab-api.php).

평생 보류 막기 (11/5 운영자 '공개 막으면 평생 보류로 있나?'): 고쳐도 다시 막힌 횟수를 TRIES_META 에 세서
MAX_TRIES 번 넘게 막힌 글·포기한 글은 목록에서 빼고(다른 보류 글이 차례를 못 받는 일 방지), RETRY_DAYS 뒤 한 번 더 기회를 준다
(그 사이 공식 안내가 생겼을 수 있음). 지우지는 않는다 (초안으로 남음, /maintenance 의 gave_up 에 보임).
"""
from __future__ import annotations

from . import freshness, review_gate, schedule, source_check
from .blocks import to_blocks
from .refresh import _html
from .settings import wp_cli
from .writer import numbers_match

MIN_KEEP_RATIO = 0.6  # 확인 못 한 내용을 빼면 줄어들 수 있어 갱신(80%)보다 느슨하게
TRIES_META = "_firstlab_hold_tries"   # '횟수|마지막 날짜' (first-lab-api.php 가 같은 값을 읽음)
MAX_TRIES = 3
RETRY_DAYS = 30


def _get(post_id: int, field: str) -> str:
    return wp_cli("post", "get", str(post_id), f"--field={field}").strip()


def _bump_tries(post_id: int, give_up: bool = False) -> int:
    import datetime as dt
    raw = wp_cli("post", "meta", "get", str(post_id), TRIES_META).strip()
    tries = int(raw.split("|")[0]) if raw.split("|")[0].isdigit() else 0
    tries = MAX_TRIES if give_up else tries + 1
    wp_cli("post", "meta", "update", str(post_id), TRIES_META, f"{tries}|{dt.date.today():%Y-%m-%d}")
    return tries


def apply_fix(data: dict) -> str:
    post_id = int(data["post_id"])
    if _get(post_id, "post_status") != "draft":
        raise RuntimeError(f"글 {post_id} 이 초안이 아님 → 반영 안 함")
    if data.get("give_up") is True:  # 글쓰기 세션이 고칠 수 없다고 판단 → 목록에서 빼고 RETRY_DAYS 뒤 다시
        _bump_tries(post_id, give_up=True)
        wp_cli("post", "meta", "update", str(post_id), review_gate.HOLD_META,
               f"포기: {str(data.get('why', ''))[:200]}")
        return f"글 {post_id} 보류 수정 포기 ({str(data.get('why', ''))[:80]}) → {RETRY_DAYS}일 뒤 다시"
    if data.get("base_modified") and data["base_modified"] != _get(post_id, "post_modified"):
        raise RuntimeError(f"글 {post_id} 이 그 사이 수정됨 → 반영 안 함")
    new_html = str(data.get("content_html") or "")
    old = _html(_get(post_id, "post_content"))
    if len(new_html) < len(old) * MIN_KEEP_RATIO:
        raise RuntimeError(f"고친 본문이 너무 짧음 ({len(new_html)}자 < 기존 {len(old)}자의 60%) → 반영 안 함")
    title = str(data.get("title") or _get(post_id, "post_title")).strip()
    excerpt = str(data.get("excerpt") or _get(post_id, "post_excerpt")).strip()
    for label, text in (("제목", title), ("요약문", excerpt)):
        if text and not numbers_match(text, new_html):
            raise RuntimeError(f"{label}의 숫자가 고친 본문에 없음 → 반영 안 함")

    wp_cli("post", "update", str(post_id), f"--post_content={to_blocks(new_html)}",
           f"--post_title={title}", f"--post_excerpt={excerpt}")
    failed = review_gate.problems(data.get("review"), title, excerpt, new_html)
    if failed:
        wp_cli("post", "meta", "update", str(post_id), review_gate.HOLD_META, "; ".join(failed)[:300])
        tries = _bump_tries(post_id)
        later = f" — {tries}번 막혀 {RETRY_DAYS}일 뒤 다시" if tries >= MAX_TRIES else f" ({tries}/{MAX_TRIES}번째)"
        return f"'{title}' 고쳤지만 재검사 미통과 → 계속 보류{later}: {'; '.join(failed)[:150]}"
    wp_cli("post", "meta", "delete", str(post_id), review_gate.HOLD_META)
    wp_cli("post", "meta", "delete", str(post_id), TRIES_META)
    schedule.mark_ready(post_id)
    freshness.mark_verified(post_id)  # 다시 검수했으니 공개 직전 확인 기준일도 오늘
    # 새 검수 기록으로 근거 대조도 다시 저장 (공개 직전 확인·법령 감시·공개 글 다시 보기가 이 근거를 봄)
    source_check.record(post_id, data.get("review"), lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val))
    return f"'{title}' 재검사 통과 → 공개 대기열 ({str(data.get('changes', ''))[:80]})"
