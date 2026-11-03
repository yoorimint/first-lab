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
				'numberposts'         => FIRSTLAB_RELATED_COUNT,
				'post_status'         => 'publish',
				'ignore_sticky_posts' => true,
			)
		) : array();
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
