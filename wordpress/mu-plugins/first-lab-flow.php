<?php
/**
 * Plugin Name: First Lab - 글 흐름 (본문 중간 연결·주제 모음)
 * Description: 검색으로 들어온 손님이 다음 글·계산기·같은 주제 모음으로 이어지게 한다. 저장된 글은 그대로, 화면에서만.
 *
 * 11/9 운영자 '1·2번만 해라' (블로그는 가게 — 글 수보다 손님 동선):
 *  1) 주제 모음: 공개 글이 FIRSTLAB_HUB_MIN 편 이상인 태그의 태그 페이지가 그 주제의 모음 페이지.
 *     글 첫 문단 뒤에 '이 주제 모아 보기' 한 줄, 태그 페이지 위에 계산기·전체 글 목록 ([firstlab_hub_intro]).
 *  2) 본문 중간 연결: 두 번째 소제목 앞에 '이어서 보면 좋은 글'(관련 글 1편)과 관련 계산기 1개.
 *     하단 '함께 보면 좋은 글'은 여기서 보여 준 글을 빼고 채움 (first-lab-related.php).
 * 글이 공개되는 대로 저절로 늘어난다 — 따로 만들 페이지·손볼 글 없음.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_HUB_MIN      = 3; // 태그에 공개 글이 이만큼 있어야 주제 모음으로 씀 (1~2편은 모음이라 할 수 없음)
const FIRSTLAB_FLOW_CALC_MIN = 2; // 계산기 제목과 글 제목의 주제 글자쌍이 이만큼 겹쳐야 '관련 계산기'

/** 두 번째 <h2 앞에 $box 를 넣음. 소제목이 2개 미만이면 null (넣을 자리 없음). */
function firstlab_flow_insert_before_h2( string $html, string $box, int $nth = 2 ): ?string {
	$pos = -1;
	for ( $i = 0; $i < $nth; $i++ ) {
		$pos = strpos( $html, '<h2', $pos + 1 );
		if ( false === $pos ) {
			return null;
		}
	}
	return substr( $html, 0, $pos ) . $box . "\n" . substr( $html, $pos );
}

/** 첫 문단(</p>) 뒤에 $line 을 넣음. 문단이 없으면 맨 앞. */
function firstlab_flow_after_first_p( string $html, string $line ): string {
	$pos = strpos( $html, '</p>' );
	return false === $pos ? $line . $html : substr( $html, 0, $pos + 4 ) . "\n" . $line . substr( $html, $pos + 4 );
}

/** 계산기 후보 [['title','url']] 중 글 제목과 가장 많이 겹치는 것 (FIRSTLAB_FLOW_CALC_MIN 미만이면 null). */
function firstlab_flow_pick_calc( string $title, array $calcs ): ?array {
	$best = null;
	$top  = FIRSTLAB_FLOW_CALC_MIN - 1;
	foreach ( $calcs as $c ) {
		$s = firstlab_related_score( $title, (string) $c['title'] );
		if ( $s > $top ) {
			$top  = $s;
			$best = $c;
		}
	}
	return $best;
}

/** 글의 태그 [['name','count','url']] 중 주제 모음으로 쓸 태그 (공개 글이 가장 많은 것, 같으면 이름 짧은 것). */
function firstlab_hub_pick( array $tags ): ?array {
	$ok = array_values( array_filter( $tags, static fn( $t ) => (int) $t['count'] >= FIRSTLAB_HUB_MIN ) );
	usort( $ok, static fn( $a, $b ) => array( (int) $b['count'], mb_strlen( $a['name'] ) ) <=> array( (int) $a['count'], mb_strlen( $b['name'] ) ) );
	return $ok[0] ?? null;
}

/** 공개된 계산기 [['title','url']] (계산기를 넣은 대표 글이 있으면 그 글 주소 — first-lab-calc-home.php). */
function firstlab_flow_calcs(): array {
	static $calcs = null;
	if ( null === $calcs ) {
		$calcs = array();
		foreach ( get_pages( array( 'post_status' => 'publish' ) ) as $p ) {
			if ( str_ends_with( urldecode( $p->post_name ), '-calculator' ) ) {
				$calcs[] = array( 'title' => $p->post_title, 'url' => function_exists( 'firstlab_calc_link' ) ? firstlab_calc_link( $p ) : get_permalink( $p ) );
			}
		}
	}
	return $calcs;
}

