"""관련 글은 제목이 아니라 글 내용으로 (11/9 운영자 '제목으로 판단할 게 아니고 글 내용으로 판단해야 되는 거 아냐?').

제목 글자만 비교하니 'ITQ 자격증 조회 … 발급비' 글에 '건물 등기부등본 발급' 글이 붙었다 ('발급' 하나 겹침).
이제 본문 전체를 낱말 조각(한글 두 글자씩·영문 낱말)으로 나눠, 흔한 말은 가볍게(여러 글에 다 나오는 말일수록 점수 작게, TF-IDF)
두 글의 내용이 얼마나 같은지(코사인 유사도)를 잰다. MIN_SIMILARITY 를 넘는 글만, 높은 순으로 TOP 개.

서버가 공개 글 전체를 다시 계산해 글마다 메타(META)에 저장 — 하루 한 번 + 글이 공개될 때 (새 글이 예전 글의 관련 글이 되게).
화면(first-lab-related.php·first-lab-flow.php)은 이 기록만 쓴다. 기록이 없으면 관련 글을 안 보인다 (엉뚱한 글보다 없는 게 낫다).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter

META = "_firstlab_related_ids"
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
    docs = {int(p["id"]): terms(p.get("title", ""), p.get("content", "")) for p in posts}
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


_POSTS_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_type' => 'post', 'post_status' => 'publish', 'numberposts' => -1 ) ) as $p ) {
  $out[] = array( 'id' => $p->ID, 'title' => $p->post_title, 'content' => $p->post_content );
}
echo wp_json_encode( $out );
"""


def run(wp=None) -> str:
    """공개 글 전체의 관련 글을 다시 계산해 메타에 저장 (바뀐 글만 씀). 봇 기록 한 줄."""
    if wp is None:
        from .settings import wp_cli as wp
    posts = json.loads(wp("eval", _POSTS_PHP) or "[]")
    result = related(posts)
    stamp = hashlib.md5(json.dumps(sorted((p["id"], len(p.get("content", ""))) for p in posts)).encode()).hexdigest()[:8]
    changed = 0
    for pid, ids in result.items():
        record = json.dumps({"v": VERSION, "ids": ids, "of": stamp}, ensure_ascii=False)
        try:
            old = wp("post", "meta", "get", str(pid), META).strip()
        except Exception:  # noqa: BLE001 - 메타가 없으면 오류로 끝남
            old = ""
        if json.loads(old or "{}").get("ids") != ids or not old:
            wp("post", "meta", "update", str(pid), META, record)
            changed += 1
    linked = sum(1 for ids in result.values() if ids)
    return f"관련 글(내용 기준) 다시 계산: 공개 {len(posts)}편 중 {linked}편에 관련 글, 바뀐 기록 {changed}편"
