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


def stale_maint_launch(cgroup_path: str = "/proc/self/cgroup", parent_cmd: str | None = None) -> bool:
    """전체 글 보정(server/maint.sh)이 옛 방식(배포 안 setsid·flock 줄서기)으로 띄워진 것인지.
    11/17: 이미 반영된 보정이 15분짜리로 하루 종일 연달아 돎(13:23·13:39·… 패치는 13시 뒤 안 바뀜) — 11/16 밤 배포들이 줄 세운 것.
    지금은 server/maint-start.sh 가 firstlab-maint 실행으로 띄우므로, maint.sh 에서 불렸는데 그 실행 밖이면 옛 줄 → 무거운 단계 건너뜀."""
    import os
    if not shutil.which("systemd-run"):
        return False
    if parent_cmd is None:
        try:
            with open(f"/proc/{os.getppid()}/cmdline", "rb") as f:
                parent_cmd = f.read().replace(b"\0", b" ").decode("utf-8", "ignore")
        except OSError:
            return False
    if "server/maint.sh" not in parent_cmd:
        return False
    try:
        with open(cgroup_path) as f:
            return "firstlab-maint.service" not in f.read()
    except OSError:
        return False



MAINT_AGAIN = Path("/var/lib/first-lab/bot/maint-again")


def relaunch_pending_maint(again: Path = MAINT_AGAIN, run=subprocess.run, now: float | None = None) -> bool:
    """전체 글 보정의 '한 번 더' 요청이 남았는데 보정이 안 돌고 있으면 다시 띄움 (매분 봇이 10분마다).
    11/19: 보정은 한 번에 최대 3회차까지만 이어서 돎 — 3회차 중에 온 요청(10:07 패치·코드)이 표시만 남고 버려져,
    그 뒤 배포는 '패치 안 바뀜'으로 건너뛰어 #7365 패치·보정 개선이 1시간 넘게 서버에 안 들어감."""
    import time
    if not again.exists() or not shutil.which("systemctl"):
        return False
    if (now or time.time()) - again.stat().st_mtime < 180:  # 방금 남긴 표시는 도는 보정이 끝날 때 가져감
        return False
    try:
        if run(["systemctl", "is-active", "--quiet", "firstlab-maint"], timeout=30).returncode == 0:
            return False
        return run(["bash", str(ROOT / "server" / "maint-start.sh")], capture_output=True, text=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
