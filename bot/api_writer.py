"""API 봇 방식: 월정액 예약 작업(bot/routine.py)과 똑같은 글쓰기 작업을 서버 안에서 Claude Code(헤드리스)로 돌린다.

운영자 11/12 '언제든 api로 바꾸면 똑같이 돌아가게': 설정 '글 작성 방식: API 봇' + 'API 봇 사용' 이면
  같은 지시서(automation/daily-writer.md, 점검은 maintenance.md) · 같은 실행 메시지(글 작성·manual-request·maintenance-check·slot-N)
  · 같은 관문(bot/publish_queue) · 같은 발행 방식(초안·바로·예약)으로 돈다. 다른 점은 요금이 설정 페이지에 넣은 Claude API 키로 나가는 것뿐.

흐름 (1분 봇은 시작만 하고 바로 돌아감):
  run.fire_routine → fire() → systemd-run 으로 따로 실행 (python -m bot.api_writer run --text ...)
  → run_once(): 잠금 → 작업용 저장소(WORKDIR)를 최신 main 으로 → claude -p (전용 사용자, 키는 환경 변수로만)
  → 그 실행이 wordpress/queue 에 새로 만든 파일만 LOCAL_QUEUE_DIR 로 → bot.publish_queue (월정액과 같은 관문) → 봇 기록·비용 기록
저장소에는 올리지 않는다 (서버에 GitHub 쓰기 권한 없음). 그래서 지시서의 'API 봇 실행(FIRSTLAB_LOCAL=1)' 항목대로
  저장소 파일을 바꿔야 하는 일(대기 글 검수 패치·계산기·검사 코드)은 이 방식에서 하지 않는다.
안전: 한 번에 1개(잠금), 시간 제한, 낮은 CPU 몫·메모리 상한(systemd), 하루 비용 상한(시작 전 확인 + --max-budget-usd).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
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
REPO_URL = "https://github.com/yoorimint/first-lab"      # 공개 저장소 (읽기만)
UNIT = "firstlab-api-writer"

DEFAULT_MODEL = "claude-opus-5-5"
RUN_TIMEOUT_MIN = 80     # 글쓰기 한 번 시간 제한 — run.CONTINUOUS_TIMEOUT_MIN(90) 보다 짧게 (연속 작성이 겹쳐 시작하지 않게)
UNIT_TIMEOUT_MIN = RUN_TIMEOUT_MIN + 25   # 바깥 제한 (올리기·사진 만들기 포함)
RUN_MAX_USD = 15.0       # 한 번 실행의 최대 비용 (하루 상한이 0=없음이어도)
MIN_START_USD = 0.5      # 하루 상한까지 이만큼도 안 남으면 시작하지 않음 (시작하자마자 예산에 걸려 끊기면 돈만 씀)
MEMORY_HIGH, MEMORY_MAX = "800M", "1200M"   # 2GB 서버: 넘치면 글쓰기만 끊기고 사이트는 그대로 (예상 300~700MB)
MAX_RESULT_BYTES = 5_000_000
KEEP_LOCAL_DAYS = 60
# 월정액 세션에 있는 도구 중 글쓰기에 쓰는 것만 (예약·작업 트리·워크플로 같은 도구는 뺌). 이 밖의 도구는 묻지 않고 거절(dontAsk).
TOOLS = "Task,Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"

PROMPT = ("저장소의 automation/daily-writer.md 를 읽고 그대로 따른다. "
          "이번 실행은 서버 안 API 봇 실행이다 (환경 변수 FIRSTLAB_LOCAL=1) — 지시서의 'API 봇 실행' 항목대로 한다.\n"
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

def claude_command(model: str, usd: float, text: str) -> list[str]:
    """Claude Code 헤드리스 명령. 키는 여기 넣지 않는다 (명령줄은 ps 로 보임) — 환경 변수로만."""
    return [CLAUDE_BIN, "-p", PROMPT.format(text=text or "(없음)"), "--output-format", "json",
            "--model", model, "--max-budget-usd", f"{usd:.2f}",
            "--permission-mode", "dontAsk", "--tools", TOOLS, "--allowedTools", TOOLS, "--strict-mcp-config"]


def writer_env(api_key: str = "", site_token: str = "") -> dict[str, str]:
    """글쓰기 사용자의 환경 (그 밖의 서버 환경 변수는 넘기지 않음). 키가 빈 값이면 넣지 않는다 (git 명령용)."""
    env = {
        "HOME": str(BASE), "USER": WRITER_USER, "LOGNAME": WRITER_USER, "LANG": "C.UTF-8", "TZ": "Asia/Seoul",
        "PATH": f"{VENV_BIN}:{NODE_BIN}:/usr/local/bin:/usr/bin:/bin",
        "ANTHROPIC_API_KEY": api_key,              # 요금은 이 키로 (설정 페이지 값, 없으면 secrets.env)
        "FIRSTLAB_API_TOKEN": site_token,          # 지시서의 /next·/law 통로 (월정액 세션과 같은 값)
        "FIRSTLAB_LOCAL": "1",                     # 지시서 'API 봇 실행' 항목
        "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # 고정 버전 그대로, 부가 통신 없음
    }
    return {k: v for k, v in env.items() if v}


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _as_writer(cmd: list[str], env: dict, timeout: int = 300, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or WORKDIR, env=env, user=WRITER_USER, group=WRITER_USER, extra_groups=[],
                          capture_output=True, text=True, timeout=timeout)


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


def write_done_file(text: str, result: str) -> str:
    """세션이 끝 표시(run_done)를 안 남기고 '성공'으로 끝났을 때 대신 만든다 — 연속 작성이 90분 기다리지 않게.
    실패한 실행에는 만들지 않는다 (실패가 이어지면 연속 작성이 스스로 멈춰야 함)."""
    slot = text[5:] if text.startswith("slot-") and text[5:].isdigit() else ""
    data = {"type": "run_done", "summary": f"(API 봇: 끝 표시 없이 끝남) {result[:150]}"}
    if slot:
        data["slot"] = int(slot)
    LOCAL_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_QUEUE_DIR / f"done-{datetime.now():%Y%m%d-%H%M}{f'-s{slot}' if slot else ''}-api.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path.name


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
        git_env = writer_env()   # git 명령에는 키를 넘기지 않음
        started, charged = time.time(), 0.0

        def _stop(signum, frame):  # systemd 바깥 제한(RuntimeMaxSec)에 걸려도 기록은 남기게
            raise SystemExit(f"중지 신호 {signum}")
        signal.signal(signal.SIGTERM, _stop)
        try:
            prepare_workdir(git_env)
            charged = usd   # 여기부터는 돈이 나감 — 결과를 못 읽으면(시간 초과·강제 종료) 예산만큼 쓴 것으로 셈 (하루 상한을 넘지 않게)
            result, error = run_session(claude_command(settings.model or DEFAULT_MODEL, usd, text),
                                        writer_env(key, token), RUN_TIMEOUT_MIN * 60)
            ok = session_ok(result)
            cost = charged = float(result.get("total_cost_usd") or 0.0) if result else usd
            summary = redact(str((result or {}).get("result") or error), key, token)
            files, taken = new_queue_files(git_env), []
            if ok:  # 월정액처럼 끝까지 마친 실행의 결과만 (월정액도 마지막에 한 번 push — 중간에 끊기면 아무것도 안 올라감)
                taken = deliver(files)
                if text != MAINT_TEXT and done_file_missing(files):  # 점검은 글쓰기 실행이 아님 (월정액도 끝 표시 없음)
                    taken.append(write_done_file(text, summary))
            others = other_changes(git_env)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 어떤 실패든 기록하고 잠금을 푼다
            _log(False, f"{label or 'API 글쓰기'} 실패: {redact(str(e), key, token)[:200]}", charged)
            STATUS_FILE.unlink(missing_ok=True)
            return 1
        minutes = (time.time() - started) / 60
        if taken:
            try:  # 월정액과 같은 관문으로 올림 (배포 때와 같은 명령)
                from . import publish_queue
                publish_queue.main()
            except Exception as e:  # noqa: BLE001 - 다음 배포·다음 실행 때 다시 시도됨 (완료 표시 없음)
                _log(False, f"API 글쓰기 결과 올리기 실패: {str(e)[:150]}")
        note = f", 반영 안 된 저장소 수정 {len(others)}곳({', '.join(others[:3])})" if others else ""
        if files and not ok:
            note += f", 끝까지 못 마쳐 결과 파일 {len(files)}개 버림"
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
