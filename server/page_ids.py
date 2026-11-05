"""고정 페이지 찾기 (wp-setup.sh 가 부름): 저장소 페이지 주소(slug) → 남길 ID 하나 + 중복 ID들.

wp post list --name=<slug> 는 초안을 못 찾는다(워드프레스가 비공개 글의 주소 조회를 막음) → 초안 계산기 페이지를
공개할 때 새 페이지를 또 만들어 주소가 '<slug>-2' 가 됨 (11/5 school-grade-calculator-2).
그래서 모든 페이지 목록(csv: ID,post_name,post_status)에서 직접 고르고, 같은 주소의 중복(<slug>, <slug>-2 …)은
공개된 것(없으면 가장 오래된 것)만 남긴다. 다른 저장소 페이지 주소는 건드리지 않는다.
사용: wp post list ... --format=csv | python3 page_ids.py <slug> <저장소 페이지 주소들...>  → "남길ID 중복ID…" (없으면 빈 줄)
"""
from __future__ import annotations

import csv
import io
import re
import sys


def pick(csv_text: str, slug: str, repo_slugs: set[str] | None = None) -> tuple[str, list[str]]:
    rows = list(csv.DictReader(io.StringIO(csv_text or "")))
    other = (repo_slugs or set()) - {slug}
    same = [r for r in rows if r.get("post_name") not in other
            and re.fullmatch(rf"{re.escape(slug)}(?:-\d+)?", r.get("post_name") or "")]
    if not same:
        return "", []
    same.sort(key=lambda r: (r.get("post_status") != "publish", r.get("post_name") != slug, int(r["ID"])))
    return same[0]["ID"], [r["ID"] for r in same[1:]]


if __name__ == "__main__":
    keep, extras = pick(sys.stdin.read(), sys.argv[1], set(sys.argv[2:]))
    print(" ".join([keep, *extras]).strip())
