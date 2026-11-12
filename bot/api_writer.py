"""API 봇 방식: 월정액 예약 작업(bot/routine.py)과 똑같은 글쓰기 작업을 서버 안에서 Claude Code(헤드리스)로 돌린다.

운영자 11/12 '언제든 api로 바꾸면 똑같이 돌아가게': 설정 '글 작성 방식: API 봇' + 'API 봇 사용' 이면
  같은 지시서(automation/daily-writer.md, 점검은 maintenance.md) · 같은 실행 메시지(글 작성·manual-request·maintenance-check·slot-N)
  · 같은 관문(bot/publish_queue) · 같은 발행 방식(초안·바로·예약)으로 돈다. 다른 점은 요금이 설정 페이지에 넣은 Claude API 키로 나가는 것뿐.

흐름 (1분 봇은 시작만 하고 바로 돌아감):
  run.fire_routine → fire() → systemd-run 으로 따로 실행 (python -m bot.api_writer run --text ...)
  → run_once(): 잠금 → 작업용 저장소(WORKDIR)를 최신 main 으로 + 아직 main 에 안 들어간 앞 실행 결과(대기 묶음)를 위에 쌓음
  → claude -p (전용 사용자, 키는 환경 변수로만, FIRSTLAB_LOCAL=push — 월정액처럼 저장소를 고치는 일까지 다 하되 커밋·push 는 안 함)
  → 끝나면 이 실행(root)이: 바뀐 것을 커밋 1개로 → 최신 main 위에 다시 쌓기 → check_and_push.sh 와 같은 검사
    → 통과하면 (1) 새 queue 파일은 바로 LOCAL_QUEUE_DIR → bot.publish_queue (월정액과 같은 관문, 다음 실행을 기다리게 하지 않음)
               (2) main 에 안 들어간 커밋 전부를 묶음(git bundle) 하나로 OUTBOX 에 (pending — 앞 묶음을 다 포함)
저장소 반영 (운영자 11/12 '예비 필요 없고 그냥 api로 할 때 지금하고 똑같은 작업을 할 수 있게' — 토큰 등 새 설정 없이):
  GitHub Actions(deploy.yml 15분 예약)가 배포에 쓰는 SSH 로 묶음을 가져가(outbox claim) automation/api_sync.py 로
  최신 main 위에 다시 쌓기 → 바꿀 수 있는 파일만인지(push_allowed) → 같은 검사(check_work) → 작업 자체 토큰(GITHUB_TOKEN)으로 main 에 push
  → 같은 실행이 바로 배포 → 결과를 서버에 알림(outbox result). 배포의 publish_queue 는 이미 올린 queue 파일을 완료 표시(같은 이름·내용)로
  건너뜀 → 같은 글이 두 번 안 올라감. 실패하면 아무것도 안 올리고 봇 기록에 실패한 검사 → 다음 실행이 새 main 에서 다시.
  GitHub 가 6시간 넘게 안 가져가면 실패로 셈. 반영이 연속 2번 실패하면 하루 동안 저장소 일은 건너뜀(FIRSTLAB_LOCAL=1) → 다음 날 다시 시도.
매일 점검 보고: 세션 마지막 메시지(월정액은 휴대폰 알림)를 봇 기록에 남기고 OUTBOX 에 보고 파일 → Health check 작업이 15분마다
  가져가 문제가 있으면 실패 → GitHub 가 운영자에게 메일 (automation/api_sync.py maint).
안전: 한 번에 1개(잠금), 시간 제한, 낮은 CPU 몫·메모리 상한(systemd), 하루 비용 상한(시작 전 확인 + --max-budget-usd).
"""
from __future__ import annotations

import argparse
import fcntl
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .routine import MAINT_TEXT

ROOT = Path(__file__).resolve().parent.parent           # 서버 코드 (/opt/first-lab)
BASE = Path("/var/lib/first-lab/writer")                 # 글쓰기 전용 사용자의 집 (Claude Code 설정·기록)
WORKDIR = BASE / "repo"                                  # 글쓰기 작업용 저장소 (배포 폴더와 따로 — 배포가 reset 해도 안 섞임)
LOCAL_QUEUE_DIR = Path("/var/lib/first-lab/bot/local-queue")  # 이 실행이 만든 결과 파일 → bot.publish_queue 가 같이 읽음
STATUS_FILE = Path("/var/lib/first-lab/bot/api-writer.json")  # 실행 중 표시 (끝 기록 없이 멈춘 실행 찾기)
LOCK_FILE = "/run/firstlab-api-writer.lock"
WRITER_USER = "firstlab-writer"                          # 권한 없는 전용 사용자 (서버 비밀값·워드프레스 파일을 못 읽음)
CLAUDE_BIN = "/opt/first-lab-claude/bin/claude"          # server/bot-setup.sh 가 고정 버전으로 설치
NODE_BIN = "/opt/first-lab-node/bin"
VENV_BIN = "/opt/first-lab-venv/bin"                     # 지시서의 python3 명령이 봇과 같은 패키지를 쓰게
REPO_URL = "https://github.com/yoorimint/first-lab"      # 공개 저장소 (작업 폴더는 읽기만 — push 는 GitHub Actions 가 함)
OUTBOX = Path("/var/lib/first-lab/bot/outbox")           # main 에 올릴 묶음 (root 전용, GitHub Actions 가 배포 SSH 로 가져감)
OUTBOX_LOCK = "/run/firstlab-outbox.lock"
SKIP_NOTE_FILE = Path("/var/lib/first-lab/bot/api-writer-skip-note.txt")  # 저장소 일 건너뜀 알림을 하루 한 번만
STALE_HOURS = 6          # GitHub 가 이만큼 묶음을 안 가져가면(Actions 멈춤) 실패로 → 다음 실행이 새 main 에서 다시
CLAIM_TRIES = 3          # 가져간 묶음이 이만큼 결과 없이 끝나면 실패로
UNHEALTHY_FAILS = 2      # 반영이 연속 이만큼 실패하면 하루 동안 저장소 일을 건너뜀 (같은 검수를 매번 다시 해 돈만 쓰지 않게)
UNHEALTHY_HOURS = 24
KEEP_FAILED_DAYS = 30
# 글쓰기 작업이 바꿀 수 있는 파일 (지시서 5단계 커밋 목록 + 검수 기록 + 검사 코드). 그 밖(server/·.github/·지시서·이 파일)은 빼고 올린다.
# GitHub 쪽(automation/api_sync.py)도 main 의 이 목록으로 다시 확인한다.
PUSH_ALLOWED = ("wordpress/queue/*", "wordpress/patches.json", "wordpress/themes/first-lab/calculators/*", "wordpress/pages/*",
                "automation/reviewed.txt", "automation/common-errors.md", "automation/review-only-log.md",
                "automation/quality-log.csv", "automation/source-check-log.md", "automation/error_types.json",
                "automation/*_check.py", "bot/*", "tests/*")
