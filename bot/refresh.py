"""발행된 글 갱신 (주당 개수 제한, 갱신할 가치가 있는 글만).

우선순위
  0) 제목 클릭률 낮음 (서치콘솔): 평균 10위 안 + 노출 100회 이상 + 클릭률 2% 미만 → 제목·요약문만 다시 쓰기
     (순위는 괜찮은데 검색 결과에서 안 눌리는 글. 실제 검색어를 보고 제목을 맞춘다. 기준값은 경험칙)
  1) 순위 정체·하락 (서치콘솔): 평균 5~20위 + 노출 30회 이상, 또는 28일 전보다 노출 30%↓ → 내용 보강
  2) 같은 주제 검색어가 쌓인 글: 새 글 대신 이 글에 답을 보강 (select_keyword 가 같은 주제 새 글을 막음)
  3) 해마다 바뀌는 정보: 1월·7월에 세금·지원금·요금 등 → 수치 재검증
  4) 오래된 글: 발행 180일 이상 + 최근 노출 있음 + 수치 포함 → 수치 재검증
건드리지 않음: 잘 나가는 글(is_strong — 검색 평균 3위 안·클릭률 2% 이상, 순위와 상관없이 클릭이 두 기간 꾸준함,
  또는 방문 30회 이상에 참여율 65% 이상),
  노출 없는 글, 최근 갱신한 글. 잘 나가는 글도 사실이 틀려진 경우(근거 변경·확인 기한·수치 재검증)는 고친다 — 보강·제목 바꾸기만 안 함
  (11/11 운영자 '데이터 모이면 잘 나가는 기존 글은 수정 안 하지?')
비용: 재검증 = Haiku + 검색 (약 30~70원), 보강 = 글쓰기 모델 + 검색 (Opus 기준 약 700원)

작성 방식이 월정액(direct)이면 API 를 쓰지 않는다: 대상만 골라 state["refresh_target"] 에 두고,
매일 글쓰기 세션이 읽기 API 로 받아 고친 본문을 wordpress/queue 에 올리면 publish_queue 가 apply_direct 로 반영한다.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta

from . import changed, expiry
from .blocks import to_blocks
from .keyword_db import connect, norm
from .keyword_filter import subject, subject_intent
from .search_console import SCHEMA as GSC_SCHEMA
from .settings import BotSettings, wp_cli
from .writer import numbers_match

META_KEY = "_firstlab_refreshed"   # 마지막 갱신 날짜
MIN_KEEP_RATIO = 0.8              # 고친 본문이 기존의 80% 미만이면 반영하지 않음 (내용 유실 방지)
MIN_DAYS_BETWEEN = 60              # 같은 글은 이 기간 안에 다시 갱신하지 않음
RETITLE_MAX_POSITION = 10
RETITLE_MIN_IMPRESSIONS = 100
RETITLE_MAX_CTR = 0.02
TITLE_MAX = 60
PLUS_META = "_firstlab_plus_at"   # +@ 보강한 날 (11/11 운영자)
STUCK_POSITION = (5, 20)
STUCK_MIN_IMPRESSIONS = 30
DECLINE_RATIO = 0.7                # 28일 전 대비 노출이 70% 이하로 떨어지면 하락
TOP_POSITION = 3
STRONG_MIN_IMPRESSIONS = 30        # 이만큼 노출된 글만 '잘 나감' 판단 (몇 번 노출로 판단하면 틀림)
STRONG_MIN_CTR = 0.02              # 3위 안이어도 클릭률이 이보다 낮으면 제목 손볼 여지가 있는 글 (retitle 기준과 같음)
STEADY_CLICKS = 20                 # 순위와 상관없이 최근 28일·그 전 28일 모두 클릭이 이만큼이면 '꾸준히 들어오는 글' (경험칙)
# 11/11 운영자 '그 기준에 안 들어도 실제 돈이 꾸준히 들어오는 글도 있을 거 아냐' — 글별 수익은 애드센스 승인 뒤 기록(운영자 11/9
# '승인 나면 그때'), 그날 수익이 꾸준한 글도 여기 기준에 더함 (automation/monitor.md 애드센스 승인 날 할 일)
OLD_DAYS = 180
YEARLY_MONTHS = (1, 7)
SIBLING_MIN_COUNT = 3              # 같은 주제 검색어가 이만큼 쌓이면 보강 대상
SIBLING_MIN_VOLUME = 3000          # 그 검색어들의 월 검색량 합
SIBLING_MIN_AGE_DAYS = 14          # 발행 직후 글은 건드리지 않음
YEARLY_WORDS = ("세금", "연말정산", "소득세", "지원금", "급여", "수당", "최저임금", "보험료", "요금",
                "연금", "신청기간", "공제", "금리", "환급")

_POSTS_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_status' => 'publish', 'numberposts' => 500 ) ) as $p ) {
    $out[] = array(
        'id' => $p->ID, 'title' => $p->post_title, 'url' => get_permalink( $p ),
        'date' => get_the_date( 'Y-m-d', $p ),
        'refreshed' => (string) get_post_meta( $p->ID, '_firstlab_refreshed', true ),
        'law_changed' => (string) get_post_meta( $p->ID, '_firstlab_law_changed', true ),
        'verified' => (string) get_post_meta( $p->ID, '_firstlab_verified_at', true ),
        'has_number' => (bool) preg_match( '/\d/', wp_strip_all_tags( $p->post_content ) ),
        'text' => mb_substr( wp_strip_all_tags( $p->post_content ), 0, 30000 ),
        'source_check' => (string) get_post_meta( $p->ID, '_firstlab_source_check', true ),
    );
}
echo wp_json_encode( $out );
"""

