"""10분 감시 한 번에 보기 (감시 세션용, automation/monitor.md). 문제 있으면 종료코드 1.

배포(서버 버전 ↔ main, GitHub Actions 최근 실패), /health, 봇 기록의 오류(최근 1시간), 패치 미반영, 보류 글,
글쓰기 작업의 최근 대기 글 검수(표본 재검수 후보)를 한 화면에.
사용: FIRSTLAB_API_TOKEN=... python3 automation/tick.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SITE = "https://first-lab.kr"


def get(url: str, token: bool = False) -> dict:
    req = urllib.request.Request(url, headers={"X-Firstlab-Token": os.environ.get("FIRSTLAB_API_TOKEN", "")} if token else {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def deploy_problem(runs: list[dict]) -> str | None:
    """가장 최근에 끝난 배포가 실패일 때만 문제 (뒤 배포가 성공했으면 이미 풀린 것 — 11/5 지난 실패를 계속 문제로 보임)."""
    done = [r for r in runs if r.get("name", "").startswith("Deploy") and r.get("status") == "completed"]
    if done and done[0].get("conclusion") == "failure":
        return f"최근 배포 실패 {done[0]['head_sha'][:7]} (run {done[0]['id']})"
    return None


BILLING_WORDS = ("spending limit", "payments have failed")


def billing_blocked(annotations: list[dict]) -> bool:
    """배포 작업이 러너 없이 바로 실패한 이유가 GitHub 결제·사용 한도인지 (11/5: 비공개 저장소 무료 분 소진 → 모든 배포가 2초 만에 실패, 로그 없음)."""
    return any(w in (a.get("message") or "") for a in annotations for w in BILLING_WORDS)


def failure_reason(run: dict) -> str:
    """실패한 배포의 첫 실패 작업 표시(annotation)로 원인 한 줄 — 로그가 없는 실패(러너 시작 안 됨)도 보이게."""
    jobs = get(f"https://api.github.com/repos/yoorimint/first-lab/actions/runs/{run['id']}/jobs")["jobs"]
    bad = next((j for j in jobs if j.get("conclusion") == "failure"), None)
    if not bad:
        return ""
    notes = get(f"https://api.github.com/repos/yoorimint/first-lab/check-runs/{bad['id']}/annotations")
    if billing_blocked(notes):
        return " — GitHub 결제·사용 한도 문제로 작업이 시작되지 않음 (운영자: GitHub Billing 확인 또는 저장소 공개 전환)"
    return f" — {bad['name']} 작업 실패"


def main() -> int:
    problems = []
    now = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    deployed = urllib.request.urlopen(f"{SITE}/deployed-version.txt", timeout=20).read().decode().strip()
    main_sha = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True).stdout.strip()
    print(f"배포: 서버 {deployed[:7]} / main {main_sha[:7]}" + ("" if deployed == main_sha else " (다름)"))
    try:
        runs = get("https://api.github.com/repos/yoorimint/first-lab/actions/runs?per_page=6")["workflow_runs"]
        for r in runs[:4]:
            print(f"  {r['name'][:16]} {r['status']} {r['conclusion']} {r['head_sha'][:7]} {r['created_at'][11:16]}Z")
        bad = deploy_problem(runs)
        if bad:
            try:
                bad += failure_reason(next(r for r in runs if r["head_sha"][:7] in bad))
            except Exception as e:  # noqa: BLE001
                bad += f" (원인 확인 실패: {e})"
            problems.append(bad)
    except Exception as e:  # noqa: BLE001
        print(f"  GitHub 확인 실패: {e}")
    h = get(f"{SITE}/wp-json/firstlab/v1/health")
    print(f"건강: 마지막 결과 {h['last_done_at']} · 실행 성공 {h['last_fire_ok_at']} · 실행 실패 {h['last_fire_error_at'] or '-'} · 배포 막힘 {h['deploy_stuck']} · 대기 {h['ready_count']}")
    if h["deploy_stuck"]:
        problems.append("배포 막힘")
    m = get(f"{SITE}/wp-json/firstlab/v1/maintenance", token=True)
    since = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    errs = [l for l in m["log"] if not l.get("ok") and str(l.get("time", "")) >= since]
    for l in errs:
        print(f"  ❌ {l['time']} {str(l['msg'])[:160]}")
    if errs:
        problems.append(f"최근 1시간 오류 {len(errs)}건")
    if m.get("patch_problems"):
        problems.append(f"패치 미반영 {len(m['patch_problems'])}건: {m['patch_problems'][0][:80]}")
    held = m.get("held_drafts") or []
    print(f"보류 {len(held)}편" + (f": {[(x['post_id'], x['reason'][:40]) for x in held]}" if held else "") + f" · 쉬는 글 {len(m.get('gave_up') or [])}편")
    reviews = [l for l in m["log"] if "대기 글 검수(직접)" in str(l.get("msg", ""))][:3]
    for l in reviews:
        print(f"  검수(글쓰기 작업): {l['time']} {str(l['msg'])[:100]}")
    print("문제: " + ("; ".join(problems) if problems else "없음"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
