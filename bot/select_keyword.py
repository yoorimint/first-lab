"""키워드 창고에서 다음 글감 고르기: 필터 → 점수 → 중복 제외 → AI 안전 검사."""
from __future__ import annotations

import datetime as dt
import random
from difflib import SequenceMatcher

from . import competition, naver_hub
from .keyword_db import competition_checks_today, norm, set_competition, set_status
from .keyword_filter import is_allowed, is_question, subject
from .llm import LLMBusy
from .safety import classify
from .scoring import find_head, score
from .search_console import load_signals
from .settings import BotSettings

SAFETY_BATCH = 15  # 한 번의 AI 호출로 검사할 후보 수
PICK_FROM_TOP = 3  # 통과한 상위 N개 중 무작위 → 매번 같은 유형만 쓰지 않게
PER_HEAD = 2       # 같은 원 키워드(헤드)에서 나온 후보는 상위 목록에 최대 N개 (주제 쏠림 방지)
COMP_WEIGHT = 3.0  # 경쟁 난이도 반영: 점수 += COMP_WEIGHT × (0.5 - 난이도)  → 쉬우면 가산, 어려우면 감점
GSC_BONUS = {"opportunity": 5.0, "good_head": 1.0, "bad_head": -1.5}  # 서치콘솔 성과 반영
TOO_HARD = 0.8     # 이 이상이면 글감에서 제외 (상위 대부분이 대형 사이트)
COMP_PER_RUN = 3   # 글 1개 쓸 때 경쟁도를 새로 조회할 최대 후보 수


_GENERIC_SUFFIXES = ("하는법", "방법", "총정리", "정리", "알아보기", "알아보자", "안내", "가이드")

# 같은 뜻 다른 표기 → 같은 주제로 비교 (11/5: '부동산양도세계산기'가 이미 쓴 '부동산양도소득세계산기'와 별개 후보로 계속 1위)
SYNONYMS = (("양도소득세", "양도세"), ("종합부동산세", "종부세"), ("나의", "내"), ("수급조건", "조건"), ("자격조건", "조건"),
            ("대출조회", "조회"), ("업체", ""), ("고용보험실업급여", "실업급여"))
SIMILAR_USED = 0.85  # 표기를 맞춘 뒤 이만큼 비슷하면 이미 쓴 주제 (bot/publisher.SIMILAR_RATIO 와 같은 기준)

# 주제 비중 (11/5 운영자 결정: 계산기·돈 주제 우선) — 광고 단가가 높고, AI 요약이 대신하기 어렵고, 우리 검수가 가장 가치 있는 주제.
# 여행 준비물·생활 요령은 빼지 않고 낮춤 (단가 낮고 경쟁 많고 AI 요약에 클릭을 뺏기기 쉬움).
TOPIC_BONUS = (
    (("계산기", "계산"), 2.0),
    (("세금", "소득세", "양도세", "증여세", "상속세", "부가세", "종부세", "취득세", "연말정산", "환급", "공제",
      "보험", "대출", "이자", "연금", "급여", "수당", "지원금", "바우처", "퇴직금", "신용"), 1.0),
)
TOPIC_PENALTY = ((("준비물", "리스트", "정리방법", "청소", "인테리어", "여행"), -1.0),)


def unify(n: str) -> str:
    """비교용 표기 통일 ('나의신용점수조회' → '내신용점수조회')."""
    for a, b in SYNONYMS:
        n = n.replace(a, b)
    return n


def topic_weight(n: str) -> float:
    """주제 비중 가감 (해당 묶음마다 한 번)."""
    return sum(w for words, w in TOPIC_BONUS + TOPIC_PENALTY if any(x in n for x in words))


def _core(n: str) -> str:
    """의미 없는 꼬리말 제거: '퇴직금계산방법' → '퇴직금계산'."""
    for suffix in _GENERIC_SUFFIXES:
        if n.endswith(suffix) and len(n) > len(suffix) + 1:
            return n[: -len(suffix)]
    return n


