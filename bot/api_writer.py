"""API 봇 방식: 월정액 예약 작업(bot/routine.py)과 똑같은 글쓰기 작업을 서버 안에서 Claude Code(헤드리스)로 돌린다.

운영자 11/12 '언제든 api로 바꾸면 똑같이 돌아가게': 설정 '글 작성 방식: API 봇' + 'API 봇 사용' 이면
  같은 지시서(automation/daily-writer.md, 점검은 maintenance.md) · 같은 실행 메시지(글 작성·manual-request·maintenance-check·slot-N)
  · 같은 관문(bot/publish_queue) · 같은 발행 방식(초안·바로·예약)으로 돈다. 다른 점은 요금이 설정 페이지에 넣은 Claude API 키로 나가는 것뿐.

흐름 (1분 봇은 시작만 하고 바로 돌아감):
  run.fire_routine → fire() → systemd-run 으로 따로 실행 (python -m bot.api_writer run --text ...)
  → run_once(): 잠금 → 작업용 저장소(WORKDIR)를 최신 main 으로 → claude -p (전용 사용자, 키는 환경 변수로만)
  → (토큰 없음) 그 실행이 wordpress/queue 에 새로 만든 파일만 LOCAL_QUEUE_DIR 로 → bot.publish_queue (월정액과 같은 관문)
    (토큰 있음) 아래 '저장소 반영' → 봇 기록·비용 기록
저장소 반영 (운영자 11/12 '대기 글 검수 결과 반영·새 계산기·연초 기준값도 api 모드에서'):
  - 서버 비밀값 GITHUB_PUSH_TOKEN 이 있으면 FIRSTLAB_LOCAL=push — 세션은 월정액처럼 저장소를 고치는 일까지 다 하되 커밋·push 는 안 함.
    끝나면 이 실행(root)이: 작업 폴더의 바뀐 것을 커밋 1개로 → 최신 main 위에 다시 쌓기 → check_and_push.sh 와 같은 검사
    (자주 틀린 유형표 다시 만들기·테스트·계산기·이미 올린 갱신 파일 안 고침) → 통과하면 토큰으로 main 에 push (push_changes).
    토큰은 세션 환경·명령줄·작업 폴더에 없다: 검사까지는 글쓰기 사용자로, push 는 root 전용 bare 저장소(PUSH_DIR)에서
    그 명령 하나에만 GIT_ASKPASS 로 (git 설정 파일에 안 남음). 올린 queue 파일은 배포가 평소처럼 올림 (대기 폴더로는 안 넣음 — 두 번 안 올라가게).
    검사·push 가 실패하면 아무것도 안 올리고 봇 기록에 실패한 검사 이름 → 새 글·건너뜀 파일은 오늘처럼 대기 폴더로 (글이 사라지지 않게),
    검수 끝 파일(review_done)은 고친 내용이 저장소에 못 들어갔으니 버림 → 다음 실행이 같은 글을 다시 검수.
  - 토큰이 없으면 FIRSTLAB_LOCAL=1 — 지시서 'API 봇 실행' 항목대로 저장소를 고치는 일은 건너뛰고, 봇 기록에 하루 한 번 알림.
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
from datetime import datetime
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
REPO_URL = "https://github.com/yoorimint/first-lab"      # 공개 저장소 (작업 폴더는 읽기만)
PUSH_DIR = Path("/var/lib/first-lab/bot/push-repo")      # root 전용 bare 저장소 — 토큰을 쓰는 push 는 여기서만 (글쓰기 사용자가 못 읽음)
PUSH_URL = REPO_URL + ".git"
SKIP_NOTE_FILE = Path("/var/lib/first-lab/bot/api-writer-skip-note.txt")  # 토큰 없음 알림을 하루 한 번만
PUSH_TRIES = 3           # main 이 그새 바뀌어 push 가 거절되면 다시 쌓아 다시 (check_and_push.sh 처럼)
# 글쓰기 작업이 바꿀 수 있는 파일 (지시서 5단계 커밋 목록 + 검수 기록 + 검사 코드). 그 밖(server/·.github/·지시서·이 파일)은 빼고 올린다.
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

def claude_command(model: str, usd: float, text: str, local: str = "1") -> list[str]:
    """Claude Code 헤드리스 명령. 키는 여기 넣지 않는다 (명령줄은 ps 로 보임) — 환경 변수로만. local: '1' 또는 'push'."""
    return [CLAUDE_BIN, "-p", PROMPT.format(text=text or "(없음)", local=local), "--output-format", "json",
            "--model", model, "--max-budget-usd", f"{usd:.2f}",
            "--permission-mode", "dontAsk", "--tools", TOOLS, "--allowedTools", TOOLS, "--strict-mcp-config"]


def writer_env(api_key: str = "", site_token: str = "", local: str = "1") -> dict[str, str]:
    """글쓰기 사용자의 환경 (그 밖의 서버 환경 변수는 넘기지 않음). 키가 빈 값이면 넣지 않는다 (git 명령용).
    GitHub 올리기 토큰은 어떤 경우에도 넣지 않는다 (push 는 root 쪽 push_changes 가 함)."""
    env = {
        "HOME": str(BASE), "USER": WRITER_USER, "LOGNAME": WRITER_USER, "LANG": "C.UTF-8", "TZ": "Asia/Seoul",
        "PATH": f"{VENV_BIN}:{NODE_BIN}:/usr/local/bin:/usr/bin:/bin",
        "ANTHROPIC_API_KEY": api_key,              # 요금은 이 키로 (설정 페이지 값, 없으면 secrets.env)
        "FIRSTLAB_API_TOKEN": site_token,          # 지시서의 /next·/law 통로 (월정액 세션과 같은 값)
        "FIRSTLAB_LOCAL": local,                   # 지시서 'API 봇 실행' 항목 (1 = 저장소 일 건너뜀, push = 다 하고 커밋·push 는 서버가)
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


def new_queue_files(env: dict) -> list[Path]:
    """이 실행이 wordpress/queue 에 새로 만든 파일 (main 에 없는 이름만 — 이미 올린 파일을 고친 것은 안 받음)."""
    r = _as_writer(["git", "ls-tree", "--name-only", "origin/main", "wordpress/queue/"], env)
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


# ---------- 저장소 반영 (GITHUB_PUSH_TOKEN 이 있을 때) ----------

class PushFailed(ApiWriterError):
    def __init__(self, msg: str, failed: list[str] | None = None, retry: bool = False):
        super().__init__(msg)
        self.failed, self.retry = failed or [], retry


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


def check_work(env: dict) -> list[str]:
    """automation/check_and_push.sh 와 같은 검사 (올리기 직전, 최신 main 위에서). 실패한 검사 이름 (통과면 빈 목록).
    글쓰기 사용자로 돌린다 (root 가 아니라서 권한 문제도 GitHub 검사와 같게 보임)."""
    r = _as_writer(["python3", "automation/common_errors.py"], env, timeout=300)
    if r.returncode != 0:
        return [f"자주 틀린 유형표 만들기(automation/common_errors.py): {(r.stderr or r.stdout).strip()[-120:]}"]
    if _git(["diff", "--quiet", "automation/common-errors.md"], env).returncode != 0:
        _must(_git(["commit", "-q", "--no-verify", "-m", "자주 틀린 유형표 다시 만듦 (병합 뒤)", "automation/common-errors.md"], env),
              "유형표 커밋")
    out = _git(["diff", "--name-status", "origin/main", "HEAD"], env).stdout
    edited = [ln.split("\t", 1)[1] for ln in out.splitlines()
              if ln.startswith("M\t") and re.fullmatch(r"wordpress/queue/refresh-.*\.json", ln.split("\t", 1)[1])]
    if edited:   # check_and_push.sh 와 같음 (11/11 #1143 — 이미 올린 갱신 파일을 고치면 서버가 거절하고 틀린 첫 판이 남음)
        return [f"이미 올린 갱신 파일을 고침: {', '.join(edited[:3])}"]
    r = _as_writer(["python3", "-B", "-m", "unittest", "discover", "-s", "tests"], env, timeout=900)
    if r.returncode != 0:
        output = (r.stderr or "") + (r.stdout or "")
        return _failed_tests(output) or [f"테스트: {output.strip()[-150:]}"]
    r = _as_writer(["node", "tests/test_calculators.mjs"], env, timeout=300)
    if r.returncode != 0:
        return [f"계산기 검사(tests/test_calculators.mjs): {((r.stdout or '') + (r.stderr or '')).strip()[-150:]}"]
    return []


def _root_env(extra: dict | None = None) -> dict:
    """root 쪽 git: 설정 파일·자격 증명 도우미를 읽지 않게 (토큰이 어디에도 저장되지 않게)."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(PUSH_DIR), "LANG": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
    env.update({k: v for k, v in os.environ.items() if k.lower() in ("https_proxy", "http_proxy", "no_proxy")})
    env.update(extra or {})
    return env


