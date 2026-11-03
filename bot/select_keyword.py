"""키워드 창고에서 다음 글감 고르기: 필터 → 점수 → 중복 제외 → AI 안전 검사."""
from __future__ import annotations

import random

from .keyword_db import norm, set_status
from .keyword_filter import is_allowed
from .safety import classify
from .scoring import find_head, score
from .settings import BotSettings

SAFETY_BATCH = 15  # 한 번의 AI 호출로 검사할 후보 수
PICK_FROM_TOP = 3  # 통과한 상위 N개 중 무작위 → 매번 같은 유형만 쓰지 않게
PER_HEAD = 2       # 같은 원 키워드(헤드)에서 나온 후보는 상위 목록에 최대 N개 (주제 쏠림 방지)


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
        if hints and any(h in r["norm"] for h in hints):
            sc += 100  # 힌트 키워드 관련이면 최우선
        result.append({"keyword": r["keyword"], "total": r["total"], "score": round(sc, 2),
                       "status": r["status"], "head": head or ""})
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


def pick_keyword(conn, settings: BotSettings, llm) -> str | None:
    candidates = ranked_candidates(conn, settings, limit=SAFETY_BATCH * 2)
    for start in range(0, len(candidates), SAFETY_BATCH):
        batch = candidates[start:start + SAFETY_BATCH]
        unchecked = [c["keyword"] for c in batch if c["status"] == "new"]
        if unchecked:
            for kw, (ok, reason) in classify(llm, unchecked).items():
                set_status(conn, kw, "ok" if ok else "rejected", reason)
                for c in batch:
                    if c["keyword"] == kw:
                        c["status"] = "ok" if ok else "rejected"
            conn.commit()
        passed = [c["keyword"] for c in batch if c["status"] == "ok"]
        if passed:
            return random.choice(passed[:PICK_FROM_TOP])
    return None


if __name__ == "__main__":
    # 점검용: python3 -m bot.select_keyword  → 다음 글감 후보 상위 20개
    from .keyword_db import connect
    from .settings import load_settings
    from .textfmt import ljust, rjust

    with connect() as conn:
        for c in ranked_candidates(conn, load_settings(), limit=20):
            print(ljust(c["keyword"], 28) + rjust(f"{c['total']:,}", 9) + rjust(str(c["score"]), 7) + "  " + c["status"])
