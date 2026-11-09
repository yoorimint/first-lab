<?php
/**
 * Plugin Name: First Lab - 본문 읽기 쉽게
 * Description: 긴 문단·목록 항목을 화면에서 문장 단위로 나눠 보여 준다 (저장된 글은 그대로).
 *
 * 11/7 운영자 '글 가독성은 체크 안 하나': 검수에서 조건·예외를 문장 뒤에 덧붙여 한 항목이 300자를 넘는 일이 많았음
 * (검수 수정 735곳 중 211곳이 200자 초과). 새 글·새 수정은 관문(bot/condition_gate.readability)이 막고,
 * 이미 있는 글은 여기서 보여 줄 때만 나눈다 — 글자는 하나도 바꾸지 않고 줄만 나눔.
 *
 * - 문단 300자 초과 → 문장 묶음(약 180자)마다 문단을 나눔
 * - 목록 항목 200자 초과 → 첫 문장만 항목에 두고 나머지는 그 아래 하위 항목으로
 * - '(○○법 제N조)' 같은 근거 괄호 → 작고 흐린 글씨 (fl-ref)
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_READ_P_MAX     = 300; // 문단이 이보다 길면 나눔 (bot/condition_gate.READ_P_MAX 와 같게)
const FIRSTLAB_READ_LI_MAX    = 200; // 목록 항목이 이보다 길면 나눔 (READ_LI_MAX)
const FIRSTLAB_READ_CHUNK     = 180; // 문단을 나눌 때 덩어리 하나의 목표 길이
const FIRSTLAB_READ_SUB       = 110; // 목록 항목의 하위 항목 하나의 목표 길이
const FIRSTLAB_READ_EXCEPTION = '/^(다만|단,|단 )/u'; // 예외 문장은 따로 보이게
const FIRSTLAB_READ_CONNECTOR = '/^(그래서|이때|이 경우|이런 경우|따라서|즉|또는|그러면)\s/u'; // 앞 문장에 붙여 둘 이어 말

/** 태그를 뺀 글자 수. */
function firstlab_read_len( string $html ): int {
	return mb_strlen( trim( html_entity_decode( firstlab_read_strip( $html ), ENT_QUOTES, 'UTF-8' ) ) );
}

function firstlab_read_strip( string $html ): string {
	return function_exists( 'wp_strip_all_tags' ) ? wp_strip_all_tags( $html ) : strip_tags( $html );
}

/**
 * 문장으로 나누기: '.?!' 뒤 공백에서 자름. 태그 안(<strong>…</strong>)이나 괄호 안에서는 자르지 않음
 * (자르면 태그 짝이 깨지거나 '(…제1항. 다만…)' 같은 괄호가 갈라짐).
 */
function firstlab_read_sentences( string $html ): array {
	$out  = array();
	$rest = trim( firstlab_read_cut( $html, $out ) );
	if ( '' !== $rest ) {
		$out[] = $rest;
	}
	return array_values( array_filter( $out, static fn( $s ) => '' !== $s ) );
}

/** 태그 밖·괄호 밖의 '.?!+공백' 마다 잘라 $out 에 넣고, 마지막 남은 부분을 돌려줌. */
function firstlab_read_cut( string $cur, array &$out ): string {
	$paren = 0;
	$depth = 0;
	$start = 0;
	$len   = strlen( $cur );
	for ( $i = 0; $i < $len; $i++ ) {
		$c = $cur[ $i ];
		if ( '<' === $c ) {
			$end = strpos( $cur, '>', $i );
			if ( false === $end ) {
				break;
			}
			$tag    = substr( $cur, $i, $end - $i + 1 );
			$depth += preg_match( '#^</#', $tag ) ? -1 : ( preg_match( '#/>$|^<br\b#i', $tag ) ? 0 : 1 );
			$i      = $end;
			continue;
		}
		if ( '(' === $c ) {
			++$paren;
		} elseif ( ')' === $c ) {
			$paren = max( 0, $paren - 1 );
		} elseif ( ( '.' === $c || '?' === $c || '!' === $c ) && 0 === $paren && $depth <= 0
			&& $i + 1 < $len && ( ' ' === $cur[ $i + 1 ] || "\n" === $cur[ $i + 1 ] ) ) {
			$out[] = trim( substr( $cur, $start, $i + 1 - $start ) );
			$start = $i + 1;
		}
	}
	return substr( $cur, $start );
}

