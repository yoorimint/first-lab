"""공공데이터포털 공식 API 읽기 (정해 둔 API 만) — 글쓰기·검수의 공식 근거.

11/18 운영자 '연결해라': 서민금융진흥원 누리집이 검수 환경 접속을 받지 않아 햇살론 글(#7050)의 대출기간·상환방식 등
18곳을 확인처 안내로 낮춤 → 같은 기관이 공공데이터포털로 공식 개방한 상품 정보를 받는다.
어디서 부르든 같은 함수 (bot/benefit_api.py 와 같은 방식):
  - 서버: 비밀값 DATA_GO_KR_KEY 로 직접
  - 글쓰기·감시 세션: 사이트 통로 /wp-json/firstlab/v1/opendata (환경 변수 FIRSTLAB_API_TOKEN)

확인용: python3 -m bot.opendata loan_basic likeFinPrdNm=햇살론
        python3 -m bot.opendata loan_search numOfRows=50
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

from .benefit_api import _server_key
from .law_api import _get

# 정해 둔 API (first-lab-api.php FIRSTLAB_OPENDATA_APIS 와 같게)
APIS = {
    # 금융위원회_서민금융상품기본정보 (data.go.kr 15094787): 상품명·지원대상·금리·한도·상환방식·취급기관
    "loan_basic": "https://apis.data.go.kr/1160100/service/GetSmallLoanFinanceInstituteInfoService/getOrdinaryFinanceInfo",
    # 서민금융진흥원_대출상품한눈에 정보 서비스 (data.go.kr 15106208): 대출한도·금리구분·용도·총 대출기간·취급기관
    "loan_search": "https://apis.data.go.kr/B553701/LoanProductSearchingInfo/getLoanProductSearchingInfo",
}
PROXY = "https://first-lab.kr/wp-json/firstlab/v1/opendata"
DEFAULTS = {"resultType": "json", "type": "json", "pageNo": "1", "numOfRows": "100"}


def direct_url(api: str, key: str, params: dict) -> str:
    encoded = key if "%" in key else urllib.parse.quote(key, safe="")
    query = {**DEFAULTS, **{k: str(v) for k, v in params.items() if k != "serviceKey"}}
    return APIS[api] + "?" + urllib.parse.urlencode(query) + "&serviceKey=" + encoded


def via_site(api: str, **params) -> dict | None:
    """사이트 통로 응답 그대로 ({status, body, request(키 가림)}). 토큰 없으면 None."""
    token = os.environ.get("FIRSTLAB_API_TOKEN", "")
    if not token:
        return None
    query = urllib.parse.urlencode({"api": api, **params})
    return json.loads(_get(PROXY + "?" + query, {"X-Firstlab-Token": token}))


def fetch(api: str, **params) -> dict | None:
    """API 응답 JSON. 못 받으면 None."""
    if api not in APIS:
        raise ValueError(f"api 는 {'/'.join(APIS)}")
    try:
        key = _server_key()
        if key:
            return json.loads(_get(direct_url(api, key, params)))
        got = via_site(api, **params)
        return json.loads(got["body"]) if got and got.get("status") == 200 else None
    except (OSError, ValueError, KeyError):
        return None


def items(resp: dict | None) -> list[dict]:
    """두 API 의 응답 모양(response.body.items.item / data 등)에서 항목 목록만."""
    if not isinstance(resp, dict):
        return []
    node = resp.get("response", resp)
    body = node.get("body", node) if isinstance(node, dict) else {}
    got = body.get("items", body.get("data", [])) if isinstance(body, dict) else []
    if isinstance(got, dict):
        got = got.get("item", [])
    return [got] if isinstance(got, dict) else [g for g in (got or []) if isinstance(g, dict)]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    params = dict(a.split("=", 1) for a in argv[2:])
    got = fetch(argv[1], **params)
    if got is None:
        print("받지 못함 (인증키·토큰이 없거나 연결 실패, 또는 포털 활용신청 전)")
        try:
            raw = via_site(argv[1], **params)
            if raw:
                print(f"통로 응답: 상태 {raw.get('status')}, 요청 {raw.get('request', '')}\n{raw.get('body', '')[:500]}")
        except (OSError, ValueError) as e:
            print(f"통로 응답 못 받음: {e}")
        return 1
    print(json.dumps(got, ensure_ascii=False, indent=1)[:20000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