def _head_difficulty(conn, volumes: dict[str, int]) -> dict[str, float]:
    """조회해 둔 경쟁도를 주제(헤드)별 평균으로 묶는다. 조회 못 한 형제 키워드의 추정값으로 쓴다.
    예) '한식조리사자격증'이 0.65 → 같은 헤드 '조리사자격증' 아래 키워드들도 0.65 로 추정."""
    groups: dict[str, list[float]] = {}
    for r in conn.execute("SELECT norm, total, difficulty FROM keywords WHERE difficulty IS NOT NULL"):
        for key in {find_head(r["norm"], r["total"], volumes) or r["norm"], r["norm"]}:
            groups.setdefault(key, []).append(r["difficulty"])
    return {k: round(sum(v) / len(v), 2) for k, v in groups.items()}


def ranked_candidates(conn, settings: BotSettings, limit: int = 50) -> list[dict]:
    """점수순 후보 (안전 검사 전). 이미 쓴 글과 겹치는 키워드는 제외."""
    latest = conn.execute("SELECT MAX(last_checked) FROM keywords").fetchone()[0]
    hub = naver_hub.load(conn)  # 블로그 문서 수·검색 성수기 (네이버 API HUB, 없으면 빈 값)
    rows = conn.execute(  # 가장 최근 수집일(=오늘)의 키워드 + 서치콘솔 기회 키워드(검색량 범위 무관) + 황금 키워드(검색 1회당 글 1개 미만)
        "SELECT * FROM keywords WHERE status IN ('new','ok') AND ("
        " (last_checked=? AND total BETWEEN ? AND ?)"
        " OR norm IN (SELECT norm FROM gsc_signals WHERE kind='opportunity')"
        " OR (total BETWEEN ? AND ? AND norm IN (SELECT n.norm FROM naver_signals n JOIN keywords k ON k.norm = n.norm"
        "     WHERE n.docs IS NOT NULL AND n.docs < k.total)))",
        (latest, settings.min_volume, settings.max_volume, settings.min_volume, settings.max_volume),
    ).fetchall()
    used = [r["norm"] for r in conn.execute("SELECT norm FROM keywords WHERE status='used'")]
    volumes = {r["norm"]: r["total"] for r in conn.execute("SELECT norm, total FROM keywords")}
    hints = [norm(h) for h in settings.hint_keywords]
    signals = load_signals(conn)

    used_cores = {_core(u) for u in used}
    used_unified = {_core(unify(u)) for u in used}
    used_subjects = {subject(u) for u in used}  # 이미 쓴 글의 주제 → 같은 주제 키워드는 새 글 대신 그 글 보강(refresh.py)
    head_difficulty = _head_difficulty(conn, volumes)
    month = dt.date.today().month

    def overlaps_used(n: str) -> bool:
        """이미 쓴 글과 같은 주제인 키워드를 막는다 (같은 주제 글이 여러 개면 서로 순위를 깎아 먹음).
        막음: 퇴직금계산 ↔ 퇴직금계산방법, 자동차정기검사비용 ↔ 자동차정기검사조회(주제가 같고 수식어만 다름),
              다이어트식단 ↔ 다이어트식단표(2글자 이내 차이)
        허용: 퇴직금계산 → 퇴직금계산1년미만"""
        if _core(n) in used_cores or subject(n) in used_subjects:
            return True
        if any((u in n or n in u) and abs(len(n) - len(u)) <= 2 for u in used):
            return True
        un = _core(unify(n))
        return any(SequenceMatcher(None, un, uu).ratio() >= SIMILAR_USED for uu in used_unified)

    result = []
    for r in rows:
        if not is_allowed(r["keyword"], settings.blocked_words) or overlaps_used(r["norm"]):
            continue
        head = find_head(r["norm"], r["total"], volumes)
        sc = score(r["keyword"], r["norm"], r["total"], r["ad_comp"], r["ad_clicks"], r["prev_total"], head)
        sc += topic_weight(r["norm"])
        sig = hub.get(r["norm"]) or hub.get(head or "")
        sc += naver_hub.signal_score(sig, r["total"], month)
        difficulty, estimated = r["difficulty"], False
        if difficulty is None and (head or r["norm"]) in head_difficulty:
            difficulty, estimated = head_difficulty[head or r["norm"]], True  # 조회 안 한 키워드: 같은 주제로 추정
        if difficulty is not None:
            if difficulty >= TOO_HARD:
                continue
            sc += COMP_WEIGHT * (0.5 - difficulty)
        gsc = signals.get(r["norm"]) or signals.get(head or "")
        if gsc:
            sc += GSC_BONUS[gsc]
        if hints and any(h in r["norm"] for h in hints):
            sc += 100  # 힌트 키워드 관련이면 최우선
        result.append({"keyword": r["keyword"], "total": r["total"], "score": round(sc, 2),
                       "status": r["status"], "head": head or "",
                       "difficulty": r["difficulty"],
                       "comp_info": r["comp_info"] or (f"같은 주제 추정 {difficulty}" if estimated else ""),
                       "gsc": {"opportunity": "서치콘솔 기회", "good_head": "잘 되는 주제",
                               "bad_head": "안 되는 주제"}.get(gsc or "", ""),
                       "naver": naver_hub.describe(sig, r["total"])})  # 글쓰기 세션이 보는 근거 (성수기·블로그 글 수)
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
    if not competition.source(settings):
        print("[경쟁도] 조회 불가: Gemini 키가 없어 같은 주제 추정값만 씁니다", flush=True)
        return
    budget = min(limit, settings.competition_checks - competition_checks_today(conn))
    for c in [c for c in candidates if c["difficulty"] is None][:max(budget, 0)]:
        try:
            comp = competition.check_any(settings, c["keyword"])
        except Exception as e:  # noqa: BLE001 - 경쟁도 조회 실패는 글쓰기를 막지 않음
            print(f"[경쟁도] {c['keyword']} 실패: {e}", flush=True)
            break
        info = f"{comp.summary}: {', '.join(comp.domains)}"
        set_competition(conn, c["keyword"], comp.difficulty, info)
        c["difficulty"], c["comp_info"] = comp.difficulty, info
        c["score"] = round(c["score"] + COMP_WEIGHT * (0.5 - comp.difficulty), 2)
        print(f"[경쟁도] {c['keyword']}: {comp.difficulty} {info}", flush=True)
    conn.commit()


