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
import functools
import html
import json
import math
import re
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
# 황금 점수 = log10(월 검색량) × min(황금지수 ÷ 0.3, 1) — 운영자 결정(11/5): 중요한 건 '검색량 대비 글 수'.
#   황금지수 = 검색량 ÷ 네이버 블로그 문서 수 (한국 키워드 도구들의 표준 방식, 0.3 이상 황금·0.1 이상 좋음).
#   검색량 크기를 곱해 검색 50·글 5 같은 작은 키워드가 1등이 되지 않게 함.
#   상위 SAMPLE 개 중 제목에 키워드가 그대로 든 '정면 경쟁' 글 수·그중 1년 넘은 글 수는 참고로만 보여 줌 (점수에 안 씀).
#   한계: 네이버는 단어가 모두 든 글을 세서 붙여 쓴 롱테일은 글 수가 부풀어 보임 (다른 도구도 같음) — 12월 서치콘솔로 검증.
GOLD_WEIGHT = 2.0            # 글감 점수에 더하는 배수 (운영자 지시: 황금 점수 높은 것부터)
GOLD_INDEX, GOOD_INDEX, SAT_INDEX = 0.3, 0.1, 0.01  # 표준 황금지수(검색량 ÷ 문서 수): 0.3 이상 황금(키워드파인더 '×100 ≥ 30'), 0.1 이상 좋음
OLD_DAYS = 365              # 정면 경쟁 글 중 1년 넘은 글 수 (참고 표시용)
FEW_DOCS = 10                # 글이 이보다 적으면 검색 도구가 만든 어색한 조합일 수도 → '확인 필요' 표시 (점수는 그대로)
# 사이트를 찾아가는 검색: 검색량은 커도 사람들이 공식 사이트로 바로 가서 글을 안 읽음 → 황금 찾기·가점에서 뺌
NAVIGATIONAL = ("로그인", "홈페이지", "바로가기", "사이트", "고객센터", "전화번호", "콜센터", "앱다운", "어플다운",
                "다운로드", "인터넷뱅킹", "공식", "주소", "위치", "영업시간", "운영시간",
                # 네이버가 검색 결과 맨 위에 계산기·조회창·실시간 값을 바로 보여 주는 검색 (블로그 글을 거의 안 엶, 11/5 첫 집계 상위가 전부 이것)
                "환율", "택배", "시세", "복권", "로또", "날씨", "운세", "주가", "날짜계산")
WORKERS = 8
CHUNK = 500                  # 이만큼씩 부르고 저장 (중간에 막혀도 받은 만큼은 남김)
SCHEMA = ("CREATE TABLE IF NOT EXISTS naver_signals (norm TEXT PRIMARY KEY, docs INTEGER, docs_checked TEXT,"
          " peak TEXT, low TEXT, trend_checked TEXT, hits INTEGER, old INTEGER)")
SAMPLE = 30                  # 상위 몇 개 글의 제목을 볼지 (호출 1회 그대로, 응답만 조금 커짐)


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


def _title_key(text: str) -> str:
    return norm(re.sub(r"<[^>]+>", "", html.unescape(text or "")))


def _one_total(query: str, keys: tuple[str, str], today: dt.date) -> dict | None:
    """{docs: 전체 문서 수(표시용), hits: 상위 SAMPLE 개 중 제목에 키워드가 그대로 든 글 수, old: 그중 1년 넘은 글 수}."""
    try:
        got = _call("/search/v1/blog", keys, params={"query": query, "display": SAMPLE})
        if "total" not in got:
            return None
        want, cutoff = norm(query), f"{today - dt.timedelta(days=OLD_DAYS):%Y%m%d}"
        matched = [it for it in got.get("items") or [] if want in _title_key(it.get("title", ""))]
        old = sum(1 for it in matched if str(it.get("postdate") or "99999999") < cutoff)
        return {"docs": int(got["total"]), "hits": len(matched), "old": old}
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Throttled("429") from e
        return None
    except (OSError, ValueError, KeyError):
        return None


