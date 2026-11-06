"""대기 글 외부 검수를 글쓰기 작업 안으로 (11/5 운영자 '니가 없어도 돌아가게 만들어야 된다').

지금은 감시 세션이 새 글마다 외부 검수(automation/review-prompt.md)를 해서 글당 1~5곳을 고친다. 감시 세션이 없어지면 이 일이 사라진다.
공개는 하루 1~3편이고 공개 대기 글이 REVIEW_WHEN_READY 편 이상 쌓여 있으면, 새 글을 더 쓰는 것보다 공개될 글의 오류를 줄이는 게 낫다
→ 그때 글쓰기 작업은 새 글 대신 '외부 검수 안 된 대기 글' 1편을 감시 세션과 같은 절차로 검수·수정한다 (daily-writer.md 0단계).
대상: 공개 대기(ready) 초안 중 automation/reviewed.txt 에 없는 가장 오래된 글. 읽기 API /next 의 review_target.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REVIEWED = Path(__file__).resolve().parent.parent / "automation" / "reviewed.txt"
# 11/5 운영자 '12/5 까지는 작성자는 최대한 빨리 쓰고, 검토·수정은 감시 세션과 다른 검수자가': 그때까지 글쓰기 작업은 대기 글 검수를
# 하지 않는다. 단 감시 세션이 멈춰 검수 안 된 대기 글이 MONITOR_BACKLOG 편 넘게 쌓이면 글쓰기 작업이 다시 맡는다 (영원히 안 쌓이게).
# 12/5 가 지나면 자동으로 원래대로 (감시 세션이 없어지므로 — 공개 편수는 운영자가 따로 정함).
MONITOR_REVIEWS_UNTIL = "2026-12-05"
MONITOR_BACKLOG = 10
REVIEW_WHEN_READY = 5   # 11/5 검토: 20 이면 공개를 늘린 뒤 대기열이 20 아래로 내려가 외부 검수가 영영 사라짐 → 검수 안 된 대기 글이 있으면 거의 늘 검수 (새 글 1편 → 다음 실행 검수)

_READY_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_queue', 'meta_value' => 'ready',
                            'numberposts' => -1, 'orderby' => 'date', 'order' => 'ASC' ) ) as $p ) {
    $out[] = array( 'post_id' => $p->ID, 'title' => $p->post_title );
}
echo wp_json_encode( $out );
"""


def reviewed_ids(path: Path = REVIEWED) -> set[int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {int(m.group(1)) for line in lines if (m := re.match(r"(\d+)\s", line))}


def pick(ready: list[dict], reviewed: set[int], today: str = "") -> dict | None:
    if len(ready) < REVIEW_WHEN_READY:
        return None
    todo = [r for r in ready if int(r["post_id"]) not in reviewed]
    if today and today < MONITOR_REVIEWS_UNTIL and len(todo) <= MONITOR_BACKLOG:
        return None  # 12/5 까지는 감시 세션이 검수 — 글쓰기 작업은 새 글
    return {**todo[0], "ready": len(ready), "unreviewed": len(todo)} if todo else None


# ── 공개 직전 재검수 (11/6 운영자 '공개 전에 한 번 더 — 그렇게 만들어라') ──
# 대기 글은 쓴 지 몇 주 뒤에 공개된다. 공개 직전 확인(bot/freshness.py)은 코드라 숫자 없는 변화(신청 방법·대상 조건)와
# 해 바뀜(페이지가 2026·2027 값을 같이 보여 줌)을 못 잡는다 → 공개 순서상 다음 글을 글쓰기 작업이 외부 검수와 같은 절차로 다시 본다.
# 공개(bot/schedule.publish_next)는 재검수를 마친 글을 먼저 내보내고, 재검수된 글이 없으면 예전처럼 코드 확인만으로 공개 (공개가 멈추지 않게).
PREPUBLISH_META = "_firstlab_prepublish_at"   # 재검수한 날 (bot/publish_queue 가 review_done(prepublish) 받으면 기록)
PREPUBLISH_FRESH_DAYS = 3                     # 이 기간 안에 재검수했으면 다시 안 함 (넘으면 공개 전에 또)

_PREPUBLISH_PHP = r"""
$out = array();
foreach ( array( IDS ) as $id ) {
    if ( $id ) { $out[ $id ] = (string) get_post_meta( $id, '_firstlab_prepublish_at', true ); }
}
echo wp_json_encode( $out );
"""


def fresh(date: str, today: str) -> bool:
    """재검수가 아직 유효한가 (오늘 포함 PREPUBLISH_FRESH_DAYS 일 안)."""
    import datetime as dt
    try:
        return 0 <= (dt.date.fromisoformat(today) - dt.date.fromisoformat(str(date)[:10])).days <= PREPUBLISH_FRESH_DAYS
    except ValueError:
        return False


def prepublish_dates(ids: list[int], wp=None) -> dict[int, str]:
    """초안 번호 → 재검수한 날 ('' = 안 함). 한 번의 조회로."""
    if not ids:
        return {}
    if wp is None:
        from .settings import wp_cli as wp
    code = _PREPUBLISH_PHP.replace("IDS", ",".join(str(int(i)) for i in ids))  # 정수만 (runuser 는 환경 변수를 안 넘길 수 있음)
    try:
        got = json.loads(wp("eval", code) or "{}")
    except Exception:  # noqa: BLE001 — 못 읽으면 재검수 안 한 것으로
        return {}
    return {int(k): str(v) for k, v in (got.items() if isinstance(got, dict) else [])}


def prepublish_pick(order: list[int], dates: dict[int, str], titles: dict[int, str], today: str, ahead: int) -> dict | None:
    """공개 순서 앞 ahead 편(= 하루 공개 수) 중 재검수가 유효하지 않은 첫 글."""
    for pid in order[:max(ahead, 1)]:
        if not fresh(dates.get(pid, ""), today):
            return {"post_id": pid, "title": titles.get(pid, ""), "kind": "prepublish"}
    return None


def prefer_prepublished(order: list[int], dates: dict[int, str], today: str) -> list[int]:
    """공개 순서에서 재검수가 유효한 글을 앞으로 (나머지 순서는 그대로)."""
    return sorted(order, key=lambda pid: not fresh(dates.get(pid, ""), today))


def target(today: str | None = None, ahead: int = 1, now=None) -> dict | None:
    """글쓰기 작업의 대기 글 검수 대상: 공개 직전 재검수가 먼저, 없으면 외부 검수 안 된 대기 글 (12/5 뒤·감시가 밀렸을 때)."""
    import datetime as dt
    from .settings import wp_cli
    today = today or f"{dt.date.today():%Y-%m-%d}"
    ready = json.loads(wp_cli("eval", _READY_PHP) or "[]")
    try:
        from . import schedule
        ids = [int(r["post_id"]) for r in ready]
        from zoneinfo import ZoneInfo
        order = schedule.publish_order(ids, now or dt.datetime.now(ZoneInfo("Asia/Seoul")))
        got = prepublish_pick(order, prepublish_dates(order[:max(ahead, 1)]), {int(r["post_id"]): r["title"] for r in ready}, today, ahead)
        if got:
            return {**got, "ready": len(ready)}
    except Exception:  # noqa: BLE001 — 순서를 못 구하면 예전 대상만
        pass
    return pick(ready, reviewed_ids(), today)