if ( function_exists( 'add_filter' ) ) {
	add_filter(
		'the_content',
		static function ( $content ) {
			if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() || ! function_exists( 'firstlab_related_ids' ) ) {
				return $content;
			}
			$post_id = get_the_ID();
			$title   = get_the_title( $post_id );

			// 1) 주제 모음 한 줄 (첫 문단 뒤)
			$tags = array_map(
				static fn( $t ) => array( 'name' => $t->name, 'count' => $t->count, 'url' => get_tag_link( $t ) ),
				(array) get_the_tags( $post_id )
			);
			$hub = $tags ? firstlab_hub_pick( $tags ) : null;
			if ( $hub ) {
				$content = firstlab_flow_after_first_p(
					$content,
					sprintf( '<p class="fl-hub-link">📚 <a href="%s">%s 글 %d편 모아 보기</a></p>', esc_url( $hub['url'] ), esc_html( $hub['name'] ), (int) $hub['count'] )
				);
			}

			// 2) 본문 중간: 이어서 볼 글 1편 + 관련 계산기 (본문에 이미 계산기가 있으면 계산기는 안 붙임)
			$items = '';
			foreach ( firstlab_related_ids( $post_id, 3 ) as $id ) {
				if ( false === strpos( $content, (string) get_permalink( $id ) ) ) { // 본문에 이미 링크된 글은 건너뜀
					$items .= sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $id ) ), esc_html( get_the_title( $id ) ) );
					$GLOBALS['firstlab_flow_shown'] = array( $id );
					break;
				}
			}
			$has_calc = false !== strpos( $content, 'firstlab-calc' ) || false !== strpos( $content, '[firstlab_calc' );
			$calc     = $has_calc ? null : firstlab_flow_pick_calc( $title, firstlab_flow_calcs() );
			if ( $calc ) {
				$items .= sprintf( '<li>🧮 <a href="%s">%s로 직접 계산해 보기</a></li>', esc_url( $calc['url'] ), esc_html( $calc['title'] ) );
			}
			if ( $items ) {
				$box  = '<aside class="fl-next" aria-label="이어서 보면 좋은 글"><p class="fl-next-title">이어서 보면 좋은 글</p><ul>' . $items . '</ul></aside>';
				$with = firstlab_flow_insert_before_h2( $content, $box );
				if ( null === $with ) {
					$GLOBALS['firstlab_flow_shown'] = array(); // 넣을 자리가 없으면 하단 목록에 그대로 둠
				} else {
					$content = $with;
				}
			}
			return $content;
		},
		15
	);

	// 주제 모음 페이지(태그 페이지) 위: 계산기와 전체 글 목록 — templates/archive.html 의 [firstlab_hub_intro]
	add_shortcode(
		'firstlab_hub_intro',
		static function (): string {
			$tag = is_tag() ? get_queried_object() : null;
			if ( ! $tag || (int) $tag->count < FIRSTLAB_HUB_MIN ) {
				return '';
			}
			$posts = get_posts( array( 'tag_id' => $tag->term_id, 'numberposts' => 50, 'post_status' => 'publish', 'orderby' => 'title', 'order' => 'ASC' ) );
			$calcs = array_filter( firstlab_flow_calcs(), static fn( $c ) => firstlab_related_score( $tag->name, (string) $c['title'] ) > 0 );
			$out   = sprintf( '<p class="fl-hub-lead">%s 관련 글 %d편을 한곳에 모았어요.</p>', esc_html( $tag->name ), count( $posts ) );
			if ( $calcs ) {
				$out .= '<ul class="fl-hub-calcs">' . implode( '', array_map( static fn( $c ) => sprintf( '<li>🧮 <a href="%s">%s</a></li>', esc_url( $c['url'] ), esc_html( $c['title'] ) ), $calcs ) ) . '</ul>';
			}
			$out .= '<ul class="fl-hub-list">' . implode( '', array_map( static fn( $p ) => sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $p ) ), esc_html( $p->post_title ) ), $posts ) ) . '</ul>';
			return '<section class="fl-hub">' . $out . '</section>';
		}
	);
}
