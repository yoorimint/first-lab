"""공식 사이트 링크 확인·공개 순서·관련 글 정리 — 매분 봇과 따로 (bot/run.link_maintenance, 11/15).

이 묶음 어딘가에서 실행이 멈춰 매분 봇이 12분씩 섰다 (run_timing killed_after 'WebP' 4번). 이제 따로 돌고,
단계마다 STEP 파일에 남겨 시간 제한(15분)에 걸려 죽으면 다음 실행이 '어디서 멈췄는지' 봇 기록에 남긴다.
11/16 운영자 '왜 부하 생기냐': 서비스별 CPU 기록에서 이 작업이 높은 부하 때 가장 많이 씀 → 단계마다 CPU 초(자식 wp-cli 포함)를
state['links_timing'][날짜] 에 더해 /maintenance 로 봄 (어느 단계가 무거운지).
사용: python -m bot.links_job
"""
from __future__ import annotations

import resource
import time
from datetime import datetime
from zoneinfo import ZoneInfo

STEP = "/var/lib/first-lab/bot/links-step"
KEEP_DAYS = 2


def cpu_now() -> float:
    """이 프로세스 + 끝난 자식(wp-cli 등) CPU 초."""
    me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


class StepCpu:
    """step(이름) 을 부를 때마다 앞 단계에 쓴 CPU·시간을 앞 단계 이름에 더함 (마지막은 finish)."""

    def __init__(self, cpu=cpu_now, clock=time.monotonic):
        self._cpu, self._clock = cpu, clock
        self.name, self.c0, self.t0 = "시작", cpu(), clock()
        self.steps: dict[str, list[float]] = {}

    def __call__(self, name: str) -> None:
        c, t = self._cpu(), self._clock()
        row = self.steps.setdefault(self.name, [0.0, 0.0])
        row[0] += c - self.c0
        row[1] += t - self.t0
        self.name, self.c0, self.t0 = name, c, t

    def finish(self) -> None:
        self("끝")


def record(state: dict, today: str, steps: dict[str, list[float]]) -> None:
    """links_timing[날짜][단계] = [CPU 초 합, 걸린 초 합, 실행 수]."""
    days = dict(state.get("links_timing") or {})
    day = dict(days.get(today) or {})
    for name, (cpu, wall) in steps.items():
        c, w, n = (day.get(name) or [0, 0, 0])
        day[name] = [round(c + cpu, 1), round(w + wall, 1), n + 1]
    days[today] = day
    state["links_timing"] = {d: days[d] for d in sorted(days)[-KEEP_DAYS:]}


def main() -> None:
    from . import run_timing
    from .run import link_maintenance
    from .state import add_log, load_state, save_state
    import signal
    import sys
    state = load_state()
    stuck = run_timing.take_step(STEP)
    if stuck:
        add_log(state, False, f"앞 링크·관련 글 정리가 '{stuck}' 단계에서 멈춰 끝나지 못함 (시간 제한) — 이번에 다시 함")
        save_state(state)  # 이번 실행도 못 끝나면 이 줄이 영영 안 남았음 (11/16: 3시간 넘게 기록 0)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    timer = StepCpu()

    def step(name: str) -> None:
        timer(name)
        run_timing.mark_step(name, STEP)

    # 시간 제한(systemd RuntimeMaxSec)에 걸리면 SIGTERM → 여기까지 단계별 CPU·멈춘 단계를 남기고 끝 (11/16 '왜 부하 생기냐':
    # 10분마다 코어 절반을 쓰는데 한 번도 끝나지 못해 어느 단계인지 기록이 없었음)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    finished = False
    try:
        link_maintenance(state, now, step=step)
        finished = True
    finally:
        last = timer.name
        timer.finish()
        if finished:
            run_timing.clear_step(STEP)
        else:
            add_log(state, False, f"링크·관련 글 정리가 '{last}' 단계에서 시간 제한으로 끝남 (단계별 CPU: links_timing)")
        record(state, f"{now:%Y-%m-%d}", timer.steps)
        save_state(state)


if __name__ == "__main__":
    main()
