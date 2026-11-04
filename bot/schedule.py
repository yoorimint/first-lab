"""예약 발행: 새 글을 바로 공개하지 않고 '공개 시각' 중 다음 빈 자리에 예약한다.

설정 페이지 > 발행 방식 '예약 발행' + 예약 공개 시각(한 줄에 하나, 줄 수 = 하루 공개 글 수).
글이 쌓이면 다음 빈 시각으로 차례대로 밀린다 (하루에 몰아서 공개되지 않게).
워드프레스가 그 시각에 공개하고, 1분마다 도는 봇이 워드프레스 예약 작업을 대신 실행해 준다 (run.run_due_wp_cron).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from .settings import wp_cli

MIN_LEAD = timedelta(minutes=10)   # 너무 임박한 시각은 건너뜀 (이미지 생성 등 처리 시간)
MAX_DAYS = 365
_FMT = "%Y-%m-%d %H:%M"


def next_slot(times: list[str], now: datetime, taken: set[str]) -> datetime:
    """now(사이트 시간대, 시간대 정보 없음) 이후 공개 시각 중 아직 예약이 없는 가장 이른 시각."""
    slots = sorted(time(*map(int, t.split(":"))) for t in (times or ["09:00"]))
    for day in range(MAX_DAYS):
        date = now.date() + timedelta(days=day)
        for slot in slots:
            when = datetime.combine(date, slot)
            if when >= now + MIN_LEAD and when.strftime(_FMT) not in taken:
                return when
    raise RuntimeError("1년 안에 빈 예약 시각이 없음")


def scheduled_times() -> set[str]:
    """이미 예약된 글의 공개 시각 ('YYYY-MM-DD HH:MM')."""
    out = wp_cli("post", "list", "--post_type=post", "--post_status=future", "--field=post_date", "--posts_per_page=-1")
    return {line.strip()[:16] for line in out.splitlines() if line.strip()}
