"""법령 변경 감시 (하루 1번, 키워드 수집 끝에 실행 — Claude 사용량 없음, 11/5 운영자 승인).

우리 글들이 근거로 쓴 법령(검수 기록의 law.go.kr 주소) 전체를 매일 열어 부칙의 시행일을 읽는다.
  - 앞으로 시행될 개정(오늘 ~ LOOKAHEAD_DAYS 안) → state["law_changes"] 에 저장
    → 읽기 API(/next) 의 law_changes 로 글쓰기 세션에 '바뀌는 제도' 글감으로 전달 (시행 몇 주 전에 선점)
  - 막 시행된 개정(최근 JUST_DAYS 안) → 그 법을 근거로 한 공개 글에 표시(LAW_META)
    → 글 갱신(bot/refresh.py)이 가장 먼저 '수치 재검증' 대상으로 고른다
공개 전 초안은 공개 직전 확인(bot/freshness.py)이 따로 막는다.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import json
import re
import urllib.parse
from pathlib import Path

from . import law_api, source_check
from .publisher import existing_post_id
from .review_gate import _ok
from .settings import wp_cli
from .state import add_log, load_state, save_state

QUEUE_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "queue"
LOOKAHEAD_DAYS = 120   # 이 안에 시행되는 개정을 글감으로
JUST_DAYS = 30         # 최근 이 안에 시행된 개정은 기존 글 재검증
MAX_LAWS = 400         # 하루에 여는 법령 수 (11/5 검토: 150 이면 몇 주 안에 넘어 가나다순 뒤쪽 법령을 영영 안 봄 — 넘으면 기록에 남김)
UPCOMING_MAX = 150     # 저장하는 시행 예정 항목 수 (계산기 감시 bot/calc_watch 도 이 목록을 봄)
WORKERS = 6            # 동시에 여는 수 (law.go.kr 이 끊지 않을 정도)
from .changed import META as LAW_META, flag  # noqa: E402  '시행일 법령명' — 갱신 대상 표시 (bot/changed.py)
_EFFECTIVE = re.compile(r"(.{0,160}?)(?:개정규정은|이 법은|이 영은|이 규칙은|이 고시는)\s*(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일부터\s*시행")


def law_name(base: str) -> str:
    plain = urllib.parse.unquote(base)
    return plain.rsplit("/", 1)[-1] if "/법령/" in plain else plain[-40:]


def watched_laws() -> dict[str, set[str]]:
    """근거 법령 주소 → 그 법을 근거로 쓴 글 키워드들 (큐 파일의 검수 기록에서)."""
    laws: dict[str, set[str]] = {}
    for path in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keyword = str(data.get("keyword", "")).strip()
        for item in data.get("review") or []:
            if not _ok(item):
                continue
            for url in item.get("urls", []):
                key = law_key(str(url))
                if key:
                    laws.setdefault(key, set()).add(keyword)
    return laws


def cited_articles() -> dict[str, dict[str, set[str]]]:
    """근거 법령 주소 → {조문('제40조', 모르면 '') → 그 조문을 근거로 쓴 글 키워드들}."""
    out: dict[str, dict[str, set[str]]] = {}
    for path in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keyword = str(data.get("keyword", "")).strip()
        for item in data.get("review") or []:
            if not _ok(item):
                continue
            for url in item.get("urls", []):
                key = law_key(str(url))
                if key:
                    art = source_check.claim_article(item, str(url)) or ""
                    out.setdefault(key, {}).setdefault(art, set()).add(keyword)
    return out


def law_key(url: str) -> str | None:
    """같은 법령을 하나로: '/법령/이름(/제N조)' → '/법령/이름', 조문 본문 주소는 lsiSeq 로.
    별표 PDF(flDownload)·조문 연결(lsLawLinkInfo) 등 본문 시행일을 못 읽는 주소는 뺀다."""
    if "law.go.kr" not in url:
        return None
    plain = urllib.parse.unquote(url)
    if "/법령/" in plain:
        return source_check.law_page(url)
    m = re.search(r"lsiSeq=(\d+)", url)
    if m and "lsInfo" in url:
        efyd = re.search(r"efYd=(\d+)", url)
        return f"https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq={m.group(1)}" + (f"&efYd={efyd.group(1)}" if efyd else "")
    return None


def title_of(text: str) -> str:
    """법령 본문 머리의 이름 ('… 고용보험법 [시행 2026. 9. 18.]' → '고용보험법')."""
    m = re.search(r"([가-힣·ㆍ ]{2,40}?)\s*\[시행", text[:600])
    words = m.group(1).split() if m else []
    if not words:
        return ""
    if words[-1] in ("시행령", "시행규칙") and len(words) >= 2:  # '법인세법 시행령'
        return f"{words[-2]} {words[-1]}"
    return words[-1]


def effective_dates(text: str) -> list[tuple[dt.date, list[str]]]:
    """(시행일, 그 시행일에 바뀌는 조문들 — 모르면 빈 목록)."""
    out = []
    for ctx, y, m, d in _EFFECTIVE.findall(text):
        try:
            when = dt.date(int(y), int(m), int(d))
        except ValueError:
            continue
        ctx = re.sub(r"제1조\s*\(\s*시행일\s*\)", "", ctx)  # 부칙 '제1조(시행일)' 은 바뀌는 조문이 아님
        out.append((when, sorted(set(re.findall(r"제\d+조(?:의\d+)?", ctx)))))
    return out


def _safe(fn, default):
    """법령 하나를 읽다 생긴 오류(깨진 응답·XML 오류 등)는 그 법령만 건너뜀."""
    def run(name):
        try:
            return fn(name)
        except Exception:  # noqa: BLE001
            return default
    return run


def scan(laws: dict[str, set[str]], today: dt.date, fetcher=source_check.fetch, names: set | None = None,
         name_of: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(앞으로 시행될 개정, 막 시행된 개정). names 를 주면 읽은 법령 이름을, name_of 를 주면 주소 → 이름을 모은다."""
    upcoming, just, seen = [], [], set()
    bases = sorted(laws)[:MAX_LAWS]
    if len(laws) > MAX_LAWS and names is not None:
        names.add(f"(법령 {len(laws) - MAX_LAWS}개는 한도를 넘어 못 봄 — MAX_LAWS 올리기)")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        texts = dict(zip(bases, pool.map(fetcher, bases)))
    for base in bases:
        text = texts.get(base)
        if not text:
            continue
        name = title_of(text) or law_name(base)
        if names is not None:
            names.add(name)
        if name_of is not None:
            name_of[base] = name
        for when, articles in effective_dates(text):
            key = (name, when, tuple(articles))
            if key in seen:  # 같은 법을 다른 주소로 적은 경우 한 번만
                for item in upcoming + just:
                    if (item["law"], item["date"], tuple(item["articles"])) == (name, f"{when:%Y-%m-%d}", tuple(articles)):
                        item["posts"] = sorted(set(item["posts"]) | laws[base])[:10]
                continue
            seen.add(key)
            item = {"law": name, "url": base, "date": f"{when:%Y-%m-%d}", "articles": articles,
                    "posts": sorted(p for p in laws[base] if p)[:10], "key": f"{name}|{when:%Y-%m-%d}"}
            if today < when <= today + dt.timedelta(days=LOOKAHEAD_DAYS):
                upcoming.append(item)
            elif today - dt.timedelta(days=JUST_DAYS) <= when <= today:
                just.append(item)
    upcoming.sort(key=lambda x: x["date"])
    return upcoming, just


