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
				'callback'            => static function ( $request = null ) {
					$state    = (array) get_option( 'firstlab_bot_state', array() );
					$settings = (array) get_option( 'firstlab_bot', array() );
					$recent   = array();
					// 후보 쪽: 40개를 다 건너뛰면 글쓰기 작업이 ?page=2, 3 으로 다음 40개 (11/6 '새 글 없음' 안전장치, bot/run.CANDIDATES_POOL)
					$page     = $request ? max( 1, min( 3, (int) $request->get_param( 'page' ) ) ) : 1;
					$pool     = array_values( (array) ( $state['candidates'] ?? array() ) );
					// 글쓰기 동시 실행 (11/6 운영자 '둘 다 해 봐', bot/run.WRITERS): ?slot=N 이면 후보를 키워드로 나눠 줌 — 두 실행이 같은 키워드를
					// 고르지 않게. 2번부터는 새 글만 (보류 수정·갱신·사진 검사·법령·새 혜택·계산기는 1번 실행 몫 — 같은 일을 겹쳐 하지 않게)
					$slot     = $request ? max( 0, min( 2, (int) $request->get_param( 'slot' ) ) ) : 0;
					// 설정 '2번 작성자' 꺼짐(11/7 운영자) → 1번 칸이 후보 전부를 받음 (반만 받으면 나머지 주제는 아무도 안 씀)
					$solo = 1 === $slot && '0' === (string) get_option( 'firstlab_writer2', '1' );
					if ( $slot && ! $solo ) {
						// 같은 주제(bot/select_keyword.topic_key)는 같은 칸으로 — 11/7 두 칸이 '연차수당 계산'·'미사용 연차수당 계산'을 동시에 씀
						$pool = array_values( array_filter( $pool, static fn( $c ) => firstlab_api_slot_of( (string) ( $c['topic'] ?? $c['keyword'] ?? '' ) ) === $slot ) );
					}
					$side = $slot < 2; // 1번(또는 혼자 도는) 실행만 곁일
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
						'refresh_target' => $side ? firstlab_api_refresh_target( $state ) : null,
						'held_drafts'    => $side ? firstlab_api_held_drafts() : array(),
						'image_checks'   => $side ? firstlab_api_image_checks() : array(), // 사진 검사할 글 (daily-writer.md 5-2)
						'candidates'    => array_slice( $pool, ( $page - 1 ) * 40, 40 ), // bot/run.CANDIDATES (11/6 20→40)
						'candidates_page' => $page,
						'slot'            => $slot,
						'candidates_more' => count( $pool ) > $page * 40, // 다음 쪽(?page=N+1)이 있음
						// 앞으로 시행될 근거 법령 개정 (bot/law_watch.py) — 아직 글을 안 쓴 것만 (daily-writer.md 1단계)
						'law_changes'   => $side ? firstlab_api_law_changes( $state ) : array(),
						// 새로 생긴 전국·광역 혜택 (bot/benefit_watch.py) — 검색량이 생기기 전에 먼저 쓰는 글감, 이미 쓴 것은 뺌
						'new_benefits'  => ! $side ? array() : array_values( array_filter( (array) ( $state['new_benefits'] ?? array() ),
							static fn( $b ) => ! in_array( $b['key'] ?? '', (array) ( $state['new_benefits_done'] ?? array() ), true ) ) ),
						// 새 사업·정책 (정책브리핑 보도자료, bot/policy_watch.py) — 법이 아닌 예산 사업, 정부24 등록 전에 먼저 쓰는 글감
						'new_policies'  => ! $side ? array() : array_values( array_filter( (array) ( $state['new_policies'] ?? array() ),
							static fn( $b ) => ! in_array( $b['key'] ?? '', (array) ( $state['new_policies_done'] ?? array() ), true ) ) ),
						// 아직 안 다룬 분야의 곧 시행될 법령 (법제처, bot/new_law_watch.py) — 2~8주 뒤 시행
						'new_laws'      => ! $side ? array() : array_values( array_filter( (array) ( $state['new_laws'] ?? array() ),
							static fn( $b ) => ! in_array( $b['key'] ?? '', (array) ( $state['new_laws_done'] ?? array() ), true ) ) ),
						// 최신 금리 (한국은행 ECOS, bot/bok_api.py) — 대출·이자 글의 금리 예시는 이 값 기준 (daily-writer.md)
						'rates'         => $state['rates'] ?? null,
						// 다음 해 요율·금액 확인이 안 된 계산기 (bot/calc_watch.py, 11/15~) — daily-writer.md 4-2
						'calc_yearly'   => $side ? (array) ( $state['calc_yearly'] ?? array() ) : array(),
						// 공개 대기 글이 많으면 새 글 대신 외부 검수할 대기 글 (bot/review_queue.py, daily-writer.md 0단계)
						'review_target' => $side ? ( $state['review_target'] ?? null ) : null,
						// 배포가 막힘(대개 검사 실패) — 글쓰기 작업이 먼저 고침 (bot/deploy_watch.py)
						'deploy_alert'  => $side ? ( $state['deploy_alert'] ?? null ) : null,
						// 고친 내용이 서버 글에 안 들어간 패치 (bot/patch_check.py) — 글쓰기 작업이 갱신 파일로 고침
						'patch_problems' => $side ? (array) ( $state['patch_problems'] ?? array() ) : array(),
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
		// 자동 운영 건강 상태 (공개, 비밀값 없음) — GitHub Actions 가 하루 1번 확인해 이상하면 실패 → 운영자에게 메일
		// (Claude 와 상관없는 알림 통로: 예약 작업 토큰이 끊기거나 요금제 한도로 글쓰기가 멈춰도 알 수 있게, 11/5 검토)
		register_rest_route(
			'firstlab/v1',
			'/health',
			array(
				'methods'             => 'GET',
				'permission_callback' => '__return_true',
				'callback'            => static function () {
					$state = (array) get_option( 'firstlab_bot_state', array() );
					return array(
						'now'             => current_time( 'Y-m-d H:i' ),
						'last_done_at'    => (string) ( $state['last_done_at'] ?? '' ),     // 마지막 글·검수 결과가 서버에 들어온 시각
						'last_fire_ok_at' => (string) ( $state['last_fire_ok_at'] ?? '' ),  // 마지막 글쓰기 작업 실행 성공
						'last_fire_error_at' => (string) ( $state['last_fire_error_at'] ?? '' ),
						'deploy_stuck'    => ! empty( $state['deploy_alert'] ),
						'expiring'        => array_values( (array) ( $state['secrets_expiring'] ?? array() ) ), // 곧 만료되는 키 이름만
						'ready_count'     => count( get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_queue', 'meta_value' => 'ready', 'numberposts' => -1, 'fields' => 'ids' ) ) ),
					);
				},
			)
		);
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
/** 보류 수정 횟수 [횟수, 마지막 날짜] (bot/hold.py TRIES_META '횟수|날짜'). */
function firstlab_api_hold_tries( int $post_id ): array {
	$parts = explode( '|', (string) get_post_meta( $post_id, '_firstlab_hold_tries', true ) );
	return array( (int) ( $parts[0] ?? 0 ), (string) ( $parts[1] ?? '' ) );
}

/** 3번 넘게 막혔거나 포기한 글이 아직 쉬는 중인지 (마지막 시도 뒤 30일). */
function firstlab_api_hold_resting( int $post_id ): bool {
	list( $tries, $last ) = firstlab_api_hold_tries( $post_id );
	return $tries >= 3 && $last && strtotime( $last . ' +30 days' ) > time();
}

/**
 * 후보 키워드가 맡겨지는 글쓰기 칸 (1 또는 2). 키워드 글자로 정해 후보 순서가 바뀌어도 늘 같은 칸.
 * 11/6: 순번(짝·홀)으로 나눴더니 2번 칸이 받은 뒤 후보 목록이 갱신돼 순번이 한 칸 밀림 → 1번 칸도 같은 키워드(프리랜서3.3%계산기)를 받아 한 실행을 버림.
 */
function firstlab_api_slot_of( string $keyword ): int {
	return ( crc32( (string) preg_replace( '/\s+/u', '', $keyword ) ) % 2 ) + 1;
}

function firstlab_api_held_drafts(): array {
	$out   = array();
	$posts = get_posts(
		array(
			'post_status' => 'draft',
			'meta_key'    => '_firstlab_hold',
			'orderby'     => 'date',
			'order'       => 'ASC',
			'numberposts' => -1, // 쉬는 글(포기·3번 막힘)이 쌓여도 새 보류 글이 보이게 (11/5 검토)
		)
	);
	// 고쳐도 3번 막혔거나 포기한 글은 30일 동안 빼서 다른 보류 글이 차례를 받게 함 (bot/hold.py TRIES_META, 11/5 '평생 보류로 있나?')
	usort( $posts, static fn( $a, $b ) => firstlab_api_hold_tries( $a->ID )[0] <=> firstlab_api_hold_tries( $b->ID )[0] );
	foreach ( $posts as $post ) {
		if ( 'ready' === get_post_meta( $post->ID, '_firstlab_queue', true ) || count( $out ) >= 3 || firstlab_api_hold_resting( $post->ID ) ) {
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
		'base_modified' => $post->post_modified, // 쉬운 말 다듬기 등 갱신 파일이 그 사이 바뀐 글을 덮지 않게 (automation/plain_check.py)
		// 근거 대조 결과 (bot/source_check.py, 기록만): 감시 세션이 외부 검수 결과와 비교
		'source_check' => json_decode( (string) get_post_meta( $post->ID, '_firstlab_source_check', true ), true ),
		'keyword'      => urldecode( $post->post_name ), // 글 주소 = 키워드 (bot/publish_queue publish slug=keyword) — +@ 보강의 네이버 상위 글 검색어
		'plus_at'      => (string) get_post_meta( $post->ID, '_firstlab_plus_at', true ), // +@ 보강한 날 (bot/refresh.PLUS_META, automation/plus_queue.py)
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
		// 고쳐도 3번 막혔거나 포기해 30일 쉬는 보류 글 (지우지 않음, 초안으로 남음)
		'gave_up'     => array_values( array_map(
			static fn( $p ) => array( 'post_id' => $p->ID, 'title' => $p->post_title, 'reason' => (string) get_post_meta( $p->ID, '_firstlab_hold', true ) ),
			array_filter( get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_hold_tries', 'numberposts' => -1 ) ), static fn( $p ) => firstlab_api_hold_resting( $p->ID ) )
		) ),
		'rates'       => $state['rates'] ?? null, // 한국은행 최신 금리
		// 오늘 공개 계획·완료 (설정 '예약 공개 시각' 줄 수 = 하루 공개 수, bot/run.py) — 운영자가 바꾼 설정이 계획에 들어갔는지 감시 세션이 확인 (11/6)
		'publish_plan' => array( 'plan' => $state['plan'] ?? array(), 'done' => $state['done'] ?? array() ),
		'server_stats' => $state['server_stats'] ?? null, // 서버 부하·메모리·사주데이 응답 (bot/server_stats.py, 서버 올릴지 판단용)
		'calc_yearly' => $state['calc_yearly'] ?? null,
		'deploy_alert' => $state['deploy_alert'] ?? null,
		'patch_problems' => $state['patch_problems'] ?? null, // 계산기 연초 값 확인 필요 (bot/calc_watch.py)
		'search'      => $state['search'] ?? null, // 구글 검색 성과 28일 요약 (bot/search_console.record, 하루 한 번)
		'ga4'         => $state['ga4'] ?? null, // GA4 방문 28일 요약 (bot/ga4.record, 하루 한 번)
		'publish_order_all' => $state['publish_order_all'] ?? null, // 대기 글 전체 공개 순서 (쉬운 말 다듬기 순서)
		'publish_next' => $state['publish_next'] ?? null, // 다음 공개 순서 (bot/schedule.upcoming, 한 시간마다) — 공개 전 쉬운 말 다듬기용
		'keyword_collect' => $state['keyword_collect'] ?? null, // 키워드 수집 마지막 성공 날짜 (bot/discover.record_collect)
		'naver_ads_check' => $state['naver_ads_check'] ?? null, // 키워드 도구 연결 점검 (bot/naver_ads.diagnose — 키 끝 4자리, 토큰 있어야 읽음)
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

/*
 * 공공데이터포털 '대한민국 공공서비스(혜택) 정보' API 통로 (행정안전부, 정부24 혜택 목록):
 * GET /wp-json/firstlab/v1/benefits?path=serviceList|serviceDetail|supportConditions&page=1&perPage=100&cond[서비스ID::EQ]=…
 * 인증키(DATA_GO_KR_KEY)는 서버에만, bot-setup.sh 가 옵션으로 옮김. 지원금 글감·사실 확인·내용 바뀐 혜택 알림용 (bot/benefit_api.py).
 */
const FIRSTLAB_BENEFIT_PATHS = array( 'serviceList', 'serviceDetail', 'supportConditions' );

add_action(
	'rest_api_init',
	static function () {
		register_rest_route(
			'firstlab/v1',
			'/benefits',
			array(
				'methods'             => 'GET',
				'permission_callback' => 'firstlab_api_allowed',
				'callback'            => 'firstlab_api_benefits',
			)
		);
	}
);

/** 혜택 API 주소 (인증키 포함, 밖으로 내보내지 않음). 허용 안 된 값이면 null. */
function firstlab_benefit_url( array $args, string $key ): ?string {
	$path = $args['path'] ?? 'serviceList';
	if ( ! in_array( $path, FIRSTLAB_BENEFIT_PATHS, true ) ) {
		return null;
	}
	$query = array( 'returnType' => 'JSON' );
	foreach ( $args as $name => $value ) {
		if ( in_array( $name, array( 'page', 'perPage' ), true ) ) {
			$query[ $name ] = (string) (int) $value;
		} elseif ( 'cond' === $name && is_array( $value ) ) { // cond[서비스ID::EQ]=… 같은 조건
			foreach ( $value as $field => $v ) {
				$query[ 'cond[' . $field . ']' ] = (string) $v;
			}
		}
	}
	// 포털이 주는 키는 '인코딩된 것'(% 포함)과 원래 것 두 가지 — 어느 쪽을 넣어도 되게
	$encoded = false !== strpos( $key, '%' ) ? $key : rawurlencode( $key );
	return 'https://api.odcloud.kr/api/gov24/v3/' . $path . '?' . http_build_query( $query ) . '&serviceKey=' . $encoded;
}

function firstlab_api_benefits( WP_REST_Request $request ) {
	$key = (string) get_option( 'firstlab_data_key', '' );
	if ( '' === $key ) {
		return new WP_Error( 'no_key', '서버에 공공데이터 인증키(DATA_GO_KR_KEY)가 없음', array( 'status' => 503 ) );
	}
	$url = firstlab_benefit_url( $request->get_query_params(), $key );
	if ( null === $url ) {
		return new WP_Error( 'bad_args', 'path 는 ' . implode( '|', FIRSTLAB_BENEFIT_PATHS ), array( 'status' => 400 ) );
	}
	$cache = 'firstlab_benefit_' . md5( $url );
	$hit   = get_transient( $cache );
	if ( false !== $hit ) {
		return $hit;
	}
	$res = wp_remote_get( $url, array( 'timeout' => 30 ) );
	if ( is_wp_error( $res ) ) {
		return new WP_Error( 'benefit_fetch', $res->get_error_message(), array( 'status' => 502 ) );
	}
	$hide = static fn( string $text ): string => str_replace( array( $key, rawurlencode( $key ), rawurldecode( $key ) ), '***', $text );
	$out  = array(
		'status'  => (int) wp_remote_retrieve_response_code( $res ),
		'body'    => $hide( (string) wp_remote_retrieve_body( $res ) ),
		'request' => $hide( $url ),
	);
	if ( 200 === $out['status'] ) {
		set_transient( $cache, $out, 6 * HOUR_IN_SECONDS );
	}
	return $out;
}
