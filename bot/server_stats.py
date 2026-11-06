"""서버 상태 기록 (봇 1분 실행마다) — 서버를 한 단계 올릴지 숫자로 정하려고 (11/6 운영자 '기록 만들어라').

왜: first-lab 과 사주데이가 같은 서버(2코어)를 쓰는데, 11/6 10:06 배포 때 부하 5.14·사주데이 /healthz 응답 없음 1분.
배포 CPU 몫을 낮춘 뒤(CPUWeight 20)에도 평소 부하가 높거나 메모리가 모자라면 서버를 올리고, 배포 때만 잠깐이면 그대로 둔다.

기록 (state["server_stats"][날짜], 최근 KEEP_DAYS 일):
  부하(1분 평균) 평균·최고·최고 시각, 높은 부하(코어당 1.5 넘음) 분 수와 시각들, 메모리 여유 최저·전체, 스왑 사용 최대,
  사주데이 응답(5분마다 /healthz) 최대 시간·실패 수.
하루가 바뀌면 어제 요약 한 줄을 봇 기록(/maintenance)에 남긴다.
"""
from __future__ import annotations

import os
import time
import urllib.request
from datetime import datetime

KEEP_DAYS = 14
HIGH_PER_CORE = 1.5          # 코어당 이 값을 넘으면 '높은 부하' 분으로 셈
SAJU_URL = "https://sajuday.kr/healthz"
SAJU_TIMEOUT = 10
MAX_HIGH_TIMES = 30          # 하루에 남길 높은 부하 시각 수 (배포 시각과 맞춰 보려고)


def read_load(path: str = "/proc/loadavg") -> float:
    with open(path, encoding="utf-8") as f:
        return float(f.read().split()[0])


def read_mem(path: str = "/proc/meminfo") -> dict[str, int]:
    """MB 단위: total, available, swap_used."""
    vals = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, _, rest = line.partition(":")
            vals[key] = int(rest.split()[0]) // 1024  # kB → MB
    return {"total": vals.get("MemTotal", 0), "available": vals.get("MemAvailable", 0),
            "swap_used": max(0, vals.get("SwapTotal", 0) - vals.get("SwapFree", 0))}


def saju_ms(url: str = SAJU_URL) -> int | None:
    """사주데이 /healthz 응답 시간(ms). 응답 없음·오류면 None."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=SAJU_TIMEOUT) as res:  # noqa: S310 — 고정 주소
            res.read(200)
            if res.status >= 400:
                return None
    except (OSError, ValueError):
        return None
    return int((time.monotonic() - start) * 1000)


def add_sample(day: dict, stamp: str, load: float, cores: int, mem: dict, saju: int | None | str = "skip") -> dict:
    """하루 집계에 한 번 잰 값을 더함 (saju='skip' 이면 이번엔 안 잼)."""
    day = dict(day)
    day["samples"] = day.get("samples", 0) + 1
    day["load_sum"] = round(day.get("load_sum", 0.0) + load, 2)
    if load > day.get("load_max", -1):
        day["load_max"], day["load_max_at"] = load, stamp
    day["cores"] = cores
    if load > cores * HIGH_PER_CORE:
        day["high_minutes"] = day.get("high_minutes", 0) + 1
        day["high_at"] = (day.get("high_at", []) + [stamp[11:16]])[:MAX_HIGH_TIMES]
    day["mem_total"] = mem["total"]
    day["mem_avail_min"] = min(day.get("mem_avail_min", mem["available"]), mem["available"])
    day["swap_max"] = max(day.get("swap_max", 0), mem["swap_used"])
    if saju != "skip":
        day["saju_checks"] = day.get("saju_checks", 0) + 1
        if saju is None:
            day["saju_fail"] = day.get("saju_fail", 0) + 1
            day["saju_fail_at"] = (day.get("saju_fail_at", []) + [stamp[11:16]])[:MAX_HIGH_TIMES]
        else:
            day["saju_ms_max"] = max(day.get("saju_ms_max", 0), saju)
    return day


def summary(date: str, day: dict) -> str:
    n = max(day.get("samples", 0), 1)
    parts = [f"서버 상태({date}): 부하 평균 {day.get('load_sum', 0) / n:.2f}·최고 {day.get('load_max', 0):.2f}"
             f"({day.get('load_max_at', '')[11:16]}, 코어 {day.get('cores', '?')})",
             f"높은 부하 {day.get('high_minutes', 0)}분" + (f"({', '.join(day['high_at'][:8])})" if day.get("high_at") else ""),
             f"메모리 여유 최저 {day.get('mem_avail_min', 0)}MB/{day.get('mem_total', 0)}MB",
             f"스왑 최대 {day.get('swap_max', 0)}MB",
             f"사주데이 응답 최대 {day.get('saju_ms_max', 0)}ms·실패 {day.get('saju_fail', 0)}/{day.get('saju_checks', 0)}"
             + (f"({', '.join(day['saju_fail_at'][:8])})" if day.get("saju_fail_at") else "")]
    return " / ".join(parts)


def record(state: dict, now: datetime, check_saju: bool, reader=None) -> str | None:
    """한 번 재서 state 에 더함. 날짜가 바뀌었으면 어제 요약 문장을 돌려줌 (부르는 쪽이 봇 기록에 남김)."""
    reader = reader or {"load": read_load, "mem": read_mem, "saju": saju_ms, "cores": os.cpu_count}
    stats = dict(state.get("server_stats") or {})
    today, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")
    saju = reader["saju"]() if check_saju else "skip"
    stats[today] = add_sample(stats.get(today, {}), stamp, reader["load"](), reader["cores"]() or 1, reader["mem"](), saju)
    done = sorted(d for d in stats if d < today)
    report = None
    last = state.get("server_stats_reported", "")
    if done and done[-1] > last:
        report = summary(done[-1], stats[done[-1]])
        state["server_stats_reported"] = done[-1]
    for old in done[:-KEEP_DAYS]:
        stats.pop(old, None)
    state["server_stats"] = stats
    return report
