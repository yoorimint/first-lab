"""배포 밀림 감지 (봇이 10분마다) — 감시 세션 없이 배포 실패를 알아채고 글쓰기 작업이 고치게 (11/5 운영자 '남은 일 다 해라').

배포(GitHub Actions)는 테스트를 통과해야 서버에 올라가고, 끝나면 deployed-version.txt 에 그 버전을 적는다.
main 이 서버 버전보다 앞서 있는데 LIMIT_MIN 분 동안 서버 버전이 한 번도 안 바뀌면 배포가 막힌 것
(대개 테스트 실패 — 글쓰기 작업이 올린 계산기·글 파일 때문). → state["deploy_alert"] → /next·/maintenance
→ 글쓰기 작업이 다음 실행 첫머리에 테스트를 돌려 원인을 고친다 (daily-writer.md 0단계).
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

DEPLOYED = Path("/var/www/first-lab/deployed-version.txt")
REMOTE = "https://github.com/yoorimint/first-lab"
LIMIT_MIN = 60   # 15분 따라잡기가 네 번 돌아도 안 올라가면


def main_sha() -> str:
    out = subprocess.run(["git", "ls-remote", REMOTE, "refs/heads/main"], capture_output=True, text=True, timeout=30).stdout
    return out.split()[0] if out.strip() else ""


def check(state: dict, now: datetime, deployed: str, main: str) -> str | None:
    """상태를 갱신하고, 막혔으면 알림 문장."""
    w = state.get("deploy_watch") or {}
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if deployed != w.get("deployed"):
        w = {"deployed": deployed, "since": stamp}          # 배포가 진행됨 → 시계 다시
    w["main"] = main
    state["deploy_watch"] = w
    if not main or not deployed or main == deployed:
        state.pop("deploy_alert", None)
        return None
    waited = (now - datetime.strptime(w["since"], "%Y-%m-%d %H:%M")).total_seconds() / 60
    if waited < LIMIT_MIN:
        state.pop("deploy_alert", None)
        return None
    alert = (f"배포가 {int(waited)}분째 안 올라감 (서버 {deployed[:7]}, main {main[:7]}) — 배포 전 검사 실패일 가능성이 큼: "
             "python3 -m unittest discover -s tests && node tests/test_calculators.mjs 로 원인을 찾아 고친다")
    state["deploy_alert"] = alert
    return alert


def run(state: dict, now: datetime | None = None) -> None:
    from .state import add_log
    now = now or datetime.now()
    try:
        deployed = DEPLOYED.read_text(encoding="utf-8").strip()
        main = main_sha()
    except (OSError, subprocess.SubprocessError):
        return  # 확인 못 하면 판단 안 함
    alert = check(state, now, deployed, main)
    if alert and state.get("deploy_alert_logged", "")[:13] != now.strftime("%Y-%m-%d %H"):
        add_log(state, False, alert)   # 한 시간에 한 번만 기록
        state["deploy_alert_logged"] = now.strftime("%Y-%m-%d %H:%M")
