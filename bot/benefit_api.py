"""정부24 공공서비스(혜택) 정보 API 읽기 (행정안전부, 공공데이터포털) — 지원금 글감·사실 확인·내용 바뀐 혜택 알림.

어디서 부르든 같은 함수 (bot/law_api.py 와 같은 방식):
  - 서버: 비밀값 DATA_GO_KR_KEY 로 직접
  - 글쓰기·감시 세션: 사이트 통로 /wp-json/firstlab/v1/benefits (환경 변수 FIRSTLAB_API_TOKEN)
  - 둘 다 없거나 실패: None

확인용: python3 -m bot.benefit_api serviceList perPage=3
        python3 -m bot.benefit_api serviceDetail 'cond[서비스ID::EQ]=<서비스ID>'
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

from .config import SECRETS_FILE, load_secrets
from .law_api import _get

KEY_SECRET = "DATA_GO_KR_KEY"
BASE = "https://api.odcloud.kr/api/gov24/v3/{path}"
PROXY = "https://first-lab.kr/wp-json/firstlab/v1/benefits"
PATHS = ("serviceList", "serviceDetail", "supportConditions")


def _server_key() -> str:
    try:
        return load_secrets(SECRETS_FILE).get(KEY_SECRET, "") if SECRETS_FILE.exists() else ""
    except OSError:
        return ""


def _direct_url(path: str, key: str, params: dict) -> str:
    # 포털 키는 '인코딩된 것'(% 포함)과 원래 것 두 가지 — 어느 쪽이든 (first-lab-api.php 와 같은 처리)
    encoded = key if "%" in key else urllib.parse.quote(key, safe="")
    return BASE.format(path=path) + "?" + urllib.parse.urlencode({"returnType": "JSON", **params}) + "&serviceKey=" + encoded


def via_site(path: str, **params) -> dict | None:
    """사이트 통로 응답 그대로 ({status, body, request(키 가림)}). 토큰 없으면 None."""
    token = os.environ.get("FIRSTLAB_API_TOKEN", "")
    if not token:
        return None
    query = urllib.parse.urlencode({"path": path, **params})
    return json.loads(_get(PROXY + "?" + query, {"X-Firstlab-Token": token}))


def fetch(path: str, **params) -> dict | None:
    """결과 JSON ({currentCount, data: [...], matchCount, page, perPage, totalCount}), 못 받으면 None."""
    if path not in PATHS:
        raise ValueError(f"path 는 {'/'.join(PATHS)}")
    try:
        key = _server_key()
        if key:
            return json.loads(_get(_direct_url(path, key, params)))
        got = via_site(path, **params)
        return json.loads(got["body"]) if got and got.get("status") == 200 else None
    except (OSError, ValueError, KeyError):  # 연결 끊김·JSON 아님
        return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    params = dict(a.split("=", 1) for a in argv[2:])
    got = fetch(argv[1], **params)
    if got is None:
        print("받지 못함 (인증키·토큰이 없거나 연결 실패)")
        try:
            raw = via_site(argv[1], **params)
            if raw:
                print(f"통로 응답: 상태 {raw.get('status')}, 요청 {raw.get('request', '')}\n{raw.get('body', '')[:500]}")
        except (OSError, ValueError) as e:
            print(f"통로 응답 못 받음: {e}")
        return 1
    print(json.dumps(got, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
