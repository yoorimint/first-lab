"""법제처 국가법령정보 공동활용 API(DRF) 읽기 — 법령 사이트 화면 읽기 대신 쓰는 정식 통로.

어디서 부르든 같은 함수:
  - 서버: 비밀값 LAW_OPEN_API_OC 로 법제처에 직접 (신청할 때 이 서버 주소를 등록함)
  - 글쓰기·감시 세션(다른 컴퓨터): 사이트 통로 /wp-json/firstlab/v1/law (환경 변수 FIRSTLAB_API_TOKEN, first-lab-api.php)
  - 둘 다 없거나 실패: None → 부르는 쪽이 예전 화면 읽기로 (끊기지 않게)

확인용: python3 -m bot.law_api search law query=고용보험법
        python3 -m bot.law_api service law MST=<법령일련번호> JO=004000
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

from .config import SECRETS_FILE, load_secrets

OC_SECRET = "LAW_OPEN_API_OC"
DRF = "https://www.law.go.kr/DRF/{page}.do"
PROXY = "https://first-lab.kr/wp-json/firstlab/v1/law"
PAGES = {"search": "lawSearch", "service": "lawService"}
TIMEOUT = 30


def _server_oc() -> str:
    try:
        return load_secrets(SECRETS_FILE).get(OC_SECRET, "") if SECRETS_FILE.exists() else ""
    except OSError:  # 권한 없음 등
        return ""


def _get(url: str, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "first-lab-bot", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:  # noqa: S310 — 고정된 법제처·우리 사이트 주소
        return res.read().decode("utf-8", "ignore")


def fetch(kind: str, target: str = "law", **params) -> str | None:
    """kind: search(목록) | service(본문). 결과 XML 글자, 못 받으면 None."""
    if kind not in PAGES:
        raise ValueError(f"kind 는 {'/'.join(PAGES)}")
    try:
        oc = _server_oc()
        if oc:
            query = urllib.parse.urlencode({"OC": oc, "target": target, "type": "XML", **params})
            body = _get(DRF.format(page=PAGES[kind]) + "?" + query)
            return None if not body or is_error(body) else body
        got = via_site(kind, target, **params)
        return got.get("body") if got and got.get("status") == 200 else None
    except (OSError, ValueError):  # 연결 끊김·시간 초과·JSON 아님 → 화면 읽기로
        return None


def is_error(body: str) -> bool:
    """법제처는 오류도 200 으로 돌려준다: <Response><result>…실패…</result> (사용자 검증·필수 입력값)."""
    return "<Response>" in body[:200] and "실패" in body[:400]


def via_site(kind: str, target: str = "law", **params) -> dict | None:
    """사이트 통로 응답 그대로 ({status, body, request(OC 가림)}). 토큰 없으면 None."""
    token = os.environ.get("FIRSTLAB_API_TOKEN", "")
    if not token:
        return None
    query = urllib.parse.urlencode({"kind": kind, "target": target, **params})
    return json.loads(_get(PROXY + "?" + query, {"X-Firstlab-Token": token}))


def available() -> bool:
    return bool(_server_oc() or os.environ.get("FIRSTLAB_API_TOKEN"))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    params = dict(a.split("=", 1) for a in argv[3:])
    body = fetch(argv[1], argv[2], **params)
    if body is None:
        print("받지 못함 (신청값·토큰이 없거나 연결 실패)")
        try:
            got = via_site(argv[1], argv[2], **params)
            if got:
                print(f"통로 응답: 상태 {got.get('status')}, 요청 {got.get('request', '')}\n{got.get('body', '')[:500]}")
        except (OSError, ValueError) as e:
            print(f"통로 응답 못 받음: {e}")
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
