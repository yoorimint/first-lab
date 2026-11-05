"""계산기 연초 값 확인 (하루 1번, 키워드 수집 끝) — 해마다 1월 1일 등에 바뀌는 요율·금액을 감시 세션 없이 넘기려고 (11/5).

계산기 data/<id>.json 은 둘 중 하나를 적는다 (tests/test_calculators.mjs 가 강제):
  "yearly": false            해마다 바뀌는 값이 없음 (평단가·CBM 등)
  "yearly_checked": 2027     2027년 값까지 확인함 — 확정된 다음 값은 "scheduled"(calc.js util.dataOn)로 미리 넣어 두면 그날 자동 전환
11월 15일부터는 다음 해, 그 전에는 올해 기준으로 확인이 안 된 계산기를 state["calc_yearly"] 에 → /next 의 calc_yearly
→ 글쓰기 세션이 공식 출처로 다음 해 값을 확인해 scheduled 를 넣고 yearly_checked 를 올림 (daily-writer.md 4-2).
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


def run(today: dt.date | None = None) -> str:
    from .state import add_log, load_state, save_state
    today = today or dt.date.today()
    items = due(today)
    state = load_state()
    state["calc_yearly"] = items
    msg = (f"계산기 연초 값: {target_year(today)}년 확인 필요 {len(items)}개"
           + (f" — {', '.join(i['id'] for i in items[:6])}" if items else " (모두 확인됨)"))
    add_log(state, not items or (today.month, today.day) < (12, 15), msg)  # 12/15 넘도록 남아 있으면 오류로 표시
    save_state(state)
    return msg


if __name__ == "__main__":
    print(run(), flush=True)
