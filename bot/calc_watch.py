"""계산기 연초 값 확인 (하루 1번, 키워드 수집 끝) — 해마다 1월 1일 등에 바뀌는 요율·금액을 감시 세션 없이 넘기려고 (11/5).

계산기 data/<id>.json 은 둘 중 하나를 적는다 (tests/test_calculators.mjs 가 강제):
  "yearly": false            해마다 바뀌는 값이 없음 (평단가·CBM 등)
  "yearly_checked": 2027     2027년 값까지 확인함 — 확정된 다음 값은 "scheduled"(calc.js util.dataOn)로 미리 넣어 두면 그날 자동 전환
11월 15일부터는 다음 해, 그 전에는 올해 기준으로 확인이 안 된 계산기를 state["calc_yearly"] 에 → /next 의 calc_yearly
→ 글쓰기 세션이 공식 출처로 다음 해 값을 확인해 scheduled 를 넣고 yearly_checked 를 올림 (daily-writer.md 4-2).

값이 바뀌는 날(scheduled 의 from) 이 되면: 옛 값을 그대로 적은 글(공개·예약·초안)에 표시(bot/changed.py)
→ 공개 글은 재검증, 초안은 공개 직전 보류. 금액은 '10,320원' 처럼 원 단위로 찾고, 비율(0.0475 → 4.75%)은
흔한 숫자라 그 계산기가 들어간 글에서만 찾는다. 한 번 한 전환은 state["calc_switch_done"] 에 남겨 다시 안 함.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "themes" / "first-lab" / "calculators" / "data"
START = (11, 15)  # 다음 해 값(최저임금·건강보험료율 등)은 대개 8~11월에 확정 → 11/15 부터 다음 해 확인 요구


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
        if checked < year:
            out.append({"id": path.stem, "checked": checked, "for": year,
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
    items = due(today)
    state = load_state()
    state["calc_yearly"] = items
    try:
        switched = mark_switches(today, state)
    except Exception as e:  # noqa: BLE001 - 표시가 실패해도 연초 확인 목록은 저장 (다음 날 다시)
        switched = [f"값 전환 표시 실패: {e}"]
    msg = (f"계산기 연초 값: {target_year(today)}년 확인 필요 {len(items)}개"
           + (f" — {', '.join(i['id'] for i in items[:6])}" if items else " (모두 확인됨)")
           + "".join(f" / {x}" for x in switched))
    add_log(state, not items or (today.month, today.day) < (12, 15), msg)  # 12/15 넘도록 남아 있으면 오류로 표시
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
