"""검수 정확도 표본 감사 (주 1회, 감시 세션용): 검수를 마친 글의 '지금 본문'에서 숫자가 든 문장을 무작위로 뽑는다.

목적: 외부 검수 + 내 최종 확인을 거친 뒤에도 남은 오류 비율을 잰다 (정답지가 없어서 표본으로).
뽑은 문장을 검수 결과를 모르는 새 검수자(model opus)가 원문을 열어 다시 확인하고, 나도 근거 원문을 직접 연다.
결과는 automation/audit-log.md 에 남긴다 (날짜, 표본 수, 맞음/틀림/확인불가, 틀린 문장과 고친 패치).

사용: FIRSTLAB_API_TOKEN=... python3 automation/audit_sample.py [표본 수=10] [시드=오늘 날짜]
출력: 글 번호, 제목, 문장 (검수자에게 그대로 줌). 같은 시드면 같은 표본.
"""
from __future__ import annotations

import datetime as dt
import html
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unreviewed import _get, reviewed_ids  # noqa: E402

FACT = re.compile(r"(?<![가-힣A-Za-z\d])\d")  # 숫자가 든 문장 = 독자가 믿고 쓸 사실 ('실손24'·'고용24' 같은 이름 속 숫자는 제외)
MIN_LEN, MAX_LEN = 20, 300


def sentences(content_html: str) -> list[str]:
    """본문 문단·목록·표 줄을 문장으로 (계산기 단축코드·글 끝 공식 링크 제외)."""
    body = content_html.split("공식 안내 바로가기")[0]
    body = re.sub(r"\[firstlab_calc[^\]]*\]", " ", body)
    out = []
    for block in re.findall(r"<(?:p|li|tr)[^>]*>(.*?)</(?:p|li|tr)>", body, flags=re.S):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", block))).strip()
        for s in re.split(r"(?<=요\.)\s+|(?<=다\.)\s+", text):
            if FACT.search(s) and MIN_LEN <= len(s) <= MAX_LEN:
                out.append(s)
    return out


def sample(posts: dict[int, dict], size: int, seed: str) -> list[tuple[int, str, str]]:
    """글마다 한 문장씩, 글 순서를 섞어 size 개 (한 글에 몰리지 않게)."""
    rng = random.Random(seed)
    ids = sorted(posts)
    rng.shuffle(ids)
    picked = []
    for pid in ids:
        cands = sentences(posts[pid]["content_html"])
        if cands:
            picked.append((pid, posts[pid]["title"], rng.choice(cands)))
        if len(picked) == size:
            break
    return picked


def main() -> None:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    seed = sys.argv[2] if len(sys.argv) > 2 else dt.date.today().isoformat()
    posts = {}
    for pid in sorted(reviewed_ids()):
        try:
            post = _get(f"/post/{pid}")
        except Exception:  # noqa: BLE001 — 지운 글
            continue
        if post.get("status") in ("publish", "draft", "future"):
            posts[pid] = post
    print(f"# 표본 {size}개 (시드 {seed}, 검수 마친 글 {len(posts)}편에서)")
    for pid, title, sent in sample(posts, size, seed):
        print(f"{pid}\t{title}\t{sent}")


if __name__ == "__main__":
    main()
