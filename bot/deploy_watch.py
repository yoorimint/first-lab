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
REPO = Path(__file__).resolve().parents[1]   # 서버 /opt/first-lab — 배포가 쓰는 origin(인증 설정 포함)을 그대로 씀
LIMIT_MIN = 60   # 15분 따라잡기가 네 번 돌아도 안 올라가면
READ_FAIL = "배포 감지 실패: main 버전을 못 읽음 (git ls-remote origin)"


def main_sha() -> str:
    # 저장소가 비공개라 주소만으로는 못 읽음 — 11/6 배포가 75분 막혔는데 조용히 빈 값 → 알림 없음
    out = subprocess.run(["git", "-C", str(REPO), "ls-remote", "origin", "refs/heads/main"],
                         capture_output=True, text=True, timeout=30).stdout
    return out.split()[0] if out.strip() else ""


def check(state: dict, now: datetime, deployed: str, main: str) -> str | None:
    """상태를 갱신하고, 막혔으면 알림 문장."""
    now = now.replace(tzinfo=None)  # 봇은 KST 시각(시간대 있음)을 넘김 — 저장한 시각(시간대 없음)과 빼려면 맞춰야 함 (11/5 검토: TypeError)
    w = state.get("deploy_watch") or {}
    stamp = now.strftime("%Y-%m-%d %H:%M")
    behind = bool(main and deployed and main != deployed)
    # 시계는 'main 이 서버보다 앞서기 시작한 때'부터 (배포가 진행되면 다시). 11/10: 서버가 main 과 같던 10시간을 기다린 시간으로 세어,
    # 새 커밋이 올라온 지 1분 만에 '580분째 안 올라감' 거짓 알림 → 건강 점검 실패
    if not behind:
        w = {"deployed": deployed, "since": ""}
    elif deployed != w.get("deployed") or not w.get("since"):
        w = {"deployed": deployed, "since": stamp}
    w["main"] = main
    state["deploy_watch"] = w
    if not behind:
        state.pop("deploy_alert", None)
        return None
    waited = (now - datetime.strptime(w["since"], "%Y-%m-%d %H:%M")).total_seconds() / 60
    if waited < LIMIT_MIN:
        state.pop("deploy_alert", None)
        return None
    alert = (f"배포가 {int(waited)}분째 안 올라감 (서버 {deployed[:7]}, main {main[:7]}) — 배포 전 검사 실패일 가능성이 큼: "
             "python3 -m unittest discover -s tests && node tests/test_calculators.mjs 로 원인을 찾아 고친다. "
             "검사가 모두 통과하면 GitHub 쪽 문제(11/6 결제·사용 한도로 작업 시작 안 됨) — 고칠 것 없음, 운영자 확인 필요")
    state["deploy_alert"] = alert
    return alert


def run(state: dict, now: datetime | None = None) -> None:
    from .state import add_log
    now = now or datetime.now()
    try:
        deployed = DEPLOYED.read_text(encoding="utf-8").strip()
        main = main_sha()
        alert = check(state, now, deployed, main) if main else READ_FAIL   # 못 읽으면 조용히 넘기지 않고 기록
    except Exception:  # noqa: BLE001 - 배포 감지가 1분 봇(공개·실행)을 멈추면 안 됨
        alert = READ_FAIL
    if alert and state.get("deploy_alert_logged", "")[:13] != now.strftime("%Y-%m-%d %H"):
        add_log(state, False, alert)   # 한 시간에 한 번만 기록
        state["deploy_alert_logged"] = now.strftime("%Y-%m-%d %H:%M")
