<?php
/**
 * Plugin Name: First Lab - 2단계 인증 (인증 앱 6자리)
 * Description: 비밀번호 + 휴대폰 인증 앱(구글 OTP 등)의 6자리 숫자로 로그인. 키 직접 입력 방식(QR 없음), 비상 코드 8개.
 *
 * 왜: 11/11 운영자 '비번 털리면 끝나잖아' → 비밀번호가 새도 휴대폰이 없으면 못 들어오게.
 * 켜는 법: 관리자 왼쪽 메뉴 '2단계 인증' (또는 사용자 → 2단계 인증, 관리자 화면 맨 위 노란 안내의 링크)
 *   1) 화면의 32자 키를 인증 앱 '설정 키 입력'(시간 기준)에 넣음
 *   2) 앱에 나온 6자리를 넣고 '켜기' → 비상 코드 8개가 한 번만 보임 (휴대폰을 잃으면 6자리 대신 하나씩 씀)
 * 켜기 전에는 로그인이 예전과 같음. 키·비상 코드는 이 화면에만 보이고 저장소·채팅·감시 기록에 남지 않음.
 * 비상 복구(휴대폰·비상 코드 모두 잃음): 서버에서 wp user meta delete <아이디> firstlab_2fa_on (operator-notes.md)
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_2FA_STEP   = 30;  // 초
const FIRSTLAB_2FA_DRIFT  = 1;   // 앞뒤 30초까지 허용 (휴대폰 시계 차이)
const FIRSTLAB_2FA_BACKUP = 8;   // 비상 코드 개수

/** RFC 4648 base32 (인증 앱이 쓰는 키 형식). */
function firstlab_2fa_b32_encode( string $bin ): string {
	$abc  = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
	$bits = '';
	foreach ( str_split( $bin ) as $c ) {
		$bits .= str_pad( decbin( ord( $c ) ), 8, '0', STR_PAD_LEFT );
	}
	$out = '';
	foreach ( str_split( $bits, 5 ) as $chunk ) {
		$out .= $abc[ bindec( str_pad( $chunk, 5, '0' ) ) ];
	}
	return $out;
}

function firstlab_2fa_b32_decode( string $b32 ): string {
	$abc  = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
	$bits = '';
	foreach ( str_split( strtoupper( preg_replace( '/[^A-Za-z2-7]/', '', $b32 ) ) ) as $c ) {
		$bits .= str_pad( decbin( strpos( $abc, $c ) ), 5, '0', STR_PAD_LEFT );
	}
	$out = '';
	foreach ( str_split( $bits, 8 ) as $byte ) {
		if ( 8 === strlen( $byte ) ) {
			$out .= chr( bindec( $byte ) );
		}
	}
	return $out;
}

/** RFC 6238 TOTP (HMAC-SHA1, 6자리). $counter = 30초 단위 시각. */
function firstlab_2fa_code( string $key, int $counter ): string {
	$hash   = hash_hmac( 'sha1', pack( 'J', $counter ), $key, true );
	$offset = ord( $hash[19] ) & 0x0f;
	$num    = ( unpack( 'N', substr( $hash, $offset, 4 ) )[1] & 0x7fffffff ) % 1000000;
	return str_pad( (string) $num, 6, '0', STR_PAD_LEFT );
}

/** 맞으면 그 시각(counter), 틀리면 null. 이미 쓴 시각 이하($last)는 다시 못 씀 (같은 코드 재사용 막음). */
function firstlab_2fa_check( string $b32, string $code, int $now, int $last = 0 ): ?int {
	$code = preg_replace( '/\D/', '', $code );
	if ( 6 !== strlen( $code ) ) {
		return null;
	}
	$key = firstlab_2fa_b32_decode( $b32 );
	$t   = intdiv( $now, FIRSTLAB_2FA_STEP );
	for ( $i = -FIRSTLAB_2FA_DRIFT; $i <= FIRSTLAB_2FA_DRIFT; $i++ ) {
		if ( $t + $i > $last && hash_equals( firstlab_2fa_code( $key, $t + $i ), $code ) ) {
			return $t + $i;
		}
	}
	return null;
}

function firstlab_2fa_on( int $user_id ): bool {
	return '1' === get_user_meta( $user_id, 'firstlab_2fa_on', true ) && '' !== (string) get_user_meta( $user_id, 'firstlab_2fa_secret', true );
}

