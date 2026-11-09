<?php
/**
 * Plugin Name: First Lab - 함께 보면 좋은 글
 * Description: 글 하단에 주제가 겹치는 다른 공개 글을 자동으로 연결 (새 글이 생기면 예전 글에도 자동 반영). 겹치는 글이 없으면 안 보임.
 *
 * 11/6 운영자 '자동차보험료 계산 글에 함께 보면 좋은 글이 왜 주식 평단가 계산기지?': 예전엔 같은 분류의 글을 제목 글자 비슷한 순으로 4개 채움
 * → 분류(금융·세금)에 공개 글이 2편뿐이라 '계산'만 겹치는 주식 글이 붙음.
 * 11/9 운영자: 제목이 아니라 글 내용으로 — 관련 글은 서버(bot/related_posts.py)가 본문 전체를 비교해 남긴 기록만 씀.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_RELATED_COUNT = 4;

/** 기록된 관련 글 id (JSON {"ids":[…]}) 중 지금 공개 상태인 것. */
function firstlab_related_from_meta( string $raw, callable $is_public ): array {
	$m = json_decode( $raw, true );
	return array_values( array_filter( array_map( 'intval', (array) ( $m['ids'] ?? array() ) ), $is_public ) );
}

/** 공개 글 중 이 글과 내용이 비슷한 글 id (비슷한 순). 본문 중간 연결(first-lab-flow.php)과 하단 목록이 같이 씀.
 * 11/9 운영자 '제목으로 판단할 게 아니고 글 내용으로' — 서버(bot/related_posts.py)가 본문 전체를 비교해 메타에 저장한 것만 씀.
 * 기록이 없으면 관련 글 없음 (제목 글자로 짐작해 엉뚱한 글을 붙이지 않음). */
function firstlab_related_ids( int $post_id, int $count = FIRSTLAB_RELATED_COUNT + 1 ): array {
	static $cache = array();
	if ( ! isset( $cache[ $post_id ] ) ) {
		$cache[ $post_id ] = firstlab_related_from_meta(
			(string) get_post_meta( $post_id, '_firstlab_related_ids', true ),
			static fn( $id ) => $id > 0 && 'publish' === get_post_status( $id )
		);
	}
	return array_slice( $cache[ $post_id ], 0, $count );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		// 본문 중간 '이어서 보면 좋은 글'에 이미 보여 준 글은 빼고 4개 (first-lab-flow.php)
		$shown = $GLOBALS['firstlab_flow_shown'] ?? array();
		$ids   = array_slice( array_values( array_diff( firstlab_related_ids( get_the_ID() ), $shown ) ), 0, FIRSTLAB_RELATED_COUNT );
		if ( ! $ids ) {
			return $content; // 관련 글이 없으면 엉뚱한 글을 채우지 않음
		}
		$items = '';
		foreach ( $ids as $id ) {
			$items .= sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $id ) ), esc_html( get_the_title( $id ) ) );
		}
		return $content . '<section class="firstlab-related"><h2>함께 보면 좋은 글</h2><ul>' . $items . '</ul></section>';
	},
	20
);