def blog_totals(words: list[str], keys: tuple[str, str], today: dt.date, save=None) -> dict[str, dict | None]:
    """블로그 검색 결과 문서 수 (여러 개 동시에). CHUNK 개씩 상한을 잡고 부르고, save 가 있으면 묶음마다 저장.
    네이버가 한도를 알리면(429) 받은 만큼만 돌려준다."""
    out: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i in range(0, len(words), CHUNK):
            chunk = words[i:i + CHUNK]
            _spend(today, len(chunk), "blog")
            try:
                got = dict(zip(chunk, pool.map(lambda w: _one_total(w, keys, today), chunk)))
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


# 정보를 찾는 검색의 수식어 (절차·자격·기한·돈 규칙). 키워드 수집용 MODIFIERS 중 쇼핑·사전 성격('가격·시세·할인·견적·평균·뜻·
# 비교·종류·장단점')과 상품명에 끼는 말('나이'→나이키)은 뺌 — 11/5 집계 상위가 '아로나민골드가격·나이키v2k런·치폴레뜻'이었음.
INFO_MODIFIERS = ("방법", "하는법", "절차", "신청", "접수", "발급", "재발급", "등록", "변경", "해지", "취소", "조회", "갱신",
                  "연장", "조건", "자격", "대상", "기준", "요금", "금액", "수수료", "지원금", "지원", "환급", "계산", "기간",
                  "기한", "마감", "과태료", "벌금", "불이익", "서류", "세금", "차이", "해결방법")


# 수식어가 없어도 정보 검색인 우리 주제 단어 (제도·돈·행정) — '근로장려금'·'청약통장'처럼 말 자체가 제도라 글로 답할 수 있음.
# 상품명·브랜드·가게·인물·지명은 여기 없어서 빠짐 (운영자 질문 '수식어 없는 황금은 필요 없나' 11/5).
DOMAIN_WORDS = ("청약", "적금", "예금", "통장", "전세", "월세", "주택", "부동산", "임대", "자동차세", "재산세", "취득세", "등록세",
                "여권", "운전면허", "등본", "초본", "인감", "주민등록", "가족관계", "국가장학금", "학자금", "장학금", "근로",
                "퇴직", "실업", "육아휴직", "출산", "임신", "보육", "양육", "아동수당", "부모급여", "기초연금", "노령", "장애",
                "건강검진", "예방접종", "국민연금", "건강보험", "고용보험", "산재", "최저임금", "주휴", "연차", "상속", "증여",
                "양도", "종합소득", "부가세", "사업자", "폐업", "창업", "청년", "신혼", "다자녀", "한부모", "기초생활", "차상위",
                "바우처", "민원", "과태료", "범칙금", "벌점", "자동차검사", "명의이전", "전입신고", "확정일자", "보증금")


GOV_VOCAB = COUNTER_DIR / "gov-vocab.json"   # 정부 혜택 서비스 이름에서 뽑은 제도 단어 (일주일마다 새로)
VOCAB_DAYS = 7
GENERIC = {"서비스", "프로그램", "지원사업", "사업", "센터", "운영", "관리", "지원금지원", "제공", "추진", "활성화", "육성", "조성",
           "구축", "설치", "개선", "강화", "교육", "상담", "안내", "기타", "일반", "특별", "긴급", "한시"}


def _vocab_words(names: list[str]) -> set[str]:
    """서비스 이름 → 제도 단어 (3~12자, 너무 흔한 말·숫자 제외). 예: '주택금융공사 월세자금보증' → 주택금융공사·월세자금보증."""
    words = set()
    for name in names:
        for part in re.split(r"[\s·,()\[\]/\-<>「」『』ㆍ:]+", name or ""):
            w = norm(part)
            if 3 <= len(w) <= 12 and not w.isdigit() and w not in GENERIC:
                words.add(w)
    return words


