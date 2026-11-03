"""오늘의 키워드 수집 (하루 1번, 네이버 키워드 도구 무료 → 매일 최대한 수집).

  1) 출발 단어: 구글 급상승 검색어 + 씨앗 단어 전부(seed_words.py) + 우선 주제
  2) 롱테일 조합: 오늘 나온 큰 키워드 × '방법/신청/조건/...' 의 실제 검색량 확인 (남은 호출 전부)
  3) 정리: 8일 지난 후보는 삭제 (7일 전 검색량과 비교해 상승세 감지용으로만 보관)
     이미 쓴 키워드·부적합·제외 기록은 중복 방지용으로 계속 보관

사용: python -m bot.discover [--calls 60]
"""
from __future__ import annotations

import argparse
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from .keyword_db import connect, norm, summary, upsert
from .naver_ads import ads_client
from .seed_words import SEED_WORDS
from .seeds import google_trends_kr

BATCH = 5              # API 1회당 출발 키워드 수 (API 최대값)
PAUSE_SECONDS = 0.3    # 호출 간격
MODIFIERS = ["방법", "신청", "조건", "기간", "비용", "서류", "계산", "차이", "뜻", "기준",
             "자격", "대상", "순서", "주의사항", "하는법"]
HEAD_MIN_VOLUME = 3000  # 롱테일 조합을 만들 '큰 키워드' 기준
KEEP_DAYS = 8           # 후보 보관 기간 (상승세 비교용)
STAMP_FILE = Path("/var/lib/first-lab/bot/discover-date-v2")  # 마지막 수집 날짜 (하루 1회 보장)


def _modifier_heads(conn, today: str, limit_heads: int) -> list[str]:
    rows = conn.execute(
        "SELECT keyword FROM keywords WHERE last_checked=? AND mod_done=0 AND total>=?"
        " AND status NOT IN ('rejected','excluded') ORDER BY total DESC LIMIT ?",
        (today, HEAD_MIN_VOLUME, limit_heads))
    return [r["keyword"] for r in rows]


def discover(calls: int, extra_seeds: list[str] | None = None) -> dict:
    ads = ads_client()
    stats = {"calls": 0, "new": 0, "errors": 0, "trends": 0}
    today = datetime.now().strftime("%Y-%m-%d")

    def run_batch(conn, batch: list[str]) -> bool:
        """한 묶음 조회. 계속 진행해도 되면 True."""
        for attempt in range(2):
            try:
                stats["new"] += upsert(conn, ads.keyword_stats(batch))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:  # 호출 과다 → 잠시 쉬고 한 번 더
                    time.sleep(30)
                    continue
                stats["errors"] += 1
                if e.code in (401, 403):
                    return False
                break
            except (urllib.error.URLError, TimeoutError):
                stats["errors"] += 1
                break
        stats["calls"] += 1
        time.sleep(PAUSE_SECONDS)
        return True

    with connect() as conn:
        # 1) 출발 단어 전부: 급상승어 + 우선 주제 + 씨앗
        trends = google_trends_kr()
        stats["trends"] = len(trends)
        words = list(dict.fromkeys(trends + (extra_seeds or []) + SEED_WORDS))
        for i in range(0, len(words), BATCH):
            if stats["calls"] >= calls or not run_batch(conn, words[i:i + BATCH]):
                break
        conn.executemany("INSERT OR REPLACE INTO seeds (word, used_date) VALUES (?,?)",
                         [(w, today) for w in words if w in SEED_WORDS])
        conn.commit()
        print(f"[수집] 출발 단어 {len(words)}개 (급상승 {len(trends)}): {stats}", flush=True)

        # 2) 롱테일 조합: 오늘 나온 큰 키워드 × 수식어
        per_head = -(-len(MODIFIERS) // BATCH)
        for head in _modifier_heads(conn, today, max(1, (calls - stats["calls"]) // per_head)):
            if stats["calls"] >= calls:
                break
            combos = [norm(head) + m for m in MODIFIERS if m not in head]
            for i in range(0, len(combos), BATCH):
                if not run_batch(conn, combos[i:i + BATCH]):
                    break
            conn.execute("UPDATE keywords SET mod_done=1 WHERE keyword=?", (head,))
            conn.commit()
        print(f"[수집] 롱테일 조합 완료: {stats}", flush=True)

        # 3) 정리: 오래된 후보 삭제 (쓴 것·부적합·제외 기록은 보관)
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        stats["pruned"] = conn.execute(
            "DELETE FROM keywords WHERE status IN ('new','ok') AND last_checked < ?", (cutoff,)).rowcount
        conn.commit()
        info = summary(conn)
    return {**stats, **info}


def _apply_search_console() -> str:
    from . import search_console

    if not search_console.available():
        return "서치콘솔: 미연동"
    try:
        rows = search_console.fetch()
        with connect() as conn:
            r = search_console.update_signals(conn, rows)
        return (f"서치콘솔: 검색어 {r['rows']}건 → 기회 키워드 {r['opportunity']}, "
                f"잘 되는 주제 {r['good_head']}, 안 되는 주제 {r['bad_head']}")
    except Exception as e:  # noqa: BLE001 - 서치콘솔 오류가 수집을 막지 않게
        return f"서치콘솔 오류: {e}"


def main() -> None:
    """systemd 일일 작업 진입점: 결과를 봇 기록에 남긴다."""
    from .settings import load_settings
    from .state import add_log, load_state, save_state

    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, help="API 호출 수 (생략 시 설정 페이지 값)")
    args = parser.parse_args()
    settings = load_settings()
    started = time.time()
    try:
        r = discover(args.calls or settings.discover_calls, settings.hint_keywords)
        msg = (f"오늘의 키워드 수집: API {r['calls']}회, 급상승어 {r['trends']}개 포함, "
               f"새 키워드 {r['new']:,}개 (보관 {r['total']:,}개, {int(time.time() - started)}초)")
        ok = True
        msg += " / " + _apply_search_console()
    except Exception as e:  # noqa: BLE001
        msg, ok = f"키워드 수집 실패: {e}", False
    print(msg, flush=True)
    STAMP_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))
    state = load_state()  # 수집 중 봇이 바꾼 상태를 덮어쓰지 않도록 다시 읽음
    add_log(state, ok, msg)
    # 발행된 글 갱신 (서치콘솔 반영 직후, 주당 한도 안에서 하루 최대 1개)
    from .refresh import run_weekly

    for line in run_weekly(settings, state):
        add_log(state, "실패" not in line, line)
    state["review_date"] = ""      # 오늘 후보로 사전 검토
    state["dashboard_at"] = ""
    save_state(state)


if __name__ == "__main__":
    main()
