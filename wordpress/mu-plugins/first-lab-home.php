<?php
/**
 * Plugin Name: First Lab - 홈·목록 화면
 * Description: 홈은 '큰 대표 글 1개 + 사진 목록', 카테고리 목록은 '사진 목록'. 계산기 모음 숏코드.
 *
 * [firstlab_feed]      : 현재 목록(메인 쿼리)을 사진 목록으로 출력. 홈 첫 페이지면 대표 글을 크게.
 *                        템플릿에서는 wp:html 블록에 넣는다 (wp:shortcode 블록은 자동 <p> 를 끼워 넣음).
 * [firstlab_calc_list] : 발행된 계산기 페이지(주소가 -calculator 로 끝남) 목록. 링크는 그 계산기를 넣은 공개 글이 있으면 그 글.
 *
 * 대표 글 순서: ① 관리자가 '맨 위에 고정'한 글 → ② 서치콘솔 클릭 1위 글(봇이 option firstlab_popular_url 에 저장,
 * bot/search_console.py) → ③ 최신 글.
 * 사진: 대표 이미지 (AI 사진 1장, 없으면 제목 카드). 글 화면 맨 위에는 AI 대표 사진만 보인다 (제목 카드는 숨김).
 * 스타일: 테마 assets/feed.css
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_POPULAR_OPTION = 'firstlab_popular_url';

// 고정 글이 목록 맨 앞에 끼어들지 않게 (고정 글은 대표 글 자리에만 쓴다)
add_action(
	'pre_get_posts',
	static function ( WP_Query $query ) {
		if ( ! is_admin() && $query->is_main_query() && $query->is_home() ) {
			$query->set( 'ignore_sticky_posts', true );
		}
	}
);

add_action(
	'wp_enqueue_scripts',
	static function () {
		if ( is_home() || is_archive() || is_page( 'calculators' ) ) {
			$rel = '/assets/feed.css';
			wp_enqueue_style( 'firstlab-feed', get_stylesheet_directory_uri() . $rel, array(), (string) filemtime( get_stylesheet_directory() . $rel ) );
		}
	}
);

/** 발행된 글이면 그 글, 아니면 null. */
function firstlab_published_post( int $id ): ?WP_Post {
	$post = $id ? get_post( $id ) : null;
	return ( $post && 'post' === $post->post_type && 'publish' === $post->post_status ) ? $post : null;
}

/** 대표 글: 고정 글 → 인기 글 → null(최신 글 사용). */
function firstlab_home_hero(): ?WP_Post {
	$sticky = array_map( 'intval', (array) get_option( 'sticky_posts', array() ) );
	foreach ( array_reverse( $sticky ) as $id ) { // 가장 최근에 고정한 글 우선
		$post = firstlab_published_post( $id );
		if ( $post ) {
			return $post;
		}
	}
	$popular = (string) get_option( FIRSTLAB_POPULAR_OPTION, '' );
	return '' === $popular ? null : firstlab_published_post( url_to_postid( $popular ) );
}

/** 글 사진 = 대표 이미지 (AI 사진 1장, 없으면 제목 카드 — bot/photo.py). */
function firstlab_post_photo( WP_Post $post, string $size, array $attr = array() ): string {
	return get_the_post_thumbnail( $post, $size, $attr + array( 'alt' => '', 'loading' => 'lazy', 'decoding' => 'async' ) );
}

// 글 화면 맨 위: AI 대표 사진만 보인다 (운영자 결정 11/4). 사진이 아직 없어 제목 카드인 글은 제목과 겹치니 숨김.
// 본문 안에는 사진을 넣지 않는다 (bot/photo.py).
add_filter(
	'render_block_core/post-featured-image',
	static function ( $html, $block, $instance ) {
		$post_id = (int) ( $instance->context['postId'] ?? 0 );
		$on_post = is_singular( 'post' ) && $post_id === get_queried_object_id();
		return ( $on_post && '' === (string) get_post_meta( $post_id, '_firstlab_photo', true ) ) ? '' : $html;
	},
	10,
	3
);

function firstlab_feed_item( WP_Post $post, bool $hero ): string {
	$cats  = get_the_category( $post->ID );
	$cat   = $cats ? sprintf( '<span class="fl-cat">%s</span>', esc_html( $cats[0]->name ) ) : '';
	$photo = $hero
		? firstlab_post_photo( $post, 'large', array( 'loading' => 'eager', 'fetchpriority' => 'high' ) )
		: firstlab_post_photo( $post, 'medium_large', array( 'sizes' => '(max-width: 600px) 120px, 240px' ) );
	$title = $hero ? 'h2' : 'h3';
	return sprintf(
		'<article class="%1$s"><a class="fl-photo" href="%2$s" tabindex="-1" aria-hidden="true">%3$s</a><div class="fl-text">%4$s<%5$s class="fl-title"><a href="%2$s">%6$s</a></%5$s><p class="fl-excerpt">%7$s</p><time datetime="%8$s">%9$s</time></div></article>',
		$hero ? 'fl-hero' : 'fl-item',
		esc_url( get_permalink( $post ) ),
		$photo,
		$cat,
		$title,
		esc_html( get_the_title( $post ) ),
		esc_html( get_the_excerpt( $post ) ),
		esc_attr( get_the_date( 'c', $post ) ),
		esc_html( get_the_date( '', $post ) )
	);
}

add_shortcode(
	'firstlab_feed',
	static function (): string {
		global $wp_query;
		$posts = (array) $wp_query->posts;
		if ( ! $posts ) {
			return '<p>아직 글이 없습니다.</p>';
		}
		$out = '';
		if ( is_home() && ! is_paged() ) {
			$hero  = firstlab_home_hero() ?? $posts[0];
			$posts = array_filter( $posts, static fn( $p ) => $p->ID !== $hero->ID );
			$out  .= firstlab_feed_item( $hero, true );
		}
		$out .= '<div class="fl-list">' . implode( '', array_map( static fn( $p ) => firstlab_feed_item( $p, false ), $posts ) ) . '</div>';
		$pages = paginate_links( array( 'total' => (int) $wp_query->max_num_pages, 'prev_text' => '이전', 'next_text' => '다음' ) );
		return '<div class="fl-feed">' . $out . ( $pages ? '<nav class="fl-pages" aria-label="페이지">' . $pages . '</nav>' : '' ) . '</div>';
	}
);

add_shortcode(
	'firstlab_calc_list',
	static function (): string {
		$pages = array_filter(
			get_pages( array( 'post_status' => 'publish', 'sort_column' => 'post_title' ) ),
			static fn( $p ) => str_ends_with( urldecode( $p->post_name ), '-calculator' )
		);
		$items = array_map(
			// 계산기를 넣은 공개 글이 있으면 그 글로 (first-lab-calc-home.php — 검색어가 글 하나로 모이게)
			static fn( $p ) => sprintf( '<li><a href="%s">%s</a></li>', esc_url( firstlab_calc_link( $p ) ), esc_html( $p->post_title ) ),
			$pages
		);
		return $items ? '<ul class="fl-calc-list">' . implode( '', $items ) . '</ul>' : '<p>준비 중입니다.</p>';
	}
);
