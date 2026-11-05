<?php
/**
 * Plugin Name: First Lab - 함께 보면 좋은 글
 * Description: 글 하단에 같은 카테고리의 다른 글을 자동으로 연결 (새 글이 생기면 예전 글에도 자동 반영).
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_RELATED_COUNT = 4;

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		$post_id = get_the_ID();
		$cats    = wp_get_post_categories( $post_id );
		$related = $cats ? get_posts(
			array(
				'category__in'        => $cats,
				'post__not_in'        => array( $post_id ),
				'numberposts'         => 40,
				'post_status'         => 'publish',
				'ignore_sticky_posts' => true,
			)
		) : array();
		// 같은 주제(제목이 많이 겹치는) 글을 먼저 — 주휴수당 뜻 ↔ 주휴수당 계산처럼 목적이 다른 같은 주제 글끼리 서로 연결 (11/5 운영자)
		$title = get_the_title( $post_id );
		$score = static function ( $p ) use ( $title ) {
			similar_text( $title, get_the_title( $p ), $pct );
			return $pct;
		};
		usort( $related, static fn( $a, $b ) => $score( $b ) <=> $score( $a ) );
		$related = array_slice( $related, 0, FIRSTLAB_RELATED_COUNT );
		if ( ! $related ) {
			return $content;
		}
		$items = '';
		foreach ( $related as $p ) {
			$items .= sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $p ) ), esc_html( get_the_title( $p ) ) );
		}
		return $content . '<section class="firstlab-related"><h2>함께 보면 좋은 글</h2><ul>' . $items . '</ul></section>';
	},
	20
);
