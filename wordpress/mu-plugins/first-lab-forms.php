<?php
/**
 * Plugin Name: First Lab - 빈 양식 내려받기
 * Description: 테마 forms/<id>.json 의 "post" 글에 '빈 양식 내려받기' 상자(Word·PDF + 공식 서식 링크)를 첫 소제목 앞에 붙인다.
 *
 * 11/12 운영자 '알바이력서양식 같은 글은 실제 양식 첨부하면 좋을까?' → '그래 해라'.
 * 파일은 automation/forms_build.py 가 spec(JSON)으로 만든다. 본문은 건드리지 않아서, 양식을 고치거나 빼도 글 수정이 필요 없다.
 * 법정 서식은 파일 대신 spec 의 "official" 링크로만 안내한다 (개정되면 우리 파일이 옛 서식이 되므로).
 */

defined( 'ABSPATH' ) || exit;

/** 글 번호 → spec (forms/*.json). 파일이 몇 개 안 돼 요청마다 읽어도 가볍다. */
function firstlab_forms_map(): array {
	static $map = null;
	if ( null !== $map ) {
		return $map;
	}
	$map = array();
	foreach ( glob( get_stylesheet_directory() . '/forms/*.json' ) ?: array() as $file ) {
		$spec = json_decode( (string) file_get_contents( $file ), true );
		if ( is_array( $spec ) && ! empty( $spec['post'] ) ) {
			$spec['id']                   = basename( $file, '.json' );
			$map[ (int) $spec['post'] ][] = $spec;
		}
	}
	return $map;
}

function firstlab_forms_box( array $spec ): string {
	$dir   = get_stylesheet_directory() . '/forms/';
	$url   = get_stylesheet_directory_uri() . '/forms/';
	$links = array();
	foreach ( array( 'docx' => 'Word 파일 (.docx)', 'pdf' => 'PDF (인쇄용)' ) as $ext => $label ) {
		$path = $dir . $spec['id'] . '.' . $ext;
		if ( is_file( $path ) ) {
			$href    = $url . rawurlencode( $spec['id'] ) . '.' . $ext . '?v=' . filemtime( $path );
			$links[] = '<a class="fl-form-btn" href="' . esc_url( $href ) . '" download>' . esc_html( $label ) . '</a>';
		}
	}
	$official = array();
	foreach ( (array) ( $spec['official'] ?? array() ) as $o ) {
		if ( ! empty( $o['url'] ) && ! empty( $o['name'] ) ) {
			$official[] = '<a href="' . esc_url( $o['url'] ) . '" target="_blank" rel="noopener nofollow">' . esc_html( $o['name'] ) . '</a>';
		}
	}
	if ( ! $links && ! $official ) {
		return '';
	}
	$html  = '<div class="fl-form-box"><p class="fl-form-title">빈 양식 내려받기: ' . esc_html( (string) $spec['title'] ) . '</p>';
	$html .= ! empty( $spec['intro'] ) ? '<p class="fl-form-intro">' . esc_html( (string) $spec['intro'] ) . '</p>' : '';
	$html .= $links ? '<p class="fl-form-links">' . implode( ' ', $links ) . '</p>' : '';
	$html .= $official ? '<p class="fl-form-official">공식 서식: ' . implode( ' · ', $official ) . '</p>' : '';
	return $html . '</div>';
}

/** 첫 소제목(h2) 앞에 넣는다 — 첫 문단(요약)을 읽은 바로 뒤. 소제목이 없으면 맨 끝. */
function firstlab_forms_insert( string $html, int $post_id ): string {
	$specs = firstlab_forms_map()[ $post_id ] ?? array();
	$boxes = implode( '', array_map( 'firstlab_forms_box', $specs ) );
	if ( '' === $boxes || str_contains( $html, 'fl-form-box' ) ) {
		return $html;
	}
	$at = stripos( $html, '<h2' );
	return false === $at ? $html . $boxes : substr( $html, 0, $at ) . $boxes . substr( $html, $at );
}

add_filter(
	'the_content',
	static fn( $html ) => is_singular( 'post' ) && in_the_loop() && is_main_query() ? firstlab_forms_insert( (string) $html, (int) get_the_ID() ) : $html,
	13 // 읽기 쉬운 꾸밈(14) 앞
);
