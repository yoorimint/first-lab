"""이미 올린 대표 사진의 화면용 크기를 WebP 로 다시 만들기 (11/6 운영자 '워드프레스 꿀팁 2번 해라').

새 사진은 올릴 때 워드프레스가 WebP 로 만든다 (wordpress/mu-plugins/first-lab-webp.php). 그 전에 올린 사진은 화면용 크기가
JPG 라, 봇이 10분마다 BATCH 장씩 다시 만든다 (원본·기존 파일은 지우지 않음 — 서버 데이터는 확실하지 않으면 안 지움).
한 번 시도한 사진은 표시를 남겨 다시 하지 않는다 (서버가 WebP 를 못 만들어도 같은 사진을 끝없이 다시 하지 않게).
"""
from __future__ import annotations

import json

from .settings import wp_cli

BATCH = 5
TRIED_META = "_firstlab_webp_tried"

# 공개·대기 글의 대표 사진 중 화면용 크기에 글 머리 크기(firstlab-hero)가 없고 아직 시도 안 한 것 BATCH 장
_PHP = r"""
require_once ABSPATH . 'wp-admin/includes/image.php';
global $wpdb;
$ids = $wpdb->get_col( "SELECT DISTINCT pm.meta_value FROM {$wpdb->postmeta} pm JOIN {$wpdb->posts} p ON p.ID = pm.post_id
  WHERE pm.meta_key = '_thumbnail_id' AND p.post_type = 'post' AND p.post_status IN ('publish','draft','future') ORDER BY p.post_date DESC" );
$done = array(); $left = 0;
foreach ( $ids as $id ) {
  $id = (int) $id;
  $meta = wp_get_attachment_metadata( $id );
  if ( ! $id || get_post_meta( $id, '_firstlab_webp_tried', true ) || isset( $meta['sizes']['firstlab-hero'] ) ) { continue; }
  if ( count( $done ) >= BATCH ) { $left++; continue; }
  $file = get_attached_file( $id );
  update_post_meta( $id, '_firstlab_webp_tried', gmdate( 'Y-m-d' ) );
  if ( ! $file || ! file_exists( $file ) ) { $done[] = array( $id, 'no-file' ); continue; }
  $new = wp_generate_attachment_metadata( $id, $file );
  if ( $new ) { wp_update_attachment_metadata( $id, $new ); }
  $mime = $new['sizes']['firstlab-hero']['mime-type'] ?? ( $new['sizes']['large']['mime-type'] ?? '' );
  $done[] = array( $id, $mime ?: 'none' );
}
echo wp_json_encode( array( 'done' => $done, 'left' => $left ) );
"""


def summary(got: dict) -> str | None:
    """봇 기록 한 줄 (한 장도 안 했으면 None)."""
    done = got.get("done") or []
    if not done:
        return None
    webp = sum(1 for _, mime in done if mime == "image/webp")
    other = [f"{i}({m})" for i, m in done if m != "image/webp"]
    line = f"사진 WebP 변환: {webp}/{len(done)}장, 남은 {int(got.get('left', 0))}장"
    return line + (f" — WebP 아님: {', '.join(other)} (서버 사진 편집기 확인)" if other else "")


def run(batch: int = BATCH, wp=None) -> str | None:
    wp = wp or wp_cli
    got = json.loads(wp("eval", _PHP.replace("BATCH", str(int(batch)))) or "{}")
    return summary(got if isinstance(got, dict) else {})
