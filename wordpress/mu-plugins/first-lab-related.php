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
const FIRSTLAB_SIBLING_COUNT = 4;   // 같은 분류의 다른 글 (11/15)

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

/**
 * 같은 분류 글 목록(공개 날짜 순)에서 이 글의 앞뒤 글을 번갈아 n 개 (끝에 닿으면 반대쪽에서 더).
 * 11/15: 구글이 '읽어 갔지만 색인 안 함'으로 뺀 글 7편이 모두 사이트 안에서 들어오는 링크가 1~5개 (서치콘솔 '참조 페이지 없음')
 * → 글마다 같은 분류의 가까운 글로 이어 주면, 오래된 글도 이웃 글 n 편에서 링크를 받는다 (앞 글만 몰리지 않게 앞뒤 고르게).
 */
function firstlab_category_neighbors( array $ids_by_date, int $current, int $n, array $skip = array() ): array {
	$pos = array_search( $current, $ids_by_date, true );
	if ( false === $pos ) {
		return array();
	}
	$len = count( $ids_by_date );
	$out = array();
	for ( $k = 1; $k < $len && count( $out ) < $n; $k++ ) {   // 처음·끝 글도 고르게 받게 목록을 원형으로 (끝 다음 = 처음)
		foreach ( array( $ids_by_date[ ( $pos - $k + $len ) % $len ], $ids_by_date[ ( $pos + $k ) % $len ] ) as $id ) {
			if ( $id !== $current && ! in_array( $id, $out, true ) && ! in_array( $id, $skip, true ) && count( $out ) < $n ) {
				$out[] = $id;
			}
		}
	}
	return $out;
}

/** 이 글의 첫 분류와, 그 분류의 공개 글 id (공개 날짜 순). */
function firstlab_category_siblings( int $post_id ): array {
	$cats = get_the_category( $post_id );
	if ( ! $cats ) {
		return array( null, array() );
	}
	$ids = get_posts(
		array(
			'post_type'   => 'post',
			'post_status' => 'publish',
			'category'    => $cats[0]->term_id,
			'numberposts' => 200,
			'orderby'     => 'date',
			'order'       => 'ASC',
			'fields'      => 'ids',
		)
	);
	return array( $cats[0], array_map( 'intval', $ids ) );
}

function firstlab_related_list( string $heading, array $ids ): string {
	$items = '';
	foreach ( $ids as $id ) {
		$items .= sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $id ) ), esc_html( get_the_title( $id ) ) );
	}
	return '<section class="firstlab-related"><h2>' . esc_html( $heading ) . '</h2><ul>' . $items . '</ul></section>';
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
		if ( $ids ) { // 내용이 비슷한 글이 없으면 엉뚱한 글을 채우지 않음
			$content .= firstlab_related_list( '함께 보면 좋은 글', $ids );
		}
		// 같은 분류의 가까운 글 (위에 이미 보인 글은 빼고) — 분류 이름을 붙여 '관련 글'로 오해하지 않게
		list( $cat, $siblings ) = firstlab_category_siblings( get_the_ID() );
		$near = firstlab_category_neighbors( $siblings, get_the_ID(), FIRSTLAB_SIBLING_COUNT, array_merge( $ids, $shown ) );
		if ( $cat && $near ) {
			$content .= firstlab_related_list( $cat->name . ' 분류의 다른 글', $near );
		}
		return $content;
	},
	20
);
