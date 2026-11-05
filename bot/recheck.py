"""공개 글 근거 다시 보기 (하루 1번, 키워드 수집 끝) — 공개한 뒤에도 글의 근거가 그대로인지 매일 몇 편씩 돌아가며 본다.

왜 (11/5 운영자 '글과 계산기가 어느 법령·규정으로 만들어졌고 그게 바뀌면 고치는 게 시스템화돼 있나'):
법령 개정은 law_watch, 금리는 bok_api, 정부24 혜택은 benefit_watch 가 표시하지만, 기관 안내 페이지(요금표·수수료·지원 금액)는
공개 직전(freshness)에만 확인하고 공개 뒤에는 아무도 다시 보지 않았다.
방법: 공개 직전 확인과 같은 검사(freshness.problems — 쓸 때 공식 페이지에서 확인된 숫자가 아직 있는지, 근거 조문이 그 뒤 개정·시행됐는지)를
공개 글에 DAILY 편씩 차례로 → 문제가 있으면 bot/changed.py 표시 → refresh 가 재검증 (주당 한도와 상관없이).
페이지를 못 열면 판단하지 않는다 (바뀌었다는 증거가 있을 때만 표시).
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse

from . import changed, freshness, source_check

DAILY = 25          # 하루에 다시 볼 공개 글 수 (공개 글 300편이면 12일에 한 바퀴)
CURSOR = "recheck_cursor"


def _meta(wp, pid: str, key: str) -> str:
    try:
        return wp("post", "meta", "get", pid, key).strip()
    except Exception:  # noqa: BLE001
        return ""


def batch(ids: list[str], cursor: str, n: int = DAILY) -> list[str]:
    """글 번호 순으로 cursor 다음부터 n 편 (끝까지 가면 처음부터)."""
    ids = sorted(ids, key=int)
    after = [i for i in ids if int(i) > int(cursor or 0)]
    return (after + [i for i in ids if i not in after])[:n]


def check_one(pid: str, post: dict, today: dt.date, wp, fetcher) -> list[str]:
    mark = _meta(wp, pid, changed.META)
    since = max(_meta(wp, pid, freshness.VERIFIED_META)[:10], _meta(wp, pid, "_firstlab_refreshed")[:10]) or today.isoformat()
    if changed.pending(mark, since):
        return []  # 이미 다시 확인할 글로 표시돼 있음
    try:
        record = json.loads(_meta(wp, pid, source_check.META) or "{}")
    except json.JSONDecodeError:
        record = {}
    review = [] if record.get("laws") else freshness._review_from_queue(urllib.parse.unquote(post.get("name", "")))
    return freshness.problems(record, review, dt.date.fromisoformat(since), today, fetcher)


def run(today: dt.date | None = None, bulk=None, wp=None, fetcher=None) -> str:
    from . import wpbulk
    from .settings import wp_cli
    from .state import add_log, load_state, save_state
    today, wp, fetcher = today or dt.date.today(), wp or wp_cli, fetcher or source_check.fetch
    posts = bulk if bulk is not None else wpbulk.posts()
    published = [pid for pid, p in posts.items() if p.get("status") == "publish"]
    state = load_state()
    todo = batch(published, state.get(CURSOR, ""))
    marked = []
    for pid in todo:
        found = check_one(pid, posts[pid], today, wp, fetcher)
        if found:
            marked += changed.flag([pid], f"{today:%Y-%m-%d} 근거 다시 보기: {'; '.join(found)[:150]}", wp)
    if todo:
        state[CURSOR] = todo[-1]
    msg = f"공개 글 근거 다시 보기: {len(todo)}편 (공개 {len(published)}편 차례로) → 근거 바뀜 표시 {len(marked)}편"
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
