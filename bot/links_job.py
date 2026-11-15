"""공식 사이트 링크 확인·공개 순서·관련 글 정리 — 매분 봇과 따로 (bot/run.link_maintenance, 11/15).

이 묶음 어딘가에서 실행이 멈춰 매분 봇이 12분씩 섰다 (run_timing killed_after 'WebP' 4번). 이제 따로 돌고,
단계마다 STEP 파일에 남겨 시간 제한(15분)에 걸려 죽으면 다음 실행이 '어디서 멈췄는지' 봇 기록에 남긴다.
사용: python -m bot.links_job
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

STEP = "/var/lib/first-lab/bot/links-step"


def main() -> None:
    from . import run_timing
    from .run import link_maintenance
    from .state import add_log, load_state, save_state
    state = load_state()
    stuck = run_timing.take_step(STEP)
    if stuck:
        add_log(state, False, f"앞 링크·관련 글 정리가 '{stuck}' 단계에서 멈춰 끝나지 못함 (시간 제한) — 이번에 다시 함")
    link_maintenance(state, datetime.now(ZoneInfo("Asia/Seoul")), step=lambda name: run_timing.mark_step(name, STEP))
    run_timing.clear_step(STEP)
    save_state(state)


if __name__ == "__main__":
    main()
