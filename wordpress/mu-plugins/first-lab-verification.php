<?php
/**
 * Plugin Name: First Lab - 검색엔진 소유 확인
 * Description: 구글 서치콘솔·네이버 서치어드바이저 확인용 메타 태그(메인 페이지), 다음 웹마스터도구 확인 줄(robots.txt).
 *
 * 코드는 공개값이므로 저장소에 두어도 안전하다. 빈 값은 출력하지 않는다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_SITE_VERIFICATION = array(
	'google-site-verification' => '0ABz8ab9NKoHBvkXE3tM7MCmIOiTpLVh25WBO0bAxok',
	'naver-site-verification'  => 'dada5a0e936084f1ec92b426f31cc9f2836f947c',
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

// 다음 웹마스터도구: robots.txt 에 '#DaumWebMasterTool:<확인 코드>' 한 줄 (공개값). 빈 값이면 넣지 않음.
const FIRSTLAB_DAUM_WEBMASTER = '';

add_filter(
	'robots_txt',
	static function ( $output ) {
		if ( '' === FIRSTLAB_DAUM_WEBMASTER || false !== strpos( $output, 'DaumWebMasterTool' ) ) {
			return $output;
		}
		return rtrim( $output ) . "\n#DaumWebMasterTool:" . FIRSTLAB_DAUM_WEBMASTER . "\n";
	},
	99
);

