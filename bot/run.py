"""봇 실행 진입점. systemd 타이머가 1분마다 실행한다.

  - 관리자가 '지금 글 1개 만들기'를 눌렀으면 → 1개 생성
  - 자동 발행이 켜져 있고, 오늘의 랜덤 발행 시각이 지난 슬롯이 있으면 → 1개 생성
  - 한 번 실행에 최대 1개만 만든다 (몰아서 발행 방지)

사용: python3 -m bot.run            (평소: 할 일이 있을 때만 실행)
      python3 -m bot.run --now      (점검용: 즉시 1개 생성)
"""
from __future__ import annotations

import argparse
import fcntl
import random
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .find_keywords import find_candidates
from .llm import make_llm
from .publisher import publish
from .seeds import google_trends_kr, random_seeds
from .settings import BotSettings, load_settings
from .state import add_log, load_state, pop_manual_request, save_state
from .writer import write_article

KST = ZoneInfo("Asia/Seoul")
LOCK_FILE = "/run/firstlab-bot.lock"
MAX_LATE_MINUTES = 180  # 서버 중단 등으로 이보다 늦어진 슬롯은 건너뜀 (몰아서 발행 방지)


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


def pick_seeds(settings: BotSettings) -> list[str]:
    """힌트 키워드가 있으면 우선 사용, 없으면 기본 주제 풀. 급상승 옵션이면 1개를 급상승어로 교체."""
    if settings.hint_keywords:
        seeds = random.sample(settings.hint_keywords, min(5, len(settings.hint_keywords)))
    else:
        seeds = random_seeds(5)
    if settings.use_trends:
        trends = google_trends_kr()
        if trends:
            seeds[-1] = random.choice(trends)
    return seeds


def create_one(settings: BotSettings, state: dict) -> str:
    """키워드 선정 → 글 작성 → 워드프레스 저장. 결과 메시지 반환."""
    for _ in range(3):  # 후보가 없으면 다른 출발 주제로 최대 3번 시도
        hints = pick_seeds(settings)
        _, candidates = find_candidates(hints, settings.min_volume, settings.max_volume,
                                        settings.blocked_words, exclude=set(state["used_keywords"]))
        if candidates:
            break
    if not candidates:
        raise RuntimeError(f"조건에 맞는 새 키워드가 없습니다 (출발 주제: {', '.join(hints)}). 검색량 범위를 넓혀 보세요")
    keyword = random.choice(candidates[:5]).keyword  # 상위 5개 중 랜덤 → 글감이 한쪽으로 쏠리지 않게

    llm = make_llm(settings.provider, settings.api_key, settings.model)
    article = write_article(llm, keyword, settings.target_length, settings.extra_prompt)
    post_id, link = publish(article, settings.post_status)

    state["used_keywords"].append(keyword)
    kind = "발행" if settings.post_status == "publish" else "초안 저장"
    return f"[{kind}] '{article.title}' (키워드: {keyword} ← 출발 주제: {', '.join(hints)}, 모델: {llm.model}) {link}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="즉시 1개 생성")
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 이전 실행이 아직 글을 쓰는 중이면 건너뜀
    except BlockingIOError:
        return

    settings = load_settings()
    state = load_state()
    now = datetime.now(KST)

    reason, slot = None, None
    if args.now or pop_manual_request():
        reason = "수동"
    elif settings.enabled:
        slot = due_slot(state, settings, now)
        if slot is not None:
            reason = f"자동 {slot + 1}회차"
    else:
        due_slot(state, settings, now)  # 꺼져 있어도 오늘 계획은 화면에 보여줌

    if reason:
        try:
            msg = create_one(settings, state)
            add_log(state, True, f"{reason}: {msg}")
            print(msg)
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 기록하고 다음 실행에 영향 없게
            detail = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else e
            add_log(state, False, f"{reason} 실패: {detail}")
            print(f"실패: {e}", file=sys.stderr)
        if slot is not None:  # 실패해도 같은 슬롯을 1분마다 재시도하지 않도록 완료 처리
            state["done"][now.strftime("%Y-%m-%d")].append(slot)

    save_state(state)


if __name__ == "__main__":
    main()
