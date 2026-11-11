"""근거 대조 (지금은 기록만): 글쓴이가 '맞음'으로 남긴 주장의 숫자가, 그 주장에 단 공식 근거 페이지에 실제로 있는지.

왜: 외부 검수에서 고친 곳 대부분이 '글쓴이는 맞음이라 했지만 공식 출처로 확인 안 됨'(응시료·메뉴·ARS 번호 등).
관문(review_gate)은 기록의 형식만 보므로, 근거 링크를 실제로 열어 숫자를 대조한다.

지금은 공개를 막지 않는다(큐넷처럼 자주 안 열리는 공식 사이트 때문에 오탐이 생길 수 있음).
결과는 글 메타(META)와 봇 기록에 남기고, 감시 세션이 외부 검수 결과와 며칠 비교한 뒤 보류에 쓸지 정한다.

대조하는 숫자: 금액·건수처럼 세 자리 이상이거나 소수점이 있는 수, 비율, 두 자리 수 + 단위(14일·39세·24개월 — 날짜 빼고).
한 자리 수(5일)·단위 없는 두 자리 수(12억의 '12')는 어디에나 있어 제외.
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
MIN_KOREAN = 400       # 한글이 이보다 적으면 본문 없는 껍데기 페이지(화면을 스크립트로 채움) → 못 엶
# (11/5 실측: 심평원 껍데기 0자, 에너지바우처 신청 안내 1,394자 — 1,500 이던 기준이 짧은 실제 페이지를 '못 읽음'으로 잘못 셈)
CALC_MARKS = ("이면", "=", "×", "계산", "합계", "예시")  # 계산 결과 주장 표시
# '→' 는 숫자→숫자일 때만 계산 (3,000원 → 2,700원). 길·절차 화살표('대구→고령→해인사행 하루 21회')까지 계산으로 봐서
# 그 주장의 숫자를 대조하지 않았음 (11/6 #1060 대장경테마파크 버스 횟수 — 외부 검수가 확인불가)
CALC_ARROW = re.compile(r"\d[^→]{0,12}→\s*(?:약\s*)?\d")


def is_calc_claim(claim: str) -> bool:
    return any(m in claim for m in CALC_MARKS) or bool(CALC_ARROW.search(claim))
MAX_NUMBERS = 3        # 숫자가 이보다 많은 주장은 계산 과정 나열로 보고 대조하지 않음 (예: 사례별 세액 목록)


def key_numbers(claim: str) -> list[str]:
    """대조할 숫자: 세 자리 이상(쉼표 뺌) 또는 소수. 계산 결과 주장은 대조하지 않음."""
    # 비율(20%·10%)은 두 자리여도 대조 — 글의 핵심 숫자인데 빠져서 다자녀 통행료 글(#861)이 '숫자 주장 0개'로 나옴 (11/5).
    # 법령은 '100분의 20' 으로 쓰지만 has_number 의 분수 표기가 함께 찾음. 비율은 계산 결과가 아니라 규칙 값이라
    # '3명 이상이면 20%' 처럼 계산 표시('이면')가 있어도 대조한다.
    pct = {m for m in re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트)", claim)}
    if is_calc_claim(claim):
        if any(m in claim for m in ("=", "×", "재계산", "예시")):
            return []  # 계산식의 비율은 예시 가정값(예금 금리 2.5% 등)이라 근거 페이지에 없음 — 표본 20개 중 2개 오탐
        return sorted(pct) if len(pct) <= MAX_NUMBERS else []
    joined = _join_eok_man(claim)
    nums = sorted({n for n in _numbers(joined) if "." in n or len(n.lstrip("0")) >= 3} | pct | unit_numbers(claim))
    return nums if len(nums) <= MAX_NUMBERS else []


def _join_eok_man(text: str) -> str:
    """'1억 400만' → '10400만' (만 원 단위 한 숫자). 따로 두면 '400' 만 대조돼 법령 '1억4백만원' 과 못 맞춤 (11/8 #2085 '페이지에 없음' 오탐)."""
    return re.sub(r"(\d+)\s*억\s*(\d[\d,]*)\s*만",
                  lambda m: f"{int(m.group(1)) * 10000 + int(m.group(2).replace(',', ''))}만", text or "")


# 두 자리 수 + 단위(14일·15회·39세·24개월·60시간) — 기간·나이·횟수 조건이 글의 핵심인데 세 자리 미만이라 대조 안 됐음 (11/6 운영자
# 'API 다 써서' 3번, #872 '14일 연장'). 날짜(11월 27일)는 페이지 표기(11.27.)가 달라 뺌. 저장소 글 40편 실측: 확인 193 · 없음 15(날짜·계산 예시 대부분).
_UNIT = re.compile(r"(?<![\d.,])(\d{2})\s*(?=일|개월|년|세|회|시간|명|주)")


_DATE_BEFORE = re.compile(r"\d\s*월\s*$|\d\s*월\s*\d{1,2}\s*일\S{0,4}\s*~\s*$")   # '11월 27일', '11월 16일(월)~27일'


def unit_numbers(claim: str) -> set[str]:
    return {m.group(1) for m in _UNIT.finditer(claim) if not _DATE_BEFORE.search(claim[:m.start()])}


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
    # 그림 설명 글(alt): 기준을 그림으로 올리고 글자는 alt 에만 둔 공식 페이지 (11/7 #1245 도로교통공단 '허용되는 사진 규격' — '못 엶')
    alts = " ".join(html.unescape(a) for a in re.findall(r"""(?is)<img\b[^>]*\balt=["']([^"']{10,})["']""", text))
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)) + (" " + alts if alts else "")
    return text.replace(",", "")


