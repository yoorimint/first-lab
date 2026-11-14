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

import json
from pathlib import Path

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
    from .settings import wp_meta
    raw = wp_meta(post_id, TRIES_META, wp_cli)
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
    if not failed:  # 근거를 못 읽는 숫자 없는 주장이 많으면 계속 보류 (새 검수 기록으로 다시 대조, bot/source_check.unreadable_hold)
        _, checked = source_check.record_result(post_id, data.get("review"),
                                                lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val))
        failed = [x for x in [source_check.unreadable_hold(checked)] if x]
    if failed:
        wp_cli("post", "meta", "update", str(post_id), review_gate.HOLD_META, "; ".join(failed)[:300])
        tries = _bump_tries(post_id)
        later = f" — {tries}번 막혀 {RETRY_DAYS}일 뒤 다시" if tries >= MAX_TRIES else f" ({tries}/{MAX_TRIES}번째)"
        return f"'{title}' 고쳤지만 재검사 미통과 → 계속 보류{later}: {'; '.join(failed)[:150]}"
    wp_cli("post", "meta", "delete", str(post_id), review_gate.HOLD_META)
    try:
        wp_cli("post", "meta", "delete", str(post_id), TRIES_META)
    except Exception:  # noqa: BLE001 - 없는 메타 지우기도 WP-CLI 는 오류 (11/5 #746: 통과했는데 대기열에 못 들어감)
        pass
    schedule.mark_ready(post_id)
    freshness.mark_verified(post_id)  # 다시 검수했으니 공개 직전 확인 기준일도 오늘
    return f"'{title}' 재검사 통과 → 공개 대기열 ({str(data.get('changes', ''))[:80]})"


# 배포 때 보류 글 다시 검사 (11/6 #980: 근거 en-ter.co.kr(한전 운영)이 공식 주소 목록에 없어 보류 → 글쓰기 작업이 포기 → 감시 세션이
# 목록을 고쳤지만 30일 쉬는 글이 됨). 검사 코드가 바뀌면(배포) 보류·쉬는 글을 지금 코드로 다시 검사해 통과하면 바로 공개 대기열로.
QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"


def latest_review(post_id: int, keyword: str, queue_dir: Path = QUEUE_DIR) -> list | None:
    """그 글의 가장 최근 검수 기록: 보류 수정 파일(fixhold-<번호>-*) 중 review 가 있는 마지막 것, 없으면 처음 올린 글 파일(같은 키워드)."""
    for path in sorted(queue_dir.glob(f"fixhold-{post_id}-*.json"), reverse=True):
        try:
            review = json.loads(path.read_text(encoding="utf-8")).get("review")
        except (OSError, ValueError):
            continue
        if review:
            return review
    for path in queue_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("keyword", "")).strip() == keyword and data.get("review"):
            return data["review"]
    return None


def _stored_unreadable(post_id: int) -> str | None:
    """저장된 근거 대조(source_check.META)로 본 보류 이유 — 배포마다 페이지를 다시 열지 않음."""
    from .settings import wp_meta
    try:
        return source_check.unreadable_hold(json.loads(wp_meta(post_id, source_check.META, wp_cli) or "null"))
    except Exception:  # noqa: BLE001 - 기록을 못 읽으면 막지 않음 (다른 검사로 판단)
        return None


PREPUBLISH = "공개 전 확인"  # bot/schedule.py 가 공개 직전 확인(freshness)으로 보류할 때 붙이는 이유 앞머리


def _hold_reason(post_id: int) -> str:
    from .settings import wp_meta
    try:
        return wp_meta(post_id, review_gate.HOLD_META, wp_cli) or ""
    except Exception:  # noqa: BLE001
        return ""


def _prepublish_problems(post_id: int) -> list[str]:
    try:
        return freshness.check_post(post_id)
    except Exception as e:  # noqa: BLE001 — 확인이 실패하면 풀지 않음 (다음 배포에 다시)
        return [f"확인 실패: {e}"]


def _release_prepublish(post_id: int) -> None:
    """공개 직전 확인으로만 보류된 글 풀기 — 검수 기록·근거 대조 기록은 그대로 둠."""
    wp_cli("post", "meta", "delete", str(post_id), review_gate.HOLD_META)
    schedule.mark_ready(post_id)


