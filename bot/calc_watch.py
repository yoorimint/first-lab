"""계산기 연초 값 확인 (하루 1번, 키워드 수집 끝) — 해마다 1월 1일 등에 바뀌는 요율·금액을 감시 세션 없이 넘기려고 (11/5).

계산기 data/<id>.json 은 둘 중 하나를 적는다 (tests/test_calculators.mjs 가 강제):
  "yearly": false            해마다 바뀌는 값이 없음 (평단가·CBM 등)
  "yearly_checked": 2027     2027년 값까지 확인함 — 확정된 다음 값은 "scheduled"(calc.js util.dataOn)로 미리 넣어 두면 그날 자동 전환
  "yearly_recheck": "2026-12-15", "yearly_pending": "무엇을 기다리는지"  아직 확정 전인 값이 남아 그날 다시 확인 (그 전에는 글쓰기 세션에 안 넘김 — 매일 같은 조사를 반복하지 않게)
11월 15일부터는 다음 해, 그 전에는 올해 기준으로 확인이 안 된 계산기를 state["calc_yearly"] 에 → /next 의 calc_yearly
→ 글쓰기 세션이 공식 출처로 다음 해 값을 확인해 scheduled 를 넣고 yearly_checked 를 올림 (daily-writer.md 4-2).

근거 법령 개정 (11/5 운영자 '법률 개정되는 거 보고 계산기도 바꾸면 되는 거 아닌가'): 계산기 출처(sources)에 적힌 법령을
법령 감시(law_watch)가 같이 보고, 시행 예정 개정이 있으면 바뀌는 조문과 함께 calc_yearly 에 넣는다 (날짜와 상관없이).
값이 바뀌면 글쓰기 세션이 그 시행일로 scheduled 를 넣고, 계산기와 상관없는 개정이면 data 의 "law_checked" 에 그 key 를 적는다.
숫자를 코드가 직접 바꾸지 않는 이유: 조문 문장에서 어느 값이 계산기의 어느 키인지 기계로 확실히 짝지을 수 없음 → 확인은 글쓰기 세션.

기관 안내 페이지 (요금표·수수료처럼 법령이 아닌 출처): 계산기 data 의 숫자 중 출처 페이지에 실제로 적혀 있는 것을 '기준 숫자'로 저장해 두고
(data 의 basis_date 가 바뀌면 새로 저장), 매일 그 페이지를 열어 기준 숫자가 사라지면 calc_yearly 에 넣는다 (source_changed).
페이지를 못 열면 판단하지 않는다.

값이 바뀌는 날(scheduled 의 from) 이 되면: 옛 값을 그대로 적은 글(공개·예약·초안)에 표시(bot/changed.py)
→ 공개 글은 재검증, 초안은 공개 직전 보류. 금액은 '10,320원' 처럼 원 단위로 찾고, 비율(0.0475 → 4.75%)은
흔한 숫자라 그 계산기가 들어간 글에서만 찾는다. 한 번 한 전환은 state["calc_switch_done"] 에 남겨 다시 안 함.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "themes" / "first-lab" / "calculators" / "data"
START = (11, 15)  # 다음 해 값(최저임금·건강보험료율 등)은 대개 8~11월에 확정 → 11/15 부터 다음 해 확인 요구


LAW_NAME = re.compile(r"((?:[가-힣]+ )*[가-힣]+(?:법률|법)(?: 시행령| 시행규칙)?)(?![가-힣])")
NOT_LAW = ("방법", "평균법", "계산법")  # '신청방법'·'이동평균법' 같은 보통 낱말
NOT_FIRST = {"국가법령정보센터", "국회", "입법예고", "및", "등에", "관한"}  # 법령 이름 앞에 붙는 말·이름 중간 말


def laws_in(text: str) -> set[str]:
    """출처 이름·주소에서 법령 이름 후보 ('근로자퇴직급여 보장법', '국민건강보험법 시행령' …).
    '국회 입법예고 소득세법' 처럼 앞말이 붙으면 뒤에서부터 잘라 모두 후보로 — 실제 법령인지는 법령 감시가 API 로 거름."""
    from urllib.parse import unquote
    text = unquote(text)
    found = {m.group(1) for m in re.finditer(r"law\.go\.kr/법령/([^/?#\s]+)", text)}
    for m in LAW_NAME.finditer(text):
        words = m.group(1).split()
        tail = [w for w in words if w in ("시행령", "시행규칙")]
        body = words[:len(words) - len(tail)]
        for i in range(len(body)):
            name = " ".join(body[i:] + tail)
            if len(name.replace(" ", "")) >= 3 and not name.endswith(NOT_LAW) and body[i] not in NOT_FIRST:
                found.add(name)
    by_key = {}
    for name in sorted(found, key=lambda n: -n.count(" ")):  # 띄어 쓴 이름을 남김
        by_key.setdefault(name.replace(" ", ""), name)
    return set(by_key.values())


def calc_laws(data_dir: Path = DATA_DIR) -> dict[str, set[str]]:
    """계산기 id → 근거 법령 이름들 (출처에 적힌 것 + data 의 "laws" — 출처가 국세청 안내 페이지뿐인 계산기용)."""
    out = {}
    for path in sorted(data_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        names = set(data.get("laws", []))
        for src in data.get("sources", []):
            names |= laws_in(f"{src.get('name', '')} {src.get('url', '')}")
        out[path.stem] = names
    return out


def _same_law(a: str, b: str) -> bool:
    return a.replace(" ", "") == b.replace(" ", "")


def law_due(today: dt.date, upcoming: list[dict], data_dir: Path = DATA_DIR) -> list[dict]:
    """근거 법령에 시행 예정 개정이 있는데 아직 반영(scheduled 같은 날짜)도 확인(law_checked)도 안 한 계산기."""
    laws = calc_laws(data_dir)
    out = []
    for path in sorted(data_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        done = set(data.get("law_checked", [])) | {s.get("from") for s in data.get("scheduled", [])}
        for item in upcoming:
            if (item.get("date", "") > f"{today:%Y-%m-%d}" and item.get("key") not in done and item.get("date") not in done
                    and any(_same_law(item.get("law", ""), law) for law in laws[path.stem])):
                out.append({"id": path.stem, "law_change": item["key"], "date": item["date"],
                            "articles": item.get("articles", [])[:8]})
    return out


ANCHORS = Path("/var/lib/first-lab/bot/calc-anchors.json")  # id → {basis_date, pages: {주소: [기준 숫자]}}
MIN_ANCHOR = 100   # 이보다 작은 수(1·2·30 같은)는 페이지 어디에나 있어 기준이 안 됨


def data_numbers(data, out=None) -> set[str]:
    """data 안의 금액·기준값 (출처·기준일·검사용 키 제외)."""
    out = set() if out is None else out
    if isinstance(data, dict):
        for k, v in data.items():
            if k not in ("sources", "basis_date", "scheduled", "won10_keys", "yearly_checked", "yearly_recheck", "laws", "law_checked"):
                data_numbers(v, out)
    elif isinstance(data, list):
        for v in data:
            data_numbers(v, out)
    elif isinstance(data, (int, float)) and not isinstance(data, bool) and data >= MIN_ANCHOR and float(data).is_integer():
        out.add(str(int(data)))  # source_check.has_number 형식 (쉼표 없이 — 페이지의 쉼표는 has_number 가 맞춰 봄)
    return out


def source_changes(data_dir: Path = DATA_DIR, fetcher=None, anchors_file: Path = ANCHORS) -> list[dict]:
    """기관 안내 페이지에서 기준 숫자가 사라진 계산기. 처음 보거나 data 를 고친 뒤면 기준만 새로 저장."""
    from . import source_check
    fetcher = fetcher or source_check.fetch
    try:
        saved = json.loads(anchors_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    from concurrent.futures import ThreadPoolExecutor
    files = {path: json.loads(path.read_text(encoding="utf-8")) for path in sorted(data_dir.glob("*.json"))}
    urls = sorted({s["url"] for d in files.values() for s in d.get("sources", []) if s.get("url") and "law.go.kr" not in s["url"]})
    with ThreadPoolExecutor(max_workers=8) as pool:  # 페이지 60여 개를 하나씩 열면 몇 분 걸림
        texts = dict(zip(urls, pool.map(fetcher, urls)))
    out = []
    for path, data in files.items():
        nums = data_numbers(data)
        pages = [s["url"] for s in data.get("sources", []) if s.get("url") and "law.go.kr" not in s["url"]]
        if not nums or not pages:
            continue
        old = saved.get(path.stem) or {}
        fresh = old.get("basis_date") != data.get("basis_date")
        keep = {} if fresh else dict(old.get("pages") or {})
        for url in pages:
            text = texts.get(url)
            if not text:
                continue  # 못 엶·문서 → 판단 안 함 (기준도 그대로)
            if fresh or url not in keep:
                keep[url] = sorted(n for n in nums if source_check.has_number(text, n))
                continue
            gone = [n for n in keep[url] if not source_check.has_number(text, n)]
            if gone:
                out.append({"id": path.stem, "source_changed": url, "missing": gone[:8]})
        saved[path.stem] = {"basis_date": data.get("basis_date"), "pages": keep}
    anchors_file.parent.mkdir(parents=True, exist_ok=True)
    anchors_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return out


def target_year(today: dt.date) -> int:
    return today.year + 1 if (today.month, today.day) >= START else today.year


def due(today: dt.date, data_dir: Path = DATA_DIR) -> list[dict]:
    """다음 해(또는 올해) 값 확인이 안 된 계산기."""
    year = target_year(today)
    out = []
    for path in sorted(data_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("yearly") is False:
            continue
        checked = int(data.get("yearly_checked") or 0)
        if checked < year and str(data.get("yearly_recheck") or "") <= f"{today:%Y-%m-%d}":
            out.append({"id": path.stem, "checked": checked, "for": year, "pending": data.get("yearly_pending", ""),
                        "scheduled": [s.get("from") for s in data.get("scheduled", [])]})
    return out


def old_texts(old, new) -> list[str]:
    """글에서 찾을 옛 값 표기 (바뀐 숫자만)."""
    if not isinstance(old, (int, float)) or isinstance(old, bool) or old == new:
        return []
    if 0 < old < 1:
        return [f"{round(old * 100, 4):g}%"]
    if old >= 1000 and float(old).is_integer():
        return [f"{int(old):,}원"]
    return []


def switches(today: dt.date, done: set[str], data_dir: Path = DATA_DIR) -> list[dict]:
    """오늘까지 시행된 값 전환 중 아직 글 표시를 안 한 것: {key, id, from, texts: {옛 표기: '이름 옛값→새값'}}."""
    out = []
    for path in sorted(data_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for sch in data.get("scheduled", []):
            key = f"{path.stem}:{sch.get('from')}"
            if key in done or str(sch.get("from")) > f"{today:%Y-%m-%d}":
                continue
            texts = {}
            for name, new in (sch.get("values") or {}).items():
                for t in old_texts(data.get(name), new):
                    texts[t] = f"{name} {data.get(name)}→{new}"
            for old_text, new_text in sch.get("texts", []):  # 화면 표기 (4.75% → 5%) — 계산기가 들어간 글에서만 찾음 (흔한 숫자)
                texts.setdefault(old_text if old_text.endswith("%") else old_text, f"{old_text}→{new_text}")
            out.append({"key": key, "id": path.stem, "from": sch.get("from"), "texts": texts})
    return out


def mark_switches(today: dt.date, state: dict, bulk=None, wp=None, data_dir: Path = DATA_DIR) -> list[str]:
    from . import changed, wpbulk
    items = switches(today, set(state.get("calc_switch_done") or []), data_dir)
    if not items:
        return []
    posts = bulk if bulk is not None else wpbulk.posts()
    logs = []
    for item in items:
        shortcode = f'[firstlab_calc id="{item["id"]}"]'
        marked = []
        for text, label in item["texts"].items():
            def has(body, text=text):
                return text in body and (not text.endswith("%") or shortcode in body)
            marked += changed.flag(changed.live_posts_with(posts, has), f"{item['from']} 계산기 값 변경({item['id']}): {label}", wp)
        state["calc_switch_done"] = ((state.get("calc_switch_done") or []) + [item["key"]])[-200:]
        logs.append(f"{item['id']} {item['from']} 값 전환 → 옛 값 적힌 글 {len(set(marked))}편 재확인 표시")
    return logs


def run(today: dt.date | None = None) -> str:
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    state = load_state()
    items = due(today) + law_due(today, (state.get("law_changes") or {}).get("upcoming", []))
    try:
        items += source_changes()
    except Exception as e:  # noqa: BLE001 - 페이지 확인이 실패해도 나머지는 넘김
        items.append({"id": "-", "error": f"기관 페이지 확인 실패: {e}"[:120]})
    state["calc_yearly"] = items
    try:
        switched = mark_switches(today, state)
    except Exception as e:  # noqa: BLE001 - 표시가 실패해도 연초 확인 목록은 저장 (다음 날 다시)
        switched = [f"값 전환 표시 실패: {e}"]
    msg = (f"계산기 연초 값·근거 법령 개정: 확인 필요 {len(items)}건"
           + (f" — {', '.join(i['id'] + (' ' + i['law_change'] if 'law_change' in i else '') + (' 기관 페이지 숫자 바뀜' if 'source_changed' in i else '') for i in items[:6])}" if items else " (모두 확인됨)")
           + "".join(f" / {x}" for x in switched))
    add_log(state, not items or (today.month, today.day) < (12, 15), msg)  # 12/15 넘도록 남아 있으면 오류로 표시
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
