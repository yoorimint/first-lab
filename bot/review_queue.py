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


def target(today: str | None = None) -> dict | None:
    import datetime as dt
    from .settings import wp_cli
    ready = json.loads(wp_cli("eval", _READY_PHP) or "[]")
    return pick(ready, reviewed_ids(), today or f"{dt.date.today():%Y-%m-%d}")
