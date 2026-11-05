<?php
/**
 * Plugin Name: First Lab - 기사 구조화 데이터·업데이트 날짜 (AEO·GEO)
 * Description: 글 화면에 Article 구조화 데이터(작성자·발행일·수정일·대표 사진·공식 출처 인용)와 상단 업데이트 날짜를 넣는다.
 *
 * 왜: 검색 답변 상자·AI 요약(AEO), ChatGPT·퍼플렉시티 같은 생성형 AI 인용(GEO)은 '누가, 언제, 무엇을 근거로' 쓴 글인지
 * 기계가 읽을 수 있어야 고른다. SEO 플러그인(The SEO Framework 무료판)은 글을 일반 WebPage 로만 표시해서 따로 넣는다 (11/5 운영자 승인).
 * 공식 출처는 글 끝 '공식 안내 바로가기'(bot/sources.py 가 실제로 열리는 정부·공공기관 주소만 넣음) 링크를 그대로 쓴다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_SOURCES_HEADING = '공식 안내 바로가기';

/** 글 끝 '공식 안내 바로가기' 목록의 링크 주소. */
function firstlab_official_links( string $content ): array {
	$at = strpos( $content, FIRSTLAB_SOURCES_HEADING );
	if ( false === $at ) {
		return array();
	}
	preg_match_all( '/<a\s[^>]*href="(https:\/\/[^"]+)"/i', substr( $content, $at ), $m );
	return array_values( array_unique( $m[1] ) );
}

/** Article 구조화 데이터 (글 화면에서만). */
function firstlab_article_schema( WP_Post $post ): array {
	$url    = get_permalink( $post );
	$author = get_the_author_meta( 'display_name', $post->post_author );
	$site   = get_bloginfo( 'name' );
	$data   = array(
		'@context'         => 'https://schema.org',
		'@type'            => 'Article',
		'@id'              => $url . '#article',
		'mainEntityOfPage' => $url,
		'headline'         => mb_substr( wp_strip_all_tags( get_the_title( $post ) ), 0, 110 ),
		'description'      => wp_strip_all_tags( get_the_excerpt( $post ) ),
		'datePublished'    => get_the_date( 'c', $post ),
		'dateModified'     => get_the_modified_date( 'c', $post ),
		'inLanguage'       => 'ko-KR',
		'author'           => array(
			'@type' => ( $author === $site ) ? 'Organization' : 'Person',
			'name'  => $author,
			'url'   => home_url( '/' ),
		),
		'publisher'        => array(
			'@type' => 'Organization',
			'name'  => $site,
			'url'   => home_url( '/' ),
			'logo'  => array(
				'@type' => 'ImageObject',
				'url'   => get_stylesheet_directory_uri() . '/assets/icons/icon-192.png',
			),
		),
	);
	$image = get_the_post_thumbnail_url( $post, 'full' );
	if ( $image ) {
		$data['image'] = array( $image );
	}
	$cats = get_the_category( $post->ID );
	if ( $cats ) {
		$data['articleSection'] = $cats[0]->name;
	}
	$links = firstlab_official_links( $post->post_content );
	if ( $links ) {
		$data['citation'] = $links; // 글의 근거가 된 정부·공공기관 페이지
	}
	return $data;
}

add_action(
	'wp_head',
	static function () {
		if ( ! is_singular( 'post' ) ) {
			return;
		}
		$json = wp_json_encode( firstlab_article_schema( get_post() ), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES );
		echo '<script type="application/ld+json">' . $json . '</script>' . "\n"; // phpcs:ignore WordPress.Security.EscapeOutput -- JSON 인코딩됨
	},
	20
);

/** 글 맨 위 업데이트 날짜 (발행 뒤 고쳤으면 '업데이트', 아니면 '작성'). 독자·AI 모두 정보 시점을 알 수 있게. */
add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		$post     = get_post();
		$modified = get_the_modified_date( 'Y-m-d', $post );
		$label    = ( $modified > get_the_date( 'Y-m-d', $post ) ) ? '업데이트' : '작성';
		$line     = sprintf(
			'<p class="firstlab-updated" style="font-size:0.85em;color:#666;margin:0 0 1.2em">%s <time datetime="%s">%s</time></p>',
			esc_html( $label ),
			esc_attr( get_the_modified_date( 'c', $post ) ),
			esc_html( get_the_modified_date( 'Y년 n월 j일', $post ) )
		);
		return $line . $content;
	},
	5
);