PROPOSAL_LIST = "https://opinion.lawmaking.go.kr/gcom/ogLmPp?pageIndex={page}"  # 국민참여입법센터 (부처)입법예고
PROPOSAL_PAGES = 5
_PROPOSAL_ROW = re.compile(r'href="/gcom/ogLmPp/(\d+)"[^>]*>([^<]+)</a>.*?소관부처[^>]*>\s*<p>([^<]*)</p>.*?접수기간">\s*<p>([^<]*)</p><p>~?([^<]*)</p>', re.S)


def _raw_text(url: str) -> str:
    got = source_check._raw(url)
    return got[0].decode("utf-8", "ignore") if got else ""


def matches(title: str, names: set[str]) -> str:
    """입법예고 제목이 우리 근거 법령의 개정인지 ('지방세법 시행령 일부개정령안' ↔ '지방세법 시행령'). 맞으면 그 이름."""
    compact = title.replace(" ", "")
    for name in sorted(names, key=len, reverse=True):  # 긴 이름 먼저 ('지방세법 시행령' 이 '지방세법' 보다 먼저)
        n = name.replace(" ", "")
        if n and compact.startswith(n) and re.match(r"(일부|전부)?개정|제정|폐지|시행", compact[len(n):] or "개정"):
            return name
    return ""


