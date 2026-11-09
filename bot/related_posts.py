"""관련 글은 제목이 아니라 글 내용으로 (11/9 운영자 '제목으로 판단할 게 아니고 글 내용으로 판단해야 되는 거 아냐?').

제목 글자만 비교하니 'ITQ 자격증 조회 … 발급비' 글에 '건물 등기부등본 발급' 글이 붙었다 ('발급' 하나 겹침).
이제 본문 전체를 낱말 조각(한글 두 글자씩·영문 낱말)으로 나눠, 흔한 말은 가볍게(여러 글에 다 나오는 말일수록 점수 작게, TF-IDF)
두 글의 내용이 얼마나 같은지(코사인 유사도)를 잰다. MIN_SIMILARITY 를 넘는 글만, 높은 순으로 TOP 개.

서버가 공개 글 전체를 다시 계산해 글마다 메타(META)에 저장 — 하루 한 번 + 글이 공개될 때 (새 글이 예전 글의 관련 글이 되게).
화면(first-lab-related.php·first-lab-flow.php)은 이 기록만 쓴다. 기록이 없으면 관련 글을 안 보인다 (엉뚱한 글보다 없는 게 낫다).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

META = "_firstlab_related_ids"
CALC_META = "_firstlab_flow_calc"   # 본문 중간 '이어서 보면 좋은 글'에 붙일 계산기 id (본문에 계산기가 없는 글만)
CALC_MIN_SIMILARITY = 0.2          # 공개 대기 글 337편 시험: 0.2 이상은 맞는 계산기(부가세 신고→부가가치세), 아래는 엉뚱함
CALC_DIR = Path(__file__).resolve().parent.parent / "wordpress" / "themes" / "first-lab" / "calculators"
VERSION = 1
TOP = 5
MIN_SIMILARITY = 0.12   # 이보다 낮으면 관련 없음 (공개 대기 글 337편으로 맞춤 — tests 의 사례)
SECTION_CUT = ("공식 안내 바로가기", "자주 묻는 질문")  # 글 끝 링크 목록·FAQ 는 형식이 비슷해 내용 비교에서 뺌

_TAG = re.compile(r"<[^>]+>")
_KO = re.compile(r"[가-힣]{2,}")
_EN = re.compile(r"[A-Za-z][A-Za-z0-9]{1,}")


def text_of(html: str) -> str:
    body = html or ""
    for mark in SECTION_CUT:
        i = body.find(mark)
        if i > 0:
            body = body[:i]
    return _TAG.sub(" ", body)


def terms(title: str, html: str) -> Counter:
    """낱말 조각 개수. 제목은 두 번 셈 (글의 중심 주제)."""
    out: Counter = Counter()
    for text, weight in ((title or "", 2), (text_of(html), 1)):
        for w in _KO.findall(text):
            for i in range(len(w) - 1):
                out[w[i:i + 2]] += weight
        for w in _EN.findall(text):
            out[w.lower()] += weight
    return out


def vectors(posts: list[dict]) -> dict[int, dict[str, float]]:
    """{글 id: {조각: 가중치}} — TF(로그) × IDF, 길이 1로 맞춤."""
    docs = {p["id"]: terms(p.get("title", ""), p.get("content", "")) for p in posts}
    n = len(docs) or 1
    df: Counter = Counter()
    for c in docs.values():
        df.update(c.keys())
    out = {}
    for pid, c in docs.items():
        v = {t: (1 + math.log(k)) * math.log((1 + n) / (1 + df[t])) for t, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out[pid] = {t: x / norm for t, x in v.items() if x > 0}
    return out


def similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def related(posts: list[dict], top: int = TOP, minimum: float = MIN_SIMILARITY) -> dict[int, list[int]]:
    """{글 id: [관련 글 id, …]} (유사도 높은 순, minimum 넘는 것만)."""
    vec = vectors(posts)
    out = {}
    for pid, v in vec.items():
        scored = sorted(((similarity(v, w), qid) for qid, w in vec.items() if qid != pid), reverse=True)
        out[pid] = [qid for s, qid in scored[:top] if s >= minimum]
    return out


_EMBED = re.compile(r'\[firstlab_calc id="([^"]+)"')


def calc_docs(calc_dir: Path = CALC_DIR, ids: set[str] | None = None) -> list[dict]:
    """계산기마다 내용 문서: 이름·입력 칸 이름·도움말·안내 문구 + 근거 이름 (defs/<id>.js, data/<id>.json)."""
    out = []
    for f in sorted((calc_dir / "defs").glob("*.js")):
        if ids is not None and f.stem not in ids:
            continue
        js = f.read_text(encoding="utf-8")
        title = re.search(r"title:\s*'([^']+)'", js)
        text = " ".join(re.findall(r"(?:label|help|title|description|note)\s*:\s*'([^']*)'", js))
        data = calc_dir / "data" / f"{f.stem}.json"
        if data.exists():
            try:
                text += " " + " ".join(s.get("name", "") for s in json.loads(data.read_text(encoding="utf-8")).get("sources", []))
            except ValueError:
                pass
        out.append({"id": "calc:" + f.stem, "title": title.group(1) if title else f.stem, "content": text})
    return out


def flow_calcs(posts: list[dict], calcs: list[dict], minimum: float = CALC_MIN_SIMILARITY) -> dict:
    """{글 id: 계산기 id 또는 ''} — 본문에 계산기가 이미 있는 글은 ''. 내용이 가장 비슷한 계산기 (minimum 이상만)."""
    vec = vectors(posts + calcs)
    out = {}
    for p in posts:
        if _EMBED.search(p.get("content", "")):
            out[p["id"]] = ""
            continue
        best = max(((similarity(vec[p["id"]], vec[c["id"]]), c["id"]) for c in calcs), default=(0.0, ""))
        out[p["id"]] = best[1][5:] if best[0] >= minimum else ""
    return out


_POSTS_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_type' => 'post', 'post_status' => 'publish', 'numberposts' => -1 ) ) as $p ) {
  $out[] = array( 'id' => $p->ID, 'title' => $p->post_title, 'content' => $p->post_content );
}
$calcs = array();
foreach ( get_pages( array( 'post_status' => 'publish' ) ) as $p ) {
  if ( str_ends_with( urldecode( $p->post_name ), '-calculator' ) ) { $calcs[] = substr( urldecode( $p->post_name ), 0, -11 ); }
}
echo wp_json_encode( array( 'posts' => $out, 'calcs' => $calcs ) );
"""


