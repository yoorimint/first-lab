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
				'permission_callback' => static function ( WP_REST_Request $request ) {
					$hash  = (string) get_option( 'firstlab_api_token_hash', '' );
					$token = (string) $request->get_header( 'x-firstlab-token' );
					return '' !== $hash && '' !== $token && hash_equals( $hash, hash( 'sha256', $token ) );
				},
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