def proposals(names: set[str], fetch_page=_raw_text) -> list[dict]:
    """입법예고 중 우리 근거 법령의 것 — 확정 아님(바뀌거나 무산될 수 있음), 지켜볼 목록."""
    out, seen = [], set()
    for page in range(1, PROPOSAL_PAGES + 1):
        for pid, title, ministry, start, end in _PROPOSAL_ROW.findall(fetch_page(PROPOSAL_LIST.format(page=page))):
            law = matches(title.strip(), names)
            if law and pid not in seen:
                seen.add(pid)
                out.append({"law": law, "title": title.strip(), "ministry": ministry.strip(),
                            "period": f"{start.strip()} ~ {end.strip()}", "url": f"https://opinion.lawmaking.go.kr/gcom/ogLmPp/{pid}"})
    return out


def api_changes(cited: dict[str, dict[str, set[str]]], name_of: dict[str, str], today: dt.date,
                api=law_api) -> tuple[list[dict], list[dict]]:
    """법제처 공개 API(시행일 기준 판)로 (앞으로 시행될 판, 막 시행된 판). 판마다 우리 글의 근거 조문이 실제로 바뀌는지 비교.
    부칙 읽기(scan)가 못 보는 '법 전체가 나중에 시행되는 개정'까지 잡고, 근거 조문이 안 바뀌는 판은 글 재검증 표시를 안 한다."""
    by_name: dict[str, dict[str, set[str]]] = {}
    for base, arts in cited.items():
        name = name_of.get(base)
        if name:
            for art, posts in arts.items():
                by_name.setdefault(name, {}).setdefault(art, set()).update(posts)

    def one(name: str) -> tuple[list[dict], list[dict]]:
        vers = api.versions(name)
        now = api.current(vers, today)
        if not now:
            return [], []
        arts = by_name[name]
        whole = sorted(p for p in arts.get("", set()) if p)  # 조문 번호 없이 법만 근거로 쓴 글
        def item(v: dict, changed: list[str]) -> dict:
            posts = set(whole) | {p for a in changed for p in arts[a] if p}
            return {"law": name, "url": "", "date": f"{v['date']:%Y-%m-%d}", "articles": changed,
                    "posts": sorted(posts)[:10], "key": f"{name}|{v['date']:%Y-%m-%d}", "source": "법제처 API"}
        upcoming = []
        for v in (v for v in vers if today < v["date"] <= today + dt.timedelta(days=LOOKAHEAD_DAYS)):
            upcoming.append(item(v, []))
        for art in (a for a in arts if a):
            for ch in api.upcoming_changes(name, art, today, vers):
                for u in upcoming:
                    if u["date"] == f"{ch['date']:%Y-%m-%d}":
                        u["articles"].append(art)
                        u["posts"] = sorted(set(u["posts"]) | {p for p in arts[art] if p})[:10]
        just = []
        before = [v for v in vers if v["date"] < now["date"]]
        if before and today - now["date"] <= dt.timedelta(days=JUST_DAYS):
            changed = []
            for art in (a for a in arts if a):
                new, old = api.article(name, art, now), api.article(name, art, before[-1])
                if new and old and not api.same_text(new["text"], old["text"]):
                    changed.append(art)
            if changed or whole:
                just.append(item(now, changed))
        return upcoming, just

    upcoming, just = [], []
    names = sorted(by_name)[:MAX_LAWS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for up, js in pool.map(_safe(one, ([], [])), names):  # 법령 하나의 오류가 전체를 멈추지 않게 (11/5 검토)
            upcoming += up
            just += js
    return upcoming, just


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """같은 법·같은 시행일이면 API 결과(근거 조문까지 비교)를 쓴다."""
    by_key = {x["key"]: x for x in old}
    by_key.update({x["key"]: x for x in new})
    return sorted(by_key.values(), key=lambda x: x["date"])


def mark_posts(just: list[dict]) -> list[str]:
    """막 시행된 개정의 근거 글(공개된 것)에 재검증 표시."""
    marked = []
    for item in just:
        for keyword in item["posts"]:
            post_id = existing_post_id(keyword) if keyword else ""
            if not post_id or wp_cli("post", "get", post_id, "--field=post_status").strip() != "publish":
                continue
            marked += flag([post_id], f"{item['date']} {item['law']}", wp_cli)
    return marked


# 우리 글이 아직 근거로 쓰지 않았어도 사람들이 많이 찾는 생활 법령 — 공포된 개정(시행 예정)을 '바뀌는 제도' 글감으로 (11/5 운영자
# '신규 정책처럼 한발 빠르게 쓸 수 있는 게 또 없나'). 시행령은 금액·요율이 자주 바뀌는 곳이라 같이 본다.
CORE_LAWS = (
    # 일·노동 (11/7 운영자 '주제 좁히기' — 일·세금·4대보험·지원금, '빠지는 거 없게 쓸모 있는 거 전부')
    "근로기준법", "근로기준법 시행령", "최저임금법", "최저임금법 시행령", "근로자퇴직급여 보장법", "근로자퇴직급여 보장법 시행령",
    "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률", "남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률 시행령",
    "기간제 및 단시간근로자 보호 등에 관한 법률", "파견근로자 보호 등에 관한 법률", "임금채권보장법", "임금채권보장법 시행령",
    "구직자 취업촉진 및 생활안정지원에 관한 법률", "국민 평생 직업능력 개발법", "건설근로자의 고용개선 등에 관한 법률",
    "가사근로자의 고용개선 등에 관한 법률", "공휴일에 관한 법률", "관공서의 공휴일에 관한 규정",
    # 4대보험
    "고용보험법", "고용보험법 시행령", "고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률",
    "고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률 시행령", "산업재해보상보험법", "산업재해보상보험법 시행령",
    "국민연금법", "국민연금법 시행령", "국민건강보험법", "국민건강보험법 시행령", "국민건강보험법 시행규칙",
    "노인장기요양보험법", "노인장기요양보험법 시행령",
    # 세금
    "소득세법", "소득세법 시행령", "소득세법 시행규칙", "조세특례제한법", "조세특례제한법 시행령", "국세기본법", "국세기본법 시행령",
    "부가가치세법", "부가가치세법 시행령", "상속세 및 증여세법", "상속세 및 증여세법 시행령", "종합부동산세법", "종합부동산세법 시행령",
    "지방세법", "지방세법 시행령", "지방세특례제한법", "지방세특례제한법 시행령", "고향사랑 기부금에 관한 법률",
    # 복지·지원금
    "기초연금법", "기초연금법 시행령", "아동수당법", "아동수당법 시행령", "국민기초생활 보장법", "국민기초생활 보장법 시행령",
    "주거급여법", "주거급여법 시행령", "의료급여법", "긴급복지지원법", "긴급복지지원법 시행령", "한부모가족지원법", "장애인연금법",
    "장애인복지법", "노인복지법", "영유아보육법", "모자보건법", "에너지법",
    "사회보장급여의 이용ㆍ제공 및 수급권자 발굴에 관한 법률", "국가유공자 등 예우 및 지원에 관한 법률",
    # 주거
    "주택임대차보호법", "주택임대차보호법 시행령",
)
# 해마다 고시로 나오는 기준값 — 고시가 나오면 그날 글감 ('2027년 최저임금 확정'처럼 발표 직후 검색이 몰림)
NOTICE_QUERIES = (("최저임금", "고용노동부"), ("기준 중위소득", "보건복지부"), ("기초연금 선정기준액", "보건복지부"),
                  ("기준소득월액", "보건복지부"), ("장기요양위원회", "보건복지부"), ("구직급여", "고용노동부"))
NOTICE_DAYS = 120             # 최근 이만큼 안에 고시됐거나 아직 시행 전인 고시


def core_changes(today: dt.date, api=law_api, names=None) -> list[dict]:
    """생활 핵심 법령의 시행 예정 판 (LOOKAHEAD_DAYS 안) + 바로 앞 판과 비교해 실제로 바뀌는 조문 (글쓴이가 무엇을 쓸지 알게).
    바뀌는 조문이 없으면(부칙·별표만 등) 글감에서 뺌."""
    def one(name: str) -> list[dict]:
        vers = api.versions(name)
        now = api.current(vers, today)
        prev = api.all_articles(now) if now else {}
        items = []
        for v in (v for v in vers if today < v["date"] <= today + dt.timedelta(days=LOOKAHEAD_DAYS)):
            after = api.all_articles(v)
            changed = api.changed_articles(prev, after) if prev and after else []
            prev = after or prev
            if changed:
                items.append({"law": name, "url": f"https://www.law.go.kr/법령/{name.replace(' ', '')}", "date": f"{v['date']:%Y-%m-%d}",
                              "articles": changed, "posts": [], "key": f"{name}|{v['date']:%Y-%m-%d}", "source": "생활 핵심 법령"})
        return items

    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for items in pool.map(_safe(one, []), names or CORE_LAWS):
            out += items
    return out


def _date8(text: str) -> dt.date | None:
    return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])) if re.fullmatch(r"\d{8}", text or "") else None


