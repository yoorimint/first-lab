"""법제처 국가법령정보 공동활용 API(DRF) 읽기 — 법령 사이트 화면 읽기 대신 쓰는 정식 통로.

어디서 부르든 같은 함수:
  - 서버: 비밀값 LAW_OPEN_API_OC 로 법제처에 직접 (신청할 때 이 서버 주소를 등록함)
  - 글쓰기·감시 세션(다른 컴퓨터): 사이트 통로 /wp-json/firstlab/v1/law (환경 변수 FIRSTLAB_API_TOKEN, first-lab-api.php)
  - 둘 다 없거나 실패: None → 부르는 쪽이 예전 화면 읽기로 (끊기지 않게)

확인용: python3 -m bot.law_api search law query=고용보험법
        python3 -m bot.law_api service law MST=<법령일련번호> JO=004000
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

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


def _date(text: str) -> dt.date | None:
    return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])) if re.fullmatch(r"\d{8}", text or "") else None


def versions(name: str) -> list[dict]:
    """법령 이름(정확히 같은 것만) → 시행일 기준 판 목록 [{mst, date, status(현행/시행예정/연혁)}], 시행일 순.
    주의: 법령 ID 로 본문을 받으면 '마지막에 공포된 판'(아직 시행 전일 수 있음)이 온다 → 현행은 반드시 여기서 고른다."""
    xml = fetch("search", "eflaw", query=name, display="100")
    if not xml:
        return []
    out = []
    for law in ET.fromstring(xml).iter("law"):
        date = _date(law.findtext("시행일자") or "")
        if (law.findtext("법령명한글") or "").replace(" ", "") == name.replace(" ", "") and date:  # 띄어쓰기 무관
            out.append({"mst": law.findtext("법령일련번호"), "date": date, "status": (law.findtext("현행연혁코드") or "").strip()})
    # 같은 날 시행되는 판이 여럿이면(같은 날 시행되는 개정이 따로 공포됨) 가장 나중 판(법령일련번호 큰 것)만 — 그 판에 모두 반영돼 있음
    by_date: dict[dt.date, dict] = {}
    for v in out:
        if v["date"] not in by_date or int(v["mst"] or 0) > int(by_date[v["date"]]["mst"] or 0):
            by_date[v["date"]] = v
    return sorted(by_date.values(), key=lambda v: v["date"])


def jo_code(article: str) -> str | None:
    """'제47조의4' → '004704' (법제처 조문 번호: 조 4자리 + 가지 2자리)."""
    m = re.fullmatch(r"제(\d+)조(?:의(\d+))?", article.replace(" ", ""))
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}" if m else None


def unit_text(unit: ET.Element) -> str:
    """조문단위 → 조문 글자 (조문내용·항·호·목을 순서대로)."""
    parts = [unit.findtext("조문내용") or ""]
    for el in unit.iter():
        if el.tag in ("항내용", "호내용", "목내용"):
            parts.append(el.text or "")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def article(name: str, art: str, version: dict) -> dict | None:
    """한 판(version)의 조문 하나 → {text, changed(그 판에서 바뀜), date}. 없으면 None."""
    jo = jo_code(art)
    xml = fetch("service", "eflaw", MST=version["mst"], efYd=f"{version['date']:%Y%m%d}", JO=jo) if jo else None
    if not xml:
        return None
    for unit in ET.fromstring(xml).iter("조문단위"):
        if unit.findtext("조문여부") == "조문":
            return {"text": unit_text(unit), "changed": unit.findtext("조문변경여부") == "Y", "date": version["date"]}
    return None


def same_text(a: str, b: str) -> bool:
    """두 판의 조문이 같은지 (끝의 '<개정 …>' 표시는 빼고 비교 — 조문변경여부 값보다 확실)."""
    strip = lambda t: re.sub(r"\s+", "", re.sub(r"<(?:개정|신설|전문개정|본조신설|제목개정)[^>]*>", "", t))
    return strip(a) == strip(b)


def upcoming_changes(name: str, art: str, today: dt.date, vers: list[dict] | None = None) -> list[dict]:
    """이 조문이 앞으로 시행될 판에서 바뀌는지: [{date, text}] (바뀌는 판만). 현행을 못 읽으면 []."""
    vers = vers if vers is not None else versions(name)
    now = current(vers, today)
    base = article(name, art, now) if now else None
    if not base:
        return []
    out = []
    for v in (v for v in vers if v["date"] > today):
        later = article(name, art, v)
        if later is not None and not same_text(base["text"], later["text"]):
            out.append({"date": v["date"], "text": later["text"]})
            base = later  # 다음 판은 바로 앞 판과 비교
    return out


def current(vers: list[dict], today: dt.date) -> dict | None:
    """오늘 효력 있는 판 (시행일이 오늘 이전인 것 중 마지막)."""
    now = [v for v in vers if v["date"] <= today]
    return now[-1] if now else None


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
