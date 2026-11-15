"""매분 봇 실행(bot/run.py)의 단계별 걸린 시간 기록 — /maintenance run_timing.

11/15: 봇이 매분 돌아야 하는데 11/12 부터 하루 실행 횟수가 1,340 → 450 으로 줄었다 (한 번에 3분 넘게 걸려
그동안 매분 실행이 건너뛰어짐 → 예약 공개가 몇 분씩 늦음). 어느 단계가 오래 걸리는지 숫자로 보려고 단계마다 잰다.
state 는 server_stats 가 매 실행 바꾸므로 이 기록 때문에 저장이 늘지 않는다.
"""
from __future__ import annotations

import json
import os
import time

CARRY_FILE = "/var/lib/first-lab/bot/run-timing-carry.json"  # 이번 실행이 state 에 못 넣는 값 → 다음 실행이 더함
SLOW_SECONDS = 50   # 한 번 실행이 이보다 길면 다음 매분 실행을 건너뛰게 됨 (타이머 1분)
KEEP_DAYS = 2


class Laps:
    """lap(이름) 을 부를 때마다 앞 lap 이후 걸린 시간을 그 이름에 더함."""

    def __init__(self, clock=time.monotonic, started_ago: float | None = None):
        self._clock = clock
        self.last = clock()
        self.start = self.last - (started_ago or 0.0)
        self.steps: dict[str, float] = {}
        self.locked = 0
        if started_ago:
            self.steps["시작(파이썬·불러오기)"] = started_ago

    def add_carry(self, carry: dict) -> None:
        """앞 실행이 남긴 값: 마지막 저장 시간(이번 총 시간에는 안 더함), 잠금 때문에 건너뛴 실행 수."""
        if carry.get("save"):
            self.steps["저장(앞 실행)"] = float(carry["save"])
        self.locked = int(carry.get("locked", 0))

    def lap(self, name: str) -> None:
        now = self._clock()
        self.steps[name] = self.steps.get(name, 0.0) + (now - self.last)
        self.last = now

    def total(self) -> float:
        return self.last - self.start


def record(state: dict, stamp: str, laps: Laps) -> None:
    """오늘 기록에 이번 실행을 더함: 실행 수·총 시간 합/최대, 단계별 합/최대, 오래 걸린 실행 수·마지막 시각."""
    today = stamp[:10]
    days = {d: v for d, v in (state.get("run_timing") or {}).items() if isinstance(v, dict)}
    day = dict(days.get(today) or {})
    total = laps.total()
    day["runs"] = int(day.get("runs", 0)) + 1
    day["locked"] = int(day.get("locked", 0)) + laps.locked
    day["total_sum"] = round(float(day.get("total_sum", 0)) + total, 1)
    if total > float(day.get("total_max", 0)):
        day["total_max"], day["total_max_at"] = round(total, 1), stamp[11:16]
    if total > SLOW_SECONDS:
        day["slow_runs"] = int(day.get("slow_runs", 0)) + 1
        day["slow_last"] = f"{stamp[11:16]} {round(total)}초: " + ", ".join(
            f"{k} {round(v)}초" for k, v in sorted(laps.steps.items(), key=lambda kv: -kv[1])[:3])
    step_sum, step_max = dict(day.get("step_sum") or {}), dict(day.get("step_max") or {})
    for name, sec in laps.steps.items():
        step_sum[name] = round(float(step_sum.get(name, 0)) + sec, 1)
        step_max[name] = max(round(sec, 1), float(step_max.get(name, 0)))
    day["step_sum"], day["step_max"] = step_sum, step_max
    days[today] = day
    state["run_timing"] = {d: days[d] for d in sorted(days)[-KEEP_DAYS:]}


def process_age() -> float | None:
    """이 프로세스가 시작된 뒤 지난 초 (파이썬 시작·모듈 불러오기 시간). 리눅스 /proc 이 없으면 None."""
    try:
        with open("/proc/self/stat") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return None


def take_carry(path: str = CARRY_FILE) -> dict:
    try:
        with open(path) as f:
            carry = json.load(f)
        os.remove(path)
        return carry if isinstance(carry, dict) else {}
    except (OSError, ValueError):
        return {}


def put_carry(key: str, value: float, path: str = CARRY_FILE, add: bool = False) -> None:
    """다음 실행에 넘길 값 (실패해도 봇은 계속)."""
    try:
        try:
            with open(path) as f:
                carry = json.load(f)
        except (OSError, ValueError):
            carry = {}
        carry[key] = round(float(carry.get(key, 0)) + value, 1) if add else round(value, 1)
        with open(path, "w") as f:
            json.dump(carry, f)
    except OSError:
        pass


def summary(day: dict) -> str:
    """감시 화면 한 줄: 실행 수·평균·최대, 합계가 큰 단계 3개."""
    runs = int(day.get("runs", 0)) or 1
    top = sorted((day.get("step_sum") or {}).items(), key=lambda kv: -kv[1])[:3]
    return (f"봇 실행 {day.get('runs', 0)}회 · 평균 {float(day.get('total_sum', 0)) / runs:.1f}초 · "
            f"최대 {day.get('total_max', 0)}초({day.get('total_max_at', '-')}) · 1분 넘김 {day.get('slow_runs', 0)}회 · "
            f"잠금으로 건너뜀 {day.get('locked', 0)}회 · "
            "오래 걸린 단계: " + ", ".join(f"{k} 합 {round(v)}초(최대 {(day.get('step_max') or {}).get(k, 0)}초)" for k, v in top))
