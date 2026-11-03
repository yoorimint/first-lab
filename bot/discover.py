"""키워드 창고 대규모 확장 (네이버 키워드 도구, 무료).

한 번 실행에 정해진 API 호출 수를 아래 3가지에 나눠 쓴다.
  1) 씨앗 단어 (seed_words.py)      : 분야 가리지 않는 일상 단어 → 연관 키워드
  2) 눈덩이 확장                    : 창고 키워드를 다시 넣어 계속 넓힘
  3) 롱테일 조합 탐색                : 큰 키워드 + '방법/신청/조건/...' 조합의 실제 검색량 확인
  + 구글 급상승 검색어

사용: python -m bot.discover --calls 1000
"""
from __future__ import annotations

import argparse
import time
import urllib.error
from datetime import datetime

from .keyword_db import connect, norm, summary, upsert
from .naver_ads import ads_client
from .seed_words import SEED_WORDS
from .seeds import google_trends_kr

BATCH = 5              # API 1회당 출발 키워드 수 (API 최대값)
PAUSE_SECONDS = 0.3    # 호출 간격
SHARE = {"seed": 0.25, "snowball": 0.40, "modifier": 0.35}  # 호출 수 배분
MODIFIERS = ["방법", "신청", "조건", "기간", "비용", "서류", "계산", "차이", "뜻", "기준",
             "자격", "대상", "순서", "주의사항", "하는법"]
HEAD_MIN_VOLUME = 3000  # 롱테일 조합을 만들 '큰 키워드' 기준


def _seed_words(conn, limit: int) -> list[str]:
    used = {r["word"] for r in conn.execute("SELECT word FROM seeds")}
    words = list(dict.fromkeys(google_trends_kr() + [w for w in SEED_WORDS if w not in used]))
    if len(words) < limit:  # 다 쓴 씨앗은 오래된 순으로 재사용 (검색량 갱신)
        words += [r["word"] for r in conn.execute("SELECT word FROM seeds ORDER BY used_date LIMIT ?", (limit,))]
    return words[:limit]


def _snowball(conn, limit: int) -> list[str]:
    rows = conn.execute(
        "SELECT keyword FROM keywords WHERE expanded=0 AND status NOT IN ('rejected','excluded')"
        " ORDER BY total DESC LIMIT ?", (limit,))
    return [r["keyword"] for r in rows]


def _modifier_heads(conn, limit_heads: int) -> list[str]:
    rows = conn.execute(
        "SELECT keyword FROM keywords WHERE mod_done=0 AND total>=? AND status NOT IN ('rejected','excluded')"
        " ORDER BY total DESC LIMIT ?", (HEAD_MIN_VOLUME, limit_heads))
    return [r["keyword"] for r in rows]


def discover(calls: int, extra_seeds: list[str] | None = None) -> dict:
    ads = ads_client()
    stats = {"calls": 0, "new": 0, "errors": 0}
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
        plan = {k: int(calls * v) for k, v in SHARE.items()}

        # 1) 씨앗 단어 (+ 힌트, 급상승어)
        words = list(dict.fromkeys((extra_seeds or []) + _seed_words(conn, plan["seed"] * BATCH)))
        for i in range(0, len(words), BATCH):
            if stats["calls"] >= plan["seed"] or not run_batch(conn, words[i:i + BATCH]):
                break
            conn.executemany("INSERT OR REPLACE INTO seeds (word, used_date) VALUES (?,?)",
                             [(w, today) for w in words[i:i + BATCH]])
            conn.commit()
        print(f"[수집] 씨앗 단계 완료: {stats}", flush=True)

        # 2) 눈덩이 확장
        limit = plan["seed"] + plan["snowball"]
        while stats["calls"] < limit:
            batch = _snowball(conn, BATCH)
            if not batch or not run_batch(conn, batch):
                break
            conn.executemany("UPDATE keywords SET expanded=1 WHERE keyword=?", [(k,) for k in batch])
            conn.commit()
        print(f"[수집] 눈덩이 단계 완료: {stats}", flush=True)

        # 3) 롱테일 조합: 큰 키워드 × 수식어 → 실제 검색량이 있는 조합만 창고에 들어옴
        per_head = -(-len(MODIFIERS) // BATCH)  # 키워드 1개당 호출 수
        for head in _modifier_heads(conn, max(1, (calls - stats["calls"]) // per_head)):
            if stats["calls"] >= calls:
                break
            combos = [norm(head) + m for m in MODIFIERS if m not in head]
            for i in range(0, len(combos), BATCH):
                if not run_batch(conn, combos[i:i + BATCH]):
                    break
            conn.execute("UPDATE keywords SET mod_done=1 WHERE keyword=?", (head,))
            conn.commit()
        print(f"[수집] 롱테일 조합 단계 완료: {stats}", flush=True)

        info = summary(conn)
    return {**stats, **info}


def main() -> None:
    """systemd 일일 작업 진입점: 설정 페이지의 수집량을 사용하고 결과를 봇 기록에 남긴다."""
    from .settings import load_settings
    from .state import add_log, load_state, save_state

    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, help="API 호출 수 (생략 시 설정 페이지 값)")
    args = parser.parse_args()
    settings = load_settings()
    calls = args.calls or settings.discover_calls
    started = time.time()
    try:
        r = discover(calls, settings.hint_keywords)
        msg = (f"키워드 수집: API {r['calls']}회, 새 키워드 {r['new']:,}개, 오류 {r['errors']}회 "
               f"(창고 {r['total']:,}개, {int(time.time() - started) // 60}분)")
        ok = True
    except Exception as e:  # noqa: BLE001
        msg, ok = f"키워드 수집 실패: {e}", False
    print(msg, flush=True)
    state = load_state()  # 수집 중 봇이 바꾼 상태를 덮어쓰지 않도록 다시 읽음
    add_log(state, ok, msg)
    state["review_date"] = ""      # 새 키워드로 후보 재검토
    state["dashboard_at"] = ""
    save_state(state)


if __name__ == "__main__":
    main()
