<?php
/**
 * Plugin Name: First Lab - 검색엔진 소유 확인
 * Description: 구글 서치콘솔·네이버 서치어드바이저 확인용 메타 태그를 메인 페이지에 출력한다.
 *
 * 코드는 공개값이므로 저장소에 두어도 안전하다. 빈 값은 출력하지 않는다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_SITE_VERIFICATION = array(
	'google-site-verification' => '0ABz8ab9NKoHBvkXE3tM7MCmIOiTpLVh25WBO0bAxok',
	'naver-site-verification'  => '',
);

add_action(
	'wp_head',
	static function () {
		if ( ! is_front_page() ) {
			return;
		}
		foreach ( FIRSTLAB_SITE_VERIFICATION as $name => $content ) {
			if ( '' === $content ) {
				continue;
			}
			printf( '<meta name="%s" content="%s" />' . "\n", esc_attr( $name ), esc_attr( $content ) );
		}
	},
	1
);
