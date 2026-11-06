<?php
/**
 * Plugin Name: First Lab - 함께 보면 좋은 글
 * Description: 글 하단에 주제가 겹치는 다른 공개 글을 자동으로 연결 (새 글이 생기면 예전 글에도 자동 반영). 겹치는 글이 없으면 안 보임.
 *
 * 11/6 운영자 '자동차보험료 계산 글에 함께 보면 좋은 글이 왜 주식 평단가 계산기지?': 예전엔 같은 분류의 글을 제목 글자 비슷한 순으로 4개 채움
 * → 분류(금융·세금)에 공개 글이 2편뿐이라 '계산'만 겹치는 주식 글이 붙음. 이제 흔한 말(계산·방법·신청…)을 뺀 주제 낱말이 겹치는 글만.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_RELATED_COUNT = 4;
// 주제가 아니라 글 형식을 말하는 흔한 말 — 이것만 겹치면 관련 글이 아님
const FIRSTLAB_RELATED_GENERIC = array( '계산기', '계산법', '계산', '방법', '신청', '조회', '양식', '기준', '비용', '요금', '가격', '총정리', '정리', '기간', '서류', '차이', '자격', '조건', '대상', '뜻', '하는', '까지', '부터', '이상', '이하', '최대', '최소', '평균' );

/** 제목의 주제 글자쌍 (한글 두 글자씩, 흔한 말·숫자·기호 뺌). */
function firstlab_related_bigrams( string $title ): array {
	$text = str_replace( FIRSTLAB_RELATED_GENERIC, ' ', $title );
	$out  = array();
	foreach ( preg_split( '/[^가-힣]+/u', $text, -1, PREG_SPLIT_NO_EMPTY ) as $word ) {
		$len = mb_strlen( $word );
		for ( $i = 0; $i + 1 < $len; $i++ ) {
			$out[ mb_substr( $word, $i, 2 ) ] = true;
		}
	}
	return array_keys( $out );
}

/** 두 제목의 주제 겹침 (겹치는 글자쌍 수, 0 이면 관련 없음). */
function firstlab_related_score( string $a, string $b ): int {
	return count( array_intersect( firstlab_related_bigrams( $a ), firstlab_related_bigrams( $b ) ) );
}

/** 후보 [['id', 'title', 'same_cat']] 중 관련 글 id (겹침 많은 순, 같으면 같은 분류 먼저, 최대 $count). */
function firstlab_related_pick( string $title, array $rows, int $count = FIRSTLAB_RELATED_COUNT ): array {
	$scored = array();
	foreach ( $rows as $row ) {
		$s = firstlab_related_score( $title, (string) $row['title'] );
		if ( $s > 0 ) {
			$scored[] = array( (int) $row['id'], $s, empty( $row['same_cat'] ) ? 1 : 0 );
		}
	}
	usort( $scored, static fn( $x, $y ) => array( $y[1], $x[2] ) <=> array( $x[1], $y[2] ) );
	return array_slice( array_column( $scored, 0 ), 0, $count );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		$post_id = get_the_ID();
		$cats    = wp_get_post_categories( $post_id );
		$rows    = array();
		// 분류가 달라도 주제가 같으면 연결 (자동차보험이 '금융·세금'·'자동차' 어느 쪽에 있든)
		foreach ( get_posts( array( 'numberposts' => 300, 'post_status' => 'publish', 'post__not_in' => array( $post_id ), 'ignore_sticky_posts' => true ) ) as $p ) {
			$rows[] = array( 'id' => $p->ID, 'title' => $p->post_title, 'same_cat' => (bool) array_intersect( $cats, wp_get_post_categories( $p->ID ) ) );
		}
		$ids = firstlab_related_pick( get_the_title( $post_id ), $rows );
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
