<?php
/**
 * Plugin Name: First Lab - 목차
 * Description: 소제목(h2)이 FIRSTLAB_TOC_MIN 개 이상인 글은 첫 소제목 바로 위에 목차를 넣고, 소제목마다 바로가기 주소(#toc-N)를 붙임.
 *
 * 11/6 운영자 '워드프레스 꿀팁 3번(긴 글 목차) 해라': 긴 글이 읽기 편해지고 구글 검색 결과에 '바로가기' 링크가 붙을 수 있음.
 * 글 본문(저장된 내용)은 바꾸지 않고 화면에 보일 때만 붙인다 (글 수정·패치와 섞이지 않게).
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_TOC_MIN = 4;

/** 본문 HTML → 목차를 넣은 HTML (소제목이 적으면 그대로). */
function firstlab_toc( string $content ): string {
	if ( preg_match_all( '/<h2\b[^>]*>(.*?)<\/h2>/is', $content, $all ) < FIRSTLAB_TOC_MIN ) {
		return $content;
	}
	$items = array();
	$n     = 0;
	$body  = preg_replace_callback(
		'/<h2\b([^>]*)>(.*?)<\/h2>/is',
		static function ( $m ) use ( &$items, &$n ) {
			$n++;
			$attrs = $m[1];
			if ( preg_match( '/\bid="([^"]+)"/', $attrs, $id ) ) {
				$anchor = $id[1]; // 이미 주소가 있으면 그대로
			} else {
				$anchor = 'toc-' . $n;
				$attrs .= ' id="' . $anchor . '"';
			}
			$label   = trim( preg_replace( '/\s+/u', ' ', wp_strip_all_tags( $m[2] ) ) );
			$items[] = sprintf( '<li><a href="#%s">%s</a></li>', esc_attr( $anchor ), esc_html( $label ) );
			return '<h2' . $attrs . '>' . $m[2] . '</h2>';
		},
		$content
	);
	$toc = '<nav class="firstlab-toc" aria-label="목차" style="margin:1.5em 0;padding:0.9em 1.2em;border:1px solid #e3e3e3;border-radius:8px;background:#fafafa;font-size:0.95em">'
		. '<p style="margin:0 0 0.4em;font-weight:600">목차</p><ol style="margin:0;padding-left:1.3em;line-height:1.8">' . implode( '', $items ) . '</ol></nav>';
	$pos = stripos( $body, '<h2' );
	return substr( $body, 0, $pos ) . $toc . substr( $body, $pos );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		return firstlab_toc( (string) $content );
	},
	8 // 운영 정보(9)·함께 보면 좋은 글(20)보다 먼저 — 그 칸들의 제목(h2)은 목차에 안 들어가게
);
