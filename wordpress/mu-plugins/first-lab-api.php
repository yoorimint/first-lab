<?php
/**
 * Plugin Name: First Lab - 글쓰기 세션용 읽기 API
 * Description: 예약된 글쓰기 세션이 다음 글감 후보와 글쓰기 설정을 읽어 가는 주소 (토큰 필요, 읽기 전용).
 *
 * GET /wp-json/firstlab/v1/next  (헤더 X-Firstlab-Token: <토큰>)
 * 토큰 원문은 서버 /etc/first-lab/secrets.env 의 FIRSTLAB_API_TOKEN, 워드프레스에는 해시만 저장한다 (bot-setup.sh).
 */

defined( 'ABSPATH' ) || exit;

add_action(
	'rest_api_init',
	static function () {
		register_rest_route(
			'firstlab/v1',
			'/next',
			array(
				'methods'             => 'GET',
				'permission_callback' => 'firstlab_api_allowed',
				'callback'            => static function () {
					$state    = (array) get_option( 'firstlab_bot_state', array() );
					$settings = (array) get_option( 'firstlab_bot', array() );
					$recent   = array();
					foreach ( get_posts( array( 'numberposts' => 30, 'post_status' => array( 'publish', 'future', 'draft' ) ) ) as $p ) {
						$recent[] = array(
								'title'  => $p->post_title,
								'slug'   => urldecode( $p->post_name ),
								'status' => $p->post_status,
								'url'    => 'publish' === $p->post_status ? urldecode( get_permalink( $p ) ) : '', // 내부 링크용 (발행 글만)
							);
					}
					return array(
						'write_mode'    => (string) ( $settings['write_mode'] ?? 'direct' ),
						'refresh_target' => firstlab_api_refresh_target( $state ),
						'held_drafts'    => firstlab_api_held_drafts(),
						'candidates'    => array_slice( (array) ( $state['candidates'] ?? array() ), 0, 20 ),
						'recent_posts'  => $recent,
						'persona'       => (string) ( $settings['persona'] ?? '' ),
						'target_length' => (int) ( $settings['target_length'] ?? 3000 ),
						'extra_prompt'  => (string) ( $settings['extra_prompt'] ?? '' ),
						'post_status'   => (string) ( $settings['post_status'] ?? 'draft' ),
					);
				},
			)
		);
		// 매일 점검(automation/maintenance.md): 최근 봇 기록·상태
		register_rest_route(
			'firstlab/v1',
			'/maintenance',
			array(
				'methods'             => 'GET',
				'permission_callback' => 'firstlab_api_allowed',
				'callback'            => 'firstlab_api_maintenance',
			)
		);
	}
);

/**
 * 오늘 갱신할 글 (월정액 방식에서 봇이 새벽에 골라 둔 것) + 현재 본문.
 * base_modified 는 반영할 때 그 사이 글이 수정됐는지 확인하는 값 (bot/refresh.py apply_direct).
 */
function firstlab_api_refresh_target( array $state ): ?array {
	$target = (array) ( $state['refresh_target'] ?? array() );
	$post   = empty( $target['id'] ) ? null : get_post( (int) $target['id'] );
	if ( ! $post || 'publish' !== $post->post_status ) {
		return null;
	}
	return $target + array(
		'base_modified' => $post->post_modified,
		'content_html'  => trim( preg_replace( '/<!--.*?-->/s', '', $post->post_content ) ),
	);
}

/**
 * 검수 관문에서 막힌 초안 (오래된 것부터 3개) — 글쓰기 세션이 고쳐서 다시 올린다 (bot/hold.py).
 */
function firstlab_api_held_drafts(): array {
	$out   = array();
	$posts = get_posts(
		array(
			'post_status' => 'draft',
			'meta_key'    => '_firstlab_hold',
			'orderby'     => 'date',
			'order'       => 'ASC',
			'numberposts' => 10,
		)
	);
	foreach ( $posts as $post ) {
		if ( 'ready' === get_post_meta( $post->ID, '_firstlab_queue', true ) || count( $out ) >= 3 ) {
			continue;
		}
		$out[] = array(
			'post_id'       => $post->ID,
			'title'         => $post->post_title,
			'excerpt'       => $post->post_excerpt,
			'reason'        => (string) get_post_meta( $post->ID, '_firstlab_hold', true ),
			'base_modified' => $post->post_modified,
			'content_html'  => trim( preg_replace( '/<!--.*?-->/s', '', $post->post_content ) ),
		);
	}
	return $out;
}

/**
 * 사진 검사가 안 끝난 글 (오래된 것부터 2개) — 글쓰기 세션이 사진을 직접 보고 결과를 올린다 (bot/image_check.py).
 * round: 이 글 사진을 이미 바꾼 횟수 (bot/image_check.py MAX_ROUND 에 닿으면 서버가 교체 대신 뺀다).
 */
function firstlab_api_image_checks(): array {
	$out   = array();
	$posts = get_posts(
		array(
			'post_status' => array( 'draft', 'publish', 'future' ),
			'meta_query'  => array( array( 'key' => '_firstlab_img_checked', 'compare' => 'NOT EXISTS' ) ),
			'orderby'     => 'date',
			'order'       => 'ASC',
			'numberposts' => -1, // 사진 없는 옛 글이 앞을 막지 않게 전부 훑음 (글 수가 적어 부담 없음)
		)
	);
	foreach ( $posts as $post ) {
		$images = array();
		preg_match_all( '#<img\b[^>]*>#', $post->post_content, $tags );
		foreach ( $tags[0] as $tag ) {
			if ( preg_match( '#src="([^"]+/(ai-[0-9a-f]+)(?:-\d+x\d+)?\.jpg)"#', $tag, $src ) ) {
				preg_match( '#alt="([^"]*)"#', $tag, $alt );
				$images[] = array( 'name' => $src[2], 'url' => $src[1], 'alt' => $alt[1] ?? '' );
			}
		}
		if ( ! $images ) {
			continue;
		}
		$out[] = array(
			'post_id' => $post->ID,
			'title'   => $post->post_title,
			'round'   => (int) get_post_meta( $post->ID, '_firstlab_img_round', true ),
			'images'  => $images,
		);
		if ( count( $out ) >= 2 ) {
			break;
		}
	}
	return $out;
}

/** 토큰 확인 (원문은 서버 비밀값, 워드프레스에는 해시만). */
function firstlab_api_allowed( WP_REST_Request $request ): bool {
	$hash  = (string) get_option( 'firstlab_api_token_hash', '' );
	$token = (string) $request->get_header( 'x-firstlab-token' );
	return '' !== $hash && '' !== $token && hash_equals( $hash, hash( 'sha256', $token ) );
}

/**
 * 매일 점검용: 최근 봇 기록, 연속 작성 상태, 공개 대기 수, 보류 초안(이유).
 */
function firstlab_api_maintenance(): array {
	$state = (array) get_option( 'firstlab_bot_state', array() );
	$held  = array_map(
		static fn( $h ) => array( 'post_id' => $h['post_id'], 'title' => $h['title'], 'reason' => $h['reason'] ),
		firstlab_api_held_drafts()
	);
	return array(
		'log'         => array_slice( (array) ( $state['log'] ?? array() ), 0, 40 ),
		'continuous'  => get_option( 'firstlab_bot_continuous', '0' ),
		'ready_count' => count( get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_queue', 'meta_value' => 'ready', 'numberposts' => -1, 'fields' => 'ids' ) ) ),
		'held_drafts' => $held,
	);
}