# 첨부 파일(PDF·hwpx) 글자 — 고시·공고 금액은 게시글 본문이 아니라 첨부에만 있는 경우가 많음
# (11/5 #808 난임치료휴가 상한액 5건, #842 기준 중위소득 18건이 '페이지에 없음'으로 나옴)
_ATTACH = re.compile(r"""href=["']([^"']*(?:fileDown|FileDown|download|Download|flDownload|atchFile|\.pdf|\.hwpx)[^"']*)["']""")
MAX_ATTACH = 3


_HWP_SKIP16 = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}  # 16바이트 확장 제어 문자


def hwp5_text(raw: bytes) -> str:
    """HWP 5.0 본문(BodyText/Section*)의 문단 글자(PARA_TEXT, 태그 67). olefile 없거나 못 읽으면 ''."""
    import io
    import struct
    import zlib
    try:
        import olefile
        ole = olefile.OleFileIO(io.BytesIO(raw))
        compressed = ole.openstream("FileHeader").read()[36] & 1
        sections = sorted((e for e in ole.listdir() if e[0] == "BodyText"), key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
        out = []
        for sec in sections:
            data = ole.openstream(sec).read()
            data = zlib.decompress(data, -15) if compressed else data
            i = 0
            while i + 4 <= len(data):
                head = struct.unpack_from("<I", data, i)[0]
                tag, size, i = head & 0x3FF, (head >> 20) & 0xFFF, i + 4
                if size == 0xFFF:
                    size, i = struct.unpack_from("<I", data, i)[0], i + 4
                if tag == 67:
                    chunk, j, chars = data[i:i + size], 0, []
                    while j + 2 <= len(chunk):
                        c = struct.unpack_from("<H", chunk, j)[0]
                        if c in _HWP_SKIP16:
                            chars.append(" ")
                            j += 16
                            continue
                        chars.append("\n" if c in (10, 13) else (chr(c) if c >= 32 else ""))
                        j += 2
                    out.append("".join(chars))
                i += size
        return "\n".join(out)
    except Exception:  # noqa: BLE001 - olefile 없음·깨진 파일 → 예전처럼 ''
        return ""


def doc_text(raw: bytes) -> str:
    """PDF(pdftotext)·hwpx(zip 안 XML) 바이트 → 글자 (쉼표 뺌). 못 읽으면 ''."""
    import io
    import subprocess
    import zipfile
    try:
        if raw[:4] == b"%PDF":
            out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, timeout=60)
            text = out.stdout.decode("utf-8", "ignore")
        elif raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # HWP 5.0 (OLE) — 법제처 고시 첨부 (11/6 #1210)
            text = hwp5_text(raw)
        elif raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                parts = [z.read(n).decode("utf-8", "ignore") for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)]
            text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))
        else:
            return ""
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError):
        return ""  # pdftotext 없음·깨진 파일 → 예전처럼 본문만
    return re.sub(r"\s+", " ", text).replace(",", "")


def attachment_text(page_html: str, page_url: str, raw_of=None) -> str:
    """공공기관(.go.kr·.or.kr) 게시글의 첨부 링크를 MAX_ATTACH 개까지 받아 글자를 붙임."""
    host = urllib.parse.urlparse(page_url).netloc
    if not re.search(r"\.(go|or)\.kr$", host):
        return ""
    raw_of = raw_of or _raw
    texts = []
    from .condition_gate import _cached
    for link in list(dict.fromkeys(_ATTACH.findall(page_html or "")))[:MAX_ATTACH]:
        full = urllib.parse.urljoin(page_url, html.unescape(link))

        def read(full=full):
            got = raw_of(full)
            return doc_text(got[0]) if got else ""
        texts.append(read() if raw_of is not _raw else _cached("attach", full, read))  # 같은 날 같은 첨부는 다시 안 받음
    return " ".join(t for t in texts if t)


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


