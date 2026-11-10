"""글 본문·메타를 한 번에 읽기 (배포 시간 단축).

wp-cli 는 호출 한 번에 0.5초쯤 걸린다. 글마다 따로 부르면 글·수정이 늘수록 배포가 길어져
11/5 오후 배포가 10분 제한을 넘겨 계속 실패했다 (보정 5분 + 사진 정리 3분). 한 번의 호출로 모아 읽는다.
"""
from __future__ import annotations

import json

from .settings import wp_cli

META_KEYS = ("_thumbnail_id", "_firstlab_card", "_firstlab_photo", "_firstlab_img_pending", "_firstlab_img_checked",
             "_firstlab_patches_upto", "_firstlab_refreshed")

_PHP = r"""
$out = array();
foreach ( get_posts( array( 'post_type' => 'post', 'post_status' => 'any', 'numberposts' => -1 ) ) as $p ) {
    $meta = array();
    foreach ( %s as $k ) { $meta[ $k ] = (string) get_post_meta( $p->ID, $k, true ); }
    $out[ $p->ID ] = array( 'content' => $p->post_content, 'title' => $p->post_title, 'excerpt' => $p->post_excerpt,
                            'name' => $p->post_name, 'status' => $p->post_status, 'date' => $p->post_date, 'meta' => $meta );
}
echo wp_json_encode( $out );
"""


def posts() -> dict[str, dict]:
    """글 번호(문자열) → {content, title, excerpt, name, status, meta{…}} (휴지통 제외 모든 상태)."""
    keys = "array(" + ", ".join(f"'{k}'" for k in META_KEYS) + ")"
    data = json.loads(wp_cli("eval", _PHP % keys) or "{}")
    return {str(k): v for k, v in (data.items() if isinstance(data, dict) else [])}
