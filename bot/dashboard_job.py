"""설정 페이지 창고 현황·글쓰기 후보 다시 계산 — 매분 봇과 따로 실행 (11/15).

후보 순위 계산(select_keyword.ranked_candidates)이 키워드 7만여 개를 훑느라 한 번에 200초 넘게 걸려,
1분 봇(bot/run.py) 안에서 돌면 그동안 예약 공개·글쓰기 시작이 몇 분씩 밀렸다 (run_timing '정리·대시보드' 최대 230초).
이제 봇은 시간만 되면 이 작업을 systemd 의 별도 실행으로 띄우고 바로 다음 일로 간다. 결과는 state 에 3방향 합치기로 저장.
사용: python -m bot.dashboard_job
"""
from __future__ import annotations


UNIT = "firstlab-dashboard"


def launch(run=None) -> bool:
    """별도 실행으로 띄움 (bot/side_jobs.launch). 못 띄우면 False — 부르는 쪽이 직접 함."""
    from . import side_jobs
    return side_jobs.launch(UNIT, "bot.dashboard_job", **({"run": run} if run else {}))


def main() -> None:
    from .run import refresh_dashboard
    from .settings import load_settings
    from .state import load_state, save_state
    state = load_state()
    refresh_dashboard(state, load_settings())
    save_state(state)


if __name__ == "__main__":
    main()