def notice_changes(today: dt.date, fetch=law_api.fetch) -> list[dict]:
    """해마다 나오는 기준값 고시(행정규칙) 중 최근 고시됐거나 아직 시행 전인 것 → 글감."""
    import xml.etree.ElementTree as ET
    out, seen = [], set()
    for query, ministry in NOTICE_QUERIES:
        xml = fetch("search", "admrul", query=query, display="10", sort="ddes")
        if not xml:
            continue
        for r in ET.fromstring(xml).iter("admrul"):
            name, key = (r.findtext("행정규칙명") or "").strip(), r.findtext("행정규칙일련번호") or ""
            issued, starts = _date8(r.findtext("발령일자") or ""), _date8(r.findtext("시행일자") or "")
            if (not name or key in seen or (r.findtext("소관부처명") or "") != ministry or not issued
                    or "변경승인" in name or re.search(r"안\s*고시$", name)):  # 통계 승인 절차·확정 전 '○○안 고시'는 뺌
                continue
            if today - issued <= dt.timedelta(days=NOTICE_DAYS) or (starts and starts > today):
                seen.add(key)
                out.append({"law": name, "url": f"https://www.law.go.kr/행정규칙/{name.replace(' ', '')}",
                            "date": f"{(starts or issued):%Y-%m-%d}", "articles": [], "posts": [],
                            "key": f"고시|{key}", "source": f"{ministry} 고시 ({issued:%Y-%m-%d} 발령)"})
    return out