/**
 * 문장들을 덩어리로: 목표 길이까지 모음. '다만·단' 으로 시작하는 예외 문장은 늘 새 덩어리(눈에 띄게),
 * '그래서·이때' 로 이어지는 문장은 앞 덩어리가 너무 길어지지 않으면(목표의 1.5배) 붙임.
 */
function firstlab_read_chunks( array $sentences, int $target ): array {
	$chunks = array();
	foreach ( $sentences as $s ) {
		$plain = firstlab_read_strip( $s );
		$last  = count( $chunks ) - 1;
		$size  = $last >= 0 ? firstlab_read_len( $chunks[ $last ] ) + firstlab_read_len( $s ) : PHP_INT_MAX;
		$join  = ! preg_match( FIRSTLAB_READ_EXCEPTION, $plain )
			&& ( $size <= $target || ( preg_match( FIRSTLAB_READ_CONNECTOR, $plain ) && $size <= $target * 1.5 ) );
		if ( $join ) {
			$chunks[ $last ] .= ' ' . $s;
		} else {
			$chunks[] = $s;
		}
	}
	return $chunks;
}

/** 근거 괄호 '(○○법 제N조…)' → 작은 글씨. */
function firstlab_read_refs( string $html ): string {
	return preg_replace(
		'/\(((?:[^()<>]{0,60})(?:법|령|규칙|규정|고시)(?:[^()<>]{0,30})제\d+조(?:[^()<>]{0,60}))\)/u',
		'<span class="fl-ref">($1)</span>',
		$html
	);
}

/** 문단 블록: 300자 넘으면 덩어리마다 문단으로. */
function firstlab_read_paragraph( string $html ): string {
	if ( ! preg_match( '#^\s*<p(\s[^>]*)?>(.*)</p>\s*$#s', $html, $m ) || false !== strpos( $m[2], '<p' ) ) {
		return $html;
	}
	$inner = $m[2];
	if ( firstlab_read_len( $inner ) > FIRSTLAB_READ_P_MAX ) {
		$chunks = firstlab_read_chunks( firstlab_read_sentences( $inner ), FIRSTLAB_READ_CHUNK );
		if ( count( $chunks ) > 1 ) {
			$attrs = $m[1] ?? '';
			return implode( "\n", array_map( static fn( $c ) => "<p{$attrs}>" . firstlab_read_refs( $c ) . '</p>', $chunks ) );
		}
	}
	return '<p' . ( $m[1] ?? '' ) . '>' . firstlab_read_refs( $inner ) . '</p>';
}

/** 목록 항목 블록: 200자 넘으면 첫 문장만 두고 나머지는 하위 항목으로 (이미 하위 목록이 있으면 그대로). */
function firstlab_read_list_item( string $html ): string {
	if ( ! preg_match( '#^\s*<li(\s[^>]*)?>(.*)</li>\s*$#s', $html, $m ) || preg_match( '#<(ul|ol|li)\b#', $m[2] ) ) {
		return preg_match( '#<(ul|ol)\b#', $html ) ? $html : firstlab_read_refs( $html );
	}
	$inner = $m[2];
	$attrs = $m[1] ?? '';
	if ( firstlab_read_len( $inner ) > FIRSTLAB_READ_LI_MAX ) {
		$sentences = firstlab_read_sentences( $inner );
		if ( count( $sentences ) > 1 ) {
			$head = array_shift( $sentences );
			$rest = firstlab_read_chunks( $sentences, FIRSTLAB_READ_SUB );
			$sub  = implode( '', array_map( static fn( $c ) => '<li>' . firstlab_read_refs( $c ) . '</li>', $rest ) );
			return "<li{$attrs}>" . firstlab_read_refs( $head ) . '<ul class="fl-split">' . $sub . '</ul></li>';
		}
	}
	return "<li{$attrs}>" . firstlab_read_refs( $inner ) . '</li>';
}