ENHANCE_SYSTEM = """너는 한국어 정보 블로그 편집자다. 이미 발행된 글을 검색 순위가 오르도록 개선한다.
1. 웹 검색으로 이 주제의 최신 정보와 검색 상위 글들이 다루는 하위 질문을 확인한다. 수치·기준은 공식 출처(정부·공공기관)를 먼저 찾고, 다른 자료와 다르면 공식 값을 따른다.
2. 기존 글에 빠진 하위 질문·구체적 수치·계산 예시를 보강하고, 바뀐 수치는 최신화한다.
3. 기존 구조·말투·제목은 유지한다. 이미지(figure) 태그와 링크(a) 태그는 그대로 둔다.
4. 출처 목록·기준일 표기·'AI'·'검색해 보니' 같은 말은 넣지 않는다.
최종 답은 JSON 하나만: {"content_html": "개선된 본문 HTML 전체", "changes": "무엇을 바꿨는지 한 줄"}"""


def _history(conn, page: str) -> tuple[dict | None, dict | None]:
    """(최근 기록, 약 28일 전 기록)"""
    rows = conn.execute("SELECT * FROM gsc_page_history WHERE page=? ORDER BY date DESC", (page,)).fetchall()
    if not rows:
        return None, None
    latest = rows[0]
    cutoff = (datetime.strptime(latest["date"], "%Y-%m-%d") - timedelta(days=28)).strftime("%Y-%m-%d")
    older = next((r for r in rows if r["date"] <= cutoff), None)
    return dict(latest), (dict(older) if older else None)


def siblings(conn, post_url: str, limit: int = 8) -> list[tuple[str, int]]:
    """글 주소(=키워드)와 같은 주제인, 아직 안 쓴 검색어 (검색량순)."""
    slug = urllib.parse.unquote(post_url.rstrip("/").rsplit("/", 1)[-1])
    subj = subject_intent(norm(slug))   # 주제·검색 목적이 같은 것만 (목적이 다르면 새 글 — select_keyword)
    if len(subj[0]) < 3:
        return []
    rows = conn.execute("SELECT keyword, norm, total FROM keywords WHERE status IN ('new','ok') AND norm LIKE ?"
                        " ORDER BY total DESC LIMIT 200", (f"%{subj[0]}%",)).fetchall()
    return [(r["keyword"], r["total"]) for r in rows if subject_intent(r["norm"]) == subj][:limit]