def law_annex(url: str) -> str:
    """'/법령/<이름>' 근거면 그 법령 현행 판 별표 글자 (쉼표 뺌) — 봉급표·과태료 별표 숫자가 조문 화면에 없어 '페이지에 없음'으로
    나왔음 (11/6 #1189 과태료 115일·#1199 군무원 봉급표 8개). 못 받으면 ''."""
    plain = urllib.parse.unquote(url)
    if "law.go.kr/법령/" not in plain:
        return ""
    name = plain.split("/법령/")[1].split("/")[0].split("?")[0]
    from . import law_api
    from .condition_gate import _cached

    def make() -> dict:
        try:
            if not law_api.available():
                return {}
            version = law_api.current(law_api.versions(name), dt.date.today())
            text = law_api.annex_text(version) if version else ""
        except Exception:  # noqa: BLE001 - 못 받으면 별표 없이 대조 (예전처럼)
            return {}
        return {"text": re.sub(r"\s+", " ", text).replace(",", "")} if text else {}
    return (_cached("annex", name, make) or {}).get("text", "")


def fetch(url: str) -> str | None:
    """law.go.kr 본문은 같은 날 다시 받지 않음 (condition_gate._cached) — 근거 대조를 여러 번 돌려도 빠르게.
    정부24 혜택 주소는 화면 글자에 공공서비스 API 상세 원문을 더함 (bot/benefit_detail, 11/6)."""
    if "law.go.kr" in urllib.parse.unquote(url):
        from .condition_gate import _cached
        page = _cached("page", url, lambda: _fetch(url))
        annex = law_annex(url)
        return f"{page} {annex}" if page and annex else page
    from .benefit_detail import detail_text, service_ids
    ids = service_ids([url])
    official = (detail_text(ids[0]) or "") if ids else ""
    page = _fetch(url)
    if official:
        return f"{page or ''} {official.replace(',', '')}".strip()
    return page


def _fetch(url: str) -> str | None:
    """공식 페이지 글자. 못 열거나 껍데기 페이지면 None, PDF 등 문서면 "". 법령 이름 주소는 본문 주소로 한 번 더 연다."""
    from .law_rules import rule_of, rule_text
    rule = rule_of(url)
    if rule:  # '/행정규칙/이름'·'/자치법규/이름' 화면은 껍데기 → 법제처 API 원문 (첨부 고시 PDF 포함, 11/6)
        return rule_text(*rule)
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
            raw = resp.read(MAX_BYTES)
            if "html" not in ctype and "text" not in ctype:
                return doc_text(raw)  # PDF·hwpx 는 글자를 뽑아 대조 (못 읽으면 '' = 열리긴 함)
            text = page_text(raw, resp.headers.get_content_charset())
            text += " " + attachment_text(raw.decode(resp.headers.get_content_charset() or "utf-8", "ignore"), url)
            return text if len(re.findall(r"[가-힣]", text)) >= MIN_KOREAN else None
    except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError, OSError):
        return None


def _fraction_forms(number: str) -> list[str]:
    """퍼센트 값의 법령식 표기: 0.5 → '1천분의 5', 20 → '100분의 20' (분자가 정수일 때만)."""
    if not re.fullmatch(r"\d+(?:\.\d+)?", number):
        return []  # 날짜 '2027.1.1' 같은 것 — float 변환 실패로 근거 대조 전체가 멈췄음 (11/6 #1023 프리랜서 3.3%)
    forms = []
    for denom, scale in (("100", 1), ("1천", 10), ("1000", 10), ("1만", 100), ("10000", 100), ("10만", 1000), ("100000", 1000),
                         ("100만", 10000), ("1000000", 10000)):  # 장기요양 '100만분의 9,448' = 0.9448% (11/6 #1228)
        value = float(number) * scale
        if value == int(value):
            forms.append(rf"{denom}\s*분의\s*{int(value)}(?!\d)")
    return forms


def _korean_forms(number: str) -> list[str]:
    """7000(만 원) → '7천', 3300 → '3천300', 15000 → '1억5천': 법령·공문은 천·억 단위를 한글로 쓰는 경우가 많다
    (11/5 #553: 소득세법 '3천300만원'을 3,300만 원과 같은 값으로 못 봐 '페이지에 없음' 오탐)."""
    if "." in number or int(number) < 1000:
        return []
    n = int(number)
    forms = []
    if n < 10000:  # 1천~9천999 (만 원 단위로 쓰는 경우)
        rest = n % 1000
        forms.append(rf"(?<![\d.]){n // 1000}\s*천\s*{rest if rest else ''}(?!\d)")
        if rest and rest % 100 == 0:  # '4천8백' (11/8 #2085 법령 '4천8백만원')
            forms.append(rf"(?<![\d.]){n // 1000}\s*천\s*{rest // 100}\s*백")
    else:  # 1억 이상 (만 원 단위 1만 = 1억)
        eok, rest = divmod(n, 10000)
        if rest == 0:
            forms.append(rf"(?<![\d.]){eok}\s*억(?!\s*\d)")
        elif rest % 1000 == 0:
            forms.append(rf"(?<![\d.]){eok}\s*억\s*{rest // 1000}\s*천")
        else:
            forms.append(rf"(?<![\d.]){eok}\s*억\s*{rest}(?!\d)")
        thousands, hundreds = divmod(rest, 1000)
        if rest % 100 == 0 and hundreds:  # '1억4백', '1억2천4백' (법령 '1억4백만원')
            th = rf"{thousands}\s*천\s*" if thousands else ""
            forms.append(rf"(?<![\d.]){eok}\s*억\s*{th}{hundreds // 100}\s*백")
    return forms