/**
 * 본문 꾸밈 표시 (11/9 운영자 '가독성 좋게 컬러나 선 — 할 수 있는데 왜 안 했노'): 글자는 그대로, 화면에서 class 만 붙인다.
 * - 첫 문단(핵심 답) → 요약 상자 (fl-lead)
 * - '다만·단,'으로 시작하는 문단 → 주의 상자 (fl-note)
 * - '자주 묻는 질문' 소제목 아래 질문 소제목 → Q 표시 (fl-q)
 * - '이런 건 꼭 확인하세요' 제목 문단 + 바로 뒤 목록 → 확인 상자 (fl-check), '한 줄 정리:' 문단 → 정리 상자 (fl-sum) (11/10 운영자 주휴수당 검토: 근거 줄 밑에 붙어 눈에 안 띄었음)
 */
function firstlab_read_decorate( string $html ): string {
	// 첫 '본문' 문단 (작성일 줄·짧은 안내 줄은 건너뜀 — 11/9 시안에서 '작성 2026년 11월 7일' 줄이 상자가 됐음)
	if ( preg_match_all( '/<p(\s[^>]*)?>(.*?)<\/p>/su', $html, $ps, PREG_OFFSET_CAPTURE ) ) {
		foreach ( $ps[0] as $i => $m ) {
			$text = trim( html_entity_decode( strip_tags( $ps[2][ $i ][0] ), ENT_QUOTES, 'UTF-8' ) );
			if ( mb_strlen( $text ) >= 40 && ! preg_match( '/^(작성|수정|업데이트|최종)/u', $text ) ) {
				$open = '<p' . $ps[1][ $i ][0] . '>';
				$html = substr_replace( $html, '<p class="fl-lead">', $m[1], strlen( $open ) );
				break;
			}
		}
	}
	$html = preg_replace( '/<p(?:\s+class="([^"]*)")?>(\s*(?:<strong>)?\s*(?:다만|단,|주의|※))/u', '<p class="fl-note $1">$2', $html ) ?? $html;
	$html = preg_replace( '/<p(?:\s[^>]*)?>\s*<strong>\s*((?:이런 건 )?꼭 확인하세요)\s*<\/strong>\s*<\/p>\s*(<ul\b.*?<\/ul>)/su', '<div class="fl-check"><p class="fl-check-title">$1</p>$2</div>', $html ) ?? $html;
	$html = preg_replace( '/<p(?:\s+class="([^"]*)")?>(\s*<strong>\s*한 줄 정리)/u', '<p class="fl-sum $1">$2', $html ) ?? $html;
	// '자주 묻는 질문' 소제목(h2) 뒤의 h3 만 — 글자로 찾으면 목차의 '자주 묻는 질문'부터 잡혀 모든 소제목에 Q 가 붙었음 (11/9 운영자 화면)
	if ( preg_match( '/<h2[^>]*>[^<]*자주 묻는 질문[^<]*<\/h2>/u', $html, $m, PREG_OFFSET_CAPTURE ) ) {
		$byte = $m[0][1];
		$tail = preg_replace( '/<h3(\s+class="([^"]*)")?>/u', '<h3 class="fl-q $2">', substr( $html, $byte ) ) ?? substr( $html, $byte );
		$html = substr( $html, 0, $byte ) . $tail;
	}
	return $html;
}

if ( function_exists( 'add_filter' ) ) {
	add_filter( 'the_content', static fn( $html ) => is_singular( 'post' ) && in_the_loop() && is_main_query() ? firstlab_read_decorate( (string) $html ) : $html, 14 ); // 글 흐름(15) 앞
	add_filter( 'render_block_core/paragraph', static fn( $html ) => is_singular( 'post' ) ? firstlab_read_paragraph( (string) $html ) : $html );
	add_filter( 'render_block_core/list-item', static fn( $html ) => is_singular( 'post' ) ? firstlab_read_list_item( (string) $html ) : $html );
	add_action(
		'wp_enqueue_scripts',
		static function () {
			if ( is_singular( 'post' ) ) {
				$rel = '/assets/readability.css';
				wp_enqueue_style( 'firstlab-readability', get_stylesheet_directory_uri() . $rel, array(), (string) filemtime( get_stylesheet_directory() . $rel ) );
			}
		}
	);
}