def gold_sibling(hub: dict, sibs: list[tuple[str, int]]) -> str:
    """같은 주제·목적 검색어 중 황금 키워드 (11/5 운영자: 황금 키워드는 제목·본문에 그 말이 있어야 먹는다 → 바로 보강)."""
    from . import naver_hub
    for kw, total in sibs:
        if naver_hub.is_gold(hub.get(norm(kw)), total, kw):
            return kw
    return ""


def title_has_keyword(title: str, keyword: str) -> bool:
    """제목에 그 검색어의 주제와 목적 말이 다 있는지 ('주휴수당 뜻과 계산: …' ↔ 주휴수당뜻)."""
    subj, _ = subject_intent(norm(keyword))
    tail = norm(keyword)[len(subj):] if norm(keyword).startswith(subj) else ""
    t = norm(title)
    return subj in t and (not tail or tail in t)


def top_queries(conn, page: str, limit: int = 5) -> list[dict]:
    """이 글로 실제 들어온 검색어 (최근 28일, 노출 많은 순)."""
    conn.executescript(GSC_SCHEMA)
    rows = conn.execute("SELECT query, clicks, impressions, position FROM gsc_queries WHERE page=?"
                        " ORDER BY impressions DESC LIMIT ?", (page, limit)).fetchall()
    return [dict(r) for r in rows]


def due_for(post: dict, today) -> tuple | None:
    """(다시 확인할 날, 이유) — 그날이 지났을 때만. 기록을 못 읽으면 기본 주기로."""
    try:
        record = json.loads(post.get("source_check") or "{}")
    except ValueError:
        record = {}
    day, why = expiry.due_date(str(post.get("text", "")), expiry.last_checked(post), expiry.unreadable(record))
    return (day, why) if day <= today else None


RECHECK_PER_DAY = 3   # 기한 지난 글 재검증을 하루 이만큼까지 이어서 (글쓰기 월정액 사용량 — 늘리려면 이 값)


def is_strong(latest: dict | None, url: str = "", strong_pages=(), older: dict | None = None) -> str:
    """잘 나가는 글이면 그 이유 (보강·제목 바꾸기를 하지 않음). 사실 고치기는 따로 — 이 판단과 상관없이 함."""
    if latest and older and latest["clicks"] >= STEADY_CLICKS and older["clicks"] >= STEADY_CLICKS:
        return f"클릭이 꾸준함 (최근 28일 {latest['clicks']}회, 그 전 {older['clicks']}회)"
    if latest and latest["impressions"] >= STRONG_MIN_IMPRESSIONS and latest["position"] <= TOP_POSITION:
        ctr = latest["clicks"] / latest["impressions"]
        if ctr >= STRONG_MIN_CTR:
            return f"검색 평균 {latest['position']:.1f}위·클릭률 {ctr:.1%}"
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path or "")
    if path and path in {urllib.parse.unquote(x) for x in strong_pages or ()}:
        return "방문 30회 이상에 참여율 65% 이상 (GA4)"
    return ""


def strong_pages_of(state: dict | None) -> list[str]:
    return list(((state or {}).get("ga4") or {}).get("high_engagement") or [])