def regate(held: list[dict], review_of=latest_review, problems=None, release=None, unreadable=None,
           reason_of=None, prepublish=None, release_prepublish=None) -> list[str]:
    """held: [{'post_id', 'keyword', 'title', 'excerpt', 'content'}]. 지금 코드로 통과하면 release(post_id, review).
    근거를 못 읽는 주장이 많아 막힌 글은 저장된 대조 결과가 그대로면 풀지 않는다 (글쓰기 작업이 고쳐 올려야 다시 대조)."""
    problems = problems or review_gate.problems
    release = release or _release
    unreadable = unreadable or _stored_unreadable
    reason_of, prepublish = reason_of or _hold_reason, prepublish or _prepublish_problems
    release_prepublish = release_prepublish or _release_prepublish
    out = []
    for h in held:
        if reason_of(int(h["post_id"])).startswith(PREPUBLISH):
            # 공개 직전 확인으로 막힌 글은 그 확인을 다시 — 검수 기록을 못 찾는 옛 글도 풀림 (11/14: 법제처 점검 화면을
            # 법령 본문으로 읽어 808·817 을 '숫자 사라짐'으로 보류, 원인을 고친 뒤에도 검수 기록이 없어 안 풀렸음)
            if not prepublish(int(h["post_id"])):
                release_prepublish(int(h["post_id"]))
                out.append(f"글 {h['post_id']} '{str(h.get('title', ''))[:30]}' 공개 전 확인 다시 통과 → 보류 해제·공개 대기열")
            continue
        review = review_of(int(h["post_id"]), h.get("keyword", ""))
        if not review:
            continue
        if not problems(review, h.get("title", ""), h.get("excerpt", ""), h.get("content", "")) \
                and not unreadable(int(h["post_id"])):
            release(int(h["post_id"]), review)
            out.append(f"글 {h['post_id']} '{str(h.get('title', ''))[:30]}' 지금 검사로 통과 → 보류 해제·공개 대기열")
    return out


def _release(post_id: int, review: list) -> None:
    wp_cli("post", "meta", "delete", str(post_id), review_gate.HOLD_META)
    try:
        wp_cli("post", "meta", "delete", str(post_id), TRIES_META)
    except Exception:  # noqa: BLE001
        pass
    schedule.mark_ready(post_id)
    source_check.record(post_id, review, lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val))


def held_drafts() -> list[dict]:
    """보류·쉬는 초안 (주소 = 키워드, publisher.existing_post_id 와 같은 규칙)."""
    import urllib.parse
    rows = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--meta_key={review_gate.HOLD_META}",
                             "--fields=ID,post_name,post_title,post_excerpt,post_content", "--format=json",
                             "--posts_per_page=-1") or "[]")
    return [{"post_id": r["ID"], "keyword": urllib.parse.unquote(r.get("post_name", "")), "title": r["post_title"],
             "excerpt": r["post_excerpt"], "content": _html(r["post_content"])} for r in rows]


MAX_REFILL = 10  # 한 번 배포에 다시 대조할 글 수 (공식 페이지를 여는 일이라 — 남은 글은 다음 배포에)


def missing_evidence(ready: list[dict], recorded: set[int], review_of=latest_review, record=None, limit: int = MAX_REFILL) -> list[str]:
    """공개 대기 글 중 근거 대조 기록이 없는 글을 저장소 검수 기록으로 다시 대조해 채움 (11/6 #1023: 대조 코드 오류로
    기록이 안 남음 → 공개 직전 '쓸 때 확인된 숫자가 아직 페이지에 있는지' 확인이 빠짐). 봇이 10분마다 1편씩 고친 코드로 다시."""
    record = record or (lambda pid, review: source_check.record(pid, review, lambda p, k, v: wp_cli("post", "meta", "update", p, k, v)))
    out = []
    for r in ready:
        pid = int(r["post_id"])
        if pid in recorded:
            continue
        review = review_of(pid, r.get("keyword", ""))
        if review and len(out) < limit:
            out.append(f"글 {pid} 근거 대조 다시: {record(pid, review)}"[:150])
    return out


def ready_without_record() -> tuple[list[dict], set[int]]:
    import urllib.parse
    ready = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--meta_key={schedule.QUEUE_META}",
                              f"--meta_value={schedule.READY}", "--fields=ID,post_name", "--format=json", "--posts_per_page=-1") or "[]")
    have = wp_cli("post", "list", "--post_type=post", "--post_status=draft", f"--meta_key={source_check.META}",
                  "--field=ID", "--posts_per_page=-1")
    return ([{"post_id": r["ID"], "keyword": urllib.parse.unquote(r.get("post_name", ""))} for r in ready],
            {int(x) for x in have.split()})


def main() -> None:
    from .state import add_log, load_state, save_state
    msgs = regate(held_drafts())  # 근거 기록 채우기는 배포 때가 아니라 봇 10분마다 1편 (bot/run.py — 배포가 길어져 밀림, 11/6)
    if msgs:
        state = load_state()
        for m in msgs:
            add_log(state, True, f"보류 다시 검사: {m}")
        save_state(state)
    print("\n".join(msgs) or "보류 다시 검사: 풀린 글 없음")


if __name__ == "__main__":
    main()

