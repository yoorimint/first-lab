<?php
/**
 * Plugin Name: First Lab - 계산기 대표 글
 * Description: 계산기가 들어간 공개 글이 있으면 그 글이 계산기의 대표 주소. 계산기 단독 페이지(…-calculator)는 그 글로 301, 계산기 목록·관련 글 링크도 그 글로, 사이트맵에서 뺌.
 *
 * 11/6 운영자: 같은 계산기가 글과 단독 페이지 두 곳에서 같은 검색어(예: 양도세 계산기)를 노려 서로 순위를 깎음 → 글 하나로.
 * 그 계산기를 넣은 글이 아직 공개 전이면 단독 페이지가 그대로 계산기를 보여 준다 (글이 공개되면 다음 요청부터 자동으로 넘어감).
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_CALC_PAGE_SUFFIX = '-calculator';

// 계산기 단독 페이지를 검색에서 뺌 (11/14 운영자 '계산기 일단 안 보이게'): 애드센스 '가치가 별로 없는 콘텐츠' 판정 —
// 사이트맵 97개 중 약 75개가 화면 글자 350자 남짓의 계산기 페이지(계산기는 자바스크립트)라 얇은 페이지가 사이트 대부분.
// 페이지와 계산기는 그대로 열리고(글 안 계산기도 그대로), 검색엔진에만 noindex + 사이트맵 제외. 설명을 채운 뒤 false 로 되돌림.
const FIRSTLAB_CALC_PAGES_NOINDEX = true;

/** 계산기 단독 페이지 슬러그 → 계산기 id ('capital-gains-tax-calculator' → 'capital-gains-tax'). 계산기 페이지가 아니면 ''. */
function firstlab_calc_id_of_slug( string $slug ): string {
	$slug = urldecode( $slug );
	return str_ends_with( $slug, FIRSTLAB_CALC_PAGE_SUFFIX ) ? substr( $slug, 0, -strlen( FIRSTLAB_CALC_PAGE_SUFFIX ) ) : '';
}

/**
 * 후보 글(먼저 공개된 순: [['id' => int, 'title' => string], ...]) 중 대표 글 id.
 * 제목에 '계산기'가 든 글을 먼저 (그 검색어를 노린 글), 없으면 가장 먼저 공개된 글. 후보가 없으면 null.
 */
function firstlab_calc_pick_home( array $rows ): ?int {
	foreach ( $rows as $row ) {
		if ( str_contains( (string) $row['title'], '계산기' ) ) {
			return (int) $row['id'];
		}
	}
	return $rows ? (int) $rows[0]['id'] : null;
}

/** 계산기 id 를 본문에 넣은 공개 글 (대표 글). 없으면 null. 한 요청 안에서는 한 번만 찾는다. */
function firstlab_calc_home_post( string $calc_id ): ?WP_Post {
	static $cache = array();
	if ( '' === $calc_id ) {
		return null;
	}
	if ( ! array_key_exists( $calc_id, $cache ) ) {
		global $wpdb;
		$like  = '%' . $wpdb->esc_like( '[firstlab_calc id="' . $calc_id . '"' ) . '%';
		$found = $wpdb->get_results(
			$wpdb->prepare(
				"SELECT ID AS id, post_title AS title FROM {$wpdb->posts}
				 WHERE post_type = 'post' AND post_status = 'publish' AND post_content LIKE %s ORDER BY post_date ASC",
				$like
			),
			ARRAY_A
		);
		$id                 = firstlab_calc_pick_home( (array) $found );
		$cache[ $calc_id ] = $id ? get_post( $id ) : null;
	}
	return $cache[ $calc_id ];
}

/** 계산기 단독 페이지가 넘어갈 대표 글 (계산기 페이지가 아니거나 대표 글이 없으면 null). */
function firstlab_calc_page_target( $page ): ?WP_Post {
	return $page instanceof WP_Post && 'page' === $page->post_type
		? firstlab_calc_home_post( firstlab_calc_id_of_slug( (string) $page->post_name ) )
		: null;
}

/** 링크에 쓸 계산기 주소: 대표 글이 있으면 그 글, 없으면 단독 페이지. */
function firstlab_calc_link( WP_Post $page ): string {
	$target = firstlab_calc_page_target( $page );
	return (string) get_permalink( $target ?? $page );
}

add_action(
	'template_redirect',
	static function (): void {
		if ( ! is_page() ) {
			return;
		}
		$target = firstlab_calc_page_target( get_queried_object() );
		if ( $target ) {
			wp_safe_redirect( get_permalink( $target ), 301 );
			exit;
		}
	}
);

// 사이트맵(The SEO Framework): 넘어가는 단독 페이지는 빼고 대표 글만 남김 (FIRSTLAB_CALC_PAGES_NOINDEX 면 계산기 단독 페이지 모두)
add_filter(
	'the_seo_framework_sitemap_exclude_ids',
	static function ( $ids ): array {
		foreach ( get_pages( array( 'post_status' => 'publish' ) ) as $page ) {
			$is_calc = '' !== firstlab_calc_id_of_slug( $page->post_name );
			if ( firstlab_calc_page_target( $page ) || ( FIRSTLAB_CALC_PAGES_NOINDEX && $is_calc ) ) {
				$ids[] = $page->ID;
			}
		}
		return (array) $ids;
	}
);

// 사이트맵은 The SEO Framework 가 저장본(transient)으로 내보내 제외 목록이 바뀌어도 바로 안 바뀜 (11/14: noindex 뒤에도 계산기 71개 남음)
// → 설정(FIRSTLAB_CALC_PAGES_NOINDEX)이 바뀌면 한 번 저장본을 지움
add_action(
	'init',
	static function (): void {
		$want = FIRSTLAB_CALC_PAGES_NOINDEX ? 'noindex' : 'index';
		if ( get_option( 'firstlab_calc_sitemap_mode' ) === $want ) {
			return;
		}
		global $wpdb;
		$wpdb->query( "DELETE FROM {$wpdb->options} WHERE option_name LIKE '\\_transient%tsf%sitemap%'" ); // phpcs:ignore WordPress.DB.DirectDatabaseQuery
		if ( function_exists( 'wp_cache_flush_group' ) ) {
			wp_cache_flush_group( 'transient' );
		}
		update_option( 'firstlab_calc_sitemap_mode', $want, false );
	}
);

// 계산기 단독 페이지·계산기 모음(/calculators/) noindex (FIRSTLAB_CALC_PAGES_NOINDEX)
add_filter(
	'the_seo_framework_robots_meta_array',
	static function ( $meta ) {
		if ( FIRSTLAB_CALC_PAGES_NOINDEX && is_page() ) {
			$page = get_queried_object();
			if ( $page instanceof WP_Post && ( '' !== firstlab_calc_id_of_slug( $page->post_name ) || 'calculators' === $page->post_name ) ) {
				$meta['noindex'] = 'noindex';
			}
		}
		return $meta;
	}
);

// 머리글 메뉴의 '계산기'(→ /calculators/) 링크도 숨김 (11/14 운영자 '계산기 페이지를 홈에서도 안 보이게') — 글 안 계산기는 그대로
add_filter(
	'render_block',
	static function ( string $html, array $block ): string {
		if ( FIRSTLAB_CALC_PAGES_NOINDEX && 'core/navigation-link' === ( $block['blockName'] ?? '' )
			&& '/calculators/' === ( $block['attrs']['url'] ?? '' ) ) {
			return '';
		}
		return $html;
	},
	10,
	2
);
