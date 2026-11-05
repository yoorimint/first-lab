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
MAX_LAWS = 150         # 하루에 여는 법령 수
WORKERS = 6            # 동시에 여는 수 (law.go.kr 이 끊지 않을 정도)
LAW_META = "_firstlab_law_changed"  # '시행일 법령명' — 갱신 대상 표시
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


def scan(laws: dict[str, set[str]], today: dt.date, fetcher=source_check.fetch, names: set | None = None,
         name_of: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(앞으로 시행될 개정, 막 시행된 개정). names 를 주면 읽은 법령 이름을, name_of 를 주면 주소 → 이름을 모은다."""
    upcoming, just, seen = [], [], set()
    bases = sorted(laws)[:MAX_LAWS]
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
        for up, js in pool.map(one, names):
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
            value = f"{item['date']} {item['law']}"
            if wp_cli("post", "meta", "get", post_id, LAW_META).strip() != value:
                wp_cli("post", "meta", "update", post_id, LAW_META, value)
                marked.append(post_id)
    return marked


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
        except (OSError, ValueError) as e:  # API 이상이어도 부칙 읽기 결과는 저장
            api_note = f"법제처 API 실패({type(e).__name__}) → 부칙 읽기만"
    pre = proposals(names)
    marked = mark_posts([j for j in just if j["posts"]])
    state = load_state()  # 저장 때 다른 실행 기록과 합쳐짐 (bot/state.save_state)
    state["law_changes"] = {"checked": f"{today:%Y-%m-%d}", "upcoming": upcoming[:30], "proposals": pre[:30]}
    msg = (f"법령 감시: 근거 법령 {len(laws)}개 확인, 앞으로 시행 {len(upcoming)}건({api_note}), "
           f"입법예고 {len(pre)}건(확정 아님), 막 시행 {len(just)}건(재검증 표시 {len(marked)}편)")
    add_log(state, True, msg)
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
