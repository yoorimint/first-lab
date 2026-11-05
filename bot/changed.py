"""바뀐 근거 표시 — 글이 기대는 사실(법령·금리·혜택 내용·계산기 값)이 바뀌면 글에 표시해 다시 확인하게 한다.

  공개 글: refresh.pick_targets 가 맨 먼저 재검증 (주당 갱신 한도와 상관없이, 하루 1편씩 글쓰기 세션이 처리)
  공개 전 초안: freshness.check_post 가 공개 직전에 보류 → 글쓰기 세션의 보류 수정으로 다시 확인
표시 값: 'YYYY-MM-DD 이유'. 아직 처리 안 된 앞 표시는 ' / ' 뒤에 붙여 둔다 (이유가 겹쳐도 하나도 안 잃게).
표시를 하는 곳: law_watch(근거 조문 개정 시행), bok_api(기준금리), benefit_watch(혜택 내용 변경), calc_watch(계산기 값 전환).
"""
from __future__ import annotations

META = "_firstlab_law_changed"      # 이름은 처음(법령 감시) 그대로 — 이미 쓰인 표시와 refresh·freshness 가 같이 읽음
LIVE = ("publish", "future", "draft")
MAX_LEN = 300


def merged(old: str, value: str) -> str:
    """새 표시를 앞에 (날짜 = 가장 최근 변경), 같은 표시는 한 번만."""
    old = (old or "").strip()
    if not old or value in old:
        return old or value
    return f"{value} / {old}"[:MAX_LEN]


def pending(mark: str, since: str) -> bool:
    """표시가 마지막 확인(갱신·검수 통과) 날짜보다 뒤면 아직 처리 안 됨."""
    # 표시가 여러 개 붙으면(' / ') 가장 늦은 날짜로 — 새 표시가 앞에 붙어도 날짜가 더 이를 수 있음 (11/5 검토)
    dates = [part.strip()[:10] for part in (mark or "").split(" / ") if part.strip()[:4].isdigit()]
    return bool(dates) and max(dates) > (since or "")


def flag(post_ids, value: str, wp=None) -> list[str]:
    """글들에 표시 (이미 같은 표시가 있으면 건너뜀). 표시한 글 번호."""
    if wp is None:
        from .settings import wp_cli as wp
    marked = []
    for pid in post_ids:
        old = wp("post", "meta", "get", str(pid), META).strip()
        new = merged(old, value)
        if new != old:
            wp("post", "meta", "update", str(pid), META, new)
            marked.append(str(pid))
    return marked


def live_posts_with(posts: dict[str, dict], test) -> list[str]:
    """wpbulk.posts() 중 공개·예약·초안 글에서 test(제목+본문) 가 참인 글 번호."""
    return [pid for pid, p in posts.items()
            if p.get("status") in LIVE and test(f"{p.get('title', '')}\n{p.get('content', '')}")]