def pick_targets(conn, limit: int, fixes_only: bool = False, strong_pages=()) -> list[tuple[dict, str, str]]:
    """(글, 방식 'retitle'|'enhance'|'recheck', 이유) 목록.
    fixes_only: 근거가 바뀐 글(bot/changed.py 표시)만 — 주당 갱신 한도를 다 써도 틀려진 글은 고친다."""
    conn.executescript(GSC_SCHEMA)
    posts = json.loads(wp_cli("eval", _POSTS_PHP) or "[]")
    now = datetime.now()
    recent = (now - timedelta(days=MIN_DAYS_BETWEEN)).strftime("%Y-%m-%d")
    picks: list[tuple[float, dict, str, str]] = []
    try:
        from . import naver_hub
        hub = naver_hub.load(conn)   # 황금 키워드 신호 (한 번만 읽음)
    except Exception:  # noqa: BLE001 - 신호 표가 없으면 예전 그대로
        hub = {}
    for p in posts:
        law = p.get("law_changed") or ""
        if changed.pending(law, max(p["refreshed"] or "", p.get("verified") or "")):  # 근거가 마지막 확인 뒤에 바뀜
            picks.append((-1, p, "recheck", f"근거 변경: {law}"))
            continue
        due = due_for(p, now.date())
        if due:  # 다시 확인할 날이 지남 (bot/expiry.py) — 틀려졌을 수 있으니 한도와 상관없이, 오래 밀린 글부터
            picks.append((-0.5 - min((now.date() - due[0]).days, 900) / 1000, p, "recheck", f"확인 기한 {due[0]} 지남 — {due[1]}"))
            continue
        if fixes_only:
            continue
        latest, older = _history(conn, p["url"])
        strong = is_strong(latest, p["url"], strong_pages, older)
        p["siblings"] = siblings(conn, p["url"])
        gold = "" if strong else gold_sibling(hub, p["siblings"])
        if gold:  # 최근에 갱신했어도 — 보강하면 그 키워드는 '사용됨'이 되어 다시 안 뽑힘 (apply_direct)
            p["gold_keyword"] = gold
            picks.append((0.5, p, "enhance", f"황금 키워드 '{gold}' — 같은 주제·목적이라 새 글 대신 이 글을 보강 (제목·본문에 그 말)"))
            continue
        if p["refreshed"] and p["refreshed"] >= recent:
            continue
        sib_volume = sum(v for _, v in p["siblings"])
        imp = latest["impressions"] if latest else 0
        pos = latest["position"] if latest else 999
        ctr = (latest["clicks"] / imp) if latest and imp else 0.0
        if strong:  # 잘 나가는 글은 보강·제목 바꾸기 안 함 — 아래 수치 재검증(사실 확인)만
            if (now.month in YEARLY_MONTHS and p["has_number"] and any(w in p["title"] for w in YEARLY_WORDS)
                    and not (p["refreshed"] or "").startswith(now.strftime("%Y-%m"))):
                picks.append((3, p, "recheck", f"{now.month}월 제도·요금 변경 시기 (잘 나가는 글: {strong} — 수치만 확인)"))
            continue
        if latest and imp >= RETITLE_MIN_IMPRESSIONS and pos <= RETITLE_MAX_POSITION and ctr < RETITLE_MAX_CTR:
            picks.append((0, p, "retitle", f"평균 {pos:.0f}위인데 클릭률 {ctr:.1%} (노출 {imp})"))
        elif latest and imp >= STUCK_MIN_IMPRESSIONS and STUCK_POSITION[0] <= pos <= STUCK_POSITION[1]:
            picks.append((1, p, "enhance", f"평균 {pos:.0f}위 정체 (노출 {imp})"))
        elif latest and older and older["impressions"] >= STUCK_MIN_IMPRESSIONS and imp <= older["impressions"] * DECLINE_RATIO:
            picks.append((1, p, "enhance", f"노출 하락 {older['impressions']}→{imp}"))
        elif (len(p["siblings"]) >= SIBLING_MIN_COUNT and sib_volume >= SIBLING_MIN_VOLUME
              and p["date"] <= (now - timedelta(days=SIBLING_MIN_AGE_DAYS)).strftime("%Y-%m-%d")):
            picks.append((2, p, "enhance", f"같은 주제 검색어 {len(p['siblings'])}개 보강 (월 {sib_volume:,}회)"))
        elif (now.month in YEARLY_MONTHS and p["has_number"] and any(w in p["title"] for w in YEARLY_WORDS)
              and not (p["refreshed"] or "").startswith(now.strftime("%Y-%m"))):
            picks.append((3, p, "recheck", f"{now.month}월 제도·요금 변경 시기"))
        elif (p["date"] <= (now - timedelta(days=OLD_DAYS)).strftime("%Y-%m-%d") and imp > 0
              and p["has_number"] and pos > TOP_POSITION):
            picks.append((4, p, "recheck", f"발행 {OLD_DAYS}일 경과"))
    picks.sort(key=lambda x: x[0])
    return [(p, mode, why) for _, p, mode, why in picks[:limit]]


