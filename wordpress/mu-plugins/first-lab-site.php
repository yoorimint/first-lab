<?php
/**
 * Plugin Name: First Lab - 사이트 공통 기능
 * Description: 사이트 공통 상수와 숏코드.
 *
 * [firstlab_email] : 연락용 이메일을 스팸봇이 수집하기 어렵게 인코딩해 mailto 링크로 출력.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_CONTACT_EMAIL = 'cldud0070@naver.com';

add_shortcode(
	'firstlab_email',
	static function () {
		$email = antispambot( FIRSTLAB_CONTACT_EMAIL );
		return sprintf( '<a href="mailto:%1$s">%1$s</a>', $email );
	}
);

/**
 * 사이트 아이콘(파비콘): 구글 검색 결과·브라우저 탭·휴대폰 홈 화면에 보인다.
 * 파일은 server/make-icons.py 로 만든 테마 assets/icons/. /favicon.ico 는 배포 때 웹 루트에 복사(server/wp-setup.sh).
 * 관리자 '사이트 아이콘'을 따로 정하면 워드프레스가 그것을 쓰므로 여기서는 내보내지 않는다(중복 방지).
 */
add_action(
	'wp_head',
	static function () {
		if ( has_site_icon() ) {
			return;
		}
		$dir = get_stylesheet_directory_uri() . '/assets/icons/';
		printf( '<link rel="icon" href="%s" sizes="48x48">' . "\n", esc_url( home_url( '/favicon.ico' ) ) );
		printf( '<link rel="icon" href="%s" type="image/svg+xml">' . "\n", esc_url( $dir . 'favicon.svg' ) );
		printf( '<link rel="icon" href="%s" type="image/png" sizes="192x192">' . "\n", esc_url( $dir . 'icon-192.png' ) );
		printf( '<link rel="apple-touch-icon" href="%s">' . "\n", esc_url( $dir . 'apple-touch-icon.png' ) );
	},
	2
);

// 글 제목에서 사이트 이름(' - 퍼스트랩') 빼기 (11/6 운영자 제목 SEO 3번): 검색 결과는 한글 30자 안팎에서 잘리는데 글 제목에
// 7자가 더 붙어 핵심 숫자가 잘림. 홈·페이지(계산기 목록 등)는 그대로. The SEO Framework 필터.
add_filter(
	'the_seo_framework_use_title_branding',
	static function ( $use, $args = null ) {
		$id = is_array( $args ) && isset( $args['id'] ) ? (int) $args['id'] : ( null === $args && is_singular( 'post' ) ? (int) get_queried_object_id() : 0 );
		return $id && 'post' === get_post_type( $id ) ? false : $use;
	},
	10,
	2
);


/**
 * 글의 검색 설명(meta description)은 요약문 전체 그대로 (11/17 구글 문서 점검: 23편 중 21편이 '합계 최대 1…'처럼
 * 문장 중간에서 잘림 — The SEO Framework 가 영어 기준 글자 수로 자름. 요약문은 봇이 120자 이내 완결 문장으로 씀).
 * 요약문이 없는 글·분류·홈은 플러그인 그대로.
 */
function firstlab_full_excerpt_description( $desc, $args = null, $type = 'search' ) {
	$id = is_array( $args ) && isset( $args['id'] ) && empty( $args['tax'] ) && empty( $args['pta'] ) && empty( $args['uid'] )
		? (int) $args['id']
		: ( null === $args && is_singular( 'post' ) ? (int) get_queried_object_id() : 0 );
	if ( ! $id || 'post' !== get_post_type( $id ) ) {
		return $desc;
	}
	$excerpt = trim( preg_replace( '/\s+/u', ' ', wp_strip_all_tags( (string) get_post_field( 'post_excerpt', $id ) ) ) );
	return '' !== $excerpt ? $excerpt : $desc;
}
add_filter( 'the_seo_framework_generated_description', 'firstlab_full_excerpt_description', 10, 3 );

/**
 * 분류·태그 목록의 검색 설명 — 설명을 따로 안 적어 비어 있었음 (11/19 빙 사이트 검사 'Meta Description 누락' 104쪽).
 * '퍼스트랩 <이름> 글 N편: 최근 글 제목 2개 등' 으로 만든다. 설명을 직접 적은 분류는 그대로.
 */