def _man_forms(number: str) -> list[str]:
    """875533 → '87만 5533' / '87 만 5533' (보도자료 표가 만 단위로 끊어 씀 — 11/5 #842 생계급여 금액 '페이지에 없음' 오탐)."""
    if not number.isdigit() or int(number) < 10000:
        return []
    man, rest = divmod(int(number), 10000)
    return [rf"(?<![\d.]){man}\s*만\s*{rest}(?!\d)" if rest else rf"(?<![\d.]){man}\s*만\s*원"]


def has_number(text: str, number: str) -> bool:
    # 시각(10:30)의 시·분은 그 숫자가 아님 (11/6 #1060: 확인된 '외국인 10명'의 10 이 버스 시각 10:30 에 맞아 지움으로 잘못 셈)
    # 소수점 뒤(3.5 의 5)는 아님, 주소 속 숫자(www.15990903.or.kr)는 그 숫자 (11/7 #1462 '페이지에 없음' 오탐)
    patterns = [rf"(?<![\d:])(?<!\d\.){re.escape(number)}(?!\d|:\d)"] + _fraction_forms(number) + _korean_forms(number) + _man_forms(number)
    return any(re.search(p, text) for p in patterns)


def check(review: list[dict] | None, fetcher=fetch, upcoming=None) -> dict:
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
            if missing:  # law.go.kr '/법령/이름' 은 현행 판만 엶 — 시행 예정 개정에만 있는 숫자는 그 판에서 찾음 (11/8 #2149 국적법 21725)
                later = upcoming or (upcoming_law_text if fetcher is fetch else (lambda u: ""))  # 시험(가짜 fetcher)에선 API 안 부름
                texts_later = [later(u) for u in urls]
                missing = [n for n in missing if not any(t and has_number(t, n) for t in texts_later)]
            status = MISSING if missing else FOUND
        claims.append({"claim": claim[:120], "numbers": nums, "missing": missing, "status": status, "urls": urls[:3]})
    for claim, _, urls in plain:
        if all(u in pages and pages[u] is None for u in urls):  # 근거가 전부 못 여는·껍데기 페이지 (PDF 는 제외)
            claims.append({"claim": claim[:120], "numbers": [], "missing": [], "status": UNREADABLE, "urls": urls[:3]})
    counts = {s: sum(c["status"] == s for c in claims) for s in (FOUND, MISSING, UNOPENED, UNREADABLE)}
    found = [{"claim": c["claim"][:80], "numbers": c["numbers"], "urls": c["urls"]} for c in claims if c["status"] == FOUND][:30]
    laws = sorted({(law_page(str(u)), art) for item in review or [] if _ok(item) for u in item.get("urls", [])
                   for art in [claim_article(item, str(u))] if art})
    # found·laws: 공개 직전 확인(bot/freshness.py)이 '쓸 때 확인된 숫자가 아직 페이지에 있는지·근거 조문이 그 뒤 바뀌었는지' 다시 본다
    return {"counts": counts, "claims": [c for c in claims if c["status"] != FOUND], "found": found, "laws": [list(x) for x in laws],
            "reviewed": reviewed_hosts(review)}


def reviewed_hosts(review: list[dict] | None) -> list[str]:
    """검수에서 '맞음'으로 확인한 주장의 공식 근거 사이트 (법령 제외, www. 뺀 주소) — 글 끝 '이 글은 … 로 확인했어요' 줄
    (wordpress/mu-plugins/first-lab-author-box.php, 11/7 운영자 '고용노동부도 확인됐으면 같이 넣어주면 되는 거 아냐?')."""
    out = []
    for item in review or []:
        if not _ok(item):
            continue
        for u in item.get("urls", []):
            host = (urllib.parse.urlparse(str(u).strip()).hostname or "").lower()
            host = host[4:] if host.startswith("www.") else host
            if host and "law.go.kr" not in host and is_official(str(u)) and host not in out:
                out.append(host)
    return out