PUSH_DENIED = ("bot/api_writer.py", "bot/config.py")
GIT_NAME, GIT_EMAIL = "first-lab API 봇", "api-writer@first-lab.kr"
UNIT = "firstlab-api-writer"

DEFAULT_MODEL = "claude-opus-5-5"
RUN_TIMEOUT_MIN = 80     # 글쓰기 한 번 시간 제한 — run.CONTINUOUS_TIMEOUT_MIN(90) 보다 짧게 (연속 작성이 겹쳐 시작하지 않게)
UNIT_TIMEOUT_MIN = RUN_TIMEOUT_MIN + 30   # 바깥 제한 (올리기·사진 만들기·push 전 검사 최대 3번 포함)
RUN_MAX_USD = 15.0       # 한 번 실행의 최대 비용 (하루 상한이 0=없음이어도)
MIN_START_USD = 0.5      # 하루 상한까지 이만큼도 안 남으면 시작하지 않음 (시작하자마자 예산에 걸려 끊기면 돈만 씀)
MEMORY_HIGH, MEMORY_MAX = "800M", "1200M"   # 2GB 서버: 넘치면 글쓰기만 끊기고 사이트는 그대로 (예상 300~700MB)
MAX_RESULT_BYTES = 5_000_000
KEEP_LOCAL_DAYS = 60
# 월정액 세션에 있는 도구 중 글쓰기에 쓰는 것만 (예약·작업 트리·워크플로 같은 도구는 뺌). 이 밖의 도구는 묻지 않고 거절(dontAsk).
TOOLS = "Task,Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"

PROMPT = ("저장소의 automation/daily-writer.md 를 읽고 그대로 따른다. "
          "이번 실행은 서버 안 API 봇 실행이다 (환경 변수 FIRSTLAB_LOCAL={local}) — 지시서의 'API 봇 실행' 항목대로 한다.\n"
          "실행 메시지: {text}")


class ApiWriterError(RuntimeError):
    pass


_launched = False  # 이번 1분 봇 실행에서 이미 시작함 (따로 실행이 잠금을 잡기 전 같은 분에 또 시작하지 않게)


# ---------- 1분 봇 쪽 (시작만) ----------

def start_problem(settings, spent: float) -> str | None:
    """시작하면 안 되는 이유 (없으면 None). 키·모델·설치·하루 비용 상한."""
    if settings.write_mode != "bot":
        return "글 작성 방식이 월정액이라 API 봇을 쓰지 않음"
    if not settings.enabled:
        return "설정 'API 봇 사용' 이 꺼져 있음 (켜야 API 키로 씀)"
    if settings.provider != "anthropic":
        return "설정 'AI 제공사' 를 Anthropic Claude 로 (API 봇은 Claude Code 로 씀)"
    if not settings.api_key:
        return "Claude API 키가 없음 (설정 페이지 Claude API 키 또는 서버 ANTHROPIC_API_KEY)"
    if not (Path(CLAUDE_BIN).exists() and BASE.is_dir()):
        return f"서버에 Claude Code·글쓰기 사용자가 아직 없음 ({CLAUDE_BIN}, 다음 전체 배포 때 server/bot-setup.sh 가 설치)"
    cap = settings.daily_cost_cap
    if cap > 0 and cap - spent < MIN_START_USD:
        return f"오늘 AI 비용 ${spent:.2f} → 하루 상한 ${cap:.2f} 도달 (내일 다시)"
    return None


def budget(settings, spent: float) -> float:
    """이번 실행 예산(달러) — 하루 상한의 남은 몫, 상한이 없으면 RUN_MAX_USD."""
    cap = settings.daily_cost_cap
    return round(min(RUN_MAX_USD, cap - spent) if cap > 0 else RUN_MAX_USD, 2)