/** 비상 코드: 맞으면 지우고 true (한 번만 씀). 해시로만 저장. */
function firstlab_2fa_use_backup( int $user_id, string $code ): bool {
	$code   = strtolower( preg_replace( '/[^A-Za-z0-9]/', '', $code ) );
	$hashes = (array) get_user_meta( $user_id, 'firstlab_2fa_backup', true );
	foreach ( $hashes as $i => $h ) {
		if ( strlen( $code ) >= 8 && wp_check_password( $code, (string) $h ) ) {
			unset( $hashes[ $i ] );
			update_user_meta( $user_id, 'firstlab_2fa_backup', array_values( $hashes ) );
			return true;
		}
	}
	return false;
}

// ---------- 로그인 화면: 인증 코드 칸 ----------
add_action(
	'login_form',
	static function () {
		echo '<p><label for="firstlab_2fa">인증 코드 (2단계 인증을 켰다면 앱의 6자리)<br>'
			. '<input type="text" name="firstlab_2fa" id="firstlab_2fa" class="input" inputmode="numeric" autocomplete="one-time-code" size="20"></label></p>';
	}
);

// 비밀번호가 맞은 뒤(core 20) 인증 코드 확인. 잠금(security 99)보다 앞.
add_filter(
	'authenticate',
	static function ( $user ) {
		if ( ! ( $user instanceof WP_User ) || ! firstlab_2fa_on( $user->ID ) ) {
			return $user;
		}
		$code = isset( $_POST['firstlab_2fa'] ) ? sanitize_text_field( wp_unslash( $_POST['firstlab_2fa'] ) ) : '';  // phpcs:ignore WordPress.Security.NonceVerification
		$last = (int) get_user_meta( $user->ID, 'firstlab_2fa_last', true );
		$step = firstlab_2fa_check( (string) get_user_meta( $user->ID, 'firstlab_2fa_secret', true ), $code, time(), $last );
		if ( null !== $step ) {
			update_user_meta( $user->ID, 'firstlab_2fa_last', $step );
			return $user;
		}
		if ( firstlab_2fa_use_backup( $user->ID, $code ) ) {
			return $user;
		}
		return new WP_Error( 'firstlab_2fa', '인증 코드가 맞지 않아요. 앱의 6자리(또는 비상 코드)를 넣어 주세요.' );
	},
	50
);

// ---------- 전용 화면: 관리자 메뉴 '사용자 → 2단계 인증' (11/11 운영자 '키가 어딨노 보이지도 않구만' — 프로필 맨 아래는 찾기 어려움) ----------
const FIRSTLAB_2FA_PAGE = 'firstlab-2fa';

function firstlab_2fa_page_url(): string {
	return admin_url( 'users.php?page=' . FIRSTLAB_2FA_PAGE );
}

add_action(
	'admin_menu',
	static function () {
		add_users_page( '2단계 인증', '2단계 인증', 'read', FIRSTLAB_2FA_PAGE, 'firstlab_2fa_render' );
		// 프로필만 보는 계정(구독자 등)도 찾게 왼쪽 맨 위 메뉴로도 둠
		add_menu_page( '2단계 인증', '2단계 인증', 'read', FIRSTLAB_2FA_PAGE . '-top', 'firstlab_2fa_render', 'dashicons-lock', 3 );
	}
);

/** 6자리를 확인해 켜거나 끔. 결과 문구를 돌려줌 (화면에 표시). */
function firstlab_2fa_handle( int $user_id, string $code, bool $off ): string {
	if ( firstlab_2fa_on( $user_id ) ) {
		if ( ! $off ) {
			return '';
		}
		if ( null === firstlab_2fa_check( (string) get_user_meta( $user_id, 'firstlab_2fa_secret', true ), $code, time() ) ) {
			return '6자리가 맞지 않아 끄지 않았어요.';
		}
		foreach ( array( 'on', 'secret', 'backup', 'last', 'pending' ) as $k ) {
			delete_user_meta( $user_id, 'firstlab_2fa_' . $k );
		}
		return '2단계 인증을 껐어요.';
	}
	$secret = (string) get_user_meta( $user_id, 'firstlab_2fa_pending', true );
	$step   = '' === $secret ? null : firstlab_2fa_check( $secret, $code, time() );
	if ( null === $step ) {
		return '6자리가 맞지 않아 켜지 않았어요. 휴대폰 시간이 자동으로 맞춰져 있는지 보고, 앱에 지금 보이는 숫자를 다시 넣어 주세요.';
	}
	$codes = array();
	for ( $i = 0; $i < FIRSTLAB_2FA_BACKUP; $i++ ) {
		$codes[] = strtolower( substr( firstlab_2fa_b32_encode( random_bytes( 10 ) ), 0, 10 ) );
	}
	update_user_meta( $user_id, 'firstlab_2fa_secret', $secret );
	update_user_meta( $user_id, 'firstlab_2fa_backup', array_map( 'wp_hash_password', $codes ) );
	update_user_meta( $user_id, 'firstlab_2fa_last', $step );
	update_user_meta( $user_id, 'firstlab_2fa_on', '1' );
	delete_user_meta( $user_id, 'firstlab_2fa_pending' );
	set_transient( 'firstlab_2fa_codes_' . $user_id, $codes, 600 );
	return '';
}