_NO_REVIEWED_PHP = r"""
$out = array();
$ids = get_posts( array( 'post_type' => 'post', 'post_status' => array( 'publish', 'draft' ), 'numberposts' => -1, 'fields' => 'ids',
  'meta_key' => '_firstlab_source_check' ) );
foreach ( $ids as $id ) {
  $r = json_decode( (string) get_post_meta( $id, '_firstlab_source_check', true ), true );
  if ( is_array( $r ) && ! array_key_exists( 'reviewed', $r ) ) { $out[] = array( 'id' => $id, 'slug' => urldecode( get_post_field( 'post_name', $id ) ) ); }
  if ( count( $out ) >= BATCH ) { break; }
}
echo wp_json_encode( $out );
"""


def backfill_reviewed(batch: int = 10, wp=None, review_of=None) -> str | None:
    """예전 근거 대조 기록에 'reviewed'(검수로 확인한 공식 사이트)가 없으면 저장소 큐 파일의 검수 기록으로 채운다.
    없는 글이 없어지면 아무것도 안 함 (기록이 없어도 빈 목록을 넣어 같은 글을 다시 안 봄)."""
    if wp is None:
        from .settings import wp_cli as wp
    if review_of is None:
        from .freshness import _review_from_queue as review_of
    todo = json.loads(wp("eval", _NO_REVIEWED_PHP.replace("BATCH", str(int(batch)))) or "[]")
    done = []
    for row in todo if isinstance(todo, list) else []:
        record = json.loads(wp("post", "meta", "get", str(row["id"]), META) or "{}")
        record["reviewed"] = reviewed_hosts(review_of(str(row.get("slug", ""))))
        wp("post", "meta", "update", str(row["id"]), META, json.dumps(record, ensure_ascii=False))
        done.append(f"{row['id']}({len(record['reviewed'])})")
    return f"검수 확인 사이트 채움: {', '.join(done)}" if done else None


RECENT_DAYS = 365  # 이 기간 안에 개정·시행된 조문은 안내 페이지가 아직 옛 내용일 수 있음 (11/5 #512 납부지연 가산세 2026.7.1 개정)


def article_of(url: str) -> str | None:
    """law.go.kr 주소의 조문 번호 ('/법령/국세기본법/제47조의4' → '제47조의4')."""
    m = re.search(r"/(제\d+조(?:의\d+)?)", urllib.parse.unquote(url))
    return m.group(1) if "law.go.kr" in url and m else None


EXCEPTION_MARKS = ("다만", "제외한다", "제외하고", "그러하지 아니하다", "넘을 때에는", "한정한다", "에도 불구하고", "보다 낮은 경우", "초과하는 경우",
                   "적용하지 아니한다")  # 11/6 #1093 지방세기본법 제55조④ '45만원 미만이면 제4호 가산세를 적용하지 아니한다'
ABSOLUTE = re.compile(r"(하나뿐|하나예요|하나인데|하나만|만 (?:돼요|가능|할 수)|에서만|모두|누구나|항상|반드시|없어요|불가|안 돼요|할 수 없어요)")


def article_text(text: str, article: str) -> str:
    """법령 본문에서 한 조문만 (다음 조문 제목 전까지)."""
    start = text.find(article + "(")
    if start < 0:
        return ""
    nxt = re.search(r"제\d+조(?:의\d+)?\(", text[start + len(article) + 1:])
    return text[start: start + len(article) + 1 + (nxt.start() if nxt else 3000)]


def exceptions_in(article_body: str, limit: int = 4) -> list[str]:
    """조문 안의 예외·단서 문장 (글이 원칙만 쓰고 이걸 빠뜨리는 일이 많음 — 11/5 #491·#512·#535)."""
    parts = re.split(r"(?<=다\.)\s+|(?=\s[②-⑳]\s?)", re.sub(r"<[^>]*>", "", article_body))
    out = []
    for x in parts:
        if not any(m in x for m in EXCEPTION_MARKS):
            continue
        items = re.split(r"\s(?=\d+(?:의\d+)?\.\s)", x)
        if len(x) > 200 and len(items) > 2:
            # 호를 나열한 긴 항(과태료 목록 등): 예외가 든 호만 따로 — 앞 200자만 보여 주면 뒤쪽 호의 '(…경우는 제외한다)'가
            # 잘려 안 보였음 (11/6 #1143 건설근로자법 제26조②7호 '도급인이 직접 내야 하는 경우는 제외')
            head = re.match(r"\s*([①-⑳])", x)
            out += [f"{head.group(1) + ' ' if head else ''}{it.strip()}"[:200] for it in items[1:] if any(m in it for m in EXCEPTION_MARKS)]
        else:
            out.append(x.strip()[:200])
    return out[:limit]