def _root_git(args: list[str], timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(PUSH_DIR), *args], env=env or _root_env(), capture_output=True, text=True,
                          timeout=timeout)


_ASKPASS = '#!/bin/sh\ncase "$1" in Username*) echo x-access-token ;; *) printf \'%s\\n\' "$FIRSTLAB_PUSH_TOKEN" ;; esac\n'


def root_push(bundle: bytes, token: str) -> str:
    """글쓰기 사용자가 만든 묶음(bundle — 데이터만, 그 사용자의 git 설정·훅은 안 따라옴)을 root 전용 bare 저장소로 받아
    바꿀 수 있는 파일만인지 다시 확인하고 main 에 fast-forward push (강제 push 없음). 올린 커밋 번호. 못 하면 PushFailed."""
    if not (PUSH_DIR / "HEAD").exists():
        PUSH_DIR.mkdir(parents=True, exist_ok=True)
        _must(_root_git(["init", "-q", "--bare"]), "push 저장소 만들기")
    os.chmod(PUSH_DIR, 0o700)
    _must(_root_git(["fetch", "-q", PUSH_URL, "+refs/heads/main:refs/remotes/origin/main"], timeout=600), "main 받기 (push 저장소)")
    with tempfile.TemporaryDirectory(dir=PUSH_DIR) as tmp:
        path = Path(tmp) / "work.bundle"
        path.write_bytes(bundle)
        _must(_root_git(["fetch", "-q", str(path), "+HEAD:refs/heads/incoming"]), "작업 묶음 받기")
    if _root_git(["merge-base", "--is-ancestor", "refs/remotes/origin/main", "refs/heads/incoming"]).returncode != 0:
        raise PushFailed("main 이 그새 바뀜", retry=True)
    out = _must(_root_git(["diff", "--name-only", "-z", "refs/remotes/origin/main", "refs/heads/incoming"]), "올릴 파일 목록").stdout
    bad = [p for p in out.split("\0") if p and not push_allowed(p)]
    if bad:   # 작업 폴더 쪽 확인은 글쓰기 사용자의 git 이 했으니 여기서 한 번 더 (root 쪽 판단만 믿음)
        raise PushFailed(f"바꾸면 안 되는 파일이 들어 있어 안 올림: {', '.join(bad[:3])}")
    sha = _must(_root_git(["rev-parse", "refs/heads/incoming"]), "커밋 번호").stdout.strip()
    with tempfile.TemporaryDirectory(dir=PUSH_DIR) as tmp:
        ask = Path(tmp) / "askpass.sh"
        ask.write_text(_ASKPASS, encoding="utf-8")
        ask.chmod(0o700)
        env = _root_env({"GIT_ASKPASS": str(ask), "FIRSTLAB_PUSH_TOKEN": token})   # 이 명령 하나에만 (설정 파일에 안 남김)
        r = _root_git(["-c", "credential.helper=", "push", "-q", PUSH_URL, "refs/heads/incoming:refs/heads/main"],
                      timeout=300, env=env)
    if r.returncode != 0:
        err = redact(r.stderr or "", token).strip()
        moved = any(w in err for w in ("non-fast-forward", "fetch first", "stale info"))
        raise PushFailed(f"main 에 push 실패: {err[-150:]}", retry=moved)
    return sha


