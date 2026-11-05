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
					// 전체 글 (30편으로 자르면 오래된 글과 같은 주제를 또 씀 — 11/5 대부업 대출 조회 중복)
						foreach ( get_posts( array( 'numberposts' => -1, 'post_status' => array( 'publish', 'future', 'draft' ) ) ) as $p ) {
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
						'image_checks'   => firstlab_api_image_checks(), // 사진 검사할 글 (daily-writer.md 5-2)
						'candidates'    => array_slice( (array) ( $state['candidates'] ?? array() ), 0, 20 ),
						// 앞으로 시행될 근거 법령 개정 (bot/law_watch.py) — 아직 글을 안 쓴 것만 (daily-writer.md 1단계)
						'law_changes'   => firstlab_api_law_changes( $state ),
						'recent_posts'  => $recent,
						'persona'       => (string) ( $settings['persona'] ?? '' ),
						'target_length' => (int) ( $settings['target_length'] ?? 3000 ),
						'extra_prompt'  => (string) ( $settings['extra_prompt'] ?? '' ),
						'post_status'   => (string) ( $settings['post_status'] ?? 'draft' ),
					);
				},
			)
		);
		// 감시 세션 외부 검수용: 글 번호로 지금 본문 (patches·보류 수정이 반영된 실제 내용, automation/unreviewed.py)
		register_rest_route(
			'firstlab/v1',
			'/post/(?P<id>\d+)',
			array(
				'methods'             => 'GET',
				'permission_callback' => 'firstlab_api_allowed',
				'callback'            => 'firstlab_api_post',
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
 * 대표 사진 검사가 안 끝난 글 (오래된 것부터 2개, 대표 사진이 없는 글 포함) — 글쓰기 세션이 사진을 직접 보고 결과를 올린다 (bot/image_check.py).
 * round: 이 글 사진을 이미 바꾼 횟수 (bot/image_check.py MAX_ROUND 에 닿으면 서버가 교체 대신 뺀다).
 */
function firstlab_api_image_checks(): array {
	$out   = array();
	$posts = get_posts(
		array(
			'post_status' => array( 'draft', 'publish', 'future' ),
			'meta_query'  => array(
				array( 'key' => '_firstlab_img_checked', 'compare' => 'NOT EXISTS' ),
				array( 'key' => '_firstlab_img_pending', 'compare' => 'NOT EXISTS' ), // 한도 소진으로 사진을 기다리는 글은 채운 뒤에 검사
			),
			'orderby'     => 'date',
			'order'       => 'ASC',
			'numberposts' => 2,
		)
	);
	foreach ( $posts as $post ) {
		$images = array();
		$name   = (string) get_post_meta( $post->ID, '_firstlab_photo', true ); // 대표 사진 (bot/photo.py)
		$thumb  = (int) get_post_thumbnail_id( $post );
		if ( '' !== $name && $thumb ) {
			$images[] = array(
				'name' => $name,
				'url'  => (string) wp_get_attachment_url( $thumb ),
				'alt'  => (string) get_post_meta( $thumb, '_wp_attachment_image_alt', true ),
			);
		}
		$out[] = array( // 대표 사진이 없으면(생성 실패 등) images 가 빈 목록 → 세션이 add 로 새 사진 요청
			'post_id' => $post->ID,
			'title'   => $post->post_title,
			'round'   => (int) get_post_meta( $post->ID, '_firstlab_img_round', true ),
			'images'  => $images,
		);
	}
	return $out;
}

/** 글 번호로 지금 본문 (제목·요약문·본문, 블록 주석 제외). */
function firstlab_api_post( WP_REST_Request $request ) {
	$post = get_post( (int) $request['id'] );
	if ( ! $post || 'post' !== $post->post_type ) {
		return new WP_Error( 'not_found', '글 없음', array( 'status' => 404 ) );
	}
	return array(
		'post_id'      => $post->ID,
		'status'       => $post->post_status,
		'title'        => $post->post_title,
		'excerpt'      => $post->post_excerpt,
		'content_html' => trim( preg_replace( '/<!--.*?-->/s', '', $post->post_content ) ),
		// 근거 대조 결과 (bot/source_check.py, 기록만): 감시 세션이 외부 검수 결과와 비교
		'source_check' => json_decode( (string) get_post_meta( $post->ID, '_firstlab_source_check', true ), true ),
	);
}

/** 법령 감시가 찾은 '앞으로 시행될 개정' 중 아직 글을 쓰지 않은 것 (이미 쓴 것은 law_changes_done). */
function firstlab_api_law_changes( array $state ): array {
	$done = (array) ( $state['law_changes_done'] ?? array() );
	$out  = array();
	foreach ( (array) ( $state['law_changes']['upcoming'] ?? array() ) as $item ) {
		if ( ! in_array( $item['key'] ?? '', $done, true ) ) {
			$out[] = $item;
		}
	}
	return array_slice( $out, 0, 10 );
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
		'law_watch'   => $state['law_changes'] ?? null, // 법령 감시: 곧 시행될 개정·입법예고(확정 아님) — 감시 세션 확인용
		// 공개 대기 글 전체 (감시 세션이 외부 검수 안 한 글을 글 번호로 찾음 — automation/unreviewed.py)
		'ready_posts' => array_map(
			static fn( $p ) => array( 'post_id' => $p->ID, 'title' => $p->post_title, 'slug' => urldecode( $p->post_name ) ),
			get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_queue', 'meta_value' => 'ready', 'numberposts' => -1, 'orderby' => 'date', 'order' => 'ASC' ) )
		),
	);
}

/*
 * 법제처 국가법령정보 공동활용 API 통로: GET /wp-json/firstlab/v1/law?kind=search|service&target=law&query=…
 *
 * 왜: 법령 API 는 신청한 서버 주소에서만 쓰도록 등록돼 있고 신청값(OC)은 서버에만 둔다. 글쓰기·감시 세션(다른 컴퓨터)은
 * 이 통로로 같은 API 를 쓴다 (bot/law_api.py). OC 는 bot-setup.sh 가 서버 비밀값에서 옵션으로 옮겨 둔다.
 * 법령 사이트 화면 읽기는 연결이 자주 끊기고 화면이 바뀌면 틀리게 읽어서 바꿈 (11/5 운영자 신청).
 */
const FIRSTLAB_LAW_TARGETS = array( 'law', 'eflaw', 'admrul', 'ordin', 'lsHistory' ); // 법령, 시행일 기준 법령, 행정규칙(고시·훈령), 자치법규(조례), 개정 이력
const FIRSTLAB_LAW_PARAMS  = array( 'query', 'MST', 'ID', 'JO', 'LID', 'efYd', 'nw', 'display', 'page', 'sort', 'type', 'search', 'ancYd', 'efYdRange' );

add_action(
	'rest_api_init',
	static function () {
		register_rest_route(
			'firstlab/v1',
			'/law',
			array(
				'methods'             => 'GET',
				'permission_callback' => 'firstlab_api_allowed',
				'callback'            => 'firstlab_api_law',
			)
		);
	}
);

/** 법령 API 주소 (OC 포함, 밖으로 내보내지 않음). 허용 안 된 값이면 null. */
function firstlab_law_url( array $args, string $oc ): ?string {
	$kind   = $args['kind'] ?? 'search';
	$target = $args['target'] ?? 'law';
	if ( ! in_array( $kind, array( 'search', 'service' ), true ) || ! in_array( $target, FIRSTLAB_LAW_TARGETS, true ) ) {
		return null;
	}
	$query = array( 'OC' => $oc, 'target' => $target, 'type' => 'XML' );
	foreach ( FIRSTLAB_LAW_PARAMS as $name ) {
		if ( isset( $args[ $name ] ) && '' !== (string) $args[ $name ] ) {
			$query[ $name ] = (string) $args[ $name ];
		}
	}
	$page = 'search' === $kind ? 'lawSearch.do' : 'lawService.do';
	return 'https://www.law.go.kr/DRF/' . $page . '?' . http_build_query( $query );
}

function firstlab_api_law( WP_REST_Request $request ) {
	$oc = (string) get_option( 'firstlab_law_oc', '' );
	if ( '' === $oc ) {
		return new WP_Error( 'no_oc', '서버에 법령 API 신청값(LAW_OPEN_API_OC)이 없음', array( 'status' => 503 ) );
	}
	$url = firstlab_law_url( $request->get_query_params(), $oc );
	if ( null === $url ) {
		return new WP_Error( 'bad_args', 'kind 는 search|service, target 은 ' . implode( '|', FIRSTLAB_LAW_TARGETS ), array( 'status' => 400 ) );
	}
	$key    = 'firstlab_law2_' . md5( $url ); // 2: 첫 판이 오류 응답을 저장했던 것 무시
	$cached = get_transient( $key );
	if ( false !== $cached ) {
		return $cached;
	}
	$res = wp_remote_get( $url, array( 'timeout' => 30 ) );
	if ( is_wp_error( $res ) ) {
		return new WP_Error( 'law_fetch', $res->get_error_message(), array( 'status' => 502 ) );
	}
	$body = (string) wp_remote_retrieve_body( $res );
	$out  = array(
		'status'  => (int) wp_remote_retrieve_response_code( $res ),
		'body'    => str_replace( $oc, '***', $body ), // 오류 응답에 OC 가 섞여 나와도 감춤
		'request' => str_replace( rawurlencode( $oc ), '***', str_replace( $oc, '***', $url ) ) . ' (OC ' . strlen( $oc ) . '자)', // 문제 확인용, OC 는 가림
	);
	// 법제처는 오류도 200 으로 돌려준다 (<Response><result>…실패…) → 저장하지 않고 오류로 표시
	if ( false !== strpos( $body, '<Response>' ) && false !== strpos( $body, '실패' ) ) {
		$out['status'] = 502;
	}
	if ( 200 === $out['status'] && '' !== $body ) {
		set_transient( $key, $out, 6 * HOUR_IN_SECONDS ); // 같은 조문을 하루에 여러 번 묻지 않게
	}
	return $out;
}
