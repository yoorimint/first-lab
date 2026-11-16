<?php
/**
 * Plugin Name: First Lab - 사이트 공통 기능
 * Description: 사이트 공통 상수와 숏코드.
 *
 * [firstlab_email] : 연락용 이메일을 스팸봇이 수집하기 어렵게 인코딩해 mailto 링크로 출력.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_CONTACT_EMAIL = 'cldud0070@naver.com';

add_shortcode(
	'firstlab_email',
	static function () {
		$email = antispambot( FIRSTLAB_CONTACT_EMAIL );
		return sprintf( '<a href="mailto:%1$s">%1$s</a>', $email );
	}
);

/**
 * 사이트 아이콘(파비콘): 구글 검색 결과·브라우저 탭·휴대폰 홈 화면에 보인다.
 * 파일은 server/make-icons.py 로 만든 테마 assets/icons/. /favicon.ico 는 배포 때 웹 루트에 복사(server/wp-setup.sh).
 * 관리자 '사이트 아이콘'을 따로 정하면 워드프레스가 그것을 쓰므로 여기서는 내보내지 않는다(중복 방지).
 */
add_action(
	'wp_head',
	static function () {
		if ( has_site_icon() ) {
			return;
		}
		$dir = get_stylesheet_directory_uri() . '/assets/icons/';
		printf( '<link rel="icon" href="%s" sizes="48x48">' . "\n", esc_url( home_url( '/favicon.ico' ) ) );
		printf( '<link rel="icon" href="%s" type="image/svg+xml">' . "\n", esc_url( $dir . 'favicon.svg' ) );
		printf( '<link rel="icon" href="%s" type="image/png" sizes="192x192">' . "\n", esc_url( $dir . 'icon-192.png' ) );
		printf( '<link rel="apple-touch-icon" href="%s">' . "\n", esc_url( $dir . 'apple-touch-icon.png' ) );
	},
	2
);

// 글 제목에서 사이트 이름(' - 퍼스트랩') 빼기 (11/6 운영자 제목 SEO 3번): 검색 결과는 한글 30자 안팎에서 잘리는데 글 제목에
// 7자가 더 붙어 핵심 숫자가 잘림. 홈·페이지(계산기 목록 등)는 그대로. The SEO Framework 필터.
add_filter(
	'the_seo_framework_use_title_branding',
	static function ( $use, $args = null ) {
		$id = is_array( $args ) && isset( $args['id'] ) ? (int) $args['id'] : ( null === $args && is_singular( 'post' ) ? (int) get_queried_object_id() : 0 );
		return $id && 'post' === get_post_type( $id ) ? false : $use;
	},
	10,
	2
);


// 분류·태그 페이지 제목의 '[카테고리:]'·'[태그:]' 머리말 빼기 — 구글 검색 결과에 '[카테고리:] 생활정보 - 퍼스트랩'으로 나와
// 관리 안 된 사이트처럼 보임 (11/16 운영자 캡처, 애드센스 '구조적 유지관리'). SEO 플러그인(The SEO Framework) 제목과 화면 제목 둘 다.
add_filter( 'the_seo_framework_use_archive_prefix', '__return_false' );
add_filter( 'get_the_archive_title_prefix', '__return_empty_string' );

// 글의 작성자 이름 링크가 /author/firstlab-1/ (404 — 작성자 목록 페이지는 막혀 있음)로 가던 것 → '소개' 페이지로 (11/16 사이트 전체 점검).
// 깨진 링크가 모든 글에 하나씩 있으면 관리 안 된 사이트로 보임 (애드센스 '구조적 유지관리').
add_filter( 'author_link', static fn() => home_url( '/about/' ) );
