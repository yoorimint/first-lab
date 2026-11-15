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
STEP_FILE = "/var/lib/first-lab/bot/run-step"                 # 지금 실행이 마지막으로 마친 단계 (끝까지 마치면 지움)
SLOW_SECONDS = 50   # 한 번 실행이 이보다 길면 다음 매분 실행을 건너뛰게 됨 (타이머 1분)
KEEP_DAYS = 2


class Laps:
    """lap(이름) 을 부를 때마다 앞 lap 이후 걸린 시간을 그 이름에 더함."""

    def __init__(self, clock=time.monotonic, started_ago: float | None = None):
        self._clock = clock
        self.last = clock()
        self.start = self.last - (started_ago or 0.0)
        self.steps: dict[str, float] = {}
        self.locked = self.started = self.errors = 0
        self.last_error = ""
        self.killed_after = ""
        if started_ago:
            self.steps["시작(파이썬·불러오기)"] = started_ago

    def add_carry(self, carry: dict) -> None:
        """앞 실행이 남긴 값: 마지막 저장 시간(이번 총 시간에는 안 더함), 잠금 때문에 건너뛴 실행 수."""
        if carry.get("save"):
            self.steps["저장(앞 실행)"] = float(carry["save"])
        self.locked = int(carry.get("locked", 0))
        self.started = int(carry.get("started", 0))
        self.errors = int(carry.get("errors", 0))
        self.last_error = str(carry.get("last_error", ""))

    def add_outside(self, gaps: dict[str, float]) -> None:
        """실행 밖에서 흐른 시간 (총 시간에는 안 더함)."""
        self.steps.update(gaps)

    def lap(self, name: str, step_file: str | None = STEP_FILE) -> None:
        now = self._clock()
        self.steps[name] = self.steps.get(name, 0.0) + (now - self.last)
        self.last = now
        if step_file:
            try:
                with open(step_file, "w") as f:
                    f.write(name)
            except OSError:
                pass

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
    day["started"] = int(day.get("started", 0)) + laps.started   # 시작한 실행 (중간에 죽은 실행 포함 — runs 와 차이 = 죽은 수)
    day["errors"] = int(day.get("errors", 0)) + laps.errors
    if laps.last_error:
        day["last_error"] = laps.last_error
    if laps.killed_after:  # 앞 실행이 이 단계 다음에서 멈춰 끝나지 못함 (11/15 '종료 뒤 대기 770초' = 12분 시간 제한에 걸린 실행)
        killed = dict(day.get("killed_after") or {})
        killed[laps.killed_after] = int(killed.get(laps.killed_after, 0)) + 1
        day["killed_after"] = killed
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
    """앞 실행들이 남긴 값을 읽음. 지우지는 않음 — 이번 실행이 저장까지 마친 뒤 clear_carry (중간에 죽으면 다음 실행이 다시 읽게)."""
    try:
        with open(path) as f:
            carry = json.load(f)
        return carry if isinstance(carry, dict) else {}
    except (OSError, ValueError):
        return {}


def take_step(path: str = STEP_FILE) -> str:
    """앞 실행이 남긴 '마지막으로 마친 단계' (있으면 앞 실행이 끝까지 못 감). 읽고 지움."""
    try:
        with open(path) as f:
            name = f.read().strip()
        os.remove(path)
        return name or "(시작 직후)"
    except OSError:
        return ""


def mark_step(name: str, path: str = STEP_FILE) -> None:
    try:
        with open(path, "w") as f:
            f.write(name)
    except OSError:
        pass


def clear_step(path: str = STEP_FILE) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def clear_carry(path: str = CARRY_FILE) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def note_error(exc: BaseException, path: str = CARRY_FILE) -> None:
    """실행이 오류로 끝남 → 다음 실행이 state 에 넣게 (11/15: 실행은 매분 시작하는데 기록이 1/3 만 남음 — 저장 실패 의심)."""
    put_carry("errors", 1, path=path, add=True)
    try:
        with open(path) as f:
            carry = json.load(f)
        carry["last_error"] = f"{time.strftime('%H:%M')} {type(exc).__name__}: {str(getattr(exc, 'stderr', '') or exc)[:200]}"
        with open(path, "w") as f:
            json.dump(carry, f, ensure_ascii=False)
    except (OSError, ValueError):
        pass


