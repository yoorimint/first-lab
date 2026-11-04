<?php
/**
 * Plugin Name: First Lab - 표 모바일 보기
 * Description: 휴대폰 폭에 다 안 들어가는 본문 표(열 3개 이상)는 휴대폰에서 '행마다 카드'로 보이게 한다
 * (좁은 칸에서 '1,000 / 원'처럼 글자가 끊기거나 세로로 늘어지는 문제). 폭에 들어가는 표는 원래 표 그대로.
 *
 * 각 칸에 머리글 이름(data-label)을 붙여 두고, 스타일(테마 assets/tables.css)이 좁은 화면에서
 * "머리글: 값" 줄로 바꿔 보여 준다. 넓은 화면에서는 원래 표 그대로.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_STACK_MIN_COLS = 3;
const FIRSTLAB_STACK_MAX_WIDTH = 330; // 휴대폰(390px)에서 표가 쓸 수 있는 폭(px). 예상 폭이 이보다 크면 카드로
const FIRSTLAB_STACK_CELL_PAD  = 24;  // 칸마다 좌우 여백(px)

/** 글 한 줄 예상 폭(px): 한글 16, 숫자·영문·기호 9. */
function firstlab_text_width( string $text ): int {
	$width = 0;
	foreach ( mb_str_split( $text ) as $ch ) {
		$width += ( mb_ord( $ch ) >= 0x1100 ) ? 16 : 9;
	}
	return $width;
}

/** 표 예상 폭: 열마다 가장 넓은 칸(줄바꿈 없이) + 여백의 합. */
function firstlab_table_width( string $html ): int {
	$cols = array();
	preg_match_all( '#<tr\b[^>]*>(.*?)</tr>#s', $html, $rows );
	foreach ( $rows[1] as $row ) {
		preg_match_all( '#<t[hd]\b[^>]*>(.*?)</t[hd]>#s', $row, $cells );
		foreach ( $cells[1] as $i => $cell ) {
			$cols[ $i ] = max( $cols[ $i ] ?? 0, firstlab_text_width( trim( wp_strip_all_tags( $cell ) ) ) );
		}
	}
	return array_sum( $cols ) + count( $cols ) * FIRSTLAB_STACK_CELL_PAD;
}

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
	if ( firstlab_table_width( $html ) <= FIRSTLAB_STACK_MAX_WIDTH ) {
		return $html; // 휴대폰 폭에 들어가면 원래 표가 더 보기 좋음
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
