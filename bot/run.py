"""봇 실행 진입점. systemd 타이머가 1분마다 실행한다.

  - (키워드 수집은 별도 작업 firstlab-discover: 매일 + 배포 직후)
  - 하루 1회: 상위 후보 경쟁도 조회 (안전 판단은 글쓰기 작업이 함)
  - 예약 발행: 오늘의 공개 시각(설정 ± 랜덤)마다 가장 오래된 대기 초안 1편 공개 (bot/schedule.py)
  - '글 작성 시각'·'지금 글 1개 만들기'·연속 작성·매일 점검 → 글쓰기 작업 시작 (fire_routine 한 곳에서 고름):
    월정액 = Claude 예약 작업(bot/routine.py, 구독 사용량) / API 봇 = 서버 안 같은 작업(bot/api_writer.py, API 키 요금).
    두 방식 모두 같은 지시서(automation/daily-writer.md)·같은 관문(bot/publish_queue)·같은 발행 방식 (운영자 11/12)
  - 한 번 실행에 글은 최대 1개 (몰아서 발행 방지)

사용: python3 -m bot.run            (평소: 할 일이 있을 때만 실행)
      python3 -m bot.run --now      (점검용: 글쓰기 작업 1번 바로 시작)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import api_writer, pending_images, routine, schedule
from .keyword_db import connect, set_status, summary
from .select_keyword import check_top_competition, ranked_candidates
from .settings import BotSettings, load_settings, wp_cli
from .state import (add_log, continuous_on, load_state, pop_excluded, pop_manual_request, save_state,
                    set_continuous, spent_today, writer2_on)

KST = ZoneInfo("Asia/Seoul")
LOCK_FILE = "/run/firstlab-bot.lock"
CONTINUOUS_TIMEOUT_MIN = 90  # 연속 작성: 한 편이 이 시간 안에 안 올라오면 실패로 보고 다음 편 실행 (11/5: 60분이면 계산기 글이 끝나기 전에 다음 실행이 겹침)
# 글쓰기 작업 한 번이 끝났다는 기록 (새 글·대기 글 검수·실행 끝 파일). 보류 수정·계산기 연초 값만 한 실행은
# 앞의 두 기록이 없어 60분 시간 초과(실패)로 셌음 → 3번이면 연속 작성이 스스로 멈춤 (11/5 22:13 실행 74번 뒤 47분 멈춤).
# 그래서 글쓰기 작업은 실행마다 끝에 run_done 파일을 올린다 (daily-writer 실행 순서 6번).
DONE_MARKS = ("직접 작성 글:", "대기 글 검수(직접):", "글쓰기 실행 끝(직접):")
CONTINUOUS_MAX_FAILS = 3     # 연속으로 이만큼 실패하면 연속 작성을 자동으로 멈춤 (사용량 한도·후보 소진 등)


CACHE_DIR = "/var/cache/nginx/first-lab"


def _latest(orderby: str, field: str) -> str:
    return wp_cli("post", "list", "--post_type=post,page", "--post_status=publish", f"--orderby={orderby}",
                  "--order=DESC", "--posts_per_page=1", f"--field={field}").strip()


def purge_cache_if_changed(state: dict) -> None:
    """가장 최근 수정 시각이나 공개 시각이 바뀌었으면 nginx 페이지 캐시 삭제.
    (예약 글이 공개될 때는 수정 시각이 그대로일 수 있어 공개 시각도 본다)"""
    try:
        modified = _latest("modified", "post_modified")
        signature = f"{modified}|{_latest('date', 'post_date')}"
    except subprocess.CalledProcessError:
        return
    if modified and signature != state.get("cache_signature"):
        _sync_recent_cards(state.get("last_modified", ""))
        clear_cache()
        state["last_modified"], state["cache_signature"] = modified, signature


def run_due_wp_cron() -> None:
    """워드프레스 예약 작업(예약 글 공개 등) 실행. 방문이 적거나 캐시된 페이지만 보면 워드프레스가 스스로 못 돌림."""
    try:
        wp_cli("cron", "event", "run", "--due-now", "--quiet")
    except subprocess.CalledProcessError as e:
        print(f"[run] 워드프레스 예약 작업 실패: {e.stderr}", flush=True)


def _sync_recent_cards(since: str) -> None:
    """마지막 확인 이후 수정된 글의 제목 카드 갱신 (제목을 고치면 대표 이미지도 바뀌게)."""
    import json

    from .fix_posts import sync_card

    try:
        posts = json.loads(wp_cli("post", "list", "--post_type=post", "--post_status=any", "--orderby=modified",
                                  "--order=DESC", "--posts_per_page=5", "--fields=ID,post_title,post_name,post_modified",
                                  "--format=json") or "[]")
        for p in posts:
            if p["post_modified"] > since and p["post_title"]:
                sync_card(p)
    except Exception as e:  # noqa: BLE001 - 카드 갱신 실패가 봇을 막지 않게
        print(f"[카드] 갱신 실패: {e}", flush=True)


def clear_cache() -> None:
    """캐시 '내용'만 삭제. 폴더 자체를 지우면 nginx 가 캐시를 못 써서 빈 응답이 나감 (실제 장애 원인)."""
    import os
    import shutil

    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
        shutil.chown(CACHE_DIR, "www-data", "www-data")
        return
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)


def daily_review(state: dict, settings: BotSettings, now: datetime) -> None:
    """하루 1회 상위 후보 경쟁도 조회 → 설정 페이지에 근거 표시 (Gemini 무료).
    안전 판단(업체·의료·정치 등)은 두 방식 모두 글쓰기 작업이 하고 제외 목록(skip 파일)을 올림."""
    today = now.strftime("%Y-%m-%d")
    if state.get("review_date") == today:
        return
    try:
        with connect() as conn:
            n = check_top_competition(conn, settings)
        add_log(state, True, f"후보 검토: 경쟁도 {n}건 조회 (안전 판단은 글쓰기 작업이 함)")
    except Exception as e:  # noqa: BLE001
        add_log(state, False, f"후보 검토 실패: {e}")
    state["review_date"], state["dashboard_at"] = today, ""


def fire_routine(state: dict, settings: BotSettings, label: str, text: str = "") -> bool:
    """글쓰기 작업 시작 — 방식은 여기 한 곳에서만 고른다 (부르는 곳: 글 작성 시각·수동·연속 작성·매일 점검).
    월정액: Claude 예약 작업 / API 봇: 서버 안에서 같은 지시서로 (bot/api_writer.py). 결과는 둘 다 wordpress/queue → publish_queue 관문."""
    api = settings.write_mode == "bot"
    try:
        if api:
            url = api_writer.fire(settings, text, spent=spent_today(state), label=label)
        else:
            url = routine.fire(settings.routine_fire_url, settings.routine_token, text)
        add_log(state, True, f"{label}: Claude 글쓰기 작업 시작{' (API 봇)' if api else ''} {url}")
        state["last_fire_ok_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")  # /health (외부 알림)
        return True
    except Exception as e:  # noqa: BLE001 - 응답 형식이 바뀌어도(미리보기 기능) 봇이 멈추지 않게 (11/5 검토)
        add_log(state, False, f"{label}: Claude 글쓰기 작업 실행 실패{' (API 봇)' if api else ''} - {str(e)[:150]}")
        state["last_fire_error_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return False


def writer_busy(settings: BotSettings) -> bool:
    """API 봇 글쓰기가 아직 도는 중 (서버 메모리 때문에 한 번에 1개) → 시작할 일은 끝날 때까지 미룸 (시각 3시간 안).
    월정액은 클라우드라 언제나 False."""
    return settings.write_mode == "bot" and api_writer.busy()


def latest_post_log(state: dict) -> str:
    """가장 최근 '직접 작성 글' 기록 (시각+내용). 없으면 ''."""
    for e in state.get("log", []):  # 최신이 앞
        if e.get("ok") and any(m in str(e.get("msg", "")) for m in DONE_MARKS):
            return f"{e.get('time', '')} {e.get('msg', '')}"
    return ""


def post_arrived_since(state: dict, since: str, seen: str | None = None) -> bool:
    """시작한 뒤 새 글이 올라왔는지 (publish_queue 기록).
    seen = 시작할 때의 최근 글 기록. 그와 다른 새 기록이 있어야 '올라옴' — 기록 시각이 분 단위라
    직전 글과 시작이 같은 분이면 직전 글을 새 글로 착각해 두 편을 동시에 돌린 일이 있었음 (11/4 22:05).
    seen 이 없는 예전 상태만 시각 비교(초과)로 판단."""
    if seen is not None:
        latest = latest_post_log(state)
        return bool(latest) and latest != seen
    return any(e.get("ok") and str(e.get("time", "")) > since and any(m in str(e.get("msg", "")) for m in DONE_MARKS)
               for e in state.get("log", []))


WRITERS = 2  # 글쓰기 동시 실행 수 (11/6 운영자 '둘 다 해 봐' — 시험). 실행마다 'slot-N' 을 보내 /next?slot=N 으로 후보를 나눠 받고,
             # 2번 실행은 새 글만 (곁일은 1번). 사용량 한도로 실패가 이어지면 아래 자동 정지가 둘 다 멈춤.
SLOT_TAG = re.compile(r"\[(\d)번\]")


def slot_key(slot: int) -> str:
    return "continuous" if slot == 1 else f"continuous_{slot}"   # 1번은 예전 키 그대로 (상태 호환)


def slot_done_log(state: dict, slot: int) -> str:
    """그 실행 칸의 가장 최근 '실행 끝' 기록 — 끝 표시(done 파일)에 slot 이 적혀 '[N번]' 이 붙음. 칸 표시가 없으면 1번."""
    for e in state.get("log", []):  # 최신이 앞
        msg = str(e.get("msg", ""))
        if not (e.get("ok") and "글쓰기 실행 끝(직접):" in msg):
            continue
        tag = SLOT_TAG.search(msg[:40])
        if (int(tag.group(1)) if tag else 1) == slot:
            return f"{e.get('time', '')} {msg}"
    return ""


def continuous_step(state: dict, settings: BotSettings, now: datetime) -> None:
    """연속 작성 (설정 페이지 시작·정지): 칸(WRITERS 개)마다 한 편이 끝나면 바로 다음 편을 실행.
    한 편이 CONTINUOUS_TIMEOUT_MIN 안에 안 끝나면 실패로 세고, 한 칸이라도 3번 연속 실패하면 스스로 멈춘다 (사용량 한도·후보 소진 대비)."""
    if not continuous_on():
        for slot in range(1, WRITERS + 1):
            state.pop(slot_key(slot), None)
        return
    if state.get("dashboard_at") == "":
        # 방금 올라온 결과(후보 제외·새 글)로 후보 목록을 새로 만들기 전 — 이번 분에 시작하면 옛 후보를 받음
        # (11/6 09:45 실행: '후보 20개는 직전 실행이 제외한 것' → 새 글 없이 끝남). 아래 refresh_dashboard 뒤 다음 분에 시작.
        return
    # 설정 페이지 '2번 작성자' 끄면 1번 칸만 (11/7 운영자). 2번이 쓰던 한 편은 그대로 마치고, 칸 기록만 지움
    # API 봇은 서버(2GB) 안에서 돌아 한 번에 1개 → 1번 칸만
    active = WRITERS if writer2_on() and settings.write_mode != "bot" else 1
    for slot in range(active + 1, WRITERS + 1):
        state.pop(slot_key(slot), None)
    for slot in range(1, active + 1):
        if not _slot_step(state, settings, now, slot):
            return


def _slot_step(state: dict, settings: BotSettings, now: datetime, slot: int) -> bool:
    """한 칸 진행. 연속 작성을 멈췄으면 False."""
    key = slot_key(slot)
    c = state.get(key) or {}
    stamp = now.strftime("%Y-%m-%d %H:%M")
    fails, count = c.get("fails", 0), c.get("count", 0)
    done_of = (lambda: slot_done_log(state, slot)) if WRITERS > 1 else (lambda: latest_post_log(state))
    if c.get("fired_at"):
        seen = c.get("seen")
        done = done_of()
        # 끝 기록이 시작 뒤 것이어야 '끝남' — 배포 직후 예전 방식으로 기록된 실행(seen 이 다른 종류)을 끝난 것으로 착각해 겹쳐 돌리지 않게
        arrived = (bool(done) and done != seen and done[:16] >= c["fired_at"]) if (WRITERS > 1 or seen is not None) \
            else post_arrived_since(state, c["fired_at"], seen)
        started = datetime.strptime(c["fired_at"], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        if not arrived and now - started < timedelta(minutes=CONTINUOUS_TIMEOUT_MIN):
            return True  # 쓰는 중
        fails, count = (0, count + 1) if arrived else (fails + 1, count)
    if fails >= CONTINUOUS_MAX_FAILS:
        set_continuous(False)
        for s_ in range(1, WRITERS + 1):
            state.pop(slot_key(s_), None)
        add_log(state, False, f"연속 작성 자동 정지: {slot}번 실행이 {fails}번 연속으로 끝나지 않음 (사용량 한도·후보 소진·오류 확인 필요)")
        return False
    if writer_busy(settings):
        return True  # 앞 실행이 결과를 올리는 중 → 끝나면 다음 분에 시작
    seen = done_of()
    state[key] = {"fired_at": stamp, "fails": fails, "count": count, "seen": seen}
    save_state(state)  # 실행 전에 저장 — 중간에 죽어도 다음 분에 또 실행하지 않게 (11/5 검토)
    label = f"연속 작성 {count + 1}번째" + (f" ({slot}번 칸)" if WRITERS > 1 else "")
    # API 봇(한 칸)은 칸 번호 없이 — /next 가 후보 전부를 줌 (칸 번호가 있으면 2번 칸 몫을 뺌)
    text = f"slot-{slot}" if WRITERS > 1 and settings.write_mode != "bot" else ""
    ok = fire_routine(state, settings, label, text)
    state[key] = {"fired_at": stamp, "fails": fails if ok else fails + 1, "count": count, "seen": seen}
    return True


CANDIDATES = 40  # 글쓰기 세션에 주는 후보 수 — 20개면 업체·상품을 빼고 나면 남는 게 없어 새 글 없이 끝나는 실행이 생김 (11/6 세 번 연속)
CANDIDATES_POOL = CANDIDATES * 3  # 서버가 준비해 두는 후보 — 한 쪽(40개)을 다 건너뛰면 /next?page=2·3 으로 다음 쪽 (11/6 운영자 '만들어라')


def refresh_dashboard(state: dict, settings: BotSettings) -> None:
    """설정 페이지에 보여줄 창고 현황과 다음 후보 (안전 검사 전 포함)."""
    with connect() as conn:
        state["keywords"] = summary(conn)
        state["candidates"] = ranked_candidates(conn, settings, limit=CANDIDATES_POOL)
    try:  # 대기 글이 많으면 새 글 대신 외부 검수할 글 (bot/review_queue.py)
        from .review_queue import target
        state["review_target"] = target(ahead=len(settings.schedule_times) if settings.post_status == "schedule" else 1)
    except Exception:  # noqa: BLE001 - 못 구하면 새 글 쓰기로
        state["review_target"] = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help="글쓰기 작업 1번 바로 시작 (설정의 방식대로)")
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 이전 실행이 아직 작업 중이면 건너뜀
    except BlockingIOError:
        print("[run] 이전 실행이 아직 진행 중 → 건너뜀", flush=True)
        return

    settings = load_settings()
    state = load_state()
    before = json.dumps(state, sort_keys=True, ensure_ascii=False)
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    # 예약 글 공개 → 글이 발행·수정되면 페이지 캐시 비우기 (앱·관리자 화면에서 발행해도 1분 안에 반영)
    run_due_wp_cron()
    purge_cache_if_changed(state)

    # 관리자 '제외' 반영
    excluded = pop_excluded()
    if excluded:
        with connect() as conn:
            for kw in excluded:
                set_status(conn, kw, "excluded", "관리자 제외")

    daily_review(state, settings, now)

    # 예약 발행: 오늘의 공개 시각(설정 ± 랜덤)마다 가장 오래된 대기 초안 1편 공개
    if settings.post_status == "schedule":
        plan = schedule.plan_times(settings.schedule_times, settings.jitter_minutes, today)
        done = [t for t in state.get("done", {}).get(today, []) if isinstance(t, str)]
        state["plan"], state["done"] = {today: plan}, {today: done}  # 지난 날짜는 정리 (설정 페이지 '오늘 공개 계획')
        state["plan_from"] = list(settings.schedule_times)  # 이 계획을 만든 설정 시각 — 설정 페이지가 '반영 대기'를 알림 (11/6)
        due = schedule.due_slot(plan, done, now.replace(tzinfo=None))
        if due:
            done.append(due)
            save_state(state)  # 공개 전에 '했음'을 먼저 저장 — 중간에 죽어도 같은 시각을 다시 공개하지 않게 (11/5 검토)
            try:
                msg = schedule.publish_next(now)
                add_log(state, True, f"예약 공개 {due}: {msg or '공개할 초안 없음'}")
            except subprocess.CalledProcessError as e:
                add_log(state, False, f"예약 공개 {due} 실패: {e.stderr or e}")
    else:
        state["plan"], state["done"] = {}, {}

    # 서버 상태 기록 (bot/server_stats.py) — 서버를 올릴지 숫자로 정하려고 (11/6 운영자). 사주데이 응답은 5분마다
    try:
        from . import server_stats
        report = server_stats.record(state, now, check_saju=now.minute % 5 == 0)
        if report:
            add_log(state, True, report)
    except Exception:  # noqa: BLE001 - 기록 실패가 봇을 멈추지 않게
        pass

    # 한도 소진으로 못 만든 대표 사진 채우기 (5분마다, 한도 초기화 후 공개 대기열 글부터)
    if now.minute % 5 == 0:
        try:
            for msg in pending_images.fill((settings.cf_account_id, settings.cf_api_token)):
                add_log(state, True, f"사진 채우기: {msg}")
        except Exception as e:  # noqa: BLE001 - 사진 실패가 글 작성·공개를 막지 않게
            add_log(state, False, f"사진 채우기 실패: {str(e)[:150]}")

    # 이미 올린 대표 사진의 화면용 크기를 WebP 로 (bot/webp.py, 11/6 운영자) — 10분마다 몇 장
    if now.minute % 10 == 3:
        try:
            from . import webp
            line = webp.run()
            if line:
                add_log(state, "WebP 아님" not in line, line)  # 서버가 WebP 를 못 만들면 ❌ (매일 점검이 봄)
        except Exception as e:  # noqa: BLE001 - 사진 변환 실패가 봇을 멈추지 않게
            add_log(state, False, f"사진 WebP 변환 실패: {str(e)[:150]}")

    # 본문에 적힌 공식 사이트 주소를 열어 보고 열리는 것만 링크 가능으로 기록 (bot/official_links.py, 11/6 운영자) — 10분마다 몇 편
    if now.minute % 10 == 5:
        try:
            from . import official_links
            official_links.run()  # 결과는 글 메타에 (성공 줄은 봇 기록에 안 남김 — 10분마다 쌓여 중요한 기록을 밀어냄)
            if now.minute < 10:  # 한 시간마다: 다음 공개 순서 기록 — 공개 전 쉬운 말 다듬기용 (/maintenance publish_next, 11/10)
                try:
                    from .schedule import upcoming
                    state["publish_next"] = {"at": f"{now:%Y-%m-%d %H:%M}", "ids": upcoming(now)}
                    from .schedule import publish_order, ready_drafts  # 대기 글 전체 공개 순서 (11/10 '초안 전부 쉬운 말로' — 이 순서대로 다듬기)
                    state["publish_order_all"] = publish_order(ready_drafts(), now)
                    from .schedule import draft_signals  # 대기 글별 검색량·블로그 글 수·황금지수 (11/11 운영자 질문)
                    state["draft_signals"] = {str(k): v for k, v in draft_signals(state["publish_order_all"]).items()}
                except Exception as e:  # noqa: BLE001
                    add_log(state, False, f"다음 공개 순서 계산 실패: {str(e)[:150]}")
            if now.minute < 10:  # 한 시간마다: 주소 연결 안 됨 보류 다시 열어 보기 (일시 장애로 평생 보류 안 되게, 11/10)
                for line in official_links.recheck_dead():
                    add_log(state, True, f"보류 다시 검사: {line}")
            if state.get("known_watch_date") != today:  # 하루 한 번: 확인된 틀린 문구가 남은 글에 다시 확인 표시 (bot/known_watch.py, 11/10)
                try:
                    from . import known_watch
                    add_log(state, True, known_watch.run())
                    state["known_watch_date"] = today
                except Exception as e:  # noqa: BLE001 - 실패하면 다음 차례에 다시
                    add_log(state, False, f"틀린 문구 감시 실패: {str(e)[:150]}")
            from . import related_posts
            stamp = f"{now:%Y-%m-%d}/v{related_posts.VERSION}"  # 계산 방식이 바뀌면(VERSION) 그날 바로 다시
            if state.get("related_posts_date") != stamp:  # 관련 글·계산기(내용 기준) 하루 한 번 다시 계산 (공개 때도 함)
                try:
                    add_log(state, True, related_posts.run())
                    state["related_posts_date"] = stamp
                except Exception as e:  # noqa: BLE001 - 실패하면 다음 차례에 다시
                    add_log(state, False, f"관련 글 계산 실패: {str(e)[:150]}")
            from . import source_check
            source_check.backfill_reviewed()  # 예전 글의 '검수로 확인한 공식 사이트' 채우기 (글 끝 확인 줄, 다 채우면 할 일 없음)
        except Exception as e:  # noqa: BLE001 - 링크 확인 실패가 봇을 멈추지 않게
            add_log(state, False, f"공식 사이트 링크 확인 실패: {str(e)[:150]}")

    # 근거 기록 없는 공개 대기 글 다시 대조 (bot/hold.missing_evidence) — 10분마다 1편. 배포 때 10편씩 하던 것을 옮김
    # (11/6: 공식 페이지를 여는 일이라 배포 '서버 적용'이 7분 넘게 걸리고, 그동안 다음 배포·글쓰기 끝 처리가 밀림)
    if now.minute % 10 == 7:
        try:
            from . import hold
            for msg in hold.missing_evidence(*hold.ready_without_record(), limit=1):
                add_log(state, True, f"근거 기록 채우기: {msg}")
        except Exception as e:  # noqa: BLE001
            add_log(state, False, f"근거 기록 채우기 실패: {str(e)[:150]}")

    # 매일 점검: 서버 기록 점검 (글쓰기 작업을 점검 모드로 실행, automation/maintenance.md) — 두 방식 모두
    if settings.maint_times and not writer_busy(settings):
        fired = state.get("maint_fired", {}).get(today, [])
        due = schedule.due_slot(settings.maint_times, fired, now.replace(tzinfo=None))
        if due:
            state["maint_fired"] = {today: fired + [due]}
            save_state(state)  # 실행 전에 저장 (중간에 죽으면 매분 다시 실행하던 경로, 11/5 검토)
            fire_routine(state, settings, f"매일 점검 {due}", routine.MAINT_TEXT)

    # 글쓰기: 수동 요청, 연속 작성, 글 작성 시각 → fire_routine (방식은 그 안에서). API 봇이 쓰는 중이면 끝날 때까지 미룸
    # (수동 요청은 지우지 않고 남겨 둠, 시각은 3시간 안에 다시)
    busy = writer_busy(settings)
    if args.now or (not busy and pop_manual_request()):
        fire_routine(state, settings, "수동 요청", routine.MANUAL_TEXT)
    continuous_step(state, settings, now)
    if settings.direct_enabled and not state.get("continuous") and not writer_busy(settings):
        fired = state.get("direct_fired", {}).get(today, [])
        due = schedule.due_slot(settings.direct_times, fired, now.replace(tzinfo=None))
        if due:
            state["direct_fired"] = {today: fired + [due]}  # 지난 날짜 기록은 버림, 실패해도 같은 시각은 다시 안 함
            save_state(state)  # 실행 전에 저장 (11/5 검토)
            fire_routine(state, settings, f"글 작성 {due}")

    if now.minute % 10 == 0:  # 배포 밀림 감지 (bot/deploy_watch.py) — 어떤 오류도 봇을 멈추지 않게
        try:
            from .deploy_watch import run as watch_deploy
            watch_deploy(state, now)
        except Exception:  # noqa: BLE001
            pass

    if excluded or state.get("dashboard_at", "")[:13] != now.strftime("%Y-%m-%d %H"):
        refresh_dashboard(state, settings)  # 변화가 있거나 1시간마다
        state["dashboard_at"] = now.strftime("%Y-%m-%d %H:%M")

    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        save_state(state)


if __name__ == "__main__":
    main()
