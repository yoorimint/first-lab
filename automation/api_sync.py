"""GitHub Actions 쪽: API 봇(서버 안 글쓰기)이 남긴 저장소 수정 묶음을 main 에 올린다 + API 봇 매일 점검 보고 알림.

운영자 11/12 '예비 필요 없고 그냥 api로 할 때 지금하고 똑같은 작업을 할 수 있게': 월정액은 글쓰기 세션이 직접 main 에 push 한다.
API 봇은 서버에 GitHub 쓰기 권한이 없다(운영자 추가 설정 없이) → 배포가 이미 쓰는 SSH 와 이 작업 자체 토큰(GITHUB_TOKEN)으로:

  deploy.yml (15분 예약·수동 실행)
    sync_fetch  (SSH 비밀값, 묶음 코드 안 돌림)   fetch : 서버 bot.api_writer outbox claim → 묶음 내려받기
    sync_check  (비밀값 없음, 읽기 권한)            check : 최신 main 위에 다시 쌓기 → 바꿀 수 있는 파일만인지(main 의 push_allowed)
                                                           → 서버와 같은 검사(bot.api_writer.check_work) → 검사한 커밋 묶음
    sync_push   (contents: write, 묶음 코드 안 돌림) push  : 검사한 바로 그 커밋을 main 에 fast-forward (강제 push 없음)
    sync_report (SSH 비밀값)                         report: 결과를 서버에 (outbox result → 봇 기록, 실패면 실패한 검사 이름)
    → gate·check·deploy 가 같은 실행에서 바로 배포 (GITHUB_TOKEN push 는 다른 작업을 깨우지 않으므로)
  health.yml (15분 예약)                             maint : 서버 outbox report → 문제가 있으면 실패 → GitHub 가 운영자에게 메일

공개 저장소라 작업 기록도 공개 — 보고 내용·묶음 내용은 기록에 찍지 않는다 (문제 수·실패한 검사 이름만).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.api_writer import check_work, push_allowed  # noqa: E402 - main 에서 받은 코드 (묶음이 못 바꾸는 파일: PUSH_DENIED)

SERVER_CMD = "cd /opt/first-lab && /opt/first-lab-venv/bin/python -m bot.api_writer outbox"
GIT_ENV = {"GIT_AUTHOR_NAME": "first-lab API 봇", "GIT_AUTHOR_EMAIL": "api-writer@first-lab.kr",
           "GIT_COMMITTER_NAME": "first-lab API 봇", "GIT_COMMITTER_EMAIL": "api-writer@first-lab.kr",
           "GIT_TERMINAL_PROMPT": "0"}


# ---------- 서버 (SSH) ----------

class Remote:
    """배포와 같은 SSH 계정 (저장소 비밀값 SERVER_HOST·SERVER_USER·SERVER_PASSWORD)."""

    def __init__(self, host: str, user: str, password: str, port: int = 22):
        import paramiko
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())   # 배포(appleboy/ssh-action)와 같게 지문 고정 없음
        self.client.connect(host, port=port, username=user, password=password, timeout=30, allow_agent=False,
                            look_for_keys=False)

    def outbox(self, *args: str) -> dict:
        """서버 bot.api_writer outbox <args> → 마지막 줄 JSON."""
        cmd = SERVER_CMD + " " + " ".join(shlex.quote(a) for a in args)
        _, stdout, stderr = self.client.exec_command(cmd, timeout=120)
        out = stdout.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(f"서버 명령 실패 ({code}): {stderr.read().decode('utf-8', 'replace').strip()[-200:]}")
        return json.loads((out.strip().splitlines() or ["{}"])[-1])

    def get(self, remote: str, local: Path) -> None:
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote, str(local))
        finally:
            sftp.close()


def remote_from_env() -> Remote:
    return Remote(os.environ["SERVER_HOST"], os.environ["SERVER_USER"], os.environ["SERVER_PASSWORD"])


def set_output(**values) -> None:
    """GitHub 작업 출력 (한 줄로). 로컬 실행이면 화면에."""
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={' '.join(str(v).split())[:900]}" for k, v in values.items()]
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


# ---------- git ----------

def git(repo: Path, *args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **GIT_ENV})
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args[0]} 실패: {r.stderr.strip()[-200:]}")
    return r


def bad_paths(repo: Path, base: str, head: str) -> list[str]:
    out = git(repo, "diff", "--name-only", "-z", base, head).stdout
    return [p for p in out.split("\0") if p and not push_allowed(p)]


def apply_bundle(repo: Path, bundle: Path, work: Path, out_bundle: Path, run_cmd=None) -> dict:
    """묶음을 최신 main(repo 의 origin/main) 위에 다시 쌓고 검사. {"status": ok|already|failed, "why", "sha", "base"}.
    검사는 work(따로 만든 작업 폴더)에서 — repo 의 코드(push_allowed·이 파일)는 묶음이 바꾸지 못함.
    ok 면 out_bundle 에 검사한 커밋 묶음 (push 작업이 그 커밋만 올림)."""
    base = git(repo, "rev-parse", "origin/main").stdout.strip()
    r = git(repo, "fetch", "-q", str(bundle), "+HEAD:refs/api/incoming", check=False)
    if r.returncode != 0:
        return {"status": "failed", "why": f"묶음을 못 읽음: {r.stderr.strip()[-150:]}", "base": base}
    git(repo, "worktree", "add", "-q", "--detach", str(work), "refs/api/incoming")
    git(work, "config", "merge.jsonlist.driver", "python3 automation/merge_json_list.py %O %A %B")   # check_and_push.sh 와 같음
    git(work, "config", "merge.ours.driver", "true")
    if git(work, "rebase", "-q", "origin/main", check=False).returncode != 0:
        git(work, "rebase", "--abort", check=False)
        return {"status": "failed", "why": "최신 main 과 합치다 충돌", "base": base}
    head = git(work, "rev-parse", "HEAD").stdout.strip()
    if head == base:
        return {"status": "already", "why": "", "sha": head, "base": base}
    bad = bad_paths(repo, base, head)
    if bad:
        return {"status": "failed", "why": f"바꾸면 안 되는 파일: {', '.join(bad[:3])}", "base": base}
    req = work / "bot" / "requirements.txt"
    if req.exists() and (ROOT / "bot" / "requirements.txt").read_bytes() != req.read_bytes():
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)], capture_output=True, timeout=600)
    run_cmd = run_cmd or (lambda cmd, timeout=300: subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout,
                                                                  env={**os.environ, **GIT_ENV}))
    failed = check_work(run_cmd)
    if failed:
        return {"status": "failed", "why": f"검사 실패: {', '.join(failed[:5])}", "base": base}
    head = git(work, "rev-parse", "HEAD").stdout.strip()   # 유형표를 다시 만들었으면 커밋이 하나 더
    bad = bad_paths(repo, base, head)
    if bad:
        return {"status": "failed", "why": f"바꾸면 안 되는 파일: {', '.join(bad[:3])}", "base": base}
    git(work, "bundle", "create", str(out_bundle), "HEAD", "^origin/main")
    return {"status": "ok", "why": "", "sha": head, "base": base}


def push_tested(repo: Path, bundle: Path, sha: str, base: str, remote: str = "origin") -> dict:
    """검사한 그 커밋만 main 에 (fast-forward). {"status": pushed|moved|failed, "why"} — moved = 그새 main 이 바뀜 (다음 실행이 다시)."""
    git(repo, "fetch", "-q", remote, "main")
    now = git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
    if now != base:
        return {"status": "moved", "why": "검사하는 사이 main 이 바뀜"}
    git(repo, "fetch", "-q", str(bundle), "+HEAD:refs/api/tested")
    got = git(repo, "rev-parse", "refs/api/tested").stdout.strip()
    if got != sha:
        return {"status": "failed", "why": "검사한 커밋과 올릴 커밋이 다름"}
    if git(repo, "merge-base", "--is-ancestor", base, sha, check=False).returncode != 0:
        return {"status": "failed", "why": "main 위에 쌓인 커밋이 아님"}
    bad = bad_paths(repo, base, sha)
    if bad:
        return {"status": "failed", "why": f"바꾸면 안 되는 파일: {', '.join(bad[:3])}"}
    r = git(repo, "push", "-q", remote, f"{sha}:refs/heads/main", check=False, timeout=300)
    if r.returncode != 0:
        err = r.stderr.strip()
        if any(w in err for w in ("non-fast-forward", "fetch first", "stale info")):
            return {"status": "moved", "why": "push 사이 main 이 바뀜"}
        return {"status": "failed", "why": f"main 에 push 거절: {err[-150:]}"}
    return {"status": "pushed", "why": "", "sha": sha}


def final_status(check_result: str, check_status: str, check_why: str, push_result: str, push_status: str,
                 push_why: str, sha: str) -> tuple[str, str, str]:
    """작업들 결과 → 서버에 알릴 (status, sha, why). 작업 자체가 죽었으면 retry (서버가 3번까지 다시, 그 뒤 실패로)."""
    if check_result != "success":
        return "retry", "", f"검사 작업이 끝나지 않음 ({check_result})"
    if check_status == "already":
        return "already", "", ""
    if check_status != "ok":
        return "failed", "", check_why or "검사 실패"
    if push_result != "success":
        return "retry", "", f"push 작업이 끝나지 않음 ({push_result})"
    if push_status == "pushed":
        return "pushed", sha, ""
    if push_status == "moved":
        return "retry", "", push_why
    return "failed", "", push_why or "push 실패"


# ---------- 명령 ----------

def cmd_fetch(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        remote = remote_from_env()
        info = remote.outbox("claim")
        if info.get("path"):
            remote.get(info["path"], out / "outbox.bundle")
    except Exception as e:  # noqa: BLE001 - 서버에 못 닿으면 이번엔 건너뜀 (배포는 그대로, 서버가 6시간 뒤 실패로 정리)
        print(f"::warning::API 봇 반영 묶음을 못 가져옴: {str(e)[:200]}")
        set_output(has="false")
        return 0
    if not info.get("path"):
        print("반영할 API 봇 묶음 없음")
        set_output(has="false")
        return 0
    print(f"API 봇 묶음: {info.get('head', '')[:7]} (시도 {info.get('tries')}번째, 실행 {len(info.get('runs') or [])}개)")
    set_output(has="true", head=info.get("head", ""))
    return 0


def cmd_check(args) -> int:
    tested = Path(args.out)
    tested.mkdir(parents=True, exist_ok=True)
    result = apply_bundle(ROOT, Path(args.bundle), Path(args.work), tested / "tested.bundle")
    print(f"검사 결과: {result['status']} {result.get('why', '')}")
    set_output(status=result["status"], why=result.get("why", ""), sha=result.get("sha", ""), base=result.get("base", ""))
    return 0


def cmd_push(args) -> int:
    result = push_tested(ROOT, Path(args.bundle), args.sha, args.base)
    print(f"push 결과: {result['status']} {result.get('why', '')}")
    set_output(status=result["status"], why=result.get("why", ""))
    return 0


def cmd_report(args) -> int:
    e = os.environ
    status, sha, why = final_status(e.get("CHECK_RESULT", ""), e.get("CHECK_STATUS", ""), e.get("CHECK_WHY", ""),
                                    e.get("PUSH_RESULT", ""), e.get("PUSH_STATUS", ""), e.get("PUSH_WHY", ""),
                                    e.get("PUSHED_SHA", ""))
    print(f"서버에 알림: {status} {sha[:7]} {why}")
    got = remote_from_env().outbox("result", "--head", e.get("HEAD_SHA", ""), "--status", status, "--sha", sha, "--why", why)
    print(f"서버: {got.get('result')}")
    return 0


def cmd_maint(args) -> int:
    """API 봇 매일 점검 보고 → 문제가 있으면 이 작업을 실패시킴 (GitHub 가 운영자에게 메일 — health.yml 과 같은 알림 통로)."""
    try:
        report = remote_from_env().outbox("report")
    except Exception as e:  # noqa: BLE001 - 서버에 못 닿는 것은 하루 1번 건강 점검이 봄
        print(f"::warning::매일 점검 보고를 못 가져옴: {str(e)[:200]}")
        return 0
    if not report:
        print("새 매일 점검 보고 없음 (월정액 방식이면 예약 작업 끝 메시지가 휴대폰 알림)")
        return 0
    n = int(report.get("problems", -1))
    at = report.get("at", "")
    if n == 0:
        print(f"✅ 매일 점검(API 봇) {at}: 이상 없음")
        return 0
    what = {-1: "보고 형식이 다름 (첫 줄 '이상 없음'/'문제 N건' 아님)", -2: "점검 실행 실패"}.get(n, f"문제 {n}건")
    print(f"::error::매일 점검(API 봇) {at}: {what} — 자세한 내용은 사이트 관리자 > 설정 > 퍼스트랩 봇 > 봇 기록 ('매일 점검 보고(API 봇)')")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--out", required=True)
    p = sub.add_parser("check")
    p.add_argument("--bundle", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("push")
    p.add_argument("--bundle", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--base", required=True)
    sub.add_parser("report")
    sub.add_parser("maint")
    args = ap.parse_args(argv)
    return {"fetch": cmd_fetch, "check": cmd_check, "push": cmd_push, "report": cmd_report, "maint": cmd_maint}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
