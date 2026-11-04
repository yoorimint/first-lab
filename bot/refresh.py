"""발행된 글 갱신 (주당 개수 제한, 갱신할 가치가 있는 글만).

우선순위
  0) 제목 클릭률 낮음 (서치콘솔): 평균 10위 안 + 노출 100회 이상 + 클릭률 2% 미만 → 제목·요약문만 다시 쓰기
     (순위는 괜찮은데 검색 결과에서 안 눌리는 글. 실제 검색어를 보고 제목을 맞춘다. 기준값은 경험칙)
  1) 순위 정체·하락 (서치콘솔): 평균 5~20위 + 노출 30회 이상, 또는 28일 전보다 노출 30%↓ → 내용 보강
  2) 같은 주제 검색어가 쌓인 글: 새 글 대신 이 글에 답을 보강 (select_keyword 가 같은 주제 새 글을 막음)
  3) 해마다 바뀌는 정보: 1월·7월에 세금·지원금·요금 등 → 수치 재검증
  4) 오래된 글: 발행 180일 이상 + 최근 노출 있음 + 수치 포함 → 수치 재검증
건드리지 않음: 3위 안에 드는 글(1번 예외 없음), 노출 없는 글, 최근 갱신한 글
비용: 재검증 = Haiku + 검색 (약 30~70원), 보강 = 글쓰기 모델 + 검색 (Opus 기준 약 700원)

작성 방식이 월정액(direct)이면 API 를 쓰지 않는다: 대상만 골라 state["refresh_target"] 에 두고,
매일 글쓰기 세션이 읽기 API 로 받아 고친 본문을 wordpress/queue 에 올리면 publish_queue 가 apply_direct 로 반영한다.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta

from . import factcheck
from .blocks import to_blocks
from .keyword_db import connect, norm
from .keyword_filter import subject
from .llm import make_llm
from .search_console import SCHEMA as GSC_SCHEMA
from .settings import BotSettings, wp_cli
from .writer import numbers_match
from .state import add_spend, spent_today

META_KEY = "_firstlab_refreshed"   # 마지막 갱신 날짜
MIN_KEEP_RATIO = 0.8              # 고친 본문이 기존의 80% 미만이면 반영하지 않음 (내용 유실 방지)
MIN_DAYS_BETWEEN = 60              # 같은 글은 이 기간 안에 다시 갱신하지 않음
RETITLE_MAX_POSITION = 10
RETITLE_MIN_IMPRESSIONS = 100
RETITLE_MAX_CTR = 0.02
TITLE_MAX = 60
STUCK_POSITION = (5, 20)
STUCK_MIN_IMPRESSIONS = 30
DECLINE_RATIO = 0.7                # 28일 전 대비 노출이 70% 이하로 떨어지면 하락
TOP_POSITION = 3
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
        'has_number' => (bool) preg_match( '/\d/', wp_strip_all_tags( $p->post_content ) ),
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
    subj = subject(norm(slug))
    if len(subj) < 3:
        return []
    rows = conn.execute("SELECT keyword, norm, total FROM keywords WHERE status IN ('new','ok') AND norm LIKE ?"
                        " ORDER BY total DESC LIMIT 200", (f"%{subj}%",)).fetchall()
    return [(r["keyword"], r["total"]) for r in rows if subject(r["norm"]) == subj][:limit]


def top_queries(conn, page: str, limit: int = 5) -> list[dict]:
    """이 글로 실제 들어온 검색어 (최근 28일, 노출 많은 순)."""
    conn.executescript(GSC_SCHEMA)
    rows = conn.execute("SELECT query, clicks, impressions, position FROM gsc_queries WHERE page=?"
                        " ORDER BY impressions DESC LIMIT ?", (page, limit)).fetchall()
    return [dict(r) for r in rows]


def pick_targets(conn, limit: int) -> list[tuple[dict, str, str]]:
    """(글, 방식 'retitle'|'enhance'|'recheck', 이유) 목록."""
    conn.executescript(GSC_SCHEMA)
    posts = json.loads(wp_cli("eval", _POSTS_PHP) or "[]")
    now = datetime.now()
    recent = (now - timedelta(days=MIN_DAYS_BETWEEN)).strftime("%Y-%m-%d")
    picks: list[tuple[int, dict, str, str]] = []
    for p in posts:
        if p["refreshed"] and p["refreshed"] >= recent:
            continue
        latest, older = _history(conn, p["url"])
        p["siblings"] = siblings(conn, p["url"])
        sib_volume = sum(v for _, v in p["siblings"])
        imp = latest["impressions"] if latest else 0
        pos = latest["position"] if latest else 999
        ctr = (latest["clicks"] / imp) if latest and imp else 0.0
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


def _queries_text(queries: list[dict]) -> str:
    return "\n".join(f"- {q['query']} (노출 {q['impressions']}, 클릭 {q['clicks']}, 평균 {q['position']:.0f}위)"
                     for q in queries) or "- (검색어 기록 없음)"


def _html(content: str) -> str:
    return re.sub(r"<!--.*?-->", "", content, flags=re.S).strip()


def refresh_post(post: dict, mode: str, settings: BotSettings) -> tuple[str, float]:
    """(결과 설명, 쓴 비용 달러)"""
    content = _html(wp_cli("post", "get", str(post["id"]), "--field=post_content"))
    if mode == "retitle":
        llm = make_llm(settings.provider, settings.api_key, settings.model)
        raw = llm.generate(RETITLE_SYSTEM, f"현재 제목: {post['title']}\n[실제 검색어]\n{_queries_text(post.get('queries', []))}"
                                           f"\n\n[본문 HTML]\n{content}")
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        title, excerpt = _checked_title(data, content)
        _save(post["id"], None, title, excerpt)
        cost = getattr(llm, "cost_usd", 0.0)
        return f"제목 수정 → '{title}' (약 ${cost:.2f})", cost
    if mode == "recheck":
        llm = make_llm(settings.provider, settings.api_key, settings.model, light=True)
        fc = factcheck.verify(llm, post["title"], content)
        new_html, note = fc.content_html, fc.summary
        changed = fc.fixed + fc.removed > 0
    else:
        llm = make_llm(settings.provider, settings.api_key, settings.model)
        related = ", ".join(k for k, _ in post.get("siblings", []))
        extra = f"\n이 글에서 함께 답할 검색어 (빠진 내용이면 보강): {related}" if related else ""
        raw = llm.generate(ENHANCE_SYSTEM, f"글 제목: {post['title']}{extra}\n\n[기존 본문 HTML]\n{content}",
                           web_search=True)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        new_html = str(data.get("content_html") or "")
        changed = len(new_html) >= len(content) * MIN_KEEP_RATIO
        note = str(data.get("changes", ""))[:80]
    _save(post["id"], new_html if changed else None)
    cost = getattr(llm, "cost_usd", 0.0)
    return f"{'수정' if changed else '변경 없음'} - {note} (약 ${cost:.2f})", cost


def _checked_title(data: dict, content: str) -> tuple[str, str]:
    """새 제목·요약문 검사: 길이, 숫자가 본문에 있는지. 통과 못 하면 RuntimeError (반영 안 함)."""
    title, excerpt = str(data.get("title") or "").strip(), str(data.get("excerpt") or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise RuntimeError(f"새 제목이 비었거나 {TITLE_MAX}자 초과 → 반영 안 함")
    for label, text in (("제목", title), ("요약문", excerpt)):
        if text and not numbers_match(text, content):
            raise RuntimeError(f"새 {label}의 숫자가 본문에 없음 ('{text[:40]}') → 반영 안 함")
    return title, excerpt


def _save(post_id: int, new_html: str | None, title: str = "", excerpt: str = "") -> None:
    """본문(None 이면 그대로)·제목·요약문(빈 값이면 그대로) 반영 + 갱신 날짜 기록.
    제목이 바뀌면 대표 이미지(제목 카드)는 1분마다 도는 봇이 다시 만든다 (run._sync_recent_cards)."""
    if new_html is not None:
        wp_cli("post", "update", str(post_id), f"--post_content={to_blocks(new_html)}")
    fields = [f"--post_title={title}"] * bool(title) + [f"--post_excerpt={excerpt}"] * bool(excerpt)
    if fields:
        wp_cli("post", "update", str(post_id), *fields)
    wp_cli("post", "meta", "update", str(post_id), META_KEY, datetime.now().strftime("%Y-%m-%d"))


def _week() -> str:
    return datetime.now().strftime("%G-W%V")


def _count_done(state: dict) -> None:
    week = _week()
    state["refresh"] = {week: state.get("refresh", {}).get(week, 0) + 1}


def plan_direct(state: dict) -> list[str]:
    """월정액 방식: 오늘 갱신할 글을 골라 두기만 한다 (매일 글쓰기 세션이 처리)."""
    with connect() as conn:
        targets = pick_targets(conn, 1)
    if not targets:
        state.pop("refresh_target", None)
        return []
    post, mode, why = targets[0]
    with connect() as conn:
        queries = top_queries(conn, post["url"])
    state["refresh_target"] = {"id": post["id"], "title": post["title"], "url": post["url"], "mode": mode,
                               "why": why, "siblings": [k for k, _ in post.get("siblings", [])], "queries": queries}
    return [f"글 갱신 대상 준비 (매일 글쓰기 세션이 처리): '{post['title']}' - {why}"]


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
    title, excerpt = _checked_title(data, new_html or old) if data.get("title") else ("", "")
    if new_html is None and not title:
        raise RuntimeError("바꿀 내용(content_html 또는 title)이 없음")
    _save(post_id, new_html, title, excerpt)
    _count_done(state)
    target = state.get("refresh_target") or {}
    if target.get("id") == post_id:
        state.pop("refresh_target", None)
    title = wp_cli("post", "get", str(post_id), "--field=post_title").strip()
    return f"'{title}' - {target.get('why', '')} → 수정 - {str(data.get('changes', ''))[:80]}"


def run_weekly(settings: BotSettings, state: dict) -> list[str]:
    """하루 1번 호출. 이번 주(월요일 시작) 갱신 수가 한도 미만이면 최대 1개 갱신 (주중에 고르게 분산)."""
    done = state.setdefault("refresh", {}).get(_week(), 0)
    if settings.refresh_per_week <= done:
        state.pop("refresh_target", None)
        return []
    if settings.write_mode == "direct":
        return plan_direct(state)
    if not settings.api_key:
        return []
    if settings.daily_cost_cap > 0 and spent_today(state) >= settings.daily_cost_cap:
        return [f"글 갱신 건너뜀: 오늘 AI 비용 하루 상한 ${settings.daily_cost_cap:.0f} 도달"]
    with connect() as conn:
        targets = pick_targets(conn, 1)
        for post, _, _ in targets:
            post["queries"] = top_queries(conn, post["url"])
    logs = []
    for post, mode, why in targets:
        try:
            result, cost = refresh_post(post, mode, settings)
            add_spend(state, cost)
            kind = {"retitle": "제목", "enhance": "보강"}.get(mode, "재검증")
            logs.append(f"글 갱신({kind}): '{post['title']}' - {why} → {result}")
        except Exception as e:  # noqa: BLE001
            logs.append(f"글 갱신 실패: '{post['title']}' - {e}")
        _count_done(state)
    return logs