def busy() -> bool:
    """글쓰기 실행 중인지 (잠금 파일을 잡고 있는 실행이 있음). 실행이 죽으면 잠금은 저절로 풀린다 (남은 잠금이 없음)."""
    if _launched:
        return True
    try:
        with open(LOCK_FILE, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def fire(settings, text: str = "", spent: float = 0.0, label: str = "") -> str:
    """routine.fire 와 같은 자리: 글쓰기 작업을 따로 시작하고 바로 돌아감. 못 하면 ApiWriterError."""
    problem = start_problem(settings, spent)
    if problem:
        raise ApiWriterError(problem)
    if busy():
        raise ApiWriterError("이미 글쓰기 실행 중 (한 번에 1개)")
    global _launched
    _launch([sys.executable, "-m", "bot.api_writer", "run", f"--text={text}", f"--label={label}"])
    _launched = True
    return f"서버 안 실행 시작 (모델 {settings.model or DEFAULT_MODEL}, 이번 예산 ${budget(settings, spent):.2f})"


def _launch(cmd: list[str]) -> None:
    """1분 봇과 따로 실행 — systemd 가 시간 제한·낮은 CPU 몫·메모리 상한을 건다. 키는 넘기지 않는다 (실행이 스스로 읽음)."""
    if shutil.which("systemd-run"):
        full = ["systemd-run", f"--unit={UNIT}", "--collect", "--quiet", f"--working-directory={ROOT}",
                "-p", f"RuntimeMaxSec={UNIT_TIMEOUT_MIN * 60}", "-p", "Nice=10", "-p", "CPUWeight=20", "-p", "IOWeight=20",
                "-p", f"MemoryHigh={MEMORY_HIGH}", "-p", f"MemoryMax={MEMORY_MAX}", *cmd]
        r = subprocess.run(full, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ApiWriterError(f"따로 실행 실패: {(r.stderr or r.stdout).strip()[:150]}")
        return
    subprocess.Popen(["nice", "-n", "10", *cmd], cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- 따로 실행되는 쪽 ----------

def claude_command(model: str, usd: float, text: str, local: str = "push") -> list[str]:
    """Claude Code 헤드리스 명령. 키는 여기 넣지 않는다 (명령줄은 ps 로 보임) — 환경 변수로만. local: '1' 또는 'push'."""
    return [CLAUDE_BIN, "-p", PROMPT.format(text=text or "(없음)", local=local), "--output-format", "json",
            "--model", model, "--max-budget-usd", f"{usd:.2f}",
            "--permission-mode", "dontAsk", "--tools", TOOLS, "--allowedTools", TOOLS, "--strict-mcp-config"]


def writer_env(api_key: str = "", site_token: str = "", local: str = "push") -> dict[str, str]:
    """글쓰기 사용자의 환경 (그 밖의 서버 환경 변수는 넘기지 않음). 키가 빈 값이면 넣지 않는다 (git 명령용).
    서버에는 GitHub 쓰기 권한이 없다 (main 에 올리는 것은 GitHub Actions)."""
    env = {
        "HOME": str(BASE), "USER": WRITER_USER, "LOGNAME": WRITER_USER, "LANG": "C.UTF-8", "TZ": "Asia/Seoul",
        "PATH": f"{VENV_BIN}:{NODE_BIN}:/usr/local/bin:/usr/bin:/bin",
        "ANTHROPIC_API_KEY": api_key,              # 요금은 이 키로 (설정 페이지 값, 없으면 secrets.env)
        "FIRSTLAB_API_TOKEN": site_token,          # 지시서의 /next·/law 통로 (월정액 세션과 같은 값)
        "FIRSTLAB_LOCAL": local,                   # 지시서 'API 봇 실행' 항목 (push = 다 하고 커밋·push 는 서버·Actions 가, 1 = 저장소 일 건너뜀)
        "GIT_AUTHOR_NAME": GIT_NAME, "GIT_AUTHOR_EMAIL": GIT_EMAIL,
        "GIT_COMMITTER_NAME": GIT_NAME, "GIT_COMMITTER_EMAIL": GIT_EMAIL,
        "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # 고정 버전 그대로, 부가 통신 없음
    }
    return {k: v for k, v in env.items() if v}


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _as_writer(cmd: list[str], env: dict, timeout: int = 300, cwd: Path | None = None,
               binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or WORKDIR, env=env, user=WRITER_USER, group=WRITER_USER, extra_groups=[],
                          capture_output=True, text=not binary, timeout=timeout)


def prepare_workdir(env: dict) -> None:
    """작업용 저장소를 최신 main 으로 (지난 실행이 남긴 파일 지움). 공개 저장소라 읽기 권한만 씀."""
    if not (WORKDIR / ".git").exists():
        r = _as_writer(["git", "clone", "-q", REPO_URL, str(WORKDIR)], env, timeout=600, cwd=BASE)
    else:
        r = _as_writer(["git", "fetch", "-q", "origin", "main"], env)
        if r.returncode == 0:
            r = _as_writer(["git", "checkout", "-q", "-f", "-B", "daily-writer", "origin/main"], env)
        if r.returncode == 0:
            r = _as_writer(["git", "clean", "-fdq"], env)
    if r.returncode != 0:
        raise ApiWriterError(f"작업용 저장소 준비 실패: {r.stderr.strip()[:150]}")


def _merge_drivers(env: dict) -> None:
    """check_and_push.sh 와 같은 병합 도구 (patches.json 양쪽 합치기, 유형표는 우리 쪽 → 다시 만듦)."""
    _as_writer(["git", "config", "merge.jsonlist.driver", "python3 automation/merge_json_list.py %O %A %B"], env)
    _as_writer(["git", "config", "merge.ours.driver", "true"], env)


def stack_pending(env: dict) -> dict | None:
    """아직 main 에 안 들어간 앞 실행 결과(가장 새 묶음 — 앞 묶음을 다 포함)를 작업 폴더(최신 main) 위에 쌓는다.
    월정액은 실행마다 바로 main 에 올라가 다음 실행이 그 결과(검수 기록·계산기 값)를 보고 시작함 → 같게 (같은 검수를 두 번 안 하게).
    쌓은 묶음 정보({} = 없음), 합치다 충돌하면 None (작업 폴더는 main 그대로)."""
    with _outbox_lock():
        src, meta = _newest_bundle()
        if not src:
            return {}
        fd, tmp = tempfile.mkstemp(dir=BASE, prefix="stack-", suffix=".bundle")   # 글쓰기 사용자가 읽을 복사본 (새 이름만 — 링크 덮어쓰기 없음)
        with os.fdopen(fd, "wb") as f:
            f.write(src.read_bytes())
    try:
        os.chmod(tmp, 0o644)
        _merge_drivers(env)
        ok = (_as_writer(["git", "fetch", "-q", tmp, "+HEAD:refs/outbox/stack"], env).returncode == 0
              and _as_writer(["git", "checkout", "-q", "-f", "-B", "daily-writer", "refs/outbox/stack"], env).returncode == 0)
        if ok and _as_writer(["git", "rebase", "-q", "origin/main"], env, timeout=600).returncode != 0:
            _as_writer(["git", "rebase", "--abort"], env)
            ok = False
        if not ok:
            _as_writer(["git", "checkout", "-q", "-f", "-B", "daily-writer", "origin/main"], env)
            _as_writer(["git", "clean", "-fdq"], env)
            return None
        heads = _as_writer(["git", "rev-parse", "HEAD", "origin/main"], env).stdout.split()
        return {} if len(heads) == 2 and heads[0] == heads[1] else meta   # 다 main 에 들어갔으면 쌓은 것 없음
    finally:
        Path(tmp).unlink(missing_ok=True)


def run_session(cmd: list[str], env: dict, timeout_s: int) -> tuple[dict | None, str]:
    """(결과 JSON 또는 None, 오류 설명). 시간이 넘으면 그 실행의 모든 프로세스를 끝냄."""
    proc = subprocess.Popen(cmd, cwd=WORKDIR, env=env, user=WRITER_USER, group=WRITER_USER, extra_groups=[],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None, f"시간 초과 ({timeout_s // 60}분)"
    for chunk in (out.strip(), (out.strip().splitlines() or [""])[-1]):
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data, ""
        except ValueError:
            continue
    return None, f"결과를 못 읽음 (종료코드 {proc.returncode}): {(err or out).strip()[-150:]}"


def session_ok(result: dict | None) -> bool:
    """claude -p 결과가 끝까지 정상으로 끝났는지 (예산 초과·API 오류·시간 초과는 실패)."""
    return bool(result) and not result.get("is_error") and result.get("subtype") == "success"


def new_queue_files(env: dict, ref: str = "origin/main") -> list[Path]:
    """이 실행이 wordpress/queue 에 새로 만든 파일 (ref 에 없는 이름만 — 이미 올린 파일을 고친 것은 안 받음).
    ref = 세션 시작 때의 작업 폴더 (main + 쌓은 앞 실행 결과) — 앞 실행 파일을 이번 결과로 세지 않게."""
    r = _as_writer(["git", "ls-tree", "--name-only", ref, "wordpress/queue/"], env)
    tracked = {Path(line).name for line in r.stdout.splitlines()}
    return [p for p in sorted((WORKDIR / "wordpress" / "queue").glob("*.json")) if p.name not in tracked]


def other_changes(env: dict) -> list[str]:
    """queue 밖에서 바뀐 저장소 파일 (반영 안 됨 — 기록만, 지시서가 하지 말라고 한 일을 했는지 보려고)."""
    r = _as_writer(["git", "status", "--porcelain", "--untracked-files=all"], env)
    paths = [line[3:] for line in r.stdout.splitlines()]
    return [p for p in paths if not p.startswith("wordpress/queue/")]


def deliver(files: list[Path], dest: Path | None = None) -> list[str]:
    """결과 파일을 서버 대기 폴더로 복사 (JSON 이 아니거나 너무 큰 파일은 뺌). 넣은 파일 이름 목록."""
    dest = dest or LOCAL_QUEUE_DIR
    dest.mkdir(parents=True, exist_ok=True)
    taken = []
    for p in files:
        data = p.read_bytes()
        if len(data) > MAX_RESULT_BYTES:
            continue
        try:
            json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        target = dest / p.name
        if target.exists():
            if target.read_bytes() == data:
                continue
            target = dest / f"{p.stem}-{datetime.now():%Y%m%d%H%M%S}{p.suffix}"
        target.write_bytes(data)
        taken.append(target.name)
    return taken


def prune_local(days: int = KEEP_LOCAL_DAYS) -> None:
    """오래된 결과 파일 정리 (이미 올린 것만 — 완료 표시가 있으니 다시 올라가지 않음)."""
    from .publish_queue import _marker
    cutoff = time.time() - days * 86400
    for p in LOCAL_QUEUE_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff and _marker(p).exists():
                p.unlink()
        except OSError:
            pass


def done_file_missing(files: list[Path]) -> bool:
    for p in files:
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("type") == "run_done":
                return False
        except (OSError, ValueError):
            continue
    return True


def _done_data(text: str, result: str) -> tuple[str, str]:
    slot = text[5:] if text.startswith("slot-") and text[5:].isdigit() else ""
    data = {"type": "run_done", "summary": f"(API 봇: 끝 표시 없이 끝남) {result[:150]}"}
    if slot:
        data["slot"] = int(slot)
    return f"done-{datetime.now():%Y%m%d-%H%M}{f'-s{slot}' if slot else ''}-api.json", json.dumps(data, ensure_ascii=False)


def write_done_file(text: str, result: str) -> str:
    """세션이 끝 표시(run_done)를 안 남기고 '성공'으로 끝났을 때 대신 만든다 — 연속 작성이 90분 기다리지 않게.
    실패한 실행에는 만들지 않는다 (실패가 이어지면 연속 작성이 스스로 멈춰야 함)."""
    name, body = _done_data(text, result)
    LOCAL_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (LOCAL_QUEUE_DIR / name).write_text(body, encoding="utf-8")
    return name


_WRITE_NEW = "import sys; open(sys.argv[1], 'x', encoding='utf-8').write(sys.argv[2])"


def write_done_in_workdir(text: str, result: str, env: dict) -> Path | None:
    """push 방식: 대신 만든 끝 표시도 커밋에 넣어 새 글과 같이 배포로 들어가게 (먼저 들어가면 연속 작성이 글 반영 전에 다음 글을 시작).
    작업 폴더는 글쓰기 사용자 것이라 그 사용자로 씀 (root 가 그 폴더에 쓰면 링크 파일로 엉뚱한 곳을 덮어쓸 수 있음)."""
    name, body = _done_data(text, result)
    path = WORKDIR / "wordpress" / "queue" / name
    r = _as_writer(["python3", "-I", "-c", _WRITE_NEW, str(path), body], env)
    return path if r.returncode == 0 else None


def is_review_done(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("type") == "review_done"
    except (OSError, ValueError):
        return False


# ---------- 저장소 반영 (서버는 묶음까지, main 에 올리는 것은 GitHub Actions — automation/api_sync.py) ----------

class PushFailed(ApiWriterError):
    def __init__(self, msg: str, failed: list[str] | None = None):
        super().__init__(msg)
        self.failed = failed or []


def push_allowed(path: str) -> bool:
    return path not in PUSH_DENIED and any(fnmatch.fnmatchcase(path, pat) for pat in PUSH_ALLOWED)


def _git(args: list[str], env: dict, timeout: int = 300, binary: bool = False) -> subprocess.CompletedProcess:
    return _as_writer(["git", *args], env, timeout=timeout, binary=binary)


def _must(r: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if r.returncode != 0:
        err = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", "replace")
        raise PushFailed(f"{what} 실패: {err.strip()[-150:]}")
    return r


def head_sha(env: dict) -> str:
    return _must(_git(["rev-parse", "HEAD"], env), "작업 폴더 버전 읽기").stdout.strip()


def commit_work(env: dict, base: str, message: str) -> tuple[list[str], list[str]]:
    """세션이 바꾼 것(세션이 스스로 한 커밋 포함)을 base 위 커밋 1개로 — 바꿀 수 있는 파일만. (올릴 파일, 뺀 파일)"""
    _must(_git(["reset", "-q", "--soft", base], env), "커밋 정리")
    _must(_git(["add", "-A"], env), "바뀐 파일 담기")
    out = _must(_git(["diff", "--cached", "--name-only", "-z", base], env), "바뀐 파일 목록").stdout
    changed = [p for p in out.split("\0") if p]
    blocked = [p for p in changed if not push_allowed(p)]
    if blocked:
        _must(_git(["reset", "-q", base, "--", *blocked], env), "올리지 않을 파일 빼기")
    keep = [p for p in changed if p not in blocked]
    if keep:
        _must(_git(["commit", "-q", "--no-verify", "-m", message], env), "커밋")
    _must(_git(["reset", "-q", "--hard", "HEAD"], env), "남은 수정 지우기")   # 뺀 파일은 버림 (다음 실행은 새로 받은 main 에서)
    _git(["clean", "-fdq"], env)
    return keep, blocked


def _failed_tests(output: str) -> list[str]:
    """unittest 출력의 실패 줄 → '클래스.테스트' (파이썬 3.10 '(모듈.클래스)', 3.11+ '(모듈.클래스.테스트)' 둘 다)."""
    out = []
    for name, where in re.findall(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", output, flags=re.M):
        if where.endswith("." + name):
            where = where[: -len(name) - 1]
        out.append(f"{where.rsplit('.', 1)[-1]}.{name}")
    return out


def check_work(run_cmd) -> list[str]:
    """automation/check_and_push.sh 와 같은 검사 + 배포 검사(deploy.yml check)의 문법·JSON 검사 (올리기 직전, 최신 main 위에서).
    실패한 검사 이름 (통과면 빈 목록). run_cmd(명령, 시간 제한) — 서버는 글쓰기 사용자로(root 가 아니라 권한 문제도 GitHub 검사와 같게),
    GitHub 쪽(automation/api_sync.py)은 작업 사용자로. 둘이 같은 함수를 써서 검사가 어긋나지 않는다."""
    r = run_cmd(["python3", "automation/common_errors.py"], 300)
    if r.returncode != 0:
        return [f"자주 틀린 유형표 만들기(automation/common_errors.py): {(r.stderr or r.stdout).strip()[-120:]}"]
    if run_cmd(["git", "diff", "--quiet", "automation/common-errors.md"], 60).returncode == 1:   # 1 = 바뀜 (그 밖은 파일 없음 등)
        _must(run_cmd(["git", "commit", "-q", "--no-verify", "-m", "자주 틀린 유형표 다시 만듦 (병합 뒤)",
                       "automation/common-errors.md"], 60), "유형표 커밋")
    out = run_cmd(["git", "diff", "--name-status", "origin/main", "HEAD"], 60).stdout
    edited = [ln.split("\t", 1)[1] for ln in out.splitlines()
              if ln.startswith("M\t") and re.fullmatch(r"wordpress/queue/refresh-.*\.json", ln.split("\t", 1)[1])]
    if edited:   # check_and_push.sh 와 같음 (11/11 #1143 — 이미 올린 갱신 파일을 고치면 서버가 거절하고 틀린 첫 판이 남음)
        return [f"이미 올린 갱신 파일을 고침: {', '.join(edited[:3])}"]
    r = run_cmd(["python3", "-B", "-m", "compileall", "-q", "bot", "tests"], 300)
    if r.returncode != 0:
        return [f"파이썬 문법(compileall): {((r.stdout or '') + (r.stderr or '')).strip()[-150:]}"]
    r = run_cmd(["python3", "-B", "-m", "unittest", "discover", "-s", "tests"], 900)
    if r.returncode != 0:
        output = (r.stderr or "") + (r.stdout or "")
        return _failed_tests(output) or [f"테스트: {output.strip()[-150:]}"]
    r = run_cmd(["node", "tests/test_calculators.mjs"], 300)
    if r.returncode != 0:
        return [f"계산기 검사(tests/test_calculators.mjs): {((r.stdout or '') + (r.stderr or '')).strip()[-150:]}"]
    r = run_cmd(["python3", "-c", "import json; json.load(open('wordpress/patches.json', encoding='utf-8'))"], 60)
    if r.returncode != 0:
        return [f"패치 목록 JSON(wordpress/patches.json): {(r.stderr or '').strip()[-120:]}"]
    return []


def _writer_runner(env: dict):
    return lambda cmd, timeout=300: _as_writer(cmd, env, timeout=timeout)


def build_bundle(env: dict, base: str, message: str) -> tuple[bytes, str, str, list[str], list[str]]:
    """세션 결과를 main 에 올릴 묶음으로. (묶음, 묶음 끝 커밋, 그때의 main, 올릴 파일, 뺀 파일) — 올릴 것이 없으면 묶음 b"".
    base = 세션 시작 때 작업 폴더 (main + 쌓은 앞 실행 결과). 묶음은 main 에 없는 커밋 전부 (앞 묶음을 포함 — 가장 새 묶음 하나면 됨).
    충돌·검사 실패면 PushFailed (묶음 안 만듦, 대기 중인 앞 묶음은 그대로)."""
    keep, blocked = commit_work(env, base, message)
    if not keep:
        return b"", "", "", keep, blocked
    _merge_drivers(env)
    _must(_git(["fetch", "-q", "origin", "main"], env, timeout=600), "최신 main 받기")
    if _git(["rebase", "-q", "origin/main"], env, timeout=600).returncode != 0:
        _git(["rebase", "--abort"], env)
        raise PushFailed("최신 main 과 합치다 충돌")
    failed = check_work(_writer_runner(env))
    if failed:
        raise PushFailed("검사 실패", failed)
    head = head_sha(env)
    origin = _must(_git(["rev-parse", "origin/main"], env), "main 버전 읽기").stdout.strip()
    if head == origin:
        return b"", "", "", keep, blocked
    bundle = _must(_git(["bundle", "create", "-", "HEAD", "^origin/main"], env, binary=True), "작업 묶음 만들기").stdout
    return bundle, head, origin, keep, blocked


def rename_collisions(files: list[Path], env: dict) -> list[Path]:
    """서버 대기 폴더에 같은 이름·다른 내용의 파일이 있으면 작업 폴더 파일 이름을 바꿈 → 대기 폴더와 저장소에 같은 이름·같은 내용
    (publish_queue 완료 표시가 '이름+내용'이라, 이름이 다르면 배포 때 같은 글이 한 번 더 올라감)."""
    out = []
    for p in files:
        target = LOCAL_QUEUE_DIR / p.name
        try:
            clash = target.exists() and target.read_bytes() != p.read_bytes()
        except OSError:
            clash = False
        if clash:
            new = p.with_name(f"{p.stem}-{datetime.now():%Y%m%d%H%M%S}{p.suffix}")
            if _as_writer(["mv", "-n", str(p), str(new)], env).returncode == 0 and new.exists():
                p = new
        out.append(p)
    return out


# ---------- 저장소 반영 대기 묶음 (OUTBOX — 서버 root 전용, GitHub Actions 가 배포 SSH 로 가져감) ----------
# pending.bundle/json : main 에 안 들어간 커밋 전부 (실행이 끝날 때마다 새 것으로 바꿈 — 앞 묶음을 쌓아 만들었으니 다 포함)
# claimed.bundle/json : GitHub 작업이 가져간 것 (결과가 오면 지움·failed 로)
# failed/             : 반영 못 한 묶음 (KEEP_FAILED_DAYS 뒤 지움)
# health.json         : 연속 실패 수·저장소 일 쉬는 기한
# maint-report.json   : 아직 안 보낸 매일 점검 보고

@contextmanager
def _outbox_lock():
    OUTBOX.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTBOX, 0o700)
    with open(OUTBOX_LOCK, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _now() -> datetime:
    return datetime.now()


def _stamp(when: datetime | None = None) -> str:
    return f"{when or _now():%Y-%m-%d %H:%M}"


def _hours_since(stamp: str) -> float:
    try:
        return (_now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except (TypeError, ValueError):
        return 0.0


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _slot(name: str) -> tuple[Path, Path]:
    return OUTBOX / f"{name}.bundle", OUTBOX / f"{name}.json"


def _newest_bundle() -> tuple[Path | None, dict]:
    """(잠금 안에서) 쌓을 묶음: 대기(pending)가 있으면 그것(가져간 것까지 다 포함), 없으면 가져간 것(claimed)."""
    for name in ("pending", "claimed"):
        bundle, meta = _slot(name)
        if bundle.exists():
            return bundle, _read(meta)
    return None, {}


def save_pending(bundle: bytes, head: str, origin: str, stacked: dict, run: dict) -> None:
    """새 대기 묶음 저장 (앞 대기 묶음은 이 묶음에 들어 있으니 바꿔 씀). since = 아직 안 올라간 가장 오래된 결과의 시각."""
    with _outbox_lock():
        path, meta_path = _slot("pending")
        _write(path, bundle)
        _write(meta_path, {"head": head, "origin": origin, "created": _stamp(), "since": stacked.get("since") or _stamp(),
                           "runs": ((stacked.get("runs") or []) + [run])[-20:]})


def _health() -> dict:
    return _read(OUTBOX / "health.json")


def repo_mode() -> tuple[str, str]:
    """('push', '') = 저장소 일까지 다 함 / ('1', 이유) = 반영이 연속 실패해 쉬는 중 (기한이 지나면 다시 push)."""
    h = _health()
    until = str(h.get("rest_until") or "")
    if until and _hours_since(until) < 0:
        return "1", f"GitHub 반영이 연속 {h.get('fails', 0)}번 실패 (마지막: {str(h.get('last_why', ''))[:120]}) — {until} 에 다시 시도"
    return "push", ""


def _fail_locked(name: str, meta: dict, why: str) -> None:
    """(잠금 안에서) 묶음을 failed 로 옮기고 봇 기록에 ❌. 새 글은 이미 같은 관문으로 올라감 — 빠지는 것은 저장소 수정뿐."""
    bundle, meta_path = _slot(name)
    failed = OUTBOX / "failed"
    failed.mkdir(exist_ok=True)
    tag = f"{_now():%Y%m%d-%H%M%S}-{str(meta.get('head', ''))[:7] or 'x'}"
    if bundle.exists():
        os.replace(bundle, failed / f"{tag}.bundle")
    _write(failed / f"{tag}.json", {**meta, "why": why, "failed_at": _stamp()})
    meta_path.unlink(missing_ok=True)
    h = _health()
    fails = int(h.get("fails") or 0) + 1
    h.update({"fails": fails, "last_why": why, "last_fail": _stamp()})
    rest = ""
    if fails >= UNHEALTHY_FAILS:
        h["rest_until"] = _stamp(_now() + timedelta(hours=UNHEALTHY_HOURS))
        rest = f" 연속 {fails}번 실패 → {h['rest_until']} 까지 저장소 일은 건너뜀 (그 뒤 다시 시도)."
    _write(OUTBOX / "health.json", h)
    runs = ", ".join(str(r.get("label", "")) for r in (meta.get("runs") or [])[-5:])
    _log(False, f"API 봇 저장소 반영 실패: {why[:300]} (실행: {runs or '-'}). 새 글·끝 표시는 이미 같은 관문으로 올라감 — "
                f"검수 결과·계산기 같은 저장소 수정은 다음 실행이 새 main 에서 다시.{rest}")


def _housekeeping_locked() -> None:
    """(잠금 안에서) 영원히 남는 것 없게: GitHub 가 오래 안 가져간 묶음은 실패로, 오래된 실패 묶음은 지움."""
    for name, key in (("pending", "since"), ("claimed", "claimed_at")):
        bundle, meta_path = _slot(name)
        meta = _read(meta_path)
        if bundle.exists() and _hours_since(str(meta.get(key) or meta.get("created") or "")) >= STALE_HOURS:
            _fail_locked(name, meta, f"GitHub Actions 가 {STALE_HOURS}시간 넘게 반영하지 않음 (Deploy server config 예약 실행 확인)")
    cutoff = time.time() - KEEP_FAILED_DAYS * 86400
    for p in (OUTBOX / "failed").glob("*"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def housekeeping() -> None:
    """1분 봇이 10분마다 부름 (방식과 상관없이 — 월정액으로 바꿔도 남은 묶음이 정리되게)."""
    if not OUTBOX.exists():
        return
    with _outbox_lock():
        _housekeeping_locked()


def outbox_claim() -> dict:
    """GitHub 작업이 가져갈 묶음 (없으면 {}). 같은 작업이 동시에 둘 돌지 않음 (deploy.yml concurrency) →
    남아 있는 claimed 는 끝난 작업 것: 새 대기 묶음이 있으면 그것이 claimed 를 포함하니 바꿔 가져감, 없으면 다시 시도."""
    with _outbox_lock():
        _housekeeping_locked()
        pend, pend_meta = _slot("pending")
        bundle, meta_path = _slot("claimed")
        if pend.exists():
            meta = {**_read(pend_meta), "claimed_at": _stamp(), "tries": 1}
            os.replace(pend, bundle)
            pend_meta.unlink(missing_ok=True)
            _write(meta_path, meta)
        elif bundle.exists():
            meta = _read(meta_path)
            meta["tries"] = int(meta.get("tries") or 0) + 1
            if meta["tries"] > CLAIM_TRIES:
                _fail_locked("claimed", meta, f"GitHub 반영이 {CLAIM_TRIES}번 결과 없이 끝남 ({str(meta.get('last_retry', ''))[:150]})")
                return {}
            _write(meta_path, meta)
        else:
            return {}
        return {"path": str(bundle), "head": meta.get("head", ""), "origin": meta.get("origin", ""), "tries": meta["tries"],
                "runs": [str(r.get("label", "")) for r in (meta.get("runs") or [])][-5:]}


def outbox_result(head: str, status: str, sha: str = "", why: str = "") -> str:
    """GitHub 작업의 결과. status: pushed(올림)·already(이미 main 에 있음)·failed(검사 실패·충돌·바꾸면 안 되는 파일·push 거절)·retry."""
    with _outbox_lock():
        bundle, meta_path = _slot("claimed")
        meta = _read(meta_path)
        if not bundle.exists() or not head or meta.get("head") != head:
            return "stale"   # 이미 처리했거나 다른 묶음 (늦게 온 결과)
        runs = ", ".join(str(r.get("label", "")) for r in (meta.get("runs") or [])[-5:])
        if status in ("pushed", "already"):
            bundle.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            _write(OUTBOX / "health.json", {"fails": 0, "last_ok": _stamp()})
            where = f"main 에 올림 {sha[:7]}" if status == "pushed" and sha else "이미 main 에 있음"
            _log(True, f"API 봇 저장소 반영: {where} (실행: {runs or '-'}) — 같은 GitHub 작업이 배포")
            return "done"
        if status == "failed":
            _fail_locked("claimed", meta, why or "GitHub 쪽 검사 실패")
            return "failed"
        meta["last_retry"] = why
        _write(meta_path, meta)
        return "retry"


def maint_problems(report: str) -> int:
    """매일 점검 보고 첫 줄 → 문제 수 ('이상 없음' 0, '문제 N건' N, 형식이 다르면 -1 — 알림은 감)."""
    first = (report.strip().splitlines() or [""])[0]
    m = re.search(r"문제\s*(\d+)\s*건", first)
    if m:
        return int(m.group(1))
    return 0 if "이상 없음" in first else -1


def save_maint_report(ok: bool, report: str) -> int:
    """API 봇 매일 점검 보고를 알림 통로로 (Health check 작업이 15분마다 가져감). 문제 수 (점검 실행 실패는 -2)."""
    problems = maint_problems(report) if ok else -2
    with _outbox_lock():
        _write(OUTBOX / "maint-report.json", {"at": _stamp(), "problems": problems})
    return problems


def take_maint_report() -> dict:
    """안 보낸 매일 점검 보고 (한 번만 — 가져가면 보낸 것으로). 없으면 {}."""
    if not OUTBOX.exists():
        return {}
    with _outbox_lock():
        path = OUTBOX / "maint-report.json"
        data = _read(path)
        if data:
            os.replace(path, OUTBOX / "maint-report.sent.json")
        return data


@contextmanager
def _lock():
    handle = open(LOCK_FILE, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


def _stale_note() -> str:
    """지난 실행이 끝 기록 없이 멈췄으면 그 설명 (서버 재시작·메모리 부족으로 강제 종료 등)."""
    try:
        old = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return f"API 글쓰기: 지난 실행({old.get('started', '?')}, {old.get('label') or '-'})이 끝 기록 없이 멈춤 — 이번 실행이 이어서 함"


def _log(ok: bool, msg: str, cost: float = 0.0) -> None:
    from .state import add_log, add_spend, load_state, save_state
    state = load_state()
    add_log(state, ok, msg)
    if cost:
        add_spend(state, cost)  # 하루 비용 상한(run.py·설정 페이지)이 이 값을 봄
    save_state(state)


def note_repo_skipped(why: str) -> None:
    """저장소를 고치는 일을 건너뛰는 중임을 봇 기록(/maintenance)에 하루 한 번 — 밀린 일 수와 함께."""
    from .state import load_state
    today = f"{datetime.now():%Y-%m-%d}"
    try:
        if SKIP_NOTE_FILE.read_text(encoding="utf-8").strip() == today:
            return
    except OSError:
        pass
    state = load_state()
    review = 1 if state.get("review_target") else 0
    yearly = len(state.get("calc_yearly") or [])
    alert = 1 if state.get("deploy_alert") else 0
    _log(False, "API 봇: 저장소를 고치는 일(대기 글 검수 반영·새 계산기·연초 기준값·배포 막힘 고치기)은 잠시 건너뜀 — "
                f"{why}. 지금 밀린 일: 대기 글 검수 {review}건, 연초 기준값 {yearly}건, 배포 막힘 {alert}건")
    try:
        SKIP_NOTE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_NOTE_FILE.write_text(today, encoding="utf-8")
    except OSError:
        pass


def finish_repo(text: str, summary: str, files: list[Path], env: dict, base: str, stacked: dict,
                label: str) -> tuple[str, list[str], str]:
    """저장소 일까지 한 실행의 마무리. (끝 기록에 붙일 말, 서버 대기 폴더로 넣은 파일, 실패 설명 — 문제 없으면 '')
    검사 통과: 새 queue 파일은 바로 대기 폴더 → 같은 관문 (월정액처럼 결과가 곧 들어와 연속 작성이 기다리지 않음),
      main 에 안 들어간 커밋은 묶음으로 OUTBOX (GitHub Actions 가 같은 검사 뒤 main 에). 배포는 같은 파일(이름+내용)을 건너뜀.
    검사 실패: 묶음 없음, 새 글·건너뜀·끝 표시는 대기 폴더로 (글이 사라지지 않게), 검수 끝 파일(review_done)은 버림
      (고친 내용이 저장소에 못 들어가 다음 실행이 다시 검수)."""
    if text != MAINT_TEXT and done_file_missing(files):
        done = write_done_in_workdir(text, summary, env)
        if done:
            files = [*files, done]
    files = rename_collisions(files, env)
    first = (summary.strip().splitlines() or ["글쓰기 실행"])[0]
    try:
        bundle, head, origin, keep, blocked = build_bundle(env, base, f"API 봇: {first[:70]}")
    except Exception as e:  # noqa: BLE001 - 검사 실패·충돌·시간 초과 모두: 묶음 없이 글은 살림
        failed = getattr(e, "failed", [])
        safe = [f for f in files if not is_review_done(f)]
        dropped = len(files) - len(safe)
        taken = deliver(safe)
        why = str(e)[:200] + (f": {', '.join(failed[:5])}" if failed else "")
        fail = (f"저장소 수정은 main 에 안 올림 — {why}. 새 글·건너뜀·끝 표시 파일 {len(taken)}개는 서버 대기 폴더로 (같은 관문)"
                + (f", 검수 끝 파일 {dropped}개는 버림 (고친 내용이 저장소에 못 들어가 다음 실행이 다시 검수)" if dropped else "")
                + ". 다음 실행이 새 main 에서 다시")
        return ", 저장소 반영 실패", taken, fail
    note = ""
    if bundle:
        save_pending(bundle, head, origin, stacked, {"label": label, "at": _stamp(), "summary": first[:100], "files": keep[:30]})
        note = f", 저장소 수정 {len(keep)}개는 GitHub 반영 대기 (15분마다, 같은 검사 통과 때만 main 에)"
    if blocked:
        note += f", 바꾸면 안 되는 파일 {len(blocked)}개는 뺌({', '.join(blocked[:3])})"
    return note, deliver(files), ""


def run_once(text: str, label: str) -> int:
    from .config import load_secrets
    from .settings import load_settings
    from .state import load_state, spent_today

    with _lock() as got:
        if not got:
            print("[api_writer] 이미 실행 중 → 끝냄", flush=True)
            return 1
        stale = _stale_note()
        if stale:
            _log(False, stale)
        settings = load_settings()
        spent = spent_today(load_state())
        problem = start_problem(settings, spent)
        if problem:
            _log(False, f"{label or 'API 글쓰기'}: 시작 안 함 — {problem}")
            return 1
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps({"started": f"{datetime.now():%Y-%m-%d %H:%M}", "label": label, "text": text},
                                          ensure_ascii=False), encoding="utf-8")
        key, usd = settings.api_key, budget(settings, spent)
        token = load_secrets().get("FIRSTLAB_API_TOKEN", "")
        local, why = repo_mode()
        git_env = writer_env(local=local)   # git 명령에는 키를 넘기지 않음
        started, charged = time.time(), 0.0

        def _stop(signum, frame):  # systemd 바깥 제한(RuntimeMaxSec)에 걸려도 기록은 남기게
            raise SystemExit(f"중지 신호 {signum}")
        signal.signal(signal.SIGTERM, _stop)
        try:
            prepare_workdir(git_env)
            stacked = stack_pending(git_env) if local == "push" else {}
            if stacked is None:   # 앞 실행 결과가 최신 main 과 충돌 — 그 묶음은 GitHub 쪽이 처리(반영·실패)할 때까지 저장소 일은 쉼
                local, why = "1", "아직 main 에 안 들어간 앞 실행 결과가 최신 main 과 충돌 (GitHub 반영이 처리하면 다시)"
                git_env, stacked = writer_env(local=local), {}
            if local != "push":
                note_repo_skipped(why)
            base = head_sha(git_env)
            charged = usd   # 여기부터는 돈이 나감 — 결과를 못 읽으면(시간 초과·강제 종료) 예산만큼 쓴 것으로 셈 (하루 상한을 넘지 않게)
            result, error = run_session(claude_command(settings.model or DEFAULT_MODEL, usd, text, local),
                                        writer_env(key, token, local), RUN_TIMEOUT_MIN * 60)
            ok = session_ok(result)
            cost = charged = float(result.get("total_cost_usd") or 0.0) if result else usd
            summary = redact(str((result or {}).get("result") or error), key, token)
            files, taken, others, note, repo_fail = new_queue_files(git_env, base), [], [], "", ""
            if ok and local == "push":   # 월정액처럼: 저장소 일까지 한 결과를 검사 → 글은 바로 관문, 저장소 수정은 GitHub 가 main 에
                note, taken, repo_fail = finish_repo(text, summary, files, git_env, base, stacked, label)
            elif ok:  # 월정액처럼 끝까지 마친 실행의 결과만 (월정액도 마지막에 한 번 push — 중간에 끊기면 아무것도 안 올라감)
                taken = deliver(files)
                if text != MAINT_TEXT and done_file_missing(files):  # 점검은 글쓰기 실행이 아님 (월정액도 끝 표시 없음)
                    taken.append(write_done_file(text, summary))
            if local != "push":
                others = other_changes(git_env)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 어떤 실패든 기록하고 잠금을 푼다
            _log(False, f"{label or 'API 글쓰기'} 실패: {redact(str(e), key, token)[:200]}", charged)
            if text == MAINT_TEXT:
                save_maint_report(False, "")
            STATUS_FILE.unlink(missing_ok=True)
            return 1
        minutes = (time.time() - started) / 60
        if taken:
            try:  # 월정액과 같은 관문으로 올림 (배포 때와 같은 명령)
                from . import publish_queue
                publish_queue.main()
            except Exception as e:  # noqa: BLE001 - 다음 배포·다음 실행 때 다시 시도됨 (완료 표시 없음)
                _log(False, f"API 글쓰기 결과 올리기 실패: {str(e)[:150]}")
        if others:
            note += f", 반영 안 된 저장소 수정 {len(others)}곳({', '.join(others[:3])})"
        if files and not ok:
            note += f", 끝까지 못 마쳐 결과 파일 {len(files)}개 버림"
        if repo_fail:   # 봇 기록에 따로 ❌ (매일 점검이 ❌ 만 봄)
            _log(False, f"{label or 'API 글쓰기'} 저장소 반영 실패(API 봇): {repo_fail}")
        if text == MAINT_TEXT:   # 월정액은 세션 마지막 메시지가 휴대폰 알림 → 보고 전문은 봇 기록에, 문제 수는 알림 통로(Health check)로
            problems = save_maint_report(ok, summary)
            _log(problems == 0, f"매일 점검 보고(API 봇): {summary[:1500] if ok else '점검 실행 실패 — ' + summary[:300]}")
        _log(ok, f"{label or 'API 글쓰기'} 끝(API 봇): {'성공' if ok else '실패'} — {summary[:300]} "
                 f"(비용 ${cost:.2f}≈{cost * 1400:,.0f}원, {minutes:.0f}분, 결과 파일 {len(taken)}개{note})", cost)
        STATUS_FILE.unlink(missing_ok=True)
        prune_local()
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="API 봇 글쓰기 한 번 (bot/run.py 가 systemd-run 으로 시작) / "
                                                 "저장소 반영 묶음 (GitHub Actions 가 SSH 로, automation/api_sync.py)")
    parser.add_argument("command", choices=["run", "outbox"])
    parser.add_argument("action", nargs="?", choices=["claim", "result", "report", "housekeeping"])
    parser.add_argument("--text", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--status", default="", choices=["", "pushed", "already", "failed", "retry"])
    parser.add_argument("--sha", default="")
    parser.add_argument("--why", default="")
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_once(args.text, args.label)
    if args.action == "claim":
        out = outbox_claim()
    elif args.action == "result":
        out = {"result": outbox_result(args.head, args.status, args.sha, args.why)}
    elif args.action == "report":
        out = take_maint_report()
    elif args.action == "housekeeping":
        housekeeping()
        out = {}
    else:
        parser.error("outbox 다음에 claim·result·report·housekeeping")
    print(json.dumps(out, ensure_ascii=False), flush=True)   # 마지막 줄 = 결과 (automation/api_sync.py 가 읽음)
    return 0


if __name__ == "__main__":
    sys.exit(main())
