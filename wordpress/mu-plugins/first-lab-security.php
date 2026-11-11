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

// ---------- 로그인 보안 (11/11 운영자 '비번 털리면 끝나잖아') ----------

// 앱 비밀번호 끔: 비밀번호 말고 따로 만들 수 있는 '두 번째 열쇠'. 우리 봇·글쓰기 작업은 자체 토큰(X-Firstlab-Token)을 써서 필요 없음.
add_filter( 'wp_is_application_passwords_available', '__return_false' );

const FIRSTLAB_LOGIN_MAX_FAILS = 5;            // 같은 IP 에서 이만큼 틀리면
const FIRSTLAB_LOGIN_LOCK_MIN  = 30;           // 이 분(分) 동안 로그인 막음 (맞는 비밀번호여도)
const FIRSTLAB_LOGIN_KEEP      = 20;           // 성공 기록 몇 건 남길지

function firstlab_login_ip(): string {
	return preg_replace( '/[^0-9a-fA-F:.]/', '', (string) ( $_SERVER['REMOTE_ADDR'] ?? '' ) );
}

function firstlab_login_fail_key( string $ip ): string {
	return 'firstlab_login_fail_' . md5( $ip );
}

// 잠긴 IP 는 비밀번호가 맞아도 들어오지 못함 (core 확인 뒤 priority 99 에서 결과를 덮음)
add_filter(
	'authenticate',
	static function ( $user ) {
		if ( (int) get_transient( firstlab_login_fail_key( firstlab_login_ip() ) ) >= FIRSTLAB_LOGIN_MAX_FAILS ) {
			return new WP_Error( 'firstlab_locked', sprintf( '로그인을 여러 번 틀려 %d분 동안 막혔어요.', FIRSTLAB_LOGIN_LOCK_MIN ) );
		}
		return $user;
	},
	99
);

// 실패: IP 별로 세고(15분 창), 한도에 닿으면 잠금 시간으로 늘림. 날짜별 실패·잠금 수는 /maintenance 로 봄
add_action(
	'wp_login_failed',
	static function () {
		$key = firstlab_login_fail_key( firstlab_login_ip() );
		$n   = (int) get_transient( $key ) + 1;
		set_transient( $key, $n, ( $n >= FIRSTLAB_LOGIN_MAX_FAILS ? FIRSTLAB_LOGIN_LOCK_MIN : 15 ) * MINUTE_IN_SECONDS );
		$log = (array) get_option( 'firstlab_login_log', array() );
		$day = wp_date( 'Y-m-d' );
		$log['fails'][ $day ] = (int) ( $log['fails'][ $day ] ?? 0 ) + 1;
		if ( FIRSTLAB_LOGIN_MAX_FAILS === $n ) {
			$log['locks'][ $day ] = (int) ( $log['locks'][ $day ] ?? 0 ) + 1;
		}
		foreach ( array( 'fails', 'locks' ) as $k ) {  // 최근 14일만
			$log[ $k ] = array_slice( (array) ( $log[ $k ] ?? array() ), -14, null, true );
		}
		update_option( 'firstlab_login_log', $log, false );
	}
);

// 성공: 언제·어느 IP·어떤 기기로 들어왔는지 남김 → 모르는 로그인을 감시 기록에서 바로 알아봄
add_action(
	'wp_login',
	static function ( $login ) {
		delete_transient( firstlab_login_fail_key( firstlab_login_ip() ) );
		$log = (array) get_option( 'firstlab_login_log', array() );
		$ok  = (array) ( $log['ok'] ?? array() );
		array_unshift(
			$ok,
			array(
				'time' => wp_date( 'Y-m-d H:i' ),
				'user' => (string) $login,
				'ip'   => firstlab_login_ip(),
				'ua'   => substr( sanitize_text_field( (string) ( $_SERVER['HTTP_USER_AGENT'] ?? '' ) ), 0, 120 ),
			)
		);
		$log['ok'] = array_slice( $ok, 0, FIRSTLAB_LOGIN_KEEP );
		update_option( 'firstlab_login_log', $log, false );
	}
);