def _safety_check(conn, llm, batch: list[dict]) -> bool:
    """AI 판정 완료면 True, AI 혼잡으로 임시 판정했으면 False."""
    unchecked = [c["keyword"] for c in batch if c["status"] == "new"]
    if not unchecked:
        return True
    try:
        results = classify(llm, unchecked)
    except LLMBusy:
        # AI 혼잡 → 질문형 롱테일(방법·신청·비용 등)만 임시 통과. 상태는 'new' 로 두어 다음에 AI 가 다시 검사
        for c in batch:
            if c["status"] == "new" and is_question(c["keyword"]):
                c["status"] = "ok"
        return False
    for kw, (ok, reason) in results.items():
        set_status(conn, kw, "ok" if ok else "rejected", reason)
        for c in batch:
            if c["keyword"] == kw:
                c["status"] = "ok" if ok else "rejected"
    conn.commit()
    return True


def review_top(conn, settings: BotSettings, llm, count: int = 10) -> int:
    """하루 1회: 상위 후보를 미리 안전 검사 + 경쟁도 조회 (설정 페이지에서 근거 확인용). 조회 수 반환."""
    before = competition_checks_today(conn)
    candidates = ranked_candidates(conn, settings, limit=count)
    safety_done = _safety_check(conn, llm, candidates)
    _check_competition(conn, settings, [c for c in candidates if c["status"] == "ok"], limit=count)
    if not safety_done:
        raise LLMBusy("AI 안전검사를 못 했습니다 (Gemini 혼잡)")
    return competition_checks_today(conn) - before


def check_top_competition(conn, settings: BotSettings, count: int = 10) -> int:
    """월정액 방식의 하루 1회 검토: 상위 후보의 경쟁도만 조회 (Gemini 무료, Claude API 안 씀). 조회 수 반환."""
    before = competition_checks_today(conn)
    _check_competition(conn, settings, ranked_candidates(conn, settings, limit=count), limit=count)
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