def _save(wp, pid, key: str, value: str) -> bool:
    try:
        old = wp("post", "meta", "get", str(pid), key).strip()
    except Exception:  # noqa: BLE001 - 메타가 없으면 오류로 끝남
        old = None
    if old != value:
        wp("post", "meta", "update", str(pid), key, value)
        return True
    return False


def run(wp=None) -> str:
    """공개 글 전체의 관련 글을 다시 계산해 메타에 저장 (바뀐 글만 씀). 봇 기록 한 줄."""
    if wp is None:
        from .settings import wp_cli as wp
    got = json.loads(wp("eval", _POSTS_PHP) or "{}")
    posts, live_calcs = got.get("posts", []), set(got.get("calcs", []))
    result = related(posts)
    calc_of = flow_calcs(posts, calc_docs(ids=live_calcs)) if live_calcs else {p["id"]: "" for p in posts}
    changed = 0
    for pid, ids in result.items():
        changed += _save(wp, pid, META, json.dumps({"v": VERSION, "ids": ids}, ensure_ascii=False))
        changed += _save(wp, pid, CALC_META, calc_of.get(pid, ""))
    linked = sum(1 for ids in result.values() if ids)
    with_calc = sum(1 for c in calc_of.values() if c)
    return f"관련 글·계산기(내용 기준) 다시 계산: 공개 {len(posts)}편 중 관련 글 {linked}편·계산기 {with_calc}편, 바뀐 기록 {changed}개"