def refresh_vocab(today: dt.date | None = None, fetch=None, path: Path | None = None) -> str:
    """정부 혜택(공공서비스) 이름 전체 → 제도 단어 사전. VOCAB_DAYS 안에 받았으면 건너뜀. 실패해도 예전 사전 유지."""
    today, path = today or dt.date.today(), path or GOV_VOCAB
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("checked", "") >= f"{today - dt.timedelta(days=VOCAB_DAYS):%Y-%m-%d}":
            return f"제도 단어 사전: {len(old.get('words', [])):,}개 (최근 받음)"
    except (OSError, ValueError):
        pass
    if fetch is None:
        from .benefit_api import fetch as fetch
    names, page = [], 1
    while page <= 20:
        got = fetch("serviceList", page=page, perPage=1000)
        rows = (got or {}).get("data") or []
        names += [r.get("서비스명", "") for r in rows]
        if len(rows) < 1000:
            break
        page += 1
    if not names:
        return "제도 단어 사전: 받지 못함 (예전 사전 유지)"
    words = sorted(_vocab_words(names))
    path.write_text(json.dumps({"checked": f"{today:%Y-%m-%d}", "words": words}, ensure_ascii=False), encoding="utf-8")
    _gov_words.cache_clear()
    return f"제도 단어 사전: 정부 혜택 {len(names):,}개 이름에서 {len(words):,}개"


@functools.lru_cache(maxsize=1)
def _gov_words() -> frozenset[str]:
    try:
        return frozenset(json.loads(GOV_VOCAB.read_text(encoding="utf-8")).get("words", []))
    except (OSError, ValueError):
        return frozenset()


def _has_gov_word(k: str) -> bool:
    """키워드 안에 제도 단어가 들어 있는지 (3~12자 부분 문자열을 사전에서 찾음 — 빠름)."""
    words = _gov_words()
    return bool(words) and any(k[i:i + n] in words for n in range(3, min(12, len(k)) + 1) for i in range(len(k) - n + 1))


def intent(keyword: str) -> str:
    """'info'(정보 검색 — 황금 가점 전부) / 'unknown'(판단 애매 — 버리지 않고 가점 절반, 글쓰기 세션이 판단) /
    'skip'(사이트 찾아가기·네이버가 바로 답함·브랜드 — 가점 없음). 운영자 지시 '놓치는 거 없게' (11/5)."""
    from .keyword_filter import is_brand
    from .select_keyword import topic_weight
    k = norm(keyword)
    if is_navigational(k) or is_brand(k):
        return "skip"
    if topic_weight(k) > 0 or any(m in k for m in INFO_MODIFIERS + DOMAIN_WORDS) or _has_gov_word(k):
        return "info"
    return "unknown"


def is_info(keyword: str) -> bool:
    """정보를 찾는 검색 (intent == 'info'). 상품명·사전 검색은 빠짐 (11/5 집계 상위가 '스케쳐스윈터부츠·아로나민골드가격' 등)."""
    return intent(keyword) == "info"


INTENT_WEIGHT = {"info": 1.0, "unknown": 0.5, "skip": 0.0}


def per_search(sig: dict | None, total: int) -> float | None:
    """검색 1회당 블로그 문서 수 (표시용 — 점수에는 안 씀, 붙여 쓴 말이 부풀어서)."""
    if not sig or sig.get("docs") is None or total <= 0:
        return None
    return sig["docs"] / total


def gold_index(sig: dict | None, total: int) -> float | None:
    """표준 황금지수 = 검색량 ÷ 블로그 문서 수 (클수록 좋음)."""
    if not sig or sig.get("docs") is None or total <= 0:
        return None
    return total / max(sig["docs"], 1)


def gold_score(sig: dict | None, total: int) -> float:
    """황금 점수 = log10(검색량) × min(황금지수 ÷ 0.3, 1)."""
    if not sig or sig.get("docs") is None or total <= 0 or sig["docs"] < FEW_DOCS:
        return 0.0  # 글이 거의 없으면(성인어·어색한 조합 등 네이버가 0으로 보이는 말) 가점 안 줌 — 가짜 황금 방지
    return math.log10(max(total, 1)) * min(gold_index(sig, total) / GOLD_INDEX, 1.0)