function firstlab_term_description( $desc, $args = null, $type = 'search' ) {
	if ( '' !== trim( (string) $desc ) ) {
		return $desc;
	}
	if ( is_array( $args ) && ! empty( $args['tax'] ) ) {
		$term = get_term( (int) ( $args['id'] ?? 0 ), (string) $args['tax'] );
	} elseif ( null === $args && ( is_category() || is_tag() ) ) {
		$term = get_queried_object();
	} else {
		return $desc;
	}
	if ( ! $term instanceof WP_Term ) {
		return $desc;
	}
	$titles = get_posts(
		array(
			'post_type'   => 'post',
			'post_status' => 'publish',
			'numberposts' => 2,
			'fields'      => 'ids',
			'tax_query'   => array( array( 'taxonomy' => $term->taxonomy, 'terms' => $term->term_id ) ),
		)
	);
	$titles = array_map( static fn( $id ) => wp_strip_all_tags( get_the_title( $id ) ), $titles );
	$text   = sprintf( '퍼스트랩 %s 글 %d편', $term->name, (int) $term->count );
	return $titles ? $text . ': ' . implode( ', ', $titles ) . ' 등.' : $text . '.';
}
add_filter( 'the_seo_framework_generated_description', 'firstlab_term_description', 11, 3 );

// 분류·태그 페이지 제목의 '[카테고리:]'·'[태그:]' 머리말 빼기 — 구글 검색 결과에 '[카테고리:] 생활정보 - 퍼스트랩'으로 나와
// 관리 안 된 사이트처럼 보임 (11/16 운영자 캡처, 애드센스 '구조적 유지관리'). SEO 플러그인(The SEO Framework) 제목과 화면 제목 둘 다.
add_filter( 'the_seo_framework_use_archive_prefix', '__return_false' );
add_filter( 'get_the_archive_title_prefix', '__return_empty_string' );

// 글의 작성자 이름 링크가 /author/firstlab-1/ (404 — 작성자 목록 페이지는 막혀 있음)로 가던 것 → '소개' 페이지로 (11/16 사이트 전체 점검).
// 깨진 링크가 모든 글에 하나씩 있으면 관리 안 된 사이트로 보임 (애드센스 '구조적 유지관리').
add_filter( 'author_link', static fn() => home_url( '/about/' ) );

/**
 * 한글 주소의 %xx 를 대문자로 (11/16 서치콘솔 'Sitemaps: 감지된 참조 사이트맵 없음 / 참조 페이지 없음').
 * 워드프레스는 한글 주소를 소문자(%ec%9e%90…)로 내보내는데, 구글은 대문자(%EC%9E%90…)로 기억함 (URL 검사 API 도 대문자로만 찾음 —
 * bot/index_check.google_form). 사이트맵·대표 주소(canonical)·글끼리 링크가 구글이 아는 주소와 글자가 달라 연결이 안 됐을 수 있음.
 * 대문자가 표준 표기(RFC 3986 6.2.2.1)라 모든 주소를 대문자로 맞춘다. 소문자 주소로 들어와도 같은 글이 열림.
 */
function firstlab_upper_pct( string $url ): string {
	return (string) preg_replace_callback( '/%[0-9a-f]{2}/i', static fn( $m ) => strtoupper( $m[0] ), $url );
}
foreach ( array( 'post_link', 'page_link', 'post_type_link', 'term_link', 'attachment_link' ) as $firstlab_link_hook ) {
	add_filter( $firstlab_link_hook, 'firstlab_upper_pct', 99 );
}

/**
 * 본문 속 우리 사이트 링크를 대표 주소(canonical) 표기로: 한글 그대로·소문자 %xx → 대문자 %XX.
 * 11/17 구글 '중복 URL 통합' 문서 점검: 내부 링크는 대표 주소로 일관되게 — 856개 중 1개가 한글 그대로였음
 * (글쓰기 작업이 본문에 'https://first-lab.kr/건물등기부등본발급/' 처럼 씀).
 */
function firstlab_canonical_hrefs( string $html ): string {
	return (string) preg_replace_callback(
		'~href="((?:https://first-lab\.kr)?/)([^"?#]*)~u',
		static function ( $m ) {
			$path = implode( '/', array_map( static fn( $seg ) => rawurlencode( rawurldecode( $seg ) ), explode( '/', $m[2] ) ) );
			return 'href="' . $m[1] . $path;
		},
		$html
	);
}
add_filter( 'the_content', 'firstlab_canonical_hrefs', 99 );
