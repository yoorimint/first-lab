<?php
/**
 * Plugin Name: First Lab - 댓글 끄기
 * Description: 댓글·핑백을 받지 않는다 (글 아래 '답글 남기기' 입력란 제거, 스팸 방지). 관리자 화면의 댓글 메뉴도 숨긴다.
 */

defined( 'ABSPATH' ) || exit;

// 새 댓글 차단 (입력란이 사라지고, wp-comments-post.php 로 직접 보내도 거부됨)
add_filter( 'comments_open', '__return_false', 20 );
add_filter( 'pings_open', '__return_false', 20 );
// 혹시 남아 있는 댓글도 화면에 보이지 않게
add_filter( 'comments_array', '__return_empty_array', 10 );

add_action(
	'init',
	static function () {
		remove_post_type_support( 'post', 'comments' );
		remove_post_type_support( 'page', 'comments' );
	},
	100
);

add_action(
	'admin_menu',
	static function () {
		remove_menu_page( 'edit-comments.php' );
	}
);

add_action(
	'admin_bar_menu',
	static function ( WP_Admin_Bar $bar ) {
		$bar->remove_node( 'comments' );
	},
	999
);
