<?php
/**
 * Plugin Name: First Lab - 애드센스
 * Description: 모든 페이지 <head> 에 애드센스 코드(사이트 소유 확인 + 자동 광고)를 넣고, /ads.txt 를 제공한다.
 *
 * 게시자 ID 는 페이지 소스에 그대로 보이는 공개값이라 저장소에 두어도 안전하다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_ADSENSE_CLIENT = 'ca-pub-7521397660882740';

add_action(
	'wp_head',
	static function () {
		printf(
			'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=%s" crossorigin="anonymous"></script>' . "\n",
			esc_attr( FIRSTLAB_ADSENSE_CLIENT )
		);
	},
	1
);

// /ads.txt — 광고 판매 권한 표시 (구글 표준 한 줄: 도메인, 게시자 ID, 직접 계약, 구글 인증 ID)
add_action(
	'init',
	static function () {
		$path = (string) wp_parse_url( $_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH );
		if ( '/ads.txt' !== $path ) {
			return;
		}
		header( 'Content-Type: text/plain; charset=utf-8' );
		echo 'google.com, ' . esc_html( str_replace( 'ca-', '', FIRSTLAB_ADSENSE_CLIENT ) ) . ", DIRECT, f08c47fec0942fa0\n";
		exit;
	},
	0
);
