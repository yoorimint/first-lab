"""새 사업·정책 감시 (하루 1번, 키워드 수집 끝) — 정책브리핑 보도자료(전 부처)에서 개인이 받을 수 있는 새 지원·제도를 글감으로.

왜: 법이 아닌 예산 사업(새 지원금·바우처·모집 공고)은 법령 감시(law_watch)에 안 잡히고, 정부24 등록(benefit_watch)은 시작 뒤에야 올라온다
(11/9 운영자 '법이 아닌 새 사업·정책 잡을 방법 강구해라'). 부처가 발표하는 날 보도자료로 먼저 잡는다.
흐름은 새 혜택 감시와 같다: state["new_policies"] → 읽기 API(/next) new_policies → 글쓰기 세션이 new_benefits 다음 글감
→ 큐 JSON "new_policy": key 로 쓴 것 표시, 쓸 만하지 않으면 skip 파일 "new_policies" (다시 안 나옴).
판단(정말 개인에게 해당하나·글로 쓸 만한가)은 글쓰기 세션이 보도자료 원문으로 하고, 여기서는 행사·외교·회의 같은 것만 걸러 낸다.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import urllib.request

LIST_URL = "https://www.korea.kr/briefing/pressReleaseList.do?pageIndex={page}"
VIEW_URL = "https://www.korea.kr/briefing/pressReleaseView.do?newsId={key}"
PAGES = 8          # 한 쪽 20건 — 하루치 보도자료(평일 100~200건)를 덮게
DAYS = 3           # 최근 이만큼 안에 나온 것만
KEEP = 10
MIN_SCORE = 3

# 개인이 받거나 해야 하는 것 (지원·신청·의무)
BENEFIT = re.compile(r"(신청|접수|모집|지원금|수당|바우처|장려금|감면|할인|환급|무료|지급|대출|융자|공제|혜택|보조금|이용권|쿠폰|상품권|"
                     r"과태료|의무화|의무|신고)")
NEW = re.compile(r"(신설|새로|처음|첫|도입|시행|확대|개편|달라지|바뀌|부터)")
# 개인 글감이 아닌 보도자료 (행사·외교·회의·실적·단속 결과 등)
NOT_PERSONAL = re.compile(r"(개최|접견|면담|회의|간담회|협약|MOU|방문|참석|기념식|시상|수상|발간|포럼|세미나|토론회|위원회 결과|성료|헌정|"
                          r"순방|정상|장관|차관|총리|국정감사|브리핑|인사|임명|해명|설명자료|사실과 다릅|통계|동향|실적|결과 발표|단속 결과|적발)")
AGENCY_SKIP = ("외교부", "국방부", "통일부", "방위사업청", "병무청", "해양경찰청", "소방청", "경찰청", "검찰청", "법무부", "방송미디어통신위원회",
               "원자력안전위원회", "국가정보원", "감사원", "대통령실", "국무조정실")

_ITEM = re.compile(r'<a href="/briefing/pressReleaseView\.do\?newsId=(\d+)[^"]*">\s*<span class="text">\s*<strong>(.*?)</strong>'
                   r'\s*<span class="lead">(.*?)</span>\s*<span class="source">\s*<span>([\d.]+)</span>\s*<span>(.*?)</span>', re.S)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse(page_html: str) -> list[dict]:
    out = []
    for key, title, lead, day, agency in _ITEM.findall(page_html or ""):
        try:
            date = dt.datetime.strptime(day, "%Y.%m.%d").date()
        except ValueError:
            continue
        out.append({"key": key, "title": _clean(title), "lead": _clean(lead)[:300], "date": f"{date:%Y-%m-%d}",
                    "agency": _clean(agency), "url": VIEW_URL.format(key=key)})
    return out


def score(item: dict) -> int:
    """개인 지원·제도일 가능성 (0 이면 글감 아님). 제목의 말은 두 배."""
    title, lead = item.get("title", ""), item.get("lead", "")
    if NOT_PERSONAL.search(title) or any(a in item.get("agency", "") for a in AGENCY_SKIP):
        return 0
    s = 2 * len(set(BENEFIT.findall(title))) + len(set(BENEFIT.findall(lead)))
    s += 2 * bool(NEW.search(title)) + bool(NEW.search(lead))
    return s if BENEFIT.search(title + lead) else 0


def pick(items: list[dict], done: set[str], today: dt.date, keep: int = KEEP) -> list[dict]:
    since = today - dt.timedelta(days=DAYS)
    fresh = {i["key"]: i for i in items if i["key"] not in done and i["date"] >= f"{since:%Y-%m-%d}"}
    scored = sorted(((score(i), i) for i in fresh.values()), key=lambda x: (-x[0], x[1]["date"]), reverse=False)
    return [dict(i, score=s) for s, i in scored if s >= MIN_SCORE][:keep]


def _fetch(page: int) -> str:
    req = urllib.request.Request(LIST_URL.format(page=page), headers={"User-Agent": "Mozilla/5.0 (first-lab policy watch)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def run(today: dt.date | None = None, fetch=_fetch) -> str:
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    items = []
    for page in range(1, PAGES + 1):
        try:
            items += parse(fetch(page))
        except Exception as e:  # noqa: BLE001 - 못 받으면 다음 날 다시 (받은 쪽까지는 씀)
            if not items:
                return f"새 정책 감시: 정책브리핑 보도자료 못 받음({type(e).__name__}) → 내일 다시"
            break
    state = load_state()
    picked = pick(items, set(state.get("new_policies_done") or []), today)
    state["new_policies"] = picked
    msg = (f"새 정책 감시: 보도자료 {len(items)}건 중 개인 지원·제도 글감 {len(picked)}건"
           + (f" — {', '.join(i['title'][:30] for i in picked[:3])}" if picked else ""))
    add_log(state, True, msg)
    save_state(state)
    return msg
