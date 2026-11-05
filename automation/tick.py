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
        failed = [r for r in runs[:3] if r["conclusion"] == "failure"]
        if failed:
            problems.append(f"최근 배포 실패 {failed[0]['head_sha'][:7]} (run {failed[0]['id']})")
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
