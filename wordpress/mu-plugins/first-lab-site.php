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