def put_carry(key: str, value: float, path: str = CARRY_FILE, add: bool = False) -> None:
    """다음 실행에 넘길 값 (실패해도 봇은 계속)."""
    try:
        try:
            with open(path) as f:
                carry = json.load(f)
        except (OSError, ValueError):
            carry = {}
        carry[key] = round(float(carry.get(key, 0) or 0) + value, 1) if add else round(value, 1)
        with open(path, "w") as f:
            json.dump(carry, f)
    except OSError:
        pass


def unit_stopped_at(unit: str = "firstlab-bot.service", run=None) -> float | None:
    """앞 실행이 systemd 에서 완전히 끝난(inactive) 시각 — time.monotonic() 과 같은 시계(초). 못 읽으면 None.
    (11/15: 서버 systemd 가 --timestamp=unix 를 몰라 빈 값 → 부팅 기준 시계 값으로)"""
    import subprocess
    run = run or subprocess.run
    try:
        out = run(["systemctl", "show", "-p", "InactiveEnterTimestampMonotonic", "--value", unit],
                  capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out) / 1_000_000 if out.isdigit() and int(out) > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def exit_gaps(carry: dict, stopped_at: float | None, started_at: float) -> dict[str, float]:
    """11/15: 실행 안 일은 10초인데 3~4분에 한 번만 시작 — 어디서 기다리는지 (시각은 모두 time.monotonic 기준).
    main 끝 → 파이썬 종료(남은 스레드 대기) → systemd 완전 종료(남은 자식 프로세스 정리) → 이번 시작(타이머)."""
    gaps: dict[str, float] = {}
    end, gone = carry.get("main_end"), carry.get("exit_at")
    if end and gone and float(gone) >= float(end):
        gaps["끝난 뒤 스레드 대기(앞 실행)"] = round(float(gone) - float(end), 1)
    last = gone or end
    if stopped_at and last and stopped_at >= float(last):
        gaps["종료 뒤 정리 대기(앞 실행)"] = round(stopped_at - float(last), 1)
    if stopped_at and started_at >= stopped_at:
        gaps["종료 → 다음 시작"] = round(started_at - stopped_at, 1)
    return gaps


def mark_exit() -> None:
    """파이썬이 남은 스레드를 다 기다린 뒤 부르는 atexit — 종료 시각을 다음 실행에 넘김."""
    put_carry("exit_at", time.monotonic())


def summary(day: dict) -> str:
    """감시 화면 한 줄: 실행 수·평균·최대, 합계가 큰 단계 3개."""
    runs = int(day.get("runs", 0)) or 1
    top = sorted((day.get("step_sum") or {}).items(), key=lambda kv: -kv[1])[:3]
    return (f"봇 실행 {day.get('runs', 0)}회 · 평균 {float(day.get('total_sum', 0)) / runs:.1f}초 · "
            f"최대 {day.get('total_max', 0)}초({day.get('total_max_at', '-')}) · 1분 넘김 {day.get('slow_runs', 0)}회 · "
            f"시작 {day.get('started', 0)}회 · 오류로 끝남 {day.get('errors', 0)}회 · 잠금으로 건너뜀 {day.get('locked', 0)}회 · "
            + (f"마지막 오류 {day['last_error'][:80]} · " if day.get("last_error") else "")
            + (f"끝나지 못한 실행(마지막으로 마친 단계) {day['killed_after']} · " if day.get("killed_after") else "")
            + "오래 걸린 단계: " + ", ".join(f"{k} 합 {round(v)}초(최대 {(day.get('step_max') or {}).get(k, 0)}초)" for k, v in top))
