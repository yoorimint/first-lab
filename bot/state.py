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
DICT_KEYS = ("plan", "done", "refresh", "spend", "direct_fired", "publish_fired", "continuous", "maint_fired", "law_changes", "rates")  # 날짜·주차를 키로 쓰는 값 (빈 값이면 PHP 가 [] 로 바꿈)
MAX_LOG = 30
MERGE_LISTS = ("used_keywords", "law_changes_done", "new_benefits_done")  # 저장 때 다른 실행의 값과 합칠 목록
STATE_LOCK = "/run/firstlab-state.lock"  # 봇(1분)·배포(글 올리기)·키워드 수집이 상태를 동시에 저장할 때 순서 맞춤
MAX_USED = 2000


_SNAPSHOT: dict[int, str] = {}  # 읽어 갈 때의 상태 (저장 때 '내가 바꾼 키'만 덮어쓰려고)
_OWN = ("log",) + ("used_keywords", "law_changes_done", "new_benefits_done")  # 따로 합치는 키


def load_state() -> dict:
    from .settings import option_missing
    try:
        state = json.loads(wp_cli("option", "get", STATE_OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError as e:
        if not option_missing(e):
            raise  # 잠깐 못 읽은 것을 빈 상태로 착각해 오늘 공개·실행 기록을 지우지 않게 (11/5 검토)
        state = {}
    for key, default in (("plan", {}), ("done", {}), ("used_keywords", []), ("log", [])):
        state.setdefault(key, default)
    # 워드프레스(PHP)를 거치면 빈 사전 {} 이 빈 목록 [] 으로 바뀌어 돌아온다 → 사전으로 되돌림
    for key in DICT_KEYS:
        if key in state and not isinstance(state[key], dict):
            state[key] = {}
    _SNAPSHOT[id(state)] = json.dumps(state, sort_keys=True, ensure_ascii=False)
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
    for key in MERGE_LISTS:  # 여러 실행이 덧붙이는 목록은 합집합
        mine_list = state.setdefault(key, [])
        seen = set(mine_list)
        mine_list += [k for k in stored.get(key, []) or [] if k not in seen]


def changed_onto(state: dict, base: dict, stored: dict) -> dict:
    """읽은 뒤(base) 내가 바꾼 최상위 키만 지금 저장된 값(stored) 위에 덮어씀 (3방향 합치기).
    11/5 검토: 배포(글 올리기)·새벽 수집이 몇 분 들고 있던 옛 상태를 통째로 저장해, 그사이 1분 봇이 적은
    오늘 공개·글쓰기 실행 기록이 지워짐 → 같은 시각을 다시 공개·실행할 수 있었음."""
    merged = dict(stored)
    for key, value in state.items():
        if key not in _OWN and (key not in base or base[key] != value):
            merged[key] = value
    for key in base:
        if key not in state and key not in _OWN:
            merged.pop(key, None)
    for key in _OWN:
        if key in state:
            merged[key] = state[key]
    return merged


def save_state(state: dict) -> None:
    with _state_lock():
        stored = load_state()
        base = _SNAPSHOT.get(id(state))
        merged = changed_onto(state, json.loads(base), stored) if base else state
        merge_stored(merged, stored)
        merged["used_keywords"] = merged["used_keywords"][-MAX_USED:]
        merged["log"] = merged["log"][:MAX_LOG]
        # 값은 표준 입력으로 (명령줄 인자 하나는 128KB 까지라 상태가 커지면 저장이 영영 실패함 — 11/5 검토)
        wp_cli("option", "update", STATE_OPTION, "--format=json", stdin=json.dumps(merged, ensure_ascii=False))
        if merged is not state:
            state.clear()
            state.update(merged)
        _SNAPSHOT[id(state)] = json.dumps(state, sort_keys=True, ensure_ascii=False)


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
