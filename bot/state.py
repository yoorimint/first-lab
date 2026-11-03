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

import json
import subprocess
from datetime import datetime

from .settings import wp_cli

STATE_OPTION = "firstlab_bot_state"
REQUEST_OPTION = "firstlab_bot_request"
MAX_LOG = 30
MAX_USED = 2000


def load_state() -> dict:
    try:
        state = json.loads(wp_cli("option", "get", STATE_OPTION, "--format=json") or "{}")
    except subprocess.CalledProcessError:
        state = {}
    for key, default in (("plan", {}), ("done", {}), ("used_keywords", []), ("log", [])):
        state.setdefault(key, default)
    return state


def save_state(state: dict) -> None:
    state["used_keywords"] = state["used_keywords"][-MAX_USED:]
    state["log"] = state["log"][:MAX_LOG]
    wp_cli("option", "update", STATE_OPTION, json.dumps(state, ensure_ascii=False), "--format=json", "--autoload=no")


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
