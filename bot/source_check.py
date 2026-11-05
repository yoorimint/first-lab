"""근거 대조 (지금은 기록만): 글쓴이가 '맞음'으로 남긴 주장의 숫자가, 그 주장에 단 공식 근거 페이지에 실제로 있는지.

왜: 외부 검수에서 고친 곳 대부분이 '글쓴이는 맞음이라 했지만 공식 출처로 확인 안 됨'(응시료·메뉴·ARS 번호 등).
관문(review_gate)은 기록의 형식만 보므로, 근거 링크를 실제로 열어 숫자를 대조한다.

지금은 공개를 막지 않는다(큐넷처럼 자주 안 열리는 공식 사이트 때문에 오탐이 생길 수 있음).
결과는 글 메타(META)와 봇 기록에 남기고, 감시 세션이 외부 검수 결과와 며칠 비교한 뒤 보류에 쓸지 정한다.

대조하는 숫자: 금액·건수처럼 세 자리 이상이거나 소수점이 있는 수 (5일·12억의 '5'·'12' 같은 짧은 수는 어디에나 있어 제외).
계산 결과 주장('~이면 ~원', '=')은 출처에 없는 값이라 대조하지 않는다 (재계산은 외부 검수가 함).
법령은 비율을 '1천분의 5'(=0.5%)·'100분의 20' 처럼 쓰므로 그 표기도 같은 값으로 본다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .review_gate import _numbers, _ok
from .sources import is_official

META = "_firstlab_source_check"
TIMEOUT = 8
MAX_URLS = 30          # 글 한 편에서 여는 근거 페이지 수 상한 (배포 시간)
MAX_BYTES = 3_000_000
WORKERS = 8
FOUND, MISSING, UNOPENED = "확인", "페이지에 없음", "못 엶"
UNREADABLE = "근거 못 읽음"  # 숫자 없는 주장(메뉴·절차 등)인데 근거 페이지를 하나도 읽을 수 없음 (11/5 #484 심평원 스크립트 화면)
MAX_ALL_URLS = 40
MIN_KOREAN = 1500      # 한글이 이보다 적으면 본문 없는 껍데기 페이지(화면을 스크립트로 채움) → 못 엶
CALC_MARKS = ("이면", "=", "×", "계산", "합계", "→")  # 계산 결과 주장 표시
MAX_NUMBERS = 3        # 숫자가 이보다 많은 주장은 계산 과정 나열로 보고 대조하지 않음 (예: 사례별 세액 목록)


def key_numbers(claim: str) -> list[str]:
    """대조할 숫자: 세 자리 이상(쉼표 뺌) 또는 소수. 계산 결과 주장은 대조하지 않음."""
    if any(m in claim for m in CALC_MARKS):
        return []
    nums = sorted(n for n in _numbers(claim) if "." in n or len(n.lstrip("0")) >= 3)
    return nums if len(nums) <= MAX_NUMBERS else []


def readable_url(url: str) -> str:
    """같은 내용을 본문째 주는 주소로: 법령 lsInfoP(껍데기) → lsInfoR(조문 본문)."""
    return url.replace("/LSW/lsInfoP.do", "/LSW/lsInfoR.do")


def page_text(raw: bytes, charset: str | None) -> str:
    """HTML → 숫자 대조용 글자 (태그·스크립트 제거, 쉼표 제거)."""
    text = None
    for enc in [c for c in (charset, "utf-8", "euc-kr") if c]:
        try:
            text = raw.decode(enc)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return text.replace(",", "")


def law_body_url(shell_html: str) -> str | None:
    """law.go.kr '/법령/이름' 껍데기 페이지 → 조문 본문 주소 (lsInfoR.do + 시행일 포함 값). 11/5 확인한 방법."""
    m = re.search(r"lsiSeq=\d+[^\"'<>\s]*?efYd=\d+", html.unescape(shell_html))
    return f"https://www.law.go.kr/LSW/lsInfoR.do?{m.group(0)}" if m else None


def _raw(url: str) -> tuple[bytes, str | None, str] | None:
    quoted = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
    req = urllib.request.Request(quoted, headers={"User-Agent": "Mozilla/5.0 (first-lab source check)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status >= 400:
                return None
            return resp.read(MAX_BYTES), resp.headers.get_content_charset(), resp.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError, OSError):
        return None


def fetch(url: str) -> str | None:
    """공식 페이지 글자. 못 열거나 껍데기 페이지면 None, PDF 등 문서면 "". 법령 이름 주소는 본문 주소로 한 번 더 연다."""
    plain_url = urllib.parse.unquote(url)
    if "law.go.kr/" in plain_url and "/법령/" in plain_url:
        # '/법령/이름/제3조' 껍데기에는 본문 주소가 없어 '/법령/이름' 껍데기에서 찾는다
        for candidate in (url, re.sub(r"(/법령/[^/?#]+)/.*$", r"\1", plain_url)):
            got = _raw(candidate)
            body = law_body_url(got[0].decode("utf-8", "ignore")) if got else None
            if body:
                url = body
                break
    quoted = urllib.parse.quote(readable_url(url), safe=":/?#[]@!$&'()*+,;=%")
    req = urllib.request.Request(quoted, headers={"User-Agent": "Mozilla/5.0 (first-lab source check)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if resp.status >= 400:
                return None
            if "html" not in ctype and "text" not in ctype:
                return ""  # PDF 등 문서: 열리긴 함(숫자 대조는 못 함, '근거 못 읽음'으로 세지 않음)
            text = page_text(resp.read(MAX_BYTES), resp.headers.get_content_charset())
            return text if len(re.findall(r"[가-힣]", text)) >= MIN_KOREAN else None
    except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError, OSError):
        return None


def _fraction_forms(number: str) -> list[str]:
    """퍼센트 값의 법령식 표기: 0.5 → '1천분의 5', 20 → '100분의 20' (분자가 정수일 때만)."""
    forms = []
    for denom, scale in (("100", 1), ("1천", 10), ("1000", 10), ("1만", 100), ("10000", 100), ("10만", 1000), ("100000", 1000)):
        value = float(number) * scale
        if value == int(value):
            forms.append(rf"{denom}\s*분의\s*{int(value)}(?!\d)")
    return forms


def _korean_forms(number: str) -> list[str]:
    """7000(만 원) → '7천'(만원): 법령·공문은 천 단위를 한글로 쓰는 경우가 많다."""
    if "." in number or int(number) < 1000 or int(number) % 1000:
        return []
    return [rf"(?<![\d.]){int(number) // 1000}\s*천"]


def has_number(text: str, number: str) -> bool:
    patterns = [rf"(?<![\d.]){re.escape(number)}(?!\d)"] + _fraction_forms(number) + _korean_forms(number)
    return any(re.search(p, text) for p in patterns)


def check(review: list[dict] | None, fetcher=fetch) -> dict:
    """숫자 주장: 확인 / 페이지에 없음 / 못 엶. 숫자 없는 주장: 근거 페이지를 하나도 못 읽으면 '근거 못 읽음'."""
    items, plain = [], []
    for item in review or []:
        nums = key_numbers(str(item.get("claim", "")))
        urls = [str(u).strip() for u in item.get("urls", []) if is_official(str(u))]
        if _ok(item) and urls:
            (items if nums else plain).append((str(item.get("claim", "")), nums, urls))
    unique = list(dict.fromkeys(u for _, _, urls in items + plain for u in urls))[:MAX_ALL_URLS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        pages = dict(zip(unique, pool.map(fetcher, unique)))
    claims = []
    for claim, nums, urls in items:
        texts = [pages[u] for u in urls if pages.get(u)]
        if not texts:
            status, missing = UNOPENED, nums
        else:
            missing = [n for n in nums if not any(has_number(t, n) for t in texts)]
            status = MISSING if missing else FOUND
        claims.append({"claim": claim[:120], "numbers": nums, "missing": missing, "status": status, "urls": urls[:3]})
    for claim, _, urls in plain:
        if all(u in pages and pages[u] is None for u in urls):  # 근거가 전부 못 여는·껍데기 페이지 (PDF 는 제외)
            claims.append({"claim": claim[:120], "numbers": [], "missing": [], "status": UNREADABLE, "urls": urls[:3]})
    counts = {s: sum(c["status"] == s for c in claims) for s in (FOUND, MISSING, UNOPENED, UNREADABLE)}
    return {"counts": counts, "claims": [c for c in claims if c["status"] != FOUND]}


RECENT_DAYS = 365  # 이 기간 안에 개정·시행된 조문은 안내 페이지가 아직 옛 내용일 수 있음 (11/5 #512 납부지연 가산세 2026.7.1 개정)


def article_of(url: str) -> str | None:
    """law.go.kr 주소의 조문 번호 ('/법령/국세기본법/제47조의4' → '제47조의4')."""
    m = re.search(r"/(제\d+조(?:의\d+)?)", urllib.parse.unquote(url))
    return m.group(1) if "law.go.kr" in url and m else None


def recent_changes(text: str, article: str, today: dt.date | None = None) -> list[str]:
    """조문 본문의 '<개정 …>'·'<신설 …>' 날짜와 부칙 '○○의 개정규정은 YYYY년 M월 D일부터 시행' 중 최근 1년·앞으로 시행되는 것."""
    today = today or dt.date.today()
    start = text.find(article + "(")
    if start < 0:
        return []
    nxt = re.search(r"제\d+조(?:의\d+)?\(", text[start + len(article) + 1:])
    body = text[start: start + len(article) + 1 + (nxt.start() if nxt else 3000)]
    dates = [dt.date(int(y), int(m), int(d)) for y, m, d in re.findall(r"(20\d\d)\.\s*(\d{1,2})\.\s*(\d{1,2})\.", body)]
    for y, m, d in re.findall(re.escape(article) + r"[^.]{0,80}?개정규정은\s*(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일", text):
        dates.append(dt.date(int(y), int(m), int(d)))
    return sorted({f"{x:%Y.%m.%d}" for x in dates if (today - x).days <= RECENT_DAYS})


def amended(review: list[dict] | None, fetcher=fetch, today: dt.date | None = None) -> list[dict]:
    """주장의 법령 근거 조문이 최근 1년 안에 개정됐거나 앞으로 시행되면 (안내 페이지·기사 값을 다시 확인할 대상)."""
    out, cache = [], {}
    for item in review or []:
        if not _ok(item):
            continue
        for url in item.get("urls", []):
            art = article_of(str(url))
            if not art:
                continue
            base = re.sub(r"(/법령/[^/?#]+)/.*$", r"\1", urllib.parse.unquote(str(url)))
            if base not in cache:
                cache[base] = fetcher(base)
            found = recent_changes(cache[base] or "", art, today)
            if found:
                out.append({"claim": str(item.get("claim", ""))[:120], "article": art, "dates": found, "url": str(url)})
    return out


def summary(result: dict) -> str:
    c = result["counts"]
    total = sum(c.values())
    if not total:
        return "근거 대조(기록만): 대조할 숫자 주장 없음"
    return (f"근거 대조(기록만): 숫자 주장 {total - c[UNREADABLE]}개 중 확인 {c[FOUND]} · {MISSING} {c[MISSING]} · {UNOPENED} {c[UNOPENED]}"
            f" / 숫자 없는 주장 중 {UNREADABLE} {c[UNREADABLE]}")


def record(post_id: int | str, review: list[dict] | None, save) -> str:
    """대조해서 글 메타에 저장(save(post_id, META, json))하고 한 줄 요약을 돌려준다. 실패해도 공개에는 영향 없음."""
    try:
        result = check(review)
        save(str(post_id), META, json.dumps(result, ensure_ascii=False))
        return summary(result)
    except Exception as e:  # noqa: BLE001 — 기록용 검사라 글 올리기를 막지 않는다
        return f"근거 대조 실패(기록만): {e}"[:150]


def main(argv: list[str]) -> None:
    """글쓰기 세션이 올리기 전에 돌린다: python3 -m bot.source_check ARTICLE.json (review 필드를 읽음)."""
    review = json.loads(open(argv[1], encoding="utf-8").read()).get("review")
    result = check(review)
    print(summary(result))
    for c in result["claims"]:
        print(f"- [{c['status']}] {c['claim']}" + (f" (없는 숫자: {', '.join(c['missing'])})" if c["missing"] else ""))
    for a in amended(review):
        print(f"- [최근 개정 조문 {a['article']} {', '.join(a['dates'])}] {a['claim']} → 안내 페이지·기사 값이 아니라 현행 조문·부칙으로 다시 확인")


if __name__ == "__main__":
    main(sys.argv)
