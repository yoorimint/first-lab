"""매분 봇(bot/run.py)에서 오래 걸리거나 멈출 수 있는 일을 systemd 별도 실행으로 띄우기 (11/15).

봇 안에서 돌면 그동안 예약 공개·글쓰기 시작이 밀리고, 멈추면 12분 시간 제한까지 매분 봇 전체가 선다.
같은 이름의 실행이 아직 돌고 있으면 새로 띄우지 않는다 (systemd 가 '이미 있음'으로 거절).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SEC = 15 * 60


def launch(unit: str, module: str, run=subprocess.run, timeout_sec: int = TIMEOUT_SEC) -> bool:
    """띄웠거나 이미 도는 중이면 True. systemd-run 이 없거나 못 띄우면 False — 부르는 쪽이 직접 한다."""
    if not shutil.which("systemd-run"):
        return False
    try:
        r = run(["systemd-run", f"--unit={unit}", "--collect", "--quiet", f"--working-directory={ROOT}",
                 "-p", f"RuntimeMaxSec={timeout_sec}", "-p", "Nice=10", "-p", "CPUWeight=20", "-p", "IOWeight=20",
                 sys.executable, "-m", module], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 or "already" in (r.stderr or "")