def watched_rules() -> dict[tuple[str, str], set[str]]:
    """근거로 쓴 행정규칙·자치법규 (종류, 이름) → 그것을 근거로 쓴 글 키워드들 (bot/law_rules, 11/6)."""
    from .law_rules import rule_of
    rules: dict[tuple[str, str], set[str]] = {}
    for path in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keyword = str(data.get("keyword", "")).strip()
        for item in data.get("review") or []:
            for url in item.get("urls", []) if _ok(item) else []:
                rule = rule_of(str(url))
                if rule:
                    rules.setdefault(rule, set()).add(keyword)
    return rules


def rule_changes(rules: dict[tuple[str, str], set[str]], seen: dict[str, str], today: dt.date,
                 versions=None) -> tuple[list[dict], list[dict], dict[str, str]]:
    """행정규칙·자치법규: 시행 예정 판(LOOKAHEAD_DAYS 안) → 글감, 지난번과 다른 판이 시행 중이면 → 근거 글 재검증.
    seen: {'종류|이름': 지난번 본 시행 중 판 일련번호}. 처음 보는 것은 기록만 (지금 판으로 이미 썼으니 재검증 안 함)."""
    from .law_rules import current
    from .law_rules import versions as _versions
    versions = versions or _versions
    upcoming, just, now_seen = [], [], dict(seen)

    def one(rule):
        return rule, _safe(lambda r: versions(*r), [])(rule)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, rules))
    for (kind, name), vers in results:
        if not vers:
            continue
        key, posts = f"{kind}|{name}", sorted(k for k in rules[(kind, name)] if k)
        url = f"https://www.law.go.kr/{kind}/{name}"
        cur = current(vers, today)
        if cur and seen.get(key) and seen[key] != cur["seq"]:
            just.append({"law": name, "url": url, "date": f"{(cur['start'] or today):%Y-%m-%d}", "articles": [], "posts": posts,
                         "key": f"{key}|{cur['seq']}", "source": f"{kind} 개정"})
        if cur:
            now_seen[key] = cur["seq"]
        for v in vers:
            if v["start"] and today < v["start"] <= today + dt.timedelta(days=LOOKAHEAD_DAYS):
                upcoming.append({"law": name, "url": url, "date": f"{v['start']:%Y-%m-%d}", "articles": [], "posts": posts,
                                 "key": f"{key}|{v['seq']}", "source": f"{kind} 시행 예정"})
    return upcoming, just, now_seen