def gold_label(sig: dict | None, total: int) -> str:
    """황금지수 등급: 0.3 이상 황금, 0.1 이상 좋음, 0.01 미만 포화."""
    index = gold_index(sig, total)
    if index is None:
        return ""
    if sig["docs"] < FEW_DOCS:
        return "확인 필요(글 거의 없음)"
    if index >= GOLD_INDEX:
        return "황금"
    if index >= GOOD_INDEX:
        return "좋음"
    return "포화" if index < SAT_INDEX else "보통"


def signal_score(sig: dict | None, total: int, month: int, keyword: str = "") -> float:
    """글감 점수 조정 (select_keyword): 황금 점수 × GOLD_WEIGHT × (정보 1 / 애매 0.5 / 제외 0) + 성수기."""
    if not sig:
        return 0.0
    s = GOLD_WEIGHT * gold_score(sig, total) * (INTENT_WEIGHT[intent(keyword)] if keyword else 1.0)
    peak = [int(x) for x in (sig.get("peak") or "").split(",") if x]
    low = [int(x) for x in (sig.get("low") or "").split(",") if x]
    if month in peak or (month % 12 + 1) in peak:  # 이번 달 또는 다음 달이 성수기 → 지금 써야 시즌에 상위 노출
        s += 1.0
    elif month in low:
        s -= 0.5
    return s


def describe(sig: dict | None, total: int, keyword: str = "") -> str:
    """후보 목록에 붙일 한 줄: '황금 3.4 / 성수기 1·2월 / 상위 30개 중 정면 경쟁 2개(1년 넘은 글 1개)'."""
    if not sig:
        return ""
    parts = []
    label = gold_label(sig, total)
    if label:
        parts.append(f"{label} {gold_score(sig, total):.1f}" + (" (분류 미정 — 정보 글로 답할 수 있으면 쓰고, 아니면 건너뛰기)"
                                                               if keyword and intent(keyword) == "unknown" else ""))
    if sig.get("peak"):
        parts.append(f"성수기 {'·'.join(sig['peak'].split(','))}월")
    index = gold_index(sig, total)
    if index is not None:
        parts.append(f"황금지수 {index:.2f}(검색 {total:,}/글 {sig['docs']:,})")
    if sig.get("hits") is not None:  # 참고
        parts.append(f"참고: 상위 {SAMPLE}개 중 제목 일치 {sig['hits']}개" + (f"(1년 넘은 글 {sig['old']}개)" if sig.get("old") else ""))
    return " / ".join(parts)


def is_gold(sig: dict | None, total: int, keyword: str = "") -> bool:
    """황금·좋음 (검색 1회당 글 1개 미만, 글 10개 이상, 사이트 찾아가는 검색 아님) — 글쓰기·공개를 먼저 할 키워드."""
    return gold_label(sig, total) in ("황금", "좋음") and is_info(keyword)


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
    have = {r[1] for r in conn.execute("PRAGMA table_info(naver_signals)")}
    for col in ("hits", "old"):  # 11/5 첫 판에는 없던 열
        if col not in have:
            conn.execute(f"ALTER TABLE naver_signals ADD COLUMN {col} INTEGER")
    return {r["norm"]: dict(r) for r in conn.execute("SELECT * FROM naver_signals")}


def _save_docs(conn, docs: dict[str, dict | int | None], today: dt.date) -> int:
    load(conn)  # 표·열 준비 (새 창고·대기 글만 먼저 조회하는 경우)
    rows = []
    for w, got in docs.items():
        if got is None:
            continue
        got = got if isinstance(got, dict) else {"docs": got, "hits": None, "old": None}
        rows.append((norm(w), got["docs"], got.get("hits"), got.get("old"), f"{today:%Y-%m-%d}"))
    conn.executemany("INSERT INTO naver_signals(norm, docs, hits, old, docs_checked) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(norm) DO UPDATE SET docs=excluded.docs, hits=excluded.hits, old=excluded.old,"
                     " docs_checked=excluded.docs_checked", rows)
    conn.commit()
    return len(rows)