function firstlab_2fa_render(): void {
	$user_id = get_current_user_id();
	$msg     = '';
	if ( isset( $_POST['firstlab_2fa_confirm'] ) && check_admin_referer( 'firstlab_2fa' ) ) {
		$msg = firstlab_2fa_handle( $user_id, sanitize_text_field( wp_unslash( $_POST['firstlab_2fa_confirm'] ) ), ! empty( $_POST['firstlab_2fa_off'] ) );
	}
	echo '<div class="wrap"><h1>2단계 인증 (휴대폰 인증 앱)</h1>';
	if ( '' !== $msg ) {
		echo '<div class="notice notice-info"><p>' . esc_html( $msg ) . '</p></div>';
	}
	echo '<form method="post">';
	wp_nonce_field( 'firstlab_2fa' );
	firstlab_2fa_section( $user_id );
	echo '<p><button type="submit" class="button button-primary">확인</button></p></form></div>';
}

/** 화면 본문: 꺼져 있으면 키·켜는 법, 켜져 있으면 상태·끄기. 비상 코드는 켠 직후 한 번만. */
function firstlab_2fa_section( int $user_id ): void {
	$shown = get_transient( 'firstlab_2fa_codes_' . $user_id );
	if ( $shown ) {
		delete_transient( 'firstlab_2fa_codes_' . $user_id );
		echo '<p><strong>켜졌어요.</strong> 아래 비상 코드는 지금 한 번만 보여요. 종이에 적거나 휴대폰 말고 다른 곳에 저장하세요. 휴대폰을 잃으면 6자리 대신 하나씩 쓰고, 쓴 코드는 없어져요.</p><pre style="font-size:16px">'
			. esc_html( implode( "\n", (array) $shown ) ) . '</pre>';
	}
	if ( firstlab_2fa_on( $user_id ) ) {
		$left = count( (array) get_user_meta( $user_id, 'firstlab_2fa_backup', true ) );
		echo '<p>켜져 있어요. 남은 비상 코드 ' . (int) $left . '개.</p>'
			. '<p><label><input type="checkbox" name="firstlab_2fa_off" value="1"> 끄기 (아래 칸에 지금 앱의 6자리를 넣어야 꺼져요)</label></p>';
	} else {
		$secret = (string) get_user_meta( $user_id, 'firstlab_2fa_pending', true );
		if ( '' === $secret ) {
			$secret = firstlab_2fa_b32_encode( random_bytes( 20 ) );
			update_user_meta( $user_id, 'firstlab_2fa_pending', $secret );
		}
		echo '<p>지금은 꺼져 있어요. 켜는 법:</p><ol>'
			. '<li>휴대폰에 인증 앱(Google Authenticator 등)을 깔고 <b>+ → 설정 키 입력</b>을 눌러요.</li>'
			. '<li>계정 이름은 아무거나(예: 퍼스트랩), 키는 아래 32자, 종류는 <b>시간 기준</b>으로 넣어요.</li>'
			. '<li>앱에 나온 6자리를 아래 칸에 넣고 <b>확인</b>을 눌러요.</li></ol>'
			. '<p>키:</p><p style="font:600 20px monospace;letter-spacing:2px;word-break:break-all">' . esc_html( trim( chunk_split( $secret, 4, ' ' ) ) ) . '</p>';
	}
	echo '<p><input type="text" name="firstlab_2fa_confirm" inputmode="numeric" autocomplete="one-time-code" placeholder="6자리" style="font-size:18px;width:10em"></p>';
}

// 아직 안 켠 관리자에게 모든 관리자 화면 맨 위에 바로가기 (켜면 사라짐)
add_action(
	'admin_notices',
	static function () {
		$uid = get_current_user_id();
		if ( ! $uid || firstlab_2fa_on( $uid ) || ! current_user_can( 'manage_options' ) ) {
			return;
		}
		if ( isset( $_GET['page'] ) && 0 === strpos( sanitize_key( wp_unslash( $_GET['page'] ) ), FIRSTLAB_2FA_PAGE ) ) {  // phpcs:ignore WordPress.Security.NonceVerification
			return;
		}
		echo '<div class="notice notice-warning"><p><b>2단계 인증이 꺼져 있어요.</b> <a href="' . esc_url( firstlab_2fa_page_url() ) . '">여기를 눌러 켜기</a> (키 직접 입력, 1분)</p></div>';
	}
);
