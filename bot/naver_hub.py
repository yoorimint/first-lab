"""NAVER API HUB (네이버 클라우드) — 블로그 문서 수·검색어 트렌드 두 가지만 (11/5 운영자 승인, 다른 네이버 API 는 안 씀).

비용: 지금은 무료지만 나중에 종량제가 될 수 있다 → 종류별 하루 호출 상한(LIMITS)을 코드로 고정하고,
유료 전환 공지가 나오면 ENABLED = False 로 끈다. 키가 없거나 실패하면 글감 고르기는 예전 그대로.

1) 황금 키워드 찾기 (scan_golden): 키워드 창고에서 아직 안 본 키워드를 돈·계산기 주제부터 하루 GOLD_SCAN 개씩
   블로그 문서 수 조회 → '검색 1회당 블로그 글 수'(포화도)가 낮을수록 가점. 낮은 키워드는 오늘 수집분이 아니어도 후보에 오름.
2) 성수기 (refresh_signals): 점수 상위 후보와 그 대표 주제의 최근 24개월 월별 검색 흐름 → 이번·다음 달이 성수기면 가점.
결과는 키워드 창고(keywords.db)의 naver_signals 표 (bot/select_keyword.py 가 읽음). 매일 키워드 수집 끝에 실행 (bot/discover.py).
한계: 블로그 문서 수는 네이버 기준 경쟁도라 구글 경쟁과 같지는 않다 (글이 넘치는 주제를 거르는 대리 지표).

확인용(서버): python3 -m bot.naver_hub 연말정산계산기 근로장려금
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import SECRETS_FILE, load_secrets
from .keyword_db import norm

ENABLED = True
BASE = "https://naverapihub.apigw.ntruss.com"
ID_SECRET, KEY_SECRET = "NAVER_HUB_ID", "NAVER_HUB_SECRET"
LIMITS = {"blog": 20000, "trend": 300}  # 하루 상한 — 무료인 동안 블로그 하루 2만5천 중 2만까지 써서 키워드 창고 전체를 훑는다 (11/5 운영자 지시)
COUNTER_DIR = Path("/var/lib/first-lab/bot")
GOLD_SCAN = 19000            # 하루에 황금 키워드 찾기로 보는 수 (나머지는 후보·배포 점검용)
CHECK_TOP = 60               # 성수기를 볼 상위 후보 수 (+ 그 대표 주제)
REFRESH_DAYS = 30            # 같은 키워드는 이 기간 지나야 다시 조회
TREND_MONTHS = 24
PEAK, LOW = 0.8, 0.4         # 월평균이 최고 달의 80% 이상이면 성수기, 40% 이하면 비수기
# 황금 점수 = log10(월 검색량) ÷ (1 + 검색 1회당 블로그 글 수)  — '검색은 어느 정도 있는데 글은 적은 것'.
#   비율만 쓰면 검색 50·글 5 같은 작은 키워드가 1등이 되므로 검색량 크기를 곱하고, 구간 없이 매끄럽게 (경계에서 점수가 튀지 않게).
#   예) 검색 5,000·글 1,000 → 3.1 / 검색 1,000·글 100 → 2.7 / 검색 20,000·글 20만 → 0.4
GOLD_WEIGHT = 2.0            # 글감 점수에 더하는 배수 (운영자 지시: 황금 점수 높은 것부터)
GOLD_LABELS = ((0.3, "황금"), (1.0, "좋음"), (3.0, "보통"))  # 검색 1회당 글 수 미만 → 표시 이름
SATURATED = 30.0             # 검색 1회당 글 30개 초과 → '포화' 표시
FEW_DOCS = 10                # 글이 이보다 적으면 검색 도구가 만든 어색한 조합일 수도 → '확인 필요' 표시 (점수는 그대로)
# 사이트를 찾아가는 검색: 검색량은 커도 사람들이 공식 사이트로 바로 가서 글을 안 읽음 → 황금 찾기·가점에서 뺌
NAVIGATIONAL = ("로그인", "홈페이지", "바로가기", "사이트", "고객센터", "전화번호", "콜센터", "앱다운", "어플다운",
                "다운로드", "인터넷뱅킹", "공식", "주소", "위치", "영업시간", "운영시간")
WORKERS = 8
CHUNK = 500                  # 이만큼씩 부르고 저장 (중간에 막혀도 받은 만큼은 남김)
SCHEMA = ("CREATE TABLE IF NOT EXISTS naver_signals (norm TEXT PRIMARY KEY, docs INTEGER, docs_checked TEXT,"
          " peak TEXT, low TEXT, trend_checked TEXT)")


class LimitReached(RuntimeError):
    pass


def _keys() -> tuple[str, str] | None:
    try:
        s = load_secrets(SECRETS_FILE) if SECRETS_FILE.exists() else {}
    except OSError:
        return None
    return (s[ID_SECRET], s[KEY_SECRET]) if ENABLED and s.get(ID_SECRET) and s.get(KEY_SECRET) else None


def _spend(today: dt.date, n: int = 1, kind: str = "blog", counter_dir: Path | None = None) -> None:
    """종류별 하루 상한 안에서만 호출 — 넘으면 LimitReached (그날은 더 안 부름). n 개를 미리 잡는다."""
    counter = (counter_dir or COUNTER_DIR) / f"naver-hub-{kind}"
    used = 0
    try:
        day, count = counter.read_text().split()
        used = int(count) if day == f"{today:%Y-%m-%d}" else 0
    except (OSError, ValueError):
        pass
    if used + n > LIMITS[kind]:
        raise LimitReached(f"{kind} 오늘 {used}회, 상한 {LIMITS[kind]}")
    counter.write_text(f"{today:%Y-%m-%d} {used + n}")


def _call(path: str, keys: tuple[str, str], params: dict | None = None, body: dict | None = None) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "X-NCP-APIGW-API-KEY-ID": keys[0], "X-NCP-APIGW-API-KEY": keys[1],
        **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 — 고정된 네이버 주소
        return json.loads(res.read().decode("utf-8"))


class Throttled(RuntimeError):
    """네이버가 호출 한도(429)를 알림 → 그날은 멈춤."""


def _one_total(query: str, keys: tuple[str, str]) -> int | None:
    try:
        got = _call("/search/v1/blog", keys, params={"query": query, "display": 1})
        return int(got["total"]) if "total" in got else None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Throttled("429") from e
        return None
    except (OSError, ValueError, KeyError):
        return None


def blog_totals(words: list[str], keys: tuple[str, str], today: dt.date, save=None) -> dict[str, int | None]:
    """블로그 검색 결과 문서 수 (여러 개 동시에). CHUNK 개씩 상한을 잡고 부르고, save 가 있으면 묶음마다 저장.
    네이버가 한도를 알리면(429) 받은 만큼만 돌려준다."""
    out: dict[str, int | None] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i in range(0, len(words), CHUNK):
            chunk = words[i:i + CHUNK]
            _spend(today, len(chunk), "blog")
            try:
                got = dict(zip(chunk, pool.map(lambda w: _one_total(w, keys), chunk)))
            except Throttled:
                break
            out.update(got)
            if save:
                save(got)
    return out


def monthly_trend(words: list[str], keys: tuple[str, str], today: dt.date) -> dict[str, dict[str, float]]:
    """검색어 트렌드 (한 번에 5개까지): {키워드: {'YYYY-MM': 상대 검색량}} — 지난달까지 TREND_MONTHS 개월."""
    end = today.replace(day=1) - dt.timedelta(days=1)  # 지난달 마지막 날 (이번 달은 덜 찬 값이라 뺌)
    y, m = end.year, end.month - (TREND_MONTHS - 1)
    while m <= 0:
        y, m = y - 1, m + 12
    start = dt.date(y, m, 1)
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(words), 5):
        batch = words[i:i + 5]
        _spend(today, 1, "trend")
        got = _call("/search-trend/v1/search", keys, body={
            "startDate": f"{start:%Y-%m-%d}", "endDate": f"{end:%Y-%m-%d}", "timeUnit": "month",
            "keywordGroups": [{"groupName": w, "keywords": [w]} for w in batch]})
        for r in got.get("results", []):
            out[r["title"]] = {d["period"][:7]: float(d["ratio"]) for d in r.get("data", [])}
    return out


def season(series: dict[str, float]) -> tuple[list[int], list[int]]:
    """월별 상대 검색량 → (성수기 달들, 비수기 달들). 해마다 같은 달끼리 평균."""
    by_month: dict[int, list[float]] = {}
    for period, ratio in series.items():
        by_month.setdefault(int(period[5:7]), []).append(ratio)
    avg = {m: sum(v) / len(v) for m, v in by_month.items()}
    top = max(avg.values(), default=0)
    if top <= 0 or len(avg) < 12:  # 1년치가 안 되면 계절을 말할 수 없음
        return [], []
    return sorted(m for m, a in avg.items() if a >= PEAK * top), sorted(m for m, a in avg.items() if a <= LOW * top)


def is_navigational(keyword: str) -> bool:
    k = norm(keyword)
    return any(w in k for w in NAVIGATIONAL)


def per_search(sig: dict | None, total: int) -> float | None:
    """검색 1회당 블로그 글 수 (낮을수록 황금)."""
    if not sig or sig.get("docs") is None or total <= 0:
        return None
    return sig["docs"] / total


def gold_score(sig: dict | None, total: int) -> float:
    """황금 점수 = log10(검색량) ÷ (1 + 검색 1회당 글 수). 신호 없으면 0."""
    ratio = per_search(sig, total)
    return 0.0 if ratio is None else math.log10(max(total, 1)) / (1 + ratio)


def gold_label(sig: dict | None, total: int) -> str:
    ratio = per_search(sig, total)
    if ratio is None:
        return ""
    if sig["docs"] < FEW_DOCS:
        return "확인 필요(글 거의 없음)"
    for limit, name in GOLD_LABELS:
        if ratio < limit:
            return name
    return "포화" if ratio > SATURATED else ""


def signal_score(sig: dict | None, total: int, month: int, keyword: str = "") -> float:
    """글감 점수 조정 (select_keyword): 황금 점수 × GOLD_WEIGHT + 성수기. 사이트를 찾아가는 검색은 황금 가점 없음."""
    if not sig:
        return 0.0
    s = 0.0 if is_navigational(keyword) else GOLD_WEIGHT * gold_score(sig, total)
    peak = [int(x) for x in (sig.get("peak") or "").split(",") if x]
    low = [int(x) for x in (sig.get("low") or "").split(",") if x]
    if month in peak or (month % 12 + 1) in peak:  # 이번 달 또는 다음 달이 성수기 → 지금 써야 시즌에 상위 노출
        s += 1.0
    elif month in low:
        s -= 0.5
    return s


def describe(sig: dict | None, total: int) -> str:
    """후보 목록에 붙일 한 줄: '황금 3.1 / 성수기 1·2월 / 블로그 글 500개(검색 1회당 0.5개)'."""
    if not sig:
        return ""
    parts = []
    label = gold_label(sig, total)
    if label:
        parts.append(f"{label} {gold_score(sig, total):.1f}")
    if sig.get("peak"):
        parts.append(f"성수기 {'·'.join(sig['peak'].split(','))}월")
    ratio = per_search(sig, total)
    if ratio is not None:
        parts.append(f"블로그 글 {sig['docs']:,}개(검색 1회당 {ratio:.1f}개)")
    return " / ".join(parts)


def is_gold(sig: dict | None, total: int, keyword: str = "") -> bool:
    """황금·좋음 (검색 1회당 글 1개 미만, 글 10개 이상, 사이트 찾아가는 검색 아님) — 글쓰기·공개를 먼저 할 키워드."""
    return gold_label(sig, total) in ("황금", "좋음") and not is_navigational(keyword)


def publish_priority(keywords: dict[int, str], month: int, conn=None) -> dict[int, float]:
    """초안 번호 → 공개 우선 점수 (황금 점수×2 + 성수기). 키워드 창고·신호가 없으면 빈 값 (공개 순서는 예전대로)."""
    from .keyword_db import DB_PATH, connect
    if not keywords or (conn is None and not DB_PATH.exists()):
        return {}

    def read(c) -> tuple[dict, dict]:
        return load(c), {r["norm"]: r["total"] for r in c.execute(
            f"SELECT norm, total FROM keywords WHERE norm IN ({','.join('?' * len(keywords))})",
            [norm(k) for k in keywords.values()])}
    try:
        if conn is not None:
            sigs, totals = read(conn)
        else:
            with connect() as c:
                sigs, totals = read(c)
    except Exception:  # noqa: BLE001 — 순서 정하기용이라 실패해도 공개는 계속
        return {}
    return {pid: signal_score(sigs.get(norm(k)), totals.get(norm(k), 0), month, k) for pid, k in keywords.items()}


def load(conn) -> dict[str, dict]:
    conn.execute(SCHEMA)
    return {r["norm"]: dict(r) for r in conn.execute("SELECT * FROM naver_signals")}


def _save_docs(conn, docs: dict[str, int | None], today: dt.date) -> int:
    conn.execute(SCHEMA)  # 표가 아직 없을 수 있음 (새 창고·대기 글만 먼저 조회하는 경우)
    rows = [(norm(w), n, f"{today:%Y-%m-%d}") for w, n in docs.items() if n is not None]
    conn.executemany("INSERT INTO naver_signals(norm, docs, docs_checked) VALUES (?,?,?) "
                     "ON CONFLICT(norm) DO UPDATE SET docs=excluded.docs, docs_checked=excluded.docs_checked", rows)
    conn.commit()
    return len(rows)


def scan_golden(conn, settings, today: dt.date | None = None, keys=None) -> str:
    """키워드 창고에서 아직(또는 30일 넘게) 안 본 키워드를 돈·계산기 주제·검색량 순으로 GOLD_SCAN 개 문서 수 조회.
    사이트를 찾아가는 검색(로그인·고객센터 등)은 조회하지 않는다."""
    from .keyword_filter import is_allowed
    from .select_keyword import topic_weight

    today = today or dt.date.today()
    keys = keys if keys is not None else _keys()
    if not keys:
        return "황금 키워드: 키 없음 → 건너뜀"
    known = load(conn)
    cutoff = f"{today - dt.timedelta(days=REFRESH_DAYS):%Y-%m-%d}"
    rows = conn.execute("SELECT keyword, norm, total FROM keywords WHERE status IN ('new','ok') AND total BETWEEN ? AND ?",
                        (settings.min_volume, settings.max_volume)).fetchall()

    def stale(n: str) -> bool:
        return ((known.get(n) or {}).get("docs_checked") or "") < cutoff

    fresh = [r for r in rows if stale(r["norm"]) and is_allowed(r["keyword"], settings.blocked_words)
             and not is_navigational(r["keyword"])]
    fresh.sort(key=lambda r: (topic_weight(r["norm"]), r["total"]), reverse=True)
    batch = [r["keyword"] for r in fresh[:GOLD_SCAN]]
    saved = 0

    def save(got: dict) -> None:
        nonlocal saved
        saved += _save_docs(conn, got, today)

    try:
        blog_totals(batch, keys, today, save=save)
    except LimitReached as e:
        return f"황금 키워드: 오늘 {saved}개 조회 후 상한 ({e})"
    return f"황금 키워드: 오늘 {saved}개 조회 (남은 미조회 {max(0, len(fresh) - len(batch)):,}개) / " + golden_report(conn, rows)


def golden_report(conn, rows=None, top_n: int = 5) -> str:
    """지금까지 찾은 황금 키워드 수와 황금 점수 상위."""
    if rows is None:
        rows = conn.execute("SELECT keyword, norm, total FROM keywords WHERE status IN ('new','ok')").fetchall()
    sigs = load(conn)
    scored = []
    for r in rows:
        sig = sigs.get(r["norm"])
        if sig and sig.get("docs") is not None and not is_navigational(r["keyword"]):
            scored.append((gold_score(sig, r["total"]), gold_label(sig, r["total"]), r["norm"], r["total"], sig["docs"]))
    scored.sort(reverse=True)
    gold = [x for x in scored if x[1] == "황금"]
    top = ", ".join(f"{n} {g:.1f}(검색 {t:,}/글 {d:,})" for g, _, n, t, d in scored[:top_n])
    return f"조회 {len(scored):,}개 중 황금 {len(gold):,}개 — 황금 점수 상위: {top or '없음'}"


def refresh_signals(conn, candidates: list[dict], today: dt.date | None = None, keys=None) -> str:
    """상위 후보와 그 대표 주제의 성수기(트렌드)·문서 수 — 30일 안에 본 것은 건너뜀."""
    today = today or dt.date.today()
    keys = keys if keys is not None else _keys()
    if not keys:
        return "네이버 성수기: 키 없음 → 건너뜀"
    known = load(conn)
    cutoff = f"{today - dt.timedelta(days=REFRESH_DAYS):%Y-%m-%d}"
    words: list[str] = []
    for c in candidates[:CHECK_TOP]:  # 후보와 그 대표 주제(head) — select_keyword 가 키워드에 신호가 없으면 주제 것을 씀
        for w in (c["keyword"], c.get("head") or ""):
            if w and norm(w) not in {norm(x) for x in words}:
                words.append(w)
    stale = lambda w, field: ((known.get(norm(w)) or {}).get(field) or "") < cutoff  # noqa: E731
    need_trend = [w for w in words if stale(w, "trend_checked")]
    need_docs = [w for w in words if stale(w, "docs_checked")]
    try:
        _save_docs(conn, blog_totals(need_docs, keys, today), today)
        trends = monthly_trend(need_trend, keys, today) if need_trend else {}
    except LimitReached as e:
        return f"네이버 성수기: 오늘 상한 ({e})"
    except (OSError, ValueError, KeyError) as e:  # 네트워크·응답 형식 이상 → 다음 날 다시
        return f"네이버 성수기: 실패 ({type(e).__name__}: {str(e)[:80]})"
    for w in need_trend:
        peak, low = season(trends.get(w, {}))
        conn.execute("INSERT INTO naver_signals(norm, peak, low, trend_checked) VALUES (?,?,?,?) "
                     "ON CONFLICT(norm) DO UPDATE SET peak=excluded.peak, low=excluded.low, trend_checked=excluded.trend_checked",
                     (norm(w), ",".join(map(str, peak)), ",".join(map(str, low)), f"{today:%Y-%m-%d}"))
    conn.commit()
    return f"네이버 성수기: 후보·대표 주제 {len(need_trend)}개 갱신"


def refresh_queue(conn, today: dt.date | None = None, keys=None, drafts=None, ads=None) -> str:
    """공개 대기 글 키워드의 문서 수·성수기를 다시 받아 공개 순서(publish_priority)에 쓰게 한다 (운영자 지시 11/5).
    검색량이 창고에 없는 키워드는 검색광고 API 로 채우고 '쓴 키워드'로 표시 (새 글 후보로 다시 뜨지 않게).
    drafts: {초안 번호: 키워드} (없으면 서버 대기열에서 읽음)."""
    from .keyword_db import set_status, upsert
    today = today or dt.date.today()
    keys = keys if keys is not None else _keys()
    if not keys:
        return "대기 글 키워드: 키 없음 → 건너뜀"
    if drafts is None:
        from . import schedule
        ids = schedule.ready_drafts()
        drafts = {i: t.split(" ")[0] for i, t in schedule._draft_texts(ids).items() if t.strip()}
    words = list(dict.fromkeys(k for k in drafts.values() if k))
    if not words:
        return "대기 글 키워드: 없음"
    missing = [w for w in words if conn.execute("SELECT 1 FROM keywords WHERE norm=?", (norm(w),)).fetchone() is None]
    filled = 0
    if missing:
        try:
            if ads is None:
                from .naver_ads import ads_client
                ads = ads_client()
            want = {norm(w) for w in missing}
            for i in range(0, len(missing), 5):
                upsert(conn, [st for st in ads.keyword_stats(missing[i:i + 5]) if norm(st.keyword) in want])
            for w in missing:
                row = conn.execute("SELECT keyword FROM keywords WHERE norm=?", (norm(w),)).fetchone()
                if row:
                    set_status(conn, row["keyword"], "used", "공개 대기 글")
                    filled += 1
            conn.commit()
        except Exception:  # noqa: BLE001 — 검색량을 못 채워도 문서 수·성수기는 받음
            pass
    try:
        _save_docs(conn, blog_totals(words, keys, today), today)
        trends = monthly_trend(words, keys, today)
    except (LimitReached, OSError, ValueError, KeyError) as e:
        return f"대기 글 키워드: 실패 ({type(e).__name__}: {str(e)[:80]})"
    for w in words:
        peak, low = season(trends.get(w, {}))
        conn.execute("INSERT INTO naver_signals(norm, peak, low, trend_checked) VALUES (?,?,?,?) "
                     "ON CONFLICT(norm) DO UPDATE SET peak=excluded.peak, low=excluded.low, trend_checked=excluded.trend_checked",
                     (norm(w), ",".join(map(str, peak)), ",".join(map(str, low)), f"{today:%Y-%m-%d}"))
    conn.commit()
    prio = publish_priority(drafts, today.month, conn=conn)
    top = sorted(prio.items(), key=lambda x: -x[1])[:5]
    return (f"대기 글 키워드 {len(words)}개 다시 조회(검색량 채움 {filled}) — 공개 순서 상위: "
            + ", ".join(f"{drafts[i]} {p:.1f}" for i, p in top))


def run_daily(log: bool = False) -> str:
    """황금 키워드 찾기 → 성수기 (키워드 수집 끝, 또는 배포 때 한 번). log=True 면 봇 기록에도 남김."""
    from .keyword_db import connect
    from .select_keyword import ranked_candidates
    from .settings import load_settings
    settings = load_settings()
    with connect() as conn:
        queue = refresh_queue(conn)  # 공개 대기 글 먼저 (적은 호출로 오늘 공개 순서에 바로 반영)
        gold = scan_golden(conn, settings)
        msg = queue + " / " + gold + " / " + refresh_signals(conn, ranked_candidates(conn, settings, limit=CHECK_TOP))
    if log:
        from .state import add_log, load_state, save_state
        state = load_state()
        add_log(state, True, msg)
        save_state(state)
    return msg


def main(argv: list[str]) -> int:
    if argv[1:] == ["--queue"]:  # 공개 대기 글만 (배포 때 한 번)
        from .keyword_db import connect
        with connect() as conn:
            msg = refresh_queue(conn)
        from .state import add_log, load_state, save_state
        state = load_state()
        add_log(state, True, msg)
        save_state(state)
        print(msg, flush=True)
        return 0
    if argv[1:] == ["--daily"]:
        print(run_daily(log=True), flush=True)
        return 0
    if argv[1:] == ["--diag"]:  # 네이버가 붙여 쓴 말을 나눠 세는지 (글 수 부풀림 확인용, 호출 3회)
        keys, today = _keys(), dt.date.today()
        if keys:
            got = blog_totals(["연말정산계산기", "연말정산 계산기", '"연말정산 계산기"'], keys, today)
            print("네이버 문서 수 비교: " + ", ".join(f"{k}={v}" for k, v in got.items()))
        return 0
    keys = _keys()
    if not keys:
        print("네이버 API HUB: 키 없음")
        return 1
    today = dt.date.today()
    words = argv[1:] or ["연말정산계산기"]
    trends = monthly_trend(words, keys, today)
    docs = blog_totals(words, keys, today)
    for w in words:
        peak, low = season(trends.get(w, {}))
        print(f"{w}: 블로그 문서 {docs.get(w)}개, 성수기 {peak or '-'}월, 비수기 {low or '-'}월, 트렌드 {len(trends.get(w, {}))}개월")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
