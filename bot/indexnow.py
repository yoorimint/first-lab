"""IndexNow: 글이 공개되는 순간 검색엔진(빙 등 참여 엔진)에 '새 주소가 생겼다'고 바로 알린다 (11/5 운영자 승인).

크롤러가 찾아올 때까지 기다리지 않아 색인이 빨라진다. 키는 공개값이다 — 사이트 루트의 <KEY>.txt 로 소유를 증명하는 방식이라
저장소에 둬도 된다 (배포 때 server/wp-setup.sh 가 웹 루트에 키 파일을 만든다).
실패해도 공개에는 영향 없음 (봇 기록에만 남김).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

SITE = "https://first-lab.kr"
KEY = "31e9a0f0ecba2259e6119e00ded31765"
ENDPOINT = "https://api.indexnow.org/indexnow"
TIMEOUT = 15


def payload(urls: list[str]) -> dict:
    return {"host": SITE.split("//", 1)[1], "key": KEY, "keyLocation": f"{SITE}/{KEY}.txt", "urlList": urls}


def notify(urls: list[str]) -> str:
    """알리고 결과 한 줄 (200·202 = 접수)."""
    urls = [u for u in urls if u.startswith(SITE)]
    if not urls:
        return "IndexNow: 알릴 주소 없음"
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload(urls)).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return f"IndexNow 알림 {resp.status} ({len(urls)}개)"
    except urllib.error.HTTPError as e:
        return f"IndexNow 알림 실패 {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"IndexNow 알림 실패: {e}"[:120]
