"""외부 검수가 필요한 글 찾기 (감시 세션용, automation/monitor.md).

서버의 공개 대기 글·보류 초안 전체를 글 번호로 automation/reviewed.txt 와 대조한다.
시각으로 '새 글'을 찾으면 경계에 걸린 글이 빠진다 (11/4 #163 토지 실거래가가 빠졌던 일).
출력: 글 번호, 제목, 검수자에게 줄 본문 파일 — 서버의 지금 본문(patches·보류 수정 반영)을 받아 OUT_DIR 에 저장.
계산기도 같이 본다: 글에 들어간 계산기 이름, 그리고 reviewed.txt 의 'calc:<이름>' 줄에 없는 계산기 전부
(11/5 공인중개사 점수 계산기가 글 검수 뒤에 따로 추가돼 검수에서 빠졌던 일).

사용: FIRSTLAB_API_TOKEN=... python3 automation/unreviewed.py [글번호 ...]
  글 번호를 주면 reviewed.txt 와 상관없이 그 글들의 본문만 받는다 (다시 검수할 때).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEWED = ROOT / "automation" / "reviewed.txt"
BASE = "https://first-lab.kr/wp-json/firstlab/v1"
OUT_DIR = Path(tempfile.gettempdir()) / "firstlab-review"  # 검수자에게 줄 본문 (저장소 밖)
CALC_DIR = ROOT / "wordpress" / "themes" / "first-lab" / "calculators"
CALC_TAG = re.compile(r'\[firstlab_calc id=\\?"([a-z0-9-]+)\\?"')  # 본문 JSON 안에서는 따옴표가 \" 로 저장됨


def _heads() -> list[str]:
    """reviewed.txt 각 줄의 첫 낱말 (설명 줄 제외)."""
    lines = REVIEWED.read_text(encoding="utf-8").splitlines()
    return [line.split()[0] for line in lines if line.strip() and not line.startswith("#")]


def reviewed_ids() -> set[int]:
    """reviewed.txt 의 글 번호 (줄 맨 앞 숫자)."""
    return {int(h) for h in _heads() if h.isdigit()}


def reviewed_calcs() -> set[str]:
    """reviewed.txt 의 'calc:<이름>' 줄 = 검수 마친 계산기."""
    return {h[5:] for h in _heads() if h.startswith("calc:")}


def all_calcs() -> list[str]:
    return sorted(p.stem for p in (CALC_DIR / "defs").glob("*.js"))


def calc_files(calc_id: str) -> str:
    """검수자에게 줄 계산기 파일 (defs·data·tests 중 있는 것)."""
    paths = [CALC_DIR / sub / f"{calc_id}{ext}" for sub, ext in (("defs", ".js"), ("data", ".json"), ("tests", ".json"))]
    return " ".join(str(p) for p in paths if p.exists())


def embedded_calcs(text: str) -> list[str]:
    return sorted(set(CALC_TAG.findall(text)))


def _get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={"X-Firstlab-Token": os.environ["FIRSTLAB_API_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def cited_urls(post: dict) -> list[str]:
    """글쓴이가 근거로 단 주소 (서버 근거 대조 기록에서). 검수자가 '확인불가'로 두기 전에 열어 볼 곳
    (11/5 #775: 검수자가 글의 근거인 고시를 안 열고 19건을 확인불가로 — 모두 그 고시에 있었음)."""
    check = post.get("source_check") or {}
    urls = [u for c in check.get("found", []) + check.get("claims", []) for u in c.get("urls", [])]
    return list(dict.fromkeys(str(u) for u in urls))


def save_body(post_id: int) -> Path:
    """서버의 지금 본문을 JSON 파일로 (title, excerpt, content_html, cited_urls)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"post-{post_id}.json"
    post = _get(f"/post/{post_id}")
    post["cited_urls"] = cited_urls(post)
    path.write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    if len(sys.argv) > 1:
        todo = [{"post_id": int(x), "title": ""} for x in sys.argv[1:]]
    else:
        state = _get("/maintenance")
        done = reviewed_ids()
        posts = state.get("ready_posts", []) + state.get("held_drafts", [])
        todo = [p for p in posts if int(p["post_id"]) not in done]
        print(f"# 검수 필요 {len(todo)}편 / 대기·보류 {len(posts)}편")
    for p in todo:
        path = save_body(int(p["post_id"]))
        raw = path.read_text(encoding="utf-8")
        title = p.get("title") or json.loads(raw)["title"]
        print(f"{p['post_id']}\t{title}\t{path}")
        for calc_id in embedded_calcs(raw):
            print(f"  계산기 {calc_id}: {calc_files(calc_id)}")
        check = json.loads(raw).get("source_check") or {}
        if check.get("counts"):  # 서버 근거 대조(기록만, bot/source_check.py) — 외부 검수 결과와 비교해 정확도를 잰다
            missing = [c["claim"][:40] for c in check.get("claims", []) if c.get("status") == "페이지에 없음"]
            print(f"  근거 대조 {check['counts']}" + (f" 없음: {' / '.join(missing)}" if missing else ""))
    todo_calcs = [c for c in all_calcs() if c not in reviewed_calcs()]
    print(f"# 계산기 검수 필요 {len(todo_calcs)}개" + "".join(f"\n{c}\t{calc_files(c)}" for c in todo_calcs))


if __name__ == "__main__":
    main()
