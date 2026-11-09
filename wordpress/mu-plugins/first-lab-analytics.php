<?php
/**
 * Plugin Name: First Lab - 방문 분석 (Google 애널리틱스 4)
 * Description: 모든 페이지 <head> 에 GA4 태그를 넣는다 — 검색으로 들어온 뒤 어떤 글로 넘어가는지(내부 흐름)를 보려고.
 *
 * 11/9 운영자 '지금 붙여 놓으면 되지': 서치콘솔은 검색 결과에서 클릭하기 전까지만 보여 줘서
 * 관련 글 연결이 실제로 눌리는지·몇 페이지 보는지를 알 수 없었음.
 * 측정 ID(G-로 시작)는 페이지 소스에 그대로 보이는 공개값이라 저장소에 두어도 안전하다 (애드센스 게시자 ID 와 같음).
 * ID 가 비어 있으면 아무것도 넣지 않는다. 로그인한 사람(운영자)의 방문은 세지 않는다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_GA4_ID = ''; // 예: 'G-ABC123XYZ9' — 운영자가 GA4 속성을 만든 뒤 채움

/** 넣을 태그 (ID 형식이 맞을 때만). */
function firstlab_ga4_tag( string $id ): string {
	if ( ! preg_match( '/^G-[A-Z0-9]{4,16}$/', $id ) ) {
		return '';
	}
	return '<script async src="https://www.googletagmanager.com/gtag/js?id=' . $id . '"></script>' . "\n"
		. "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','" . $id . "');</script>\n";
}

if ( function_exists( 'add_action' ) ) {
	add_action(
		'wp_head',
		static function () {
			if ( is_user_logged_in() ) {
				return; // 운영자 방문은 통계에서 뺌
			}
			echo firstlab_ga4_tag( FIRSTLAB_GA4_ID ); // phpcs:ignore WordPress.Security.EscapeOutput -- 형식 검사한 고정 문자열
		},
		2
	);
}
