"""봇 실행 상태를 워드프레스 옵션에 저장 (설정 페이지에서 보여주기 위함).

firstlab_bot_state = {
  "plan": {"2026-11-03": ["09:13", "20:41"]},   # 오늘 랜덤으로 정해진 발행 시각
  "done": {"2026-11-03": [0]},                   # 오늘 완료한 슬롯 번호
  "used_keywords": ["..."],                      # 이미 쓴 키워드 (중복 방지)
  "log": [{"time": "...", "ok": true, "msg": "..."}]  # 최근 기록 (최신이 앞)
}
firstlab_bot_request = 관리자가 '지금 글 1개 만들기'를 누른 시각 (없으면 비어 있음)
"""
from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import contextmanager
from datetime import datetime

from .settings import wp_cli

STATE_OPTION = "firstlab_bot_state"
REQUEST_OPTION = "firstlab_bot_request"
EXCLUDE_OPTION = "firstlab_bot_excluded"  # 관리자가 '제외' 누른 키워드 목록 (봇이 반영 후 지움)
DICT_KEYS = ("plan", "done", "refresh", "spend", "direct_fired", "publish_fired", "continuous", "maint_fired")  # 날짜·주차를 키로 쓰는 값 (빈 값이면 PHP 가 [] 로 바꿈)
MAX_LOG = 30
STATE_LOCK = "/run/firstlab-state.lock"  # 봇(1분)·배포(글 올리기)·키워드 수집이 상태를 동시에 저장할 때 순서 맞춤
MAX_USED = 2000


def load_state() -> dict:
    try:
        state = json.loads(wp_cli("option", "get", STATE_OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError:
        state = {}
    for key, default in (("plan", {}), ("done", {}), ("used_keywords", []), ("log", [])):
        state.setdefault(key, default)
    # 워드프레스(PHP)를 거치면 빈 사전 {} 이 빈 목록 [] 으로 바뀌어 돌아온다 → 사전으로 되돌림
    for key in DICT_KEYS:
        if key in state and not isinstance(state[key], dict):
            state[key] = {}
    return state


@contextmanager
def _state_lock():
    try:
        handle = open(STATE_LOCK, "a")
    except OSError:  # 잠금 파일을 못 만드는 환경(테스트 등)에서는 잠금 없이
        yield
        return
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def merge_stored(state: dict, stored: dict) -> None:
    """읽은 뒤에 다른 실행이 저장한 기록·사용 키워드를 합친다 (통째로 덮어쓰면 사라짐).
    11/5 오전: 봇이 사진 채우기로 길게 도는 사이 배포가 남긴 '직접 작성 글' 기록을 봇이 덮어써,
    연속 작성이 글이 올라온 줄 모르고 60분 제한까지 기다림."""
    mine = {(e.get("time"), e.get("msg")) for e in state["log"]}
    extra = [e for e in stored.get("log", []) if (e.get("time"), e.get("msg")) not in mine]
    if extra:
        state["log"] = sorted(state["log"] + extra, key=lambda e: str(e.get("time", "")), reverse=True)
    seen = set(state["used_keywords"])
    state["used_keywords"] += [k for k in stored.get("used_keywords", []) if k not in seen]


def save_state(state: dict) -> None:
    with _state_lock():
        merge_stored(state, load_state())
        state["used_keywords"] = state["used_keywords"][-MAX_USED:]
        state["log"] = state["log"][:MAX_LOG]
        wp_cli("option", "update", STATE_OPTION, json.dumps(state, ensure_ascii=False), "--format=json")


def add_spend(state: dict, usd: float) -> None:
    """오늘 쓴 AI 비용 누적 (하루 상한 판단용). 지난 날짜는 지운다."""
    today = datetime.now().strftime("%Y-%m-%d")
    spend = state.get("spend") if isinstance(state.get("spend"), dict) else {}
    state["spend"] = {today: round(spend.get(today, 0.0) + usd, 4)}


def spent_today(state: dict) -> float:
    spend = state.get("spend") if isinstance(state.get("spend"), dict) else {}
    return spend.get(datetime.now().strftime("%Y-%m-%d"), 0.0)


def add_log(state: dict, ok: bool, msg: str) -> None:
    state["log"].insert(0, {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "ok": ok, "msg": msg})


def pop_manual_request() -> bool:
    """수동 발행 요청이 있으면 지우고 True."""
    try:
        value = wp_cli("option", "get", REQUEST_OPTION).strip()
    except subprocess.CalledProcessError:
        return False
    if not value:
        return False
    wp_cli("option", "delete", REQUEST_OPTION)
    return True


def pop_excluded() -> list[str]:
    try:
        items = json.loads(wp_cli("option", "get", EXCLUDE_OPTION, "--format=json") or "[]")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []
    if items:
        wp_cli("option", "delete", EXCLUDE_OPTION)
    return [str(x) for x in items] if isinstance(items, list) else []


CONTINUOUS_OPTION = "firstlab_bot_continuous"  # 설정 페이지 '연속 작성' 시작(1)·정지(0)


def continuous_on() -> bool:
    try:
        return wp_cli("option", "get", CONTINUOUS_OPTION).strip() == "1"
    except subprocess.CalledProcessError:
        return False


def set_continuous(on: bool) -> None:
    wp_cli("option", "update", CONTINUOUS_OPTION, "1" if on else "0")
