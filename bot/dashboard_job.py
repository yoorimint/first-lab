"""설정 페이지 창고 현황·글쓰기 후보 다시 계산 — 매분 봇과 따로 실행 (11/15).

후보 순위 계산(select_keyword.ranked_candidates)이 키워드 7만여 개를 훑느라 한 번에 200초 넘게 걸려,
1분 봇(bot/run.py) 안에서 돌면 그동안 예약 공개·글쓰기 시작이 몇 분씩 밀렸다 (run_timing '정리·대시보드' 최대 230초).
이제 봇은 시간만 되면 이 작업을 systemd 의 별도 실행으로 띄우고 바로 다음 일로 간다. 결과는 state 에 3방향 합치기로 저장.
사용: python -m bot.dashboard_job
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT = "firstlab-dashboard"
ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SEC = 15 * 60


def launch(run=subprocess.run) -> bool:
    """별도 실행으로 띄움. 이미 도는 중이면(같은 이름 실행이 있음) 띄우지 않음. systemd-run 이 없으면 False (부르는 쪽이 직접 함)."""
    if not shutil.which("systemd-run"):
        return False
    import sys
    try:
        r = run(["systemd-run", f"--unit={UNIT}", "--collect", "--quiet", f"--working-directory={ROOT}",
                 "-p", f"RuntimeMaxSec={TIMEOUT_SEC}", "-p", "Nice=10", "-p", "CPUWeight=20", "-p", "IOWeight=20",
                 sys.executable, "-m", "bot.dashboard_job"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 or "already" in (r.stderr or "")  # 앞 계산이 아직 도는 중이면 그대로 둠


def main() -> None:
    from .run import refresh_dashboard
    from .settings import load_settings
    from .state import load_state, save_state
    state = load_state()
    refresh_dashboard(state, load_settings())
    save_state(state)


if __name__ == "__main__":
    main()