def claim_article(item: dict, url: str) -> str | None:
    """조문 번호: 주소('/법령/이름/제N조')에 없으면 주장 문장('고용보험법 제46조')에서."""
    if "law.go.kr" not in url:
        return None
    m = re.search(r"제\d+조(?:의\d+)?", str(item.get("claim", "")))
    return article_of(url) or (m.group(0) if m else None)


_UPCOMING: dict[str, str] = {}


def upcoming_law_text(url: str, today=None) -> str:
    """'/법령/이름' 주소 → 시행 예정 판들의 본문(공포번호·부칙 포함 XML). 법제처 API 를 못 쓰거나 예정 판이 없으면 ''."""
    import datetime as dt
    m = re.search(r"law\.go\.kr/법령/([^/?#()]+)", urllib.parse.unquote(url))
    if not m:
        return ""
    name = m.group(1)
    if name not in _UPCOMING:
        try:
            from . import law_api
            today = today or dt.date.today()
            _UPCOMING[name] = " ".join(law_api.fetch("service", "law", MST=v["mst"]) or ""
                                       for v in law_api.versions(name) if v["date"] > today)
        except Exception:  # noqa: BLE001 - 대조 보조라 실패해도 원래 결과('페이지에 없음')대로
            _UPCOMING[name] = ""
    return _UPCOMING[name]


def law_page(url: str) -> str:
    """조문을 찾을 법령 본문 주소 ('/법령/이름/제N조' → '/법령/이름', lsInfoR 주소는 그대로)."""
    return re.sub(r"(/법령/[^/?#]+)/.*$", r"\1", urllib.parse.unquote(url))


def listed_cases(article_body: str, limit: int = 12) -> list[str]:
    """'다음 각 호의 어느 하나에 해당하는 …' 처럼 경우를 나열한 조문의 각 호 (글이 그중 일부만 쓰는 일이 잦음 —
    11/5 #651 연차 출근 간주 제60조⑥에서 유산·사산휴가 빠짐, #687 대체공휴일 제3조①에서 '평일에 다른 공휴일과 겹침' 빠짐)."""
    text = re.sub(r"<[^>]*>", "", article_body)
    out = []
    for m in re.finditer(r"다음 각 호[^.]*?\.", text):
        rest = text[m.end():]
        stop = re.search(r"\s[②-⑳]\s?", rest)
        block = rest[: stop.start() if stop else 2500]
        items = re.split(r"\s(?=\d{1,2}\.\s)", " " + block)
        out += [re.sub(r"\s+", " ", x).strip()[:160] for x in items if re.match(r"\s*\d{1,2}\.\s", x)]
    return out[:limit]


def law_exceptions(review: list[dict] | None, fetcher=fetch) -> list[dict]:
    """글이 근거로 단 법령 조문마다 예외·단서 문장. 글쓴이가 그 예외를 글에 넣었는지 확인하게 한다."""
    out, cache, done = [], {}, set()
    for item in review or []:
        if not _ok(item):
            continue
        for url in item.get("urls", []):
            art = claim_article(item, str(url))
            base = law_page(str(url))
            if not art or (base, art) in done:
                continue
            done.add((base, art))
            if base not in cache:
                cache[base] = fetcher(base)
            body = article_text(cache[base] or "", art)
            found, cases = exceptions_in(body), listed_cases(body)
            if found or cases:
                name = base.rsplit("/", 1)[-1] if "/법령/" in base else str(item.get("claim", ""))[:20]
                out.append({"law": name, "article": art, "exceptions": found, "cases": cases})
    return out


# 법 조문이 내용을 하위 규정에 맡기는 말 — 그 하위 규정(시행령·시행규칙·고시)을 안 읽고 쓰면 기간·범위가 빠짐
# (11/6 #885 채용서류 반환 청구 기간은 시행령 제4조에만, #896 전기자전거 안전기준 적용 범위 '이륜자전거'는 고시에만 — 같은 꼴 2번 → 검사)
_DELEGATE = re.compile(r"[^.]*?(?:대통령령|총리령|[가-힣]{1,8}부령|[가-힣]{2,8}장관이 정하여 고시|[가-힣]{2,8}장관이 고시)[^.]*?\.")
LOWER_RULE_MARKS = ("시행령", "시행규칙", "/행정규칙/", "admrul", "flDownload")


