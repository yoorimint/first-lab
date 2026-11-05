"""NAVER API HUB (네이버 클라우드) — 검색어 트렌드·블로그 문서 수 두 가지만 (11/5 운영자 승인, 다른 네이버 API 는 안 씀).

비용: 지금은 무료지만 나중에 종량제가 될 수 있다 → 하루 호출 상한(DAILY_LIMIT)을 코드로 고정하고,
유료 전환 공지가 나오면 ENABLED = False 로 끈다. 키가 없거나 실패하면 글감 고르기에 아무 영향 없음.

글감 고르기 보조 (bot/select_keyword.py 가 읽음):
  - 블로그 문서 수: 검색량에 비해 이미 쓴 글이 적으면 가점, 넘치면 감점 (포화도)
  - 검색어 트렌드(최근 24개월, 월별): 검색이 몰리는 달(성수기)·그 전달이면 가점, 비수기면 감점
결과는 키워드 창고(keywords.db)의 hub_signals 표. 매일 키워드 수집 끝에 상위 후보만 새로 조회 (bot/discover.py).

확인용(서버): python3 -m bot.naver_hub 연말정산계산기 근로장려금
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from .config import SECRETS_FILE, load_secrets
from .keyword_db import norm

ENABLED = True
BASE = "https://naverapihub.apigw.ntruss.com"
ID_SECRET, KEY_SECRET = "NAVER_HUB_ID", "NAVER_HUB_SECRET"
DAILY_LIMIT = 300            # 하루 전체 호출 상한 (무료 범위: 블로그 하루 2만5천·트렌드 월 5만보다 훨씬 적게)
COUNTER = Path("/var/lib/first-lab/bot/naver-hub-calls")  # 'YYYY-MM-DD 호출수'
CHECK_TOP = 60               # 하루에 새로 조회할 후보 수 (+ 그 대표 주제들 — 같은 주제 키워드가 주제의 성수기를 같이 씀)
REFRESH_DAYS = 30            # 같은 키워드는 이 기간 지나야 다시 조회
TREND_MONTHS = 24
PEAK, LOW = 0.8, 0.4         # 월평균이 최고 달의 80% 이상이면 성수기, 40% 이하면 비수기
SCHEMA = "CREATE TABLE IF NOT EXISTS hub_signals (norm TEXT PRIMARY KEY, docs INTEGER, peak TEXT, low TEXT, checked TEXT)"


class LimitReached(RuntimeError):
    pass


def _keys() -> tuple[str, str] | None:
    try:
        s = load_secrets(SECRETS_FILE) if SECRETS_FILE.exists() else {}
    except OSError:
        return None
    return (s[ID_SECRET], s[KEY_SECRET]) if ENABLED and s.get(ID_SECRET) and s.get(KEY_SECRET) else None


def _spend(today: dt.date, n: int = 1, counter: Path = COUNTER) -> None:
    """하루 상한 안에서만 호출 (넘으면 LimitReached — 그날은 더 안 부름)."""
    used = 0
    try:
        day, count = counter.read_text().split()
        used = int(count) if day == f"{today:%Y-%m-%d}" else 0
    except (OSError, ValueError):
        pass
    if used + n > DAILY_LIMIT:
        raise LimitReached(f"오늘 호출 {used}회 (상한 {DAILY_LIMIT})")
    counter.write_text(f"{today:%Y-%m-%d} {used + n}")


def _call(path: str, keys: tuple[str, str], params: dict | None = None, body: dict | None = None) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "X-NCP-APIGW-API-KEY-ID": keys[0], "X-NCP-APIGW-API-KEY": keys[1],
        **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 — 고정된 네이버 주소
        return json.loads(res.read().decode("utf-8"))


def blog_total(query: str, keys: tuple[str, str], today: dt.date) -> int | None:
    """블로그 검색 결과 문서 수."""
    _spend(today)
    got = _call("/search/v1/blog", keys, params={"query": query, "display": 1})
    return int(got["total"]) if "total" in got else None


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
        _spend(today)
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


def signal_score(sig: dict | None, total: int, month: int) -> float:
    """글감 점수 조정 (select_keyword). 신호가 없으면 0."""
    if not sig:
        return 0.0
    s = 0.0
    docs = sig.get("docs")
    if docs is not None and total > 0:
        per_search = docs / total  # 월 검색 1회당 이미 있는 블로그 글 수
        s += 1.0 if per_search < 1 else (-1.0 if per_search > 30 else 0.0)
    peak = [int(x) for x in (sig.get("peak") or "").split(",") if x]
    low = [int(x) for x in (sig.get("low") or "").split(",") if x]
    if month in peak or (month % 12 + 1) in peak:  # 이번 달 또는 다음 달이 성수기 → 지금 써야 색인·상위 노출이 시즌에 맞음
        s += 1.0
    elif month in low:
        s -= 0.5
    return s


def load(conn) -> dict[str, dict]:
    conn.execute(SCHEMA)
    return {r["norm"]: dict(r) for r in conn.execute("SELECT * FROM hub_signals")}


def refresh_signals(conn, candidates: list[dict], today: dt.date | None = None, keys=None) -> str:
    """상위 후보 중 최근 조회 안 한 것만 문서 수·트렌드를 받아 저장. 돌려주는 값은 봇 기록용 한 줄."""
    today = today or dt.date.today()
    keys = keys if keys is not None else _keys()
    if not keys:
        return "네이버 API HUB: 키 없음 → 건너뜀"
    known = load(conn)
    cutoff = f"{today - dt.timedelta(days=REFRESH_DAYS):%Y-%m-%d}"
    words: list[str] = []
    for c in candidates[:CHECK_TOP]:  # 후보와 그 대표 주제(head) — select_keyword 가 키워드에 신호가 없으면 주제 것을 씀
        for w in (c["keyword"], c.get("head") or ""):
            if w and norm(w) not in {norm(x) for x in words}:
                words.append(w)
    todo = [{"keyword": w} for w in words if norm(w) not in known or known[norm(w)]["checked"] < cutoff]
    done = 0
    try:
        trends = monthly_trend([c["keyword"] for c in todo], keys, today) if todo else {}
        for c in todo:
            peak, low = season(trends.get(c["keyword"], {}))
            docs = blog_total(c["keyword"], keys, today)
            conn.execute("INSERT OR REPLACE INTO hub_signals VALUES (?,?,?,?,?)",
                         (norm(c["keyword"]), docs, ",".join(map(str, peak)), ",".join(map(str, low)), f"{today:%Y-%m-%d}"))
            done += 1
    except LimitReached as e:
        conn.commit()
        return f"네이버 API HUB: {done}개 조회 후 하루 상한 도달 ({e})"
    except (OSError, ValueError, KeyError) as e:  # 네트워크·응답 형식 이상 → 다음 날 다시
        conn.commit()
        return f"네이버 API HUB: {done}개 조회 후 실패 ({type(e).__name__}: {str(e)[:80]})"
    conn.commit()
    return f"네이버 API HUB: 후보·대표 주제 {done}개 문서 수·검색 트렌드 갱신"


def main(argv: list[str]) -> int:
    keys = _keys()
    if not keys:
        print("네이버 API HUB: 키 없음")
        return 1
    today = dt.date.today()
    words = argv[1:] or ["연말정산계산기"]
    trends = monthly_trend(words, keys, today)
    for w in words:
        peak, low = season(trends.get(w, {}))
        print(f"{w}: 블로그 문서 {blog_total(w, keys, today)}개, 성수기 {peak or '-'}월, 비수기 {low or '-'}월, 트렌드 {len(trends.get(w, {}))}개월")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
