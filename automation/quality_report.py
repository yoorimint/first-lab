"""최근 외부 검수에서 고친 곳 수 — 품질이 유지되는지 매일 점검 보고에 한 줄 (11/6 운영자 '가벼운 2번 만들어라').

automation/quality-log.csv (apply_review 가 검수마다 남김: 새 글 외부 검수·공개 직전 재검수·대기 글 검수)의 최근 DAYS 일
글당 고친 곳 평균. 넘기기 기준(automation/autopilot.md '글당 평균 1개 미만')을 넘으면 경고.
사용: python3 automation/quality_report.py   (매일 점검 automation/maintenance.md, 감시 tick.py)
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent / "quality-log.csv"
DAYS = 7
LIMIT = 1.0   # 글당 고친 곳 평균 기준 (autopilot.md 넘기기 확인 1)


def summary(rows: list[dict], today: dt.date, days: int = DAYS) -> tuple[int, float, str]:
    """(검수 수, 글당 고친 곳 평균, 보고 문장)."""
    since = today - dt.timedelta(days=days - 1)
    recent = []
    for r in rows:
        try:
            if dt.date.fromisoformat(str(r.get("date", ""))[:10]) >= since:
                recent.append(int(r.get("fixes") or 0))
        except ValueError:
            continue
    if not recent:
        return 0, 0.0, f"품질(최근 {days}일): 검수 기록 없음 — 검수가 멈췄는지 확인"
    avg = sum(recent) / len(recent)
    warn = f" ⚠ 기준 {LIMIT:g}개 넘음 — 자주 틀린 유형(automation/common-errors.md)을 검사로 옮기기" if avg > LIMIT else ""
    return len(recent), avg, f"품질(최근 {days}일): 검수 {len(recent)}편, 글당 고친 곳 평균 {avg:.1f}개 (기준 {LIMIT:g}개 미만){warn}"


def main() -> int:
    try:
        with LOG.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        rows = []
    n, avg, line = summary(rows, dt.date.today())
    print(line)
    return 1 if (n == 0 or avg > LIMIT) else 0


if __name__ == "__main__":
    sys.exit(main())
