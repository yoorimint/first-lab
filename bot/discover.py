"""키워드 창고 확장 (하루 1회, 정해진 API 호출 수만큼).

사용: python3 -m bot.discover [--calls 50]
"""
from __future__ import annotations

import argparse
import time
import urllib.error

from .keyword_db import connect, summary, upsert
from .naver_ads import ads_client
from .seeds import BOOTSTRAP_SEEDS, BOOTSTRAP_UNTIL, google_trends_kr

BATCH = 5            # API 1회당 출발 키워드 수 (API 최대값)
PAUSE_SECONDS = 0.5  # 호출 간격 (API 부담 줄이기)


def _seed_queue(conn, extra: list[str], need: int) -> list[str]:
    seeds = list(dict.fromkeys(extra + google_trends_kr()))
    if conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0] < BOOTSTRAP_UNTIL:
        seeds += BOOTSTRAP_SEEDS
    rows = conn.execute(
        "SELECT keyword FROM keywords WHERE expanded=0 AND status NOT IN ('rejected','excluded')"
        " ORDER BY total DESC LIMIT ?", (need,),
    )
    seeds += [r["keyword"] for r in rows]
    return list(dict.fromkeys(seeds))[:need]


def discover(calls: int, extra_seeds: list[str] | None = None) -> dict:
    ads = ads_client()
    done_calls = new = 0
    errors: list[str] = []
    with connect() as conn:
        queue = _seed_queue(conn, extra_seeds or [], calls * BATCH)
        for i in range(0, len(queue), BATCH):
            if done_calls >= calls:
                break
            batch = queue[i:i + BATCH]
            try:
                new += upsert(conn, ads.keyword_stats(batch))
            except urllib.error.HTTPError as e:  # 특수문자 등으로 거절된 묶음은 건너뜀
                errors.append(f"{e.code} {','.join(batch)}")
                if e.code in (401, 403, 429):
                    break
            done_calls += 1
            conn.executemany("UPDATE keywords SET expanded=1 WHERE keyword=?", [(k,) for k in batch])
            conn.commit()
            time.sleep(PAUSE_SECONDS)
        info = summary(conn)
    return {"calls": done_calls, "new": new, "errors": errors[:5], **info}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=20)
    print(discover(parser.parse_args().calls))
