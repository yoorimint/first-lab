"""네이버 검색 화면에서 상위 글을 열어 본다 (11/11 운영자 '네이버 검색 화면 직접 여는 거 해라').

글쓰기 작업이 새 글을 쓰기 전(daily-writer.md 3단계)에 쓴다. 일반 웹 검색 순서는 네이버 상위 글과 달라서,
네이버 블로그 탭과 통합검색 화면을 그대로 열어 상위 글 주소와 본문을 모은다. 네이버 API 는 쓰지 않는다
(API 는 검색어 트렌드·블로그 문서 수 두 가지만 — bot/naver_hub.py).

사용: python3 automation/naver_top.py "<키워드>" [블로그 수, 기본 5] > 상위글.txt
출력: 상위 글마다 주소·제목·본문 앞부분·물음표 문장. 글쓰기 작업은 이것을 읽고 serp(주소·질문·더한 것)를 적는다 (bot/serp_gate.py).
사실·숫자는 여기서 가져오지 않는다 (틀리거나 낡은 글이 많음) — 다루는 질문·목차만 본다.
"""
from __future__ import annotations

import html
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
SEARCH = "https://search.naver.com/search.naver?{q}"
SKIP = re.compile(r"naver\.(com|net)|pstatic|navercorp|google\.|apple\.com|youtube|first-lab\.kr")
TEXT_MAX = 2500
PAUSE = 1.0   # 네이버에 부담 주지 않게 요청 사이 쉼


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def blog_links(page: str, limit: int) -> list[str]:
    """블로그 탭 결과 순서대로 글 주소 (같은 글 중복 제거)."""
    out = []
    for m in re.finditer(r"https://(?:m\.)?blog\.naver\.com/([A-Za-z0-9_-]+)/(\d{6,})", page):
        url = f"https://blog.naver.com/{m.group(1)}/{m.group(2)}"
        if url not in out:
            out.append(url)
    return out[:limit]


def web_links(page: str, limit: int) -> list[str]:
    """통합검색 화면의 네이버 밖 사이트 (계산기·정보 사이트·공공기관). 첫 화면 순서대로, 사이트당 1개."""
    out, hosts = [], set()
    for url in re.findall(r'href="(https?://[^"]+)"', page):
        url = html.unescape(url)
        host = urllib.parse.urlparse(url).netloc.lower()
        if SKIP.search(host) or host in hosts or urllib.parse.urlparse(url).path in ("", "/"):
            continue
        hosts.add(host)
        out.append(url)
    return out[:limit]


def mobile(url: str) -> str:
    m = re.match(r"https://blog\.naver\.com/([^/]+)/(\d+)", url)
    return f"https://m.blog.naver.com/PostView.naver?blogId={m.group(1)}&logNo={m.group(2)}" if m else url


def text_of(page: str) -> tuple[str, str]:
    """(제목, 본문 글자). 네이버 블로그는 본문 칸만, 그 밖엔 script·style 뺀 전체."""
    title = (re.findall(r'<meta property="og:title" content="([^"]*)"', page) or re.findall(r"<title>(.*?)</title>", page, re.S) or [""])[0]
    m = re.search(r'<div class="se-main-container">(.*?)<div class="(?:comment_area|post_footer|ssp-adcontent)', page, re.S)
    body = m.group(1) if m else page
    body = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", body)
    words = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))).replace("​", "").strip()
    return html.unescape(title).strip(), words


def questions(text: str) -> list[str]:
    return list(dict.fromkeys(q.strip() for q in re.findall(r"[^.?!]{6,80}\?", text)))[:12]


def report(keyword: str, n_blog: int = 5, n_web: int = 3, get=fetch, pause: float = PAUSE) -> str:
    q = urllib.parse.quote(keyword)
    lines = [f"# 네이버 상위 글: {keyword}"]
    targets = []
    try:
        targets += [("블로그", u) for u in blog_links(get(SEARCH.format(q=f"ssc=tab.blog.all&query={q}")), n_blog)]
        time.sleep(pause)
        targets += [("웹", u) for u in web_links(get(SEARCH.format(q=f"where=nexearch&query={q}")), n_web)]
    except Exception as e:  # noqa: BLE001 - 검색 화면이 안 열리면 serp 에 unavailable 로 적게
        return "\n".join(lines + [f"검색 화면을 못 엶: {e} → serp 에 {{\"unavailable\": \"네이버 검색 화면 안 열림\"}}"])
    for i, (kind, url) in enumerate(targets, 1):
        time.sleep(pause)
        try:
            title, words = text_of(get(mobile(url)))
        except Exception as e:  # noqa: BLE001 - 글 하나 못 열어도 나머지는 봄
            lines.append(f"\n## {i}. [{kind}] {url}\n(못 엶: {e})")
            continue
        lines.append(f"\n## {i}. [{kind}] {title}\n{url}")
        qs = questions(words)
        if qs:
            lines.append("물음표 문장: " + " / ".join(qs))
        lines.append(words[:TEXT_MAX])
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    print(report(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5))