def scan_golden(conn, settings, today: dt.date | None = None, keys=None) -> str:
    """키워드 창고에서 아직(또는 30일 넘게) 안 본 키워드를 돈·계산기 주제·검색량 순으로 GOLD_SCAN 개 문서 수 조회.
    정보 검색(is_info)만 조회 — 상품명·사이트 찾아가는 검색은 한도를 쓰지 않는다."""
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
        sig = known.get(n) or {}
        return (sig.get("docs_checked") or "") < cutoff

    kinds = {r["norm"]: intent(r["keyword"]) for r in rows}
    fresh = [r for r in rows if stale(r["norm"]) and is_allowed(r["keyword"], settings.blocked_words) and kinds[r["norm"]] != "skip"]
    fresh.sort(key=lambda r: (kinds[r["norm"]] == "info", topic_weight(r["norm"]), r["total"]), reverse=True)  # 정보 검색 먼저
    batch = [r["keyword"] for r in fresh[:GOLD_SCAN]]
    saved = 0

    def save(got: dict) -> None:
        nonlocal saved
        saved += _save_docs(conn, got, today)

    try:
        blog_totals(batch, keys, today, save=save)
        head = f"황금 키워드: 오늘 {saved}개 조회 (남은 미조회 {max(0, len(fresh) - len(batch)):,}개)"
    except LimitReached as e:  # 한도에 닿아도 지금까지의 집계는 남김 (11/5: 집계 없이 끝나 '몇 개 찾았나'에 답 못 함)
        head = f"황금 키워드: 오늘 {saved}개 조회 후 상한 ({e}), 남은 미조회 {max(0, len(fresh) - saved):,}개"
    return head + " / " + golden_report(conn, rows)


def golden_report(conn, rows=None, top_n: int = 5, volume: tuple[int, int] | None = None) -> str:
    """지금까지 찾은 황금 키워드 수와 황금 점수 상위 — 글감 후보와 같은 검색량 범위(설정의 최소~최대)로 셈."""
    if rows is None:
        if volume is None:
            from .settings import load_settings
            st = load_settings()
            volume = (st.min_volume, st.max_volume)
        rows = conn.execute("SELECT keyword, norm, total FROM keywords WHERE status IN ('new','ok') AND total BETWEEN ? AND ?",
                            volume).fetchall()
    sigs = load(conn)
    counts = {"info": [0, 0, 0], "unknown": [0, 0, 0]}  # 조회, 황금, 좋음
    scored = []
    for r in rows:
        sig = sigs.get(r["norm"])
        kind = intent(r["keyword"])
        if not sig or sig.get("docs") is None or kind == "skip":
            continue
        label = gold_label(sig, r["total"])
        counts[kind][0] += 1
        counts[kind][1] += label == "황금"
        counts[kind][2] += label == "좋음"
        if kind == "info" and label in ("황금", "좋음"):
            scored.append((label == "황금", gold_score(sig, r["total"]), r["norm"], r["total"], sig["docs"]))
    scored.sort(reverse=True)
    top = ", ".join(f"{n} {g:.1f}(검색 {t:,}/글 {d:,})" for _, g, n, t, d in scored[:top_n])
    i, u = counts["info"], counts["unknown"]
    return (f"정보 검색 {i[0]:,}개 중 황금 {i[1]:,}개·좋음 {i[2]:,}개 / 분류 미정 {u[0]:,}개 중 황금 {u[1]:,}개·좋음 {u[2]:,}개"
            f" — 정보 검색 상위: {top or '없음'}")


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
    vocab = refresh_vocab()  # 정부 혜택 이름 → 제도 단어 사전 (일주일마다)
    with connect() as conn:
        queue = vocab + " / " + refresh_queue(conn)  # 공개 대기 글 먼저 (적은 호출로 오늘 공개 순서에 바로 반영)
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
    if argv[1:] == ["--report"]:  # 지금까지 조회한 결과 집계만 (호출 없음)
        from .keyword_db import connect
        with connect() as conn:
            msg = "황금 키워드 집계: " + golden_report(conn, top_n=10)
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
