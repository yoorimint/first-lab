<?php
/**
 * Plugin Name: First Lab - 애드센스
 * Description: 모든 페이지 <head> 에 애드센스 코드(사이트 소유 확인 + 자동 광고)를 넣고, /ads.txt 를 제공한다.
 *
 * 게시자 ID 는 페이지 소스에 그대로 보이는 공개값이라 저장소에 두어도 안전하다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_ADSENSE_CLIENT = 'ca-pub-7521397660882740';

const FIRSTLAB_NOADS_COOKIE = 'firstlab_noads';

/**
 * 우리 쪽 방문에는 광고를 안 띄움 — 무효 클릭·무효 트래픽 방지 (11/11 운영자 '무효클릭 무효트래픽 방지하는 거').
 * 로그인한 사람(운영자), 이 기기 광고 끄기 쿠키(/?firstlab_noads=1 한 번 열면 1년), 우리 봇 이름(FirstLab)이 든 요청.
 * 구글 확인용 크롤러는 로그인·쿠키가 없어 그대로 광고 코드를 봄.
 */
function firstlab_ads_off(): bool {
	if ( is_user_logged_in() ) {
		return true;
	}
	if ( ! empty( $_COOKIE[ FIRSTLAB_NOADS_COOKIE ] ) ) {
		return true;
	}
	return false !== stripos( (string) ( $_SERVER['HTTP_USER_AGENT'] ?? '' ), 'FirstLab' );
}

// /?firstlab_noads=1 → 이 기기에서 광고 끄기 (1년), =0 → 다시 켜기
add_action(
	'init',
	static function () {
		if ( ! isset( $_GET['firstlab_noads'] ) ) {
			return;
		}
		$on = '1' === (string) $_GET['firstlab_noads'];
		setcookie( FIRSTLAB_NOADS_COOKIE, $on ? '1' : '', $on ? time() + YEAR_IN_SECONDS : time() - 3600, '/', '', is_ssl(), true );
		$_COOKIE[ FIRSTLAB_NOADS_COOKIE ] = $on ? '1' : '';
	},
	0
);

add_action(
	'wp_head',
	static function () {
		if ( firstlab_ads_off() ) {
			return;
		}
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
