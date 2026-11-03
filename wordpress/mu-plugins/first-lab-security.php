<?php
/**
 * Plugin Name: First Lab - 보안 보강
 * Description: 워드프레스 버전 노출 제거, 사용자 목록 API 비공개.
 */

defined( 'ABSPATH' ) || exit;

// 버전 노출 제거 (공격자가 버전별 취약점을 찾는 데 쓰임)
remove_action( 'wp_head', 'wp_generator' );
add_filter( 'the_generator', '__return_empty_string' );

// CSS/JS 주소 끝의 ?ver=워드프레스버전 제거 (플러그인·테마 자체 버전은 유지)
add_filter( 'style_loader_src', 'firstlab_strip_wp_version', 9999 );
add_filter( 'script_loader_src', 'firstlab_strip_wp_version', 9999 );
function firstlab_strip_wp_version( $src ) {
	return ( is_string( $src ) && strpos( $src, 'ver=' . get_bloginfo( 'version' ) ) !== false )
		? remove_query_arg( 'ver', $src )
		: $src;
}

// 사용자 목록 API: 로그인한 사람만 (외부에서 계정 정보 수집 방지)
add_filter(
	'rest_endpoints',
	static function ( $endpoints ) {
		if ( ! is_user_logged_in() ) {
			unset( $endpoints['/wp/v2/users'], $endpoints['/wp/v2/users/(?P<id>[\d]+)'] );
		}
		return $endpoints;
	}
);
