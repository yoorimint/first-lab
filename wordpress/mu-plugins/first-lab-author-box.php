<?php
/**
 * Plugin Name: First Lab - 글 운영 정보
 * Description: 글 본문 끝에 운영 방식·마지막 업데이트 날짜·오류 제보 안내를 표시 (누가·어떻게 쓴 글인지 밝혀 신뢰도 보강).
 */

defined( 'ABSPATH' ) || exit;

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		$updated = get_the_modified_date( 'Y년 n월 j일' );
		$box     = sprintf(
			'<aside class="firstlab-author-box" style="margin-top:2.5em;padding:1em 1.2em;border-top:1px solid #ddd;font-size:0.9em;line-height:1.7;color:#555">'
			. '<p style="margin:0"><strong>퍼스트랩</strong> · 정부·공공기관 안내와 최신 자료를 바탕으로 정리하고, 발행 전에 핵심 내용을 다시 확인합니다. <a href="%s">퍼스트랩 소개</a></p>'
			. '<p style="margin:0.4em 0 0">마지막 업데이트: %s · 잘못된 내용 제보: <a href="%s">문의하기</a></p>'
			. '</aside>',
			esc_url( home_url( '/about/' ) ),
			esc_html( $updated ),
			esc_url( home_url( '/contact/' ) )
		);
		return $content . $box;
	},
	9 // '함께 보면 좋은 글'(우선순위 10)보다 먼저 → 본문 바로 아래
);