def push_changes(env: dict, base: str, token: str, message: str) -> tuple[str, list[str], list[str]]:
    """작업 폴더의 결과를 main 에 올린다. (올린 커밋 번호, 올린 파일, 뺀 파일) — 바뀐 것이 없으면 커밋 번호 ''.
    검사 실패·충돌·push 실패면 PushFailed (아무것도 안 올라감, 다음 실행이 새 main 에서 다시)."""
    keep, blocked = commit_work(env, base, message)
    if not keep:
        return "", keep, blocked
    _git(["config", "merge.jsonlist.driver", "python3 automation/merge_json_list.py %O %A %B"], env)   # check_and_push.sh 와 같음
    _git(["config", "merge.ours.driver", "true"], env)
    last = None
    for attempt in range(PUSH_TRIES):
        if attempt:
            time.sleep(5 * attempt)
        _must(_git(["fetch", "-q", "origin", "main"], env, timeout=600), "최신 main 받기")
        if _git(["rebase", "-q", "origin/main"], env, timeout=600).returncode != 0:
            _git(["rebase", "--abort"], env)
            raise PushFailed("최신 main 과 합치다 충돌")
        failed = check_work(env)
        if failed:
            raise PushFailed("검사 실패", failed)
        bundle = _must(_git(["bundle", "create", "-", "HEAD", "^origin/main"], env, binary=True), "작업 묶음 만들기").stdout
        try:
            return root_push(bundle, token), keep, blocked
        except PushFailed as e:
            if not e.retry:
                raise
            last = e
    raise last or PushFailed("main 에 push 실패")


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


