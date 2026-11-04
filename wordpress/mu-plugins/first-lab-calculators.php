<?php
/**
 * Plugin Name: First Lab - 계산기
 * Description: 본문의 [firstlab_calc id="..."] 를 계산기로 바꾼다. 계산기 파일은 테마의 calculators/ (구조는 calculators/calc.js 머리말 참고).
 *
 * 요율(data/<id>.json)은 화면에 data-rates 로 넣어 추가 요청 없이 계산한다.
 * 스크립트·스타일은 계산기가 들어간 페이지에서만 불러온다.
 */

defined( 'ABSPATH' ) || exit;

function firstlab_calc_path( string $rel = '' ): string {
	return get_stylesheet_directory() . '/calculators' . $rel;
}

function firstlab_calc_url( string $rel = '' ): string {
	return get_stylesheet_directory_uri() . '/calculators' . $rel;
}

/** 파일이 바뀌면 주소가 바뀌어 브라우저·서버 캐시(30일)를 새로 받게 한다. */
function firstlab_calc_enqueue( string $handle, string $rel, array $deps = array() ): void {
	$version = (string) filemtime( firstlab_calc_path( $rel ) );
	if ( str_ends_with( $rel, '.css' ) ) {
		wp_enqueue_style( $handle, firstlab_calc_url( $rel ), $deps, $version );
	} else {
		wp_enqueue_script( $handle, firstlab_calc_url( $rel ), $deps, $version, true );
	}
}

add_shortcode(
	'firstlab_calc',
	static function ( $atts ): string {
		$id   = sanitize_key( (string) ( $atts['id'] ?? '' ) );
		$def  = "/defs/$id.js";
		$data = "/data/$id.json";
		if ( '' === $id || ! is_readable( firstlab_calc_path( $def ) ) || ! is_readable( firstlab_calc_path( $data ) ) ) {
			return ''; // 없는 계산기는 조용히 생략 (글은 그대로 보임)
		}
		firstlab_calc_enqueue( 'firstlab-calc', '/calc.css' );
		firstlab_calc_enqueue( 'firstlab-calc', '/calc.js' );
		firstlab_calc_enqueue( "firstlab-calc-$id", $def, array( 'firstlab-calc' ) );

		return sprintf(
			'<div class="firstlab-calc" data-calc="%s" data-rates="%s"><noscript>계산기는 자바스크립트를 켜야 동작합니다.</noscript></div>',
			esc_attr( $id ),
			esc_attr( (string) file_get_contents( firstlab_calc_path( $data ) ) )
		);
	}
);
