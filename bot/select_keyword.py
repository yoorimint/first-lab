"""키워드 창고에서 다음 글감 고르기: 필터 → 점수 → 중복 제외 → AI 안전 검사."""
from __future__ import annotations

import random

from .competition import check as check_competition
from .keyword_db import competition_checks_today, norm, set_competition, set_status
from .keyword_filter import is_allowed
from .safety import classify
from .scoring import find_head, score
from .settings import BotSettings

SAFETY_BATCH = 15  # 한 번의 AI 호출로 검사할 후보 수
PICK_FROM_TOP = 3  # 통과한 상위 N개 중 무작위 → 매번 같은 유형만 쓰지 않게
PER_HEAD = 2       # 같은 원 키워드(헤드)에서 나온 후보는 상위 목록에 최대 N개 (주제 쏠림 방지)
COMP_WEIGHT = 3.0  # 경쟁 난이도 반영: 점수 += COMP_WEIGHT × (0.5 - 난이도)  → 쉬우면 가산, 어려우면 감점
TOO_HARD = 0.8     # 이 이상이면 글감에서 제외 (상위 대부분이 대형 사이트)
COMP_PER_RUN = 3   # 글 1개 쓸 때 경쟁도를 새로 조회할 최대 후보 수


def ranked_candidates(conn, settings: BotSettings, limit: int = 50) -> list[dict]:
    """점수순 후보 (안전 검사 전). 이미 쓴 글과 겹치는 키워드는 제외."""
    rows = conn.execute(
        "SELECT * FROM keywords WHERE status IN ('new','ok') AND total BETWEEN ? AND ?",
        (settings.min_volume, settings.max_volume),
    ).fetchall()
    used = [r["norm"] for r in conn.execute("SELECT norm FROM keywords WHERE status='used'")]
    volumes = {r["norm"]: r["total"] for r in conn.execute("SELECT norm, total FROM keywords")}
    hints = [norm(h) for h in settings.hint_keywords]

    def overlaps_used(n: str) -> bool:  # '다이어트식단' vs '다이어트식단표' 같은 중복 방지
        return any(u in n or n in u for u in used if len(u) >= 3)

    result = []
    for r in rows:
        if not is_allowed(r["keyword"], settings.blocked_words) or overlaps_used(r["norm"]):
            continue
        head = find_head(r["norm"], r["total"], volumes)
        sc = score(r["keyword"], r["norm"], r["total"], r["ad_comp"], r["ad_clicks"], r["prev_total"], head)
        if r["difficulty"] is not None:
            if r["difficulty"] >= TOO_HARD:
                continue
            sc += COMP_WEIGHT * (0.5 - r["difficulty"])
        if hints and any(h in r["norm"] for h in hints):
            sc += 100  # 힌트 키워드 관련이면 최우선
        result.append({"keyword": r["keyword"], "total": r["total"], "score": round(sc, 2),
                       "status": r["status"], "head": head or "",
                       "difficulty": r["difficulty"], "comp_info": r["comp_info"] or ""})
    result.sort(key=lambda x: x["score"], reverse=True)
    picked, per_head = [], {}
    for c in result:
        key = c["head"] or c["keyword"]
        if per_head.get(key, 0) >= PER_HEAD:
            continue
        per_head[key] = per_head.get(key, 0) + 1
        picked.append(c)
        if len(picked) >= limit:
            break
    return picked


def _check_competition(conn, settings: BotSettings, candidates: list[dict], limit: int) -> None:
    """경쟁도 미조회 후보를 하루 한도 안에서 조회해 DB·후보 목록에 반영."""
    if not settings.gemini_key:
        return
    budget = min(limit, settings.competition_checks - competition_checks_today(conn))
    for c in [c for c in candidates if c["difficulty"] is None][:max(budget, 0)]:
        try:
            comp = check_competition(settings.gemini_key, c["keyword"])
        except Exception as e:  # noqa: BLE001 - 경쟁도 조회 실패는 글쓰기를 막지 않음
            print(f"[경쟁도] {c['keyword']} 실패: {e}", flush=True)
            break
        info = f"{comp.summary}: {', '.join(comp.domains)}"
        set_competition(conn, c["keyword"], comp.difficulty, info)
        c["difficulty"], c["comp_info"] = comp.difficulty, info
        c["score"] = round(c["score"] + COMP_WEIGHT * (0.5 - comp.difficulty), 2)
        print(f"[경쟁도] {c['keyword']}: {comp.difficulty} {info}", flush=True)
    conn.commit()


def _safety_check(conn, llm, batch: list[dict]) -> None:
    unchecked = [c["keyword"] for c in batch if c["status"] == "new"]
    if not unchecked:
        return
    for kw, (ok, reason) in classify(llm, unchecked).items():
        set_status(conn, kw, "ok" if ok else "rejected", reason)
        for c in batch:
            if c["keyword"] == kw:
                c["status"] = "ok" if ok else "rejected"
    conn.commit()


def review_top(conn, settings: BotSettings, llm, count: int = 10) -> int:
    """하루 1회: 상위 후보를 미리 안전 검사 + 경쟁도 조회 (설정 페이지에서 근거 확인용). 조회 수 반환."""
    before = competition_checks_today(conn)
    candidates = ranked_candidates(conn, settings, limit=count)
    _safety_check(conn, llm, candidates)
    _check_competition(conn, settings, [c for c in candidates if c["status"] == "ok"], limit=count)
    return competition_checks_today(conn) - before


def pick_keyword(conn, settings: BotSettings, llm) -> str | None:
    """안전 검사 통과 → 경쟁도 조회 → 너무 어려운 것 제외 → 최종 점수 상위 중 무작위."""
    candidates = ranked_candidates(conn, settings, limit=SAFETY_BATCH * 2)
    for start in range(0, len(candidates), SAFETY_BATCH):
        batch = candidates[start:start + SAFETY_BATCH]
        _safety_check(conn, llm, batch)
        passed = [c for c in batch if c["status"] == "ok"]
        _check_competition(conn, settings, passed, limit=COMP_PER_RUN)
        passed = sorted((c for c in passed if (c["difficulty"] or 0) < TOO_HARD),
                        key=lambda c: c["score"], reverse=True)
        if passed:
            return random.choice(passed[:PICK_FROM_TOP])["keyword"]
    return None

if __name__ == "__main__":
    # 점검용: python3 -m bot.select_keyword  → 다음 글감 후보 상위 20개
    from .keyword_db import connect
    from .settings import load_settings
    from .textfmt import ljust, rjust

    with connect() as conn:
        for c in ranked_candidates(conn, load_settings(), limit=20):
            print(ljust(c["keyword"], 28) + rjust(f"{c['total']:,}", 9) + rjust(str(c["score"]), 7) + "  " + c["status"])
