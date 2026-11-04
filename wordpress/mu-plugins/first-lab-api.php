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
					foreach ( get_posts( array( 'numberposts' => 30, 'post_status' => array( 'publish', 'draft' ) ) ) as $p ) {
						$recent[] = array( 'title' => $p->post_title, 'slug' => urldecode( $p->post_name ), 'status' => $p->post_status );
					}
					return array(
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