RETITLE_SYSTEM = """너는 한국어 정보 블로그 편집자다. 검색 순위는 괜찮은데 검색 결과에서 잘 안 눌리는 글의 제목과 요약문을 다시 쓴다.
1. 실제 검색어 목록을 보고, 검색한 사람이 찾는 답이 이 글에 있다는 게 제목에서 바로 보이게 쓴다.
2. 제목과 요약문의 숫자는 본문에 있는 숫자만 쓴다. 본문에 없는 내용·과장·낚시성 표현은 쓰지 않는다.
3. 제목은 60자 이내, 요약문은 120자 이내.
최종 답은 JSON 하나만: {"title": "...", "excerpt": "...", "changes": "무엇을 바꿨는지 한 줄"}"""


def _html(content: str) -> str:
    return re.sub(r"<!--.*?-->", "", content, flags=re.S).strip()


def _checked_title(data: dict, content: str) -> tuple[str, str]:
    """새 제목·요약문 검사: 길이, 숫자가 본문에 있는지. 통과 못 하면 RuntimeError (반영 안 함)."""
    title, excerpt = str(data.get("title") or "").strip(), str(data.get("excerpt") or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise RuntimeError(f"새 제목이 비었거나 {TITLE_MAX}자 초과 → 반영 안 함")
    for label, text in (("제목", title), ("요약문", excerpt)):
        if text and not numbers_match(text, content):
            raise RuntimeError(f"새 {label}의 숫자가 본문에 없음 ('{text[:40]}') → 반영 안 함")
    return title, excerpt


def _save(post_id: int, new_html: str | None, title: str = "", excerpt: str = "", stamp: bool = True) -> None:
    """본문(None 이면 그대로)·제목·요약문(빈 값이면 그대로) 반영 + 갱신 날짜 기록.
    제목이 바뀌면 대표 이미지(제목 카드)는 1분마다 도는 봇이 다시 만든다 (run._sync_recent_cards)."""
    if new_html is not None:
        wp_cli("post", "update", str(post_id), f"--post_content={to_blocks(new_html)}")
    fields = [f"--post_title={title}"] * bool(title) + [f"--post_excerpt={excerpt}"] * bool(excerpt)
    if fields:
        wp_cli("post", "update", str(post_id), *fields)
    if stamp:
        wp_cli("post", "meta", "update", str(post_id), META_KEY, datetime.now().strftime("%Y-%m-%d"))


def _week() -> str:
    return datetime.now().strftime("%G-W%V")


def _count_done(state: dict) -> None:
    week = _week()
    state["refresh"] = {week: state.get("refresh", {}).get(week, 0) + 1}


def plan_direct(state: dict, fixes_only: bool = False) -> list[str]:
    """월정액 방식: 오늘 갱신할 글을 골라 두기만 한다 (매일 글쓰기 세션이 처리)."""
    with connect() as conn:
        targets = pick_targets(conn, 1, fixes_only, strong_pages_of(state))
    if not targets:
        state.pop("refresh_target", None)
        return []
    post, mode, why = targets[0]
    with connect() as conn:
        queries = top_queries(conn, post["url"])
    state["refresh_target"] = {"id": post["id"], "title": post["title"], "url": post["url"], "mode": mode,
                               "why": why, "siblings": [k for k, _ in post.get("siblings", [])], "queries": queries,
                               "gold_keyword": post.get("gold_keyword", "")}
    return [f"글 갱신 대상 준비 (매일 글쓰기 세션이 처리): '{post['title']}' - {why}"]


def _chain_next(state: dict) -> None:
    """재검증 하나가 끝나면 그날 RECHECK_PER_DAY 편까지 다음 '고칠 글'(근거 바뀜·확인 기한 지남)을 바로 준비 — 하루 1편이면
    글이 쌓일수록 기한 지난 글이 영영 밀림 (11/7). 못 고르면 다음 날 정기 선택이 다시 함."""
    today = datetime.now().strftime("%Y-%m-%d")
    days = state.get("recheck_day", {})
    count = days.get(today, 0) + 1
    state["recheck_day"] = {today: count}
    if count >= RECHECK_PER_DAY:
        return
    try:
        plan_direct(state, fixes_only=True)
    except Exception:  # noqa: BLE001 - 다음 날 정기 선택이 다시 함
        pass


def apply_direct(data: dict, state: dict) -> str:
    """글쓰기 세션이 올린 갱신 반영 (본문 / 제목·요약문, 있는 것만).
    그 사이 글이 수정됐거나, 본문이 크게 줄었거나, 새 제목 숫자가 본문에 없으면 반영하지 않는다."""
    post_id = int(data["post_id"])
    modified = wp_cli("post", "get", str(post_id), "--field=post_modified").strip()
    if data.get("base_modified") and data["base_modified"] != modified:
        raise RuntimeError(f"글 {post_id} 이 그 사이 수정됨 ({modified}) → 반영 안 함")
    old = _html(wp_cli("post", "get", str(post_id), "--field=post_content"))
    new_html = data.get("content_html")
    if new_html is not None:
        new_html = str(new_html)
        if len(new_html) < len(old) * MIN_KEEP_RATIO:
            raise RuntimeError(f"고친 본문이 너무 짧음 ({len(new_html)}자 < 기존 {len(old)}자의 80%) → 반영 안 함")
        from . import plain_style  # 고친 글도 쉬운 말 기준 (11/10 운영자 '새로 적거나 수정하면 새로운 방식대로')
        style = plain_style.problems(new_html)
        if style:
            raise RuntimeError(f"글 {post_id} 고친 본문이 쉬운 말 기준 미달 → 반영 안 함: {' / '.join(style[:3])}")
    if data.get("kind") == "plus":  # +@ 보강(automation/plus_check.py) — 상위 글 질문을 다 다뤘고 베끼지 않았는지 서버가 다시 봄
        from . import serp_gate
        failed = serp_gate.problems(data.get("serp"), new_html or "") + serp_gate.copied(data.get("serp"), new_html or "")
        if failed:
            raise RuntimeError(f"글 {post_id} +@ 보강이 상위 글 기준 미달 → 반영 안 함: {' / '.join(failed[:2])}")
    title, excerpt = _checked_title(data, new_html or old) if data.get("title") else ("", "")
    target = state.get("refresh_target") or {}
    gold = target.get("gold_keyword") if target.get("id") == post_id else ""
    if gold and not title_has_keyword(title or wp_cli("post", "get", str(post_id), "--field=post_title"), gold):
        raise RuntimeError(f"황금 키워드 '{gold}' 보강인데 제목에 그 말이 없음 → 반영 안 함 (제목에 '{gold}' 의 주제와 목적 말을 넣기)")
    if new_html is None and not title and data.get("checked") is not True:
        raise RuntimeError("바꿀 내용(content_html 또는 title)이 없음 (확인만 했으면 \"checked\": true)")
    # checked: 재검증해 보니 고칠 게 없음 → 갱신 날짜만 기록 (안 하면 근거 변경 표시가 안 풀려 매일 같은 글이 다시 뽑힘)
    plain = data.get("kind") in ("plain", "plus")  # +@ 보강도 새로 더한 부분만 확인한 것 → 확인한 날·주간 한도는 다듬기와 같게
    # 쉬운 말 다듬기는 사실을 다시 확인한 게 아님 → 확인한 날(갱신일·검수 통과일)을 오늘로 바꾸지 않음.
    # 바꾸면 공개 직전 지난 기한 검사(expiry.passed_rounds)·다시 확인할 날(expiry.due_date)이 미뤄짐 (11/10 #561 '11월 9일까지예요')
    _save(post_id, new_html, title, excerpt, stamp=not plain)
    if data.get("kind") == "plus":  # 보강 끝난 글 표시 (automation/plus_queue.py 가 건너뜀, /post 의 plus_at)
        wp_cli("post", "meta", "update", str(post_id), PLUS_META, datetime.now().strftime("%Y-%m-%d"))
    if new_html is not None:  # 다시 쓴 본문이 기준 — 그 전 패치는 배포 때 다시 덮어쓰지 않음 (bot/fix_posts.UPTO_META)
        from .fix_posts import UPTO_META, patch_count
        upto = data.get("patches_upto")
        wp_cli("post", "meta", "update", str(post_id), UPTO_META, str(int(upto) if str(upto).isdigit() else patch_count()))
    if not plain:
        _renew_evidence(post_id, data.get("review"))
    if not plain:  # 쉬운 말 다듬기(automation/plain_check.py)는 사실이 같아 주간 갱신 한도에서 안 셈 (11/10: 다듬기 16편이 한도를 채워 보강이 멈췄음)
        _count_done(state)
    if target.get("id") == post_id:
        state.pop("refresh_target", None)
        _chain_next(state)
    if gold:  # 이 글이 그 검색어를 맡음 → 다시 새 글·보강 대상으로 안 뽑힘
        from .keyword_db import set_status
        with connect() as conn:
            set_status(conn, gold, "used", f"글 {post_id} 보강으로 답함")
            conn.commit()
    title = wp_cli("post", "get", str(post_id), "--field=post_title").strip()
    return f"'{title}' - {target.get('why', '')} → 수정 - {str(data.get('changes', ''))[:80]}"


def _renew_evidence(post_id: int, review: list | None) -> None:
    """갱신한 글의 근거 기록을 새로 (11/5 검토: 옛 숫자가 기록에 남아 공개 글 다시 보기가 같은 글을 끝없이 다시 올림).
    검수 기록을 받았으면 그것으로 다시 대조, 없으면 지금 공식 페이지에 없는 숫자 항목만 기록에서 뺌. 확인한 날도 오늘로."""
    from . import freshness, source_check
    save = lambda pid, key, val: wp_cli("post", "meta", "update", pid, key, val)  # noqa: E731
    try:
        if review:
            source_check.record(post_id, review, save)
        else:
            from .settings import wp_meta
            record = json.loads(wp_meta(post_id, source_check.META, wp_cli) or "{}")
            kept = []
            for item in record.get("found", []):
                texts = [t for t in (source_check.fetch(u) for u in item.get("urls", [])) if t]
                if not texts or all(any(source_check.has_number(t, n) for t in texts) for n in item.get("numbers", [])):
                    kept.append(item)
            if len(kept) != len(record.get("found", [])):
                record["found"] = kept
                save(str(post_id), source_check.META, json.dumps(record, ensure_ascii=False))
        freshness.mark_verified(post_id)
    except Exception:  # noqa: BLE001 - 기록 정리가 실패해도 갱신은 반영됨
        pass


def run_weekly(settings: BotSettings, state: dict) -> list[str]:
    """하루 1번 호출. 갱신할 글 1개를 골라 두면 글쓰기 작업(월정액·API 봇 같은 지시서 6단계)이 고쳐 갱신 파일로 올림
    → publish_queue 의 refresh.apply_direct 관문. 이번 주 한도를 넘어도 근거가 바뀐 글은 계속 (틀린 채 두지 않음).
    (11/12: 예전 API 봇은 여기서 API 로 바로 고쳐 저장했음 — 관문 없이 반영되던 길이라 없앰)"""
    done = state.setdefault("refresh", {}).get(_week(), 0)
    return plan_direct(state, settings.refresh_per_week <= done)
