<?php
/**
 * Plugin Name: First Lab - 표 모바일 보기
 * Description: 열이 3개 이상인 본문 표는 휴대폰에서 '행마다 카드'로 보이게 한다 (좁은 칸에 긴 글이 세로로 늘어지는 문제).
 *
 * 각 칸에 머리글 이름(data-label)을 붙여 두고, 스타일(테마 assets/tables.css)이 좁은 화면에서
 * "머리글: 값" 줄로 바꿔 보여 준다. 넓은 화면에서는 원래 표 그대로.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_STACK_MIN_COLS = 3;

/** 표 HTML 에 카드 보기용 표시(class fl-stack, data-label)를 붙인다. 머리글이 없거나 열이 적으면 그대로. */
function firstlab_stack_table( string $html ): string {
	if ( ! preg_match( '#<thead>(.*?)</thead>#s', $html, $head ) ) {
		return $html;
	}
	preg_match_all( '#<th\b[^>]*>(.*?)</th>#s', $head[1], $th );
	$labels = array_map( static fn( $t ) => esc_attr( trim( wp_strip_all_tags( $t ) ) ), $th[1] );
	if ( count( $labels ) < FIRSTLAB_STACK_MIN_COLS ) {
		return $html;
	}
	$html = preg_replace_callback(
		'#<tbody>(.*?)</tbody>#s',
		static fn( $body ) => '<tbody>' . preg_replace_callback(
			'#<tr\b[^>]*>.*?</tr>#s',
			static function ( $row ) use ( $labels ) {
				$i = 0;
				return preg_replace_callback(
					'#<td\b#',
					static function () use ( $labels, &$i ) {
						$label = $labels[ $i++ ] ?? '';
						return '<td data-label="' . $label . '"';
					},
					$row[0]
				);
			},
			$body[1]
		) . '</tbody>',
		$html
	);
	return preg_replace( '#<table\b(?: class="([^"]*)")?#', '<table class="$1 fl-stack"', $html, 1 );
}

add_filter( 'render_block_core/table', static fn( $html ) => firstlab_stack_table( (string) $html ) );

add_action(
	'wp_enqueue_scripts',
	static function () {
		if ( is_singular() ) {
			$rel = '/assets/tables.css';
			wp_enqueue_style( 'firstlab-tables', get_stylesheet_directory_uri() . $rel, array(), (string) filemtime( get_stylesheet_directory() . $rel ) );
		}
	}
);