def keep_upcoming(upcoming: list[dict], done: list[str]) -> list[dict]:
    """아직 안 쓴 항목을 앞에, 쓴 항목을 뒤에 두고 UPCOMING_MAX 개까지 (같은 무리 안에서는 원래 순서)."""
    gone = set(done)
    return sorted(upcoming, key=lambda u: u.get("key") in gone)[:UPCOMING_MAX]


def run(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    laws = watched_laws()
    names: set[str] = set()
    name_of: dict[str, str] = {}
    upcoming, just = scan(laws, today, names=names, name_of=name_of)
    api_note = "법제처 API 없음 → 부칙 읽기만"
    if law_api.available():
        try:
            api_up, api_just = api_changes(cited_articles(), name_of, today)
            upcoming, just = merge(upcoming, api_up), merge(just, api_just)
            hit = sum(1 for x in api_up if x["articles"])
            api_note = f"법제처 API 시행 예정 판 {len(api_up)}건 중 근거 조문 바뀜 {hit}건"
        except Exception as e:  # noqa: BLE001 - API 이상(XML 깨짐 포함)이어도 부칙 읽기 결과는 저장
            api_note = f"법제처 API 실패({type(e).__name__}) → 부칙 읽기만"
        try:  # 우리 글과 상관없는 생활 핵심 법령·해마다 나오는 기준값 고시 (먼저 쓸 글감)
            from .calc_watch import calc_laws  # 계산기 근거 법령도 같이 (개정되면 계산기 값 고치기 — bot/calc_watch.py)
            watch = sorted(set(CORE_LAWS) | {law for laws_ in calc_laws().values() for law in laws_})
            core, notices = core_changes(today, names=watch), notice_changes(today)
            upcoming = merge(upcoming, core + notices)
            api_note += f", 생활 핵심 법령 시행 예정 {len(core)}건, 기준값 고시 {len(notices)}건"
        except Exception as e:  # noqa: BLE001
            api_note += f", 핵심 법령·고시 실패({type(e).__name__})"
        try:  # 근거로 쓴 행정규칙(고시)·자치법규(조례) — 화면이 껍데기라 부칙 읽기로는 못 봤음 (bot/law_rules, 11/6)
            rules = watched_rules()
            r_up, r_just, rule_seen = rule_changes(rules, load_state().get("rule_versions") or {}, today)
            upcoming, just = merge(upcoming, r_up), merge(just, r_just)
            api_note += f", 행정규칙·자치법규 {len(rules)}개(시행 예정 {len(r_up)}·개정 {len(r_just)})"
        except Exception as e:  # noqa: BLE001
            rule_seen = None
            api_note += f", 행정규칙·자치법규 실패({type(e).__name__})"
    else:
        rule_seen = None
    pre = proposals(names)
    marked = mark_posts([j for j in just if j["posts"]])
    state = load_state()  # 저장 때 다른 실행 기록과 합쳐짐 (bot/state.save_state)
    # 이미 글을 쓴(또는 건너뛴) 항목은 뒤로 — 60칸이 쓴 항목으로 차서 새 글감이 잘리던 일 (11/7: 60건 모두 쓴 것, 새 글감 0)
    upcoming = keep_upcoming(upcoming, state.get("law_changes_done") or [])
    state["law_changes"] = {"checked": f"{today:%Y-%m-%d}", "upcoming": upcoming, "proposals": pre[:30]}
    if rule_seen is not None:
        state["rule_versions"] = rule_seen
    msg = (f"법령 감시: 근거 법령 {len(laws)}개 확인, 앞으로 시행 {len(upcoming)}건({api_note}), "
           f"입법예고 {len(pre)}건(확정 아님), 막 시행 {len(just)}건(재검증 표시 {len(marked)}편)")
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