def note_repo_skipped() -> None:
    """토큰이 없어 저장소를 고치는 일을 건너뛰는 중임을 봇 기록(/maintenance)에 하루 한 번 — 밀린 일 수와 함께."""
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
    _log(False, "API 봇: 저장소를 고치는 일(대기 글 검수 반영·새 계산기·연초 기준값·배포 막힘 고치기)은 건너뛰는 중 — "
                f"서버 비밀값 GITHUB_PUSH_TOKEN 없음. 지금 밀린 일: 대기 글 검수 {review}건, 연초 기준값 {yearly}건, 배포 막힘 {alert}건. "
                "켜려면 서버에서 bash /opt/first-lab/server/set-secrets.sh GITHUB_PUSH_TOKEN")
    try:
        SKIP_NOTE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_NOTE_FILE.write_text(today, encoding="utf-8")
    except OSError:
        pass


def finish_push(text: str, summary: str, files: list[Path], env: dict, base: str, gh_token: str) -> tuple[str, list[str], str]:
    """push 방식의 마무리. (끝 기록에 붙일 말, 서버 대기 폴더로 넣은 파일, 실패 설명 — 올렸으면 '')
    올렸으면 queue 파일은 배포가 평소처럼 올리니 대기 폴더로 넣지 않는다 (같은 글이 두 번 올라가지 않게)."""
    if text != MAINT_TEXT and done_file_missing(files):
        done = write_done_in_workdir(text, summary, env)
        if done:
            files = [*files, done]
    first = (summary.strip().splitlines() or ["글쓰기 실행"])[0]
    try:
        sha, keep, blocked = push_changes(env, base, gh_token, f"API 봇: {first[:70]}")
    except Exception as e:  # noqa: BLE001 - 검사 실패·충돌·push 거절·시간 초과 모두: 아무것도 안 올리고 글은 살림
        failed = getattr(e, "failed", [])
        safe = [f for f in files if not is_review_done(f)]
        dropped = len(files) - len(safe)
        taken = deliver(safe)
        why = redact(str(e), gh_token)[:200] + (f": {', '.join(failed[:5])}" if failed else "")
        fail = (f"main 에 안 올림 — {why}. 새 글·건너뜀·끝 표시 파일 {len(taken)}개는 서버 대기 폴더로 (같은 관문)"
                + (f", 검수 끝 파일 {dropped}개는 버림 (고친 내용이 저장소에 못 들어가 다음 실행이 다시 검수)" if dropped else "")
                + ". 다음 실행이 새 main 에서 다시")
        return ", 저장소 반영 실패", taken, fail
    if not sha:
        note = ", 저장소 바뀐 것 없음"
    else:
        note = f", main 에 올림 {sha[:7]} (파일 {len(keep)}개 — 배포가 올림)"
    if blocked:
        note += f", 바꾸면 안 되는 파일 {len(blocked)}개는 뺌({', '.join(blocked[:3])})"
    return note, [], ""


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
        secrets = load_secrets()
        token = secrets.get("FIRSTLAB_API_TOKEN", "")
        gh_token = secrets.get("GITHUB_PUSH_TOKEN", "")   # 세션 환경·명령줄에는 절대 안 넘김 (push_changes 의 root 쪽 push 만)
        local = "push" if gh_token else "1"
        if not gh_token:
            note_repo_skipped()
        git_env = writer_env(local=local)   # git 명령에는 키를 넘기지 않음
        started, charged = time.time(), 0.0

        def _stop(signum, frame):  # systemd 바깥 제한(RuntimeMaxSec)에 걸려도 기록은 남기게
            raise SystemExit(f"중지 신호 {signum}")
        signal.signal(signal.SIGTERM, _stop)
        try:
            prepare_workdir(git_env)
            base = head_sha(git_env) if gh_token else ""
            charged = usd   # 여기부터는 돈이 나감 — 결과를 못 읽으면(시간 초과·강제 종료) 예산만큼 쓴 것으로 셈 (하루 상한을 넘지 않게)
            result, error = run_session(claude_command(settings.model or DEFAULT_MODEL, usd, text, local),
                                        writer_env(key, token, local), RUN_TIMEOUT_MIN * 60)
            ok = session_ok(result)
            cost = charged = float(result.get("total_cost_usd") or 0.0) if result else usd
            summary = redact(str((result or {}).get("result") or error), key, token)
            files, taken, others, note, push_fail = new_queue_files(git_env), [], [], "", ""
            if ok and gh_token:   # 월정액처럼: 저장소 일까지 한 결과를 검사 통과 후 main 에 (배포가 queue 파일을 올림)
                note, taken, push_fail = finish_push(text, summary, files, git_env, base, gh_token)
            elif ok:  # 월정액처럼 끝까지 마친 실행의 결과만 (월정액도 마지막에 한 번 push — 중간에 끊기면 아무것도 안 올라감)
                taken = deliver(files)
                if text != MAINT_TEXT and done_file_missing(files):  # 점검은 글쓰기 실행이 아님 (월정액도 끝 표시 없음)
                    taken.append(write_done_file(text, summary))
            if not gh_token:
                others = other_changes(git_env)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 어떤 실패든 기록하고 잠금을 푼다
            _log(False, f"{label or 'API 글쓰기'} 실패: {redact(str(e), key, token, gh_token)[:200]}", charged)
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
        if push_fail:   # 봇 기록에 따로 ❌ (매일 점검이 ❌ 만 봄)
            _log(False, f"{label or 'API 글쓰기'} 저장소 반영 실패(API 봇): {push_fail}")
        _log(ok, f"{label or 'API 글쓰기'} 끝(API 봇): {'성공' if ok else '실패'} — {summary[:300]} "
                 f"(비용 ${cost:.2f}≈{cost * 1400:,.0f}원, {minutes:.0f}분, 결과 파일 {len(taken)}개{note})", cost)
        STATUS_FILE.unlink(missing_ok=True)
        prune_local()
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="API 봇 글쓰기 한 번 (bot/run.py 가 systemd-run 으로 시작)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--text", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    return run_once(args.text, args.label)


if __name__ == "__main__":
    sys.exit(main())
