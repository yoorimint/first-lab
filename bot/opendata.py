"""공공데이터포털 공식 API 읽기 (정해 둔 API 만) — 글쓰기·검수의 공식 근거.

11/18 운영자 '연결해라': 서민금융진흥원 누리집이 검수 환경 접속을 받지 않아 햇살론 글(#7050)의 대출기간·상환방식 등
18곳을 확인처 안내로 낮춤 → 같은 기관이 공공데이터포털로 공식 개방한 상품 정보를 받는다.
어디서 부르든 같은 함수 (bot/benefit_api.py 와 같은 방식):
  - 서버: 비밀값 DATA_GO_KR_KEY 로 직접
  - 글쓰기·감시 세션: 사이트 통로 /wp-json/firstlab/v1/opendata (환경 변수 FIRSTLAB_API_TOKEN)

확인용: python3 -m bot.opendata loan_basic likeFinPrdNm=햇살론
        python3 -m bot.opendata loan_search numOfRows=50
        python3 -m bot.opendata welfare_local_list srchKeyCode=001 searchWrd=해녀      (지자체 복지사업 목록, servId)
        python3 -m bot.opendata welfare_local_detail servId=<servId>                     (대상·선정기준·서류·신청방법)
        python3 -m bot.opendata welfare_nat_list callTp=L srchKeyCode=001 searchWrd=청년 (중앙부처)
        python3 -m bot.opendata welfare_nat_detail callTp=D servId=<servId>
        python3 -m bot.opendata kstartup_notice perPage=50                               (K-Startup 공고)
        python3 -m bot.opendata didimdol_rate  /  rent_loan_rate                         (주택금융공사 금리)
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
    # 11/18 운영자 활용신청 6개 — 지역 지원사업·복지·창업 공고·주택 대출 금리
    # 한국사회보장정보원_지자체복지서비스 목록 (data.go.kr 15108347): searchWrd·ctpvNm·sggNm
    "welfare_local_list": "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
    # 지자체복지서비스 상세 (servId): 지원대상·선정기준·서류·신청방법
    "welfare_local_detail": "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfaredetailed",
    # 한국사회보장정보원_중앙부처복지서비스 목록 (15090532): callTp=L·searchWrd
    "welfare_nat_list": "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
    # 중앙부처복지서비스 상세 (callTp=D·servId)
    "welfare_nat_detail": "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001",
    # 중소벤처기업부_중소기업 지원사업 공고(기업마당) (15157820)
    "bizinfo": "https://apis.data.go.kr/1421000/bizinfo/pblancBsnsService",
    # 창업진흥원_K-Startup 사업공고 (15125364) — 11/25 Service02 로 바뀜(지원내용 support_ctnt·선정절차 eval_method 추가),
    # Service01 은 90일 뒤 중지 → 새 주소 기본, 옛 주소 _v1 (first-lab-api.php 와 같게)
    "kstartup_notice": "https://apis.data.go.kr/B552735/kisedKstartupService02/getAnnouncementInformation02",
    "kstartup_notice_v1": "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01",
    # K-Startup 사업소개
    "kstartup_business": "https://apis.data.go.kr/B552735/kisedKstartupService02/getBusinessInformation02",
    "kstartup_business_v1": "https://apis.data.go.kr/B552735/kisedKstartupService01/getBusinessInformation01",
    # 한국주택금융공사_디딤돌대출금리정보 (15082028)
    "didimdol_rate": "https://apis.data.go.kr/B551408/didimdol-loan-rate/didimdol-info",
    # 한국주택금융공사_전세자금대출 금리 정보 (15082033)
    "rent_loan_rate": "https://apis.data.go.kr/B551408/rent-loan-rate-info/rate-list",
}
PROXY = "https://first-lab.kr/wp-json/firstlab/v1/opendata"
DEFAULTS = {"resultType": "json", "type": "json", "dataType": "json", "returnType": "json", "pageNo": "1", "numOfRows": "100",
            "page": "1", "perPage": "100"}


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


def parse(text: str) -> dict:
    """JSON 이면 그대로, XML(복지서비스 API 등 JSON 을 안 주는 곳)이면 같은 모양의 사전으로."""
    text = text.strip()
    if not text.startswith("<"):
        return json.loads(text)
    import xml.etree.ElementTree as ET

    def conv(el):
        kids = list(el)
        if not kids:
            return (el.text or "").strip()
        out: dict = {}
        for k in kids:
            v = conv(k)
            if k.tag in out:
                out[k.tag] = out[k.tag] if isinstance(out[k.tag], list) else [out[k.tag]]
                out[k.tag].append(v)
            else:
                out[k.tag] = v
        return out
    root = ET.fromstring(text)
    return {root.tag: conv(root)}


def fetch(api: str, **params) -> dict | None:
    """API 응답 (JSON·XML 모두 사전으로). 못 받으면 None."""
    if api not in APIS:
        raise ValueError(f"api 는 {'/'.join(APIS)}")
    try:
        key = _server_key()
        if key:
            return parse(_get(direct_url(api, key, params)))
        got = via_site(api, **params)
        return parse(got["body"]) if got and got.get("status") == 200 else None
    except (OSError, ValueError, KeyError, SyntaxError):
        return None


def items(resp: dict | None) -> list[dict]:
    """두 API 의 응답 모양(response.body.items.item / data 등)에서 항목 목록만."""
    if not isinstance(resp, dict):
        return []
    if isinstance(resp.get("wantedList"), dict):  # 복지서비스 목록·상세 (XML: wantedList/servList)
        got = resp["wantedList"].get("servList", resp["wantedList"])
        return [got] if isinstance(got, dict) else [g for g in (got or []) if isinstance(g, dict)]
    if isinstance(resp.get("wantedDtl"), dict):
        return [resp["wantedDtl"]]
    node = resp.get("response", resp)
    body = node.get("body", node) if isinstance(node, dict) else {}
    got = body.get("items", body.get("data", [])) if isinstance(body, dict) else []
    if isinstance(got, dict):
        got = got.get("item", [])
    if not got and isinstance(body, dict) and isinstance(body.get("item"), dict):  # 디딤돌 금리: body.item 하나
        got = [body["item"]]
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