def delegations(review: list[dict] | None, fetcher=fetch, limit: int = 3) -> list[dict]:
    """근거 법 조문이 하위 규정에 맡긴 문장 중, 글의 근거에 그 법의 시행령·시행규칙·고시가 하나도 없는 것."""
    urls = [urllib.parse.unquote(str(u)) for item in review or [] if _ok(item) for u in item.get("urls", [])]
    out, cache, done = [], {}, set()
    for item in review or []:
        if not _ok(item):
            continue
        for url in item.get("urls", []):
            art, base = claim_article(item, str(url)), law_page(str(url))
            if not art or "/법령/" not in base or (base, art) in done:
                continue
            done.add((base, art))
            name = base.rsplit("/", 1)[-1]
            if re.search(r"시행령|시행규칙$", name):
                continue                      # 이미 하위 규정
            if any(name in u and any(m in u for m in ("시행령", "시행규칙")) for u in urls) or any("/행정규칙/" in u for u in urls):
                continue                      # 하위 규정도 근거로 읽음
            if base not in cache:
                cache[base] = fetcher(base)
            body = article_text(cache[base] or "", art)
            found = [re.sub(r"\s+", " ", m.group(0)).strip()[:200] for m in _DELEGATE.finditer(body)][:limit]
            if found:
                out.append({"law": name, "article": art, "sentences": found})
    return out


def _addenda_text(name: str) -> str:
    """법령 현행 판의 부칙 글자 (법제처 API, 같은 날 다시 안 받음). 못 받으면 ''."""
    import datetime as _dt
    from . import law_api
    from .condition_gate import _cached

    def make() -> str:
        cur = law_api.current(law_api.versions(name), _dt.date.today())
        xml = law_api.fetch("service", "law", MST=str(cur["mst"])) if cur else ""
        parts = re.findall(r"<부칙내용>(.*?)</부칙내용>", xml or "", re.S)
        return re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>|\s+", " ", " ".join(parts))
    return _cached("addenda", name, make) or ""


def addendum_transitions(review: list[dict] | None, addenda_of=None, limit: int = 3) -> list[dict]:
    """근거 조문을 부칙이 '제N조…의 개정규정에도 불구하고' 대상자별로 달리 정한 경과조치 (예전 면허·계약 등).
    11/6 #973 건설기계 안전교육: 2019.10.18 전 면허는 발급일에 따라 2020~2022년 — 글은 '취득일부터 3년'만, #928 건축법 부칙 제5조도 같은 꼴."""
    addenda_of = addenda_of or _addenda_text
    out, done = [], set()
    for item in review or []:
        if not _ok(item):
            continue
        for url in item.get("urls", []):
            art, base = claim_article(item, str(url)), law_page(str(url))
            if not art or "/법령/" not in base or (base, art) in done:
                continue
            done.add((base, art))
            text = addenda_of(base.rsplit("/", 1)[-1])
            found = [re.sub(r"\s+", " ", m.group(0)).strip()[:220] for m in
                     re.finditer(rf"[^.]*{re.escape(art)}(?![의\d])(?:제\d+항)?[^.]{{0,20}}개정규정에도 불구하고[^.]*\.", text)][:limit]
            if found:
                out.append({"law": base.rsplit("/", 1)[-1], "article": art, "sentences": found})
    return out


CONDITION_WORDS = ("이하", "이상", "초과", "미만", "경우", "한해", "한정", "때만", "기준", "가정")
QUALIFIERS = ("최대", "최소", "이하", "이상", "초과", "미만", "까지", "부터", "기준", "약", "예시", "최고", "최저")


def headline_conditions(review: list[dict] | None, title: str, excerpt: str) -> list[str]:
    """제목·요약 숫자가 조건이 붙은 주장에서 왔는데 제목·요약엔 조건이 없을 때 (11/5 #493 수수료·#553 '한도 74만 원')."""
    from .review_gate import _headline_numbers, _numbers
    out = []
    for field, text in (("제목", title), ("요약", excerpt)):
        for n in sorted(_headline_numbers(text)):
            claims = [str(i.get("claim", "")) for i in review or [] if _ok(i) and n in _numbers(str(i.get("claim", "")))]
            conditional = [c for c in claims if any(w in c for w in CONDITION_WORDS)]
            if not conditional or len(conditional) < len(claims):
                continue  # 조건 없이 뒷받침하는 주장도 있으면 제목 그대로 괜찮음
            raw = re.search(rf"[^·,:]*{re.escape(n[:-3] + ',' + n[-3:]) if len(n) > 3 else re.escape(n)}[^·,]*", text) or re.search(re.escape(n), text)
            around = raw.group(0) if raw else text
            if "~" not in around and not any(q in around for q in QUALIFIERS):  # '6~45%' 처럼 범위로 쓴 것은 조건 포함
                out.append(f"{field}의 '{around.strip()[:40]}' — 근거 주장엔 조건이 있음: {conditional[0][:70]}")
    return out


