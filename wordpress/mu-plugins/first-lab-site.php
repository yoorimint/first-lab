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
