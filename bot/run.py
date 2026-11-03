"""봇 실행 진입점. systemd 타이머가 1분마다 실행한다.

  - 하루 1회(새벽 4시 이후 첫 실행): 키워드 창고 확장
  - 관리자가 '지금 글 1개 만들기'를 눌렀으면 → 1개 생성
  - 자동 발행이 켜져 있고, 오늘의 랜덤 발행 시각이 지난 슬롯이 있으면 → 1개 생성
  - 한 번 실행에 글은 최대 1개 (몰아서 발행 방지)

사용: python3 -m bot.run            (평소: 할 일이 있을 때만 실행)
      python3 -m bot.run --now      (점검용: 즉시 1개 생성)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import random
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .discover import discover
from .keyword_db import connect, set_status, summary
from .llm import make_llm
from .publisher import publish
from .select_keyword import pick_keyword, ranked_candidates
from .settings import BotSettings, load_settings
from .state import add_log, load_state, pop_excluded, pop_manual_request, save_state
from .writer import write_article

KST = ZoneInfo("Asia/Seoul")
LOCK_FILE = "/run/firstlab-bot.lock"
MAX_LATE_MINUTES = 180  # 서버 중단 등으로 이보다 늦어진 슬롯은 건너뜀 (몰아서 발행 방지)
DISCOVER_HOUR = 4       # 키워드 창고 확장 시각(이후 첫 실행)
MIN_POOL = 30           # 후보가 이보다 적으면 글 쓰기 전에 창고를 조금 확장


def plan_for_today(settings: BotSettings, today: str) -> list[str]:
    """오늘의 발행 시각 = 설정 시각 ± 랜덤(분). 날짜로 시드를 고정해 하루 동안 같은 값."""
    plan = []
    for i, hhmm in enumerate(settings.publish_times):
        base = datetime.strptime(f"{today} {hhmm}", "%Y-%m-%d %H:%M")
        offset = random.Random(f"{today}-{i}-{hhmm}").randint(-settings.jitter_minutes, settings.jitter_minutes)
        day_start = base.replace(hour=0, minute=0)
        t = min(max(base + timedelta(minutes=offset), day_start), day_start.replace(hour=23, minute=59))
        plan.append(t.strftime("%H:%M"))  # 날짜를 넘어가지 않게 00:00~23:59 로 제한
    return plan


def due_slot(state: dict, settings: BotSettings, now: datetime) -> int | None:
    """실행할 차례인 슬롯 번호 (없으면 None). 오늘 계획을 상태에 기록한다."""
    today = now.strftime("%Y-%m-%d")
    state["plan"] = {today: plan_for_today(settings, today)}            # 지난 날짜는 정리
    state["done"] = {today: state["done"].get(today, [])}
    for i, hhmm in enumerate(state["plan"][today]):
        if i in state["done"][today] or now.strftime("%H:%M") < hhmm:
            continue
        due = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)
        if now - due > timedelta(minutes=MAX_LATE_MINUTES):
            state["done"][today].append(i)  # 너무 늦음 → 건너뜀
            continue
        return i
    return None


def create_one(settings: BotSettings) -> str:
    """키워드 선정 → 글 작성 → 워드프레스 저장. 결과 메시지 반환."""
    llm = make_llm(settings.provider, settings.api_key, settings.model)
    with connect() as conn:
        if len(ranked_candidates(conn, settings)) < MIN_POOL:
            discover(5, settings.hint_keywords)
        keyword = pick_keyword(conn, settings, llm)
        if not keyword:
            raise RuntimeError("안전 검사를 통과한 후보가 없습니다. 새벽 키워드 수집 후 다시 시도됩니다")
        article = write_article(llm, keyword, settings.target_length, settings.extra_prompt)
        post_id, link = publish(article, settings.post_status)
        set_status(conn, keyword, "used", f"post {post_id}")
    kind = "발행" if settings.post_status == "publish" else "초안 저장"
    return f"[{kind}] '{article.title}' (키워드: {keyword}, 모델: {llm.model}) {link}"


def refresh_dashboard(state: dict, settings: BotSettings) -> None:
    """설정 페이지에 보여줄 창고 현황과 다음 후보 (안전 검사 전 포함)."""
    with connect() as conn:
        state["keywords"] = summary(conn)
        state["candidates"] = ranked_candidates(conn, settings, limit=20)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="즉시 1개 생성")
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 이전 실행이 아직 작업 중이면 건너뜀
    except BlockingIOError:
        return

    settings = load_settings()
    state = load_state()
    before = json.dumps(state, sort_keys=True, ensure_ascii=False)
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    # 관리자 '제외' 반영
    excluded = pop_excluded()
    if excluded:
        with connect() as conn:
            for kw in excluded:
                set_status(conn, kw, "excluded", "관리자 제외")

    # 하루 1회 키워드 창고 확장
    if state.get("discover_date") != today and now.hour >= DISCOVER_HOUR:
        try:
            r = discover(settings.discover_calls, settings.hint_keywords)
            add_log(state, True, f"키워드 수집: API {r['calls']}회, 새 키워드 {r['new']}개 (창고 {r['total']}개)")
        except Exception as e:  # noqa: BLE001
            add_log(state, False, f"키워드 수집 실패: {e}")
        state["discover_date"] = today

    # 글 생성 (수동 요청 또는 자동 발행 시각)
    reason, slot = None, None
    if args.now or pop_manual_request():
        reason = "수동"
    slot_candidate = due_slot(state, settings, now)
    if reason is None and settings.enabled and slot_candidate is not None:
        reason, slot = f"자동 {slot_candidate + 1}회차", slot_candidate

    if reason:
        try:
            msg = create_one(settings)
            add_log(state, True, f"{reason}: {msg}")
            print(msg)
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 기록하고 다음 실행에 영향 없게
            detail = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else e
            add_log(state, False, f"{reason} 실패: {detail}")
            print(f"실패: {detail}", file=sys.stderr)
        if slot is not None:  # 실패해도 같은 슬롯을 1분마다 재시도하지 않도록 완료 처리
            state["done"][today].append(slot)

    if reason or excluded or state.get("dashboard_at", "")[:13] != now.strftime("%Y-%m-%d %H"):
        refresh_dashboard(state, settings)  # 변화가 있거나 1시간마다
        state["dashboard_at"] = now.strftime("%Y-%m-%d %H:%M")

    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        save_state(state)


if __name__ == "__main__":
    main()