def absolute_sentences(content_html: str, limit: int = 15) -> list[str]:
    """본문에서 단정하는 문장 ('하나·만·모두·없어요·불가' …) — 예외를 원문에서 확인했는지 볼 목록."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content_html or "")))
    sents = re.split(r"(?<=요\.)\s+|(?<=다\.)\s+", text)
    return [x.strip()[:160] for x in sents if ABSOLUTE.search(x)][:limit]


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
            art = claim_article(item, str(url))
            if not art:
                continue
            base = law_page(str(url))
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
    for law in law_exceptions(review):
        print(f"- [예외 확인: {law['law']} {law['article']}] 이 조문의 단서·예외가 글에 필요한지 확인:")
        for x in law["exceptions"]:
            print(f"    · {x}")
        if law.get("cases"):
            print(f"  [경우 나열: {law['law']} {law['article']}] 이 조문이 나열한 경우를 글이 모두 다루는지(일부만 쓰면 '등'·'다음 중 하나'로) 확인:")
            for x in law["cases"]:
                print(f"    · {x}")
    for d in addendum_transitions(review):
        print(f"- [부칙 경과조치: {d['law']} {d['article']}] 이 조문을 예전 대상자(시행 전 면허·계약 등)에게는 달리 적용하는 부칙 — 해당 독자가 있으면 글에 넣는다:")
        for x in d["sentences"]:
            print(f"    · {x}")
    for d in delegations(review):
        print(f"- [위임 확인: {d['law']} {d['article']}] 이 조문이 하위 규정에 맡긴 내용(기간·금액·범위)을 그 시행령·시행규칙·고시에서 읽었는지 — "
              "python3 -m bot.lawtext <법령명>시행령 <제N조> 로 확인하고 그 주소도 근거에 단다:")
        for x in d["sentences"]:
            print(f"    · {x}")
    data = json.loads(open(argv[1], encoding="utf-8").read())
    for x in headline_conditions(review, data.get("title", ""), data.get("excerpt", "")):
        print(f"- [제목 조건 확인] {x} → 제목·요약에 조건(최대·이하·기준 등)을 붙이거나 조건 없는 값으로")
    from .condition_gate import TITLE_WARN, lead_vs_exceptions, title_numbers
    nums = title_numbers(data.get("title", ""))
    if len(nums) > 1:
        print(f"- [제목 숫자] {len(nums)}개({', '.join(nums)}) — 핵심 숫자 1개만, 나머지는 요약문·본문으로 (제목 형식: 키워드+찾는 말+숫자 1개)")
    if len(data.get("title", "")) > TITLE_WARN:
        print(f"- [제목 길이] {len(data['title'])}자 — {TITLE_WARN}자 안팎으로 (45자 넘으면 서버가 보류): 키워드+찾는 말+핵심 숫자 1개")
    from .condition_gate import cross_law_exceptions, excerpt_caveats, excerpt_parentheticals, excerpt_qualifiers, limited_event_year
    for x in excerpt_parentheticals(data.get("excerpt", ""), data.get("content_html", "")):
        print(f"- [요약 조건] {x}")
    for x in limited_event_year(data.get("excerpt", ""), data.get("content_html", "")):
        print(f"- [한시 행사 연도] {x}")
    from .condition_gate import deadline_holiday
    for x in deadline_holiday(data.get("content_html", "")):
        print(f"- [예시 기한 휴일] {x}")
    for x in excerpt_qualifiers(data.get("excerpt", ""), data.get("content_html", "")) + excerpt_caveats(data.get("excerpt", ""), data.get("content_html", "")):
        print(f"- [요약 조건] {x}")
    for x in cross_law_exceptions(data.get("content_html", "")):
        print(f"- [다른 법 예외] {x}")
    for x in lead_vs_exceptions(data.get("content_html", "")):
        print(f"- [첫 문단 예외 확인] {x}")
    for x in absolute_sentences(data.get("content_html", "")):
        print(f"- [단정 문장] {x} → 예외(제외 대상·조건·상한·하한)를 원문에서 확인")
    # 서버 관문과 똑같은 검사 (review_gate.problems 가 조건 누락 관문까지 부름) — 여기서 고치면 보류되지 않음 (11/5 검토: 일부만 보던 것)
    from . import review_gate
    gate = review_gate.problems(review, data.get("title", ""), data.get("excerpt", ""), data.get("content_html", ""))
    gate += review_gate.second_review_missing(review)  # 공개 대기열 첫 저장과 같게 (11/8)
    from . import serp_gate
    gate += serp_gate.problems(data.get("serp"), data.get("content_html", ""))  # 상위 글 분석 (11/11)
    gate += serp_gate.copied(data.get("serp"), data.get("content_html", ""))
    for x in gate:
        print(f"- [관문] {x}")
    for a in amended(review):
        print(f"- [최근 개정 조문 {a['article']} {', '.join(a['dates'])}] {a['claim']} → 안내 페이지·기사 값이 아니라 현행 조문·부칙으로 다시 확인")
    if gate:
        print(f"관문 {len(gate)}건 — 이대로 올리면 서버가 보류함")
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
