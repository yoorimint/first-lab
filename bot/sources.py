"""글 끝 '공식 안내 바로가기': 글쓰기 세션이 실제로 확인한 공식 페이지 링크 (신뢰도·독자 편의).

본문 안의 외부 링크는 계속 지운다 (related.clean_links). 여기서는 정부·공공기관 주소만,
실제로 열리는 것만 최대 3개 글 끝에 붙인다.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

OFFICIAL_SUFFIXES = (".go.kr", ".or.kr", ".re.kr", ".ac.kr", ".mil.kr")  # 정부·공공기관·공단·연구기관
# 주소 끝이 위와 다른 공식 기관 (정부 정책브리핑, 공항공사, 도로공사 등). 필요하면 여기에 추가
OFFICIAL_HOSTS = ("korea.kr", "gov.kr", "airport.kr", "airport.co.kr", "ex.co.kr", "hipass.co.kr", "kepco.co.kr",
                  "korcham.net")  # korcham.net: 대한상공회의소(법정단체) 자격평가사업단 — 컴활·워드 국가기술자격 시행기관 (11/5 #431 오탐)
MAX_LINKS = 3
TIMEOUT = 10
HEADING = "공식 안내 바로가기"


# 외국 정부 주소 (여행·비자 글의 근거): 미국 .gov, 영국·호주 등 .gov.XX, 일본·태국 등 .go.XX, 프랑스 .gouv.XX,
# 스페인어권 .gob.XX, 캐나다 .gc.ca, EU europa.eu — 정부만 쓸 수 있는 주소 (11/5 #567 ESTA: 미국 정부 근거가 '공식 출처 없음'으로 보류)
FOREIGN_GOV = re.compile(r"(?:^|\.)(?:[a-z0-9-]+\.)*(?:gov|gov\.[a-z]{2}|go\.[a-z]{2}|gouv\.[a-z]{2}|gob\.[a-z]{2}|gc\.ca|europa\.eu)$")


def is_official(url: str) -> bool:
    parts = urllib.parse.urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    official = any(host.endswith(s) or host == s[1:] for s in OFFICIAL_SUFFIXES)
    official = official or any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS)
    official = official or bool(FOREIGN_GOV.search(host))
    return parts.scheme == "https" and official


def reachable(url: str) -> bool:
    """서버에서 열어 봐서 정상 응답(400 미만)이면 True. 깨진 링크를 싣지 않기 위함."""
    # 한글 주소(예: law.go.kr/법령/...)는 그대로 요청할 수 없어 인코딩해서 연다 (링크는 원래 주소 그대로 씀)
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (first-lab link check)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status < 400
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def section(sources: list[dict], check=reachable) -> tuple[str, list[str]]:
    """(글 끝에 붙일 HTML, 뺀 링크와 이유). 쓸 링크가 없으면 HTML 은 빈 문자열."""
    links, dropped, seen = [], [], set()
    for s in sources or []:
        url, name = str(s.get("url", "")).strip(), str(s.get("name", "")).strip()
        if not url or not name or url in seen:
            continue
        seen.add(url)
        if not is_official(url):
            dropped.append(f"{url} (공식 기관 주소 아님)")
        elif not check(url):
            dropped.append(f"{url} (열리지 않음)")
        elif len(links) < MAX_LINKS:
            links.append(f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
                         f"{html.escape(name)}</a></li>")
    if not links:
        return "", dropped
    return f"<h2>{HEADING}</h2><ul>{''.join(links)}</ul>", dropped
