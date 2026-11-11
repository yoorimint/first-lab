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
const FIRSTLAB_GUARD_VIEWS   = 15;     // 1분에 이보다 많이 열면 새로고침 폭주로 봄
const FIRSTLAB_GUARD_CLICKS  = 3;      // 하루에 광고를 이만큼 누르면 클릭 폭탄으로 봄
const FIRSTLAB_GUARD_SECONDS = 86400;  // 그 기기는 하루 동안 광고 안 띄움

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
		// 같은 기기에서 1분에 페이지 15번 넘게 열거나(새로고침 폭주), 하루에 광고를 3번 넘게 누르면(클릭 폭탄)
		// 그 기기는 하루 동안 광고를 안 띄움 — 쿠키는 서버(firstlab_ads_off)와 nginx 캐시 제외가 같이 봄.
		// 광고 클릭은 광고 틀(iframe)로 포커스가 넘어갈 때(blur)로 셈 (광고 안은 다른 사이트라 직접 못 봄).
		echo "<script>(function(){try{var S=window.localStorage,N=Date.now();"
			. "function L(k,ms){return JSON.parse(S.getItem(k)||'[]').filter(function(t){return Date.now()-t<ms});}"
			. "function off(){document.cookie='" . FIRSTLAB_NOADS_COOKIE . "=1; max-age=" . FIRSTLAB_GUARD_SECONDS . "; path=/; SameSite=Lax';"
			. "document.querySelectorAll('ins.adsbygoogle').forEach(function(e){e.remove();});}"
			. "var v=L('firstlab_pv',60000);v.push(N);S.setItem('firstlab_pv',JSON.stringify(v));if(v.length>" . FIRSTLAB_GUARD_VIEWS . "){off();}"
			. "window.addEventListener('blur',function(){var f=document.activeElement;"
			. "if(f&&f.tagName==='IFRAME'&&f.closest&&f.closest('ins.adsbygoogle')){var c=L('firstlab_adclk',86400000);c.push(Date.now());"
			. "S.setItem('firstlab_adclk',JSON.stringify(c));if(c.length>=" . FIRSTLAB_GUARD_CLICKS . "){off();}}});"
			. "}catch(e){}})();</script>\n";
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
