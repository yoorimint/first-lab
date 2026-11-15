<?php
/**
 * Plugin Name: First Lab 사이트 성과
 * Description: 관리자 왼쪽 메뉴 '사이트 성과' — 구글 검색 노출·클릭, 방문(GA4), 구글 색인을 한 화면에.
 *
 * 11/15 운영자: '서치콘솔 안 들어가도 대충 볼 수 있게', '자동발행(봇 설정)에 넣지 말고 따로, 보기 편하게'.
 * 서버가 매일 새벽 모은 값(봇 상태 firstlab_bot_state 의 search·ga4·index — bot/search_console, ga4, index_check)을
 * 보여 주기만 한다. 페이지를 열 때 외부 요청 없음.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_STATS_SLUG      = 'firstlab-stats';
const FIRSTLAB_INDEX_REQ_META  = '_firstlab_index_requested';   // 운영자가 서치콘솔에서 색인 생성을 요청한 날 (구글이 알려 주지 않아 직접 표시)
const FIRSTLAB_PLUS_META       = '_firstlab_plus_at';           // +@ 보강한 날 (bot/refresh.PLUS_META)
const FIRSTLAB_REQUESTABLE     = array( 'request', 'waiting' );   // '요청했어요' 단추를 보일 단계 — 아직 안 읽은 글도 운영자가 요청함 (11/15 운영자)

/**
 * 글 하나의 색인 단계 (11/15 운영자 '아직 신청 안 된 거, 색인 중인 거, 됐다가 빠진 거 — 빠진 거 다시 보강해서 신청하면 되는 거 아닌가').
 *  indexed   : 색인됨
 *  improve   : 구글이 읽고도 안 넣음(빠진 글 포함) + 그 뒤 보강 전 → +@ 차례 맨 앞 (automation/plus_queue.dropped_published)
 *  request   : 구글이 마지막으로 읽은 뒤 보강함 → 운영자가 서치콘솔에서 '색인 생성 요청'
 *  requested : 요청함 → 구글 결과 기다림 (색인 점검이 매일 다시 봄)
 *  waiting   : 구글이 아직 안 읽음(모름·발견만) → 기다림 (사이트맵·글끼리 링크로 찾아옴)
 */
function firstlab_index_stage( array $p, string $plus_at, string $requested ): string {
	$state = (string) ( $p['state'] ?? '' );
	if ( 'indexed' === $state ) {
		return 'indexed';
	}
	if ( 'crawled' === $state || ( 'other' === $state && ! empty( $p['was_indexed'] ) ) ) {
		if ( '' === $plus_at || $plus_at < (string) ( $p['last_crawl'] ?? '' ) ) {
			return 'improve';
		}
		return ( '' !== $requested && $requested >= $plus_at ) ? 'requested' : 'request';
	}
	return '' !== $requested ? 'requested' : 'waiting';
}

add_action(
	'admin_post_firstlab_index_requested',
	static function () {
		$id = (int) ( $_POST['post_id'] ?? 0 );
		if ( ! current_user_can( 'manage_options' ) || ! $id ) {
			wp_die( '권한 없음' );
		}
		check_admin_referer( 'firstlab_index_requested_' . $id );
		update_post_meta( $id, FIRSTLAB_INDEX_REQ_META, current_time( 'Y-m-d' ) );
		wp_safe_redirect( admin_url( 'admin.php?page=' . FIRSTLAB_STATS_SLUG . '#index-stages' ) );
		exit;
	}
);

/** '요청했어요' 단추 (이 글을 서치콘솔에서 색인 생성 요청했다고 표시). */
function firstlab_stats_request_button( int $post_id ): string {
	return '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '" style="display:inline">'
		. '<input type="hidden" name="action" value="firstlab_index_requested"><input type="hidden" name="post_id" value="' . (int) $post_id . '">'
		. wp_nonce_field( 'firstlab_index_requested_' . $post_id, '_wpnonce', true, false )
		. '<button class="button button-small">요청했어요</button></form>';
}

/** 글별 색인 상태 — 단계마다 표 하나 (할 일이 있는 단계부터). */
function firstlab_stats_index_groups( array $posts ): void {
	$titles = array(
		'request'   => array( '보강 끝 — 색인 생성 요청하세요', '서치콘솔 맨 위 검색창에 주소를 넣고 \'색인 생성 요청\'을 누른 뒤 \'요청했어요\'를 눌러 주세요. (하루 요청 수 한도가 있어요)' ),
		'improve'   => array( '구글이 읽고도 안 넣은 글 — 보강 차례', '+@ 보강 차례 맨 앞에 자동으로 들어가요. 보강이 끝나면 위 \'요청하세요\'로 올라와요.' ),
		'requested' => array( '요청함 — 구글 결과 기다리는 중', '색인 점검이 매일 다시 봐요. 보통 며칠~2주 걸려요.' ),
		'waiting'   => array( '구글이 아직 안 읽은 글', '서치콘솔에서 \'색인 생성 요청\'을 했으면 \'요청했어요\'를 눌러 주세요. 요청 안 해도 사이트맵·글끼리 링크로 구글이 찾아와요.' ),
		'indexed'   => array( '색인된 글', '' ),
	);
	$groups = array_fill_keys( array_keys( $titles ), array() );
	foreach ( $posts as $p ) {
		$id        = (int) ( $p['post_id'] ?? 0 );
		$plus_at   = (string) get_post_meta( $id, FIRSTLAB_PLUS_META, true );
		$requested = (string) get_post_meta( $id, FIRSTLAB_INDEX_REQ_META, true );
		$stage     = firstlab_index_stage( (array) $p, $plus_at, $requested );
		$note      = ! empty( $p['was_indexed'] ) ? '전에 색인됨 → 빠짐' : '';
		$groups[ $stage ][] = array(
			firstlab_stats_page_link( (string) ( $p['url'] ?? '' ), $id ),
			esc_html( (string) ( $p['published'] ?? '' ) ),
			esc_html( (string) ( $p['last_crawl'] ?? '' ) ),
			esc_html( $plus_at ),
			esc_html( $requested ),
			esc_html( $note ) . ( in_array( $stage, FIRSTLAB_REQUESTABLE, true ) ? ' ' . firstlab_stats_request_button( $id ) : '' ),
		);
	}
	echo '<h3 id="index-stages">글별 색인 상태</h3>';
	foreach ( $titles as $stage => list( $title, $help ) ) {
		if ( ! $groups[ $stage ] ) {
			continue;
		}
		printf( '<h4>%s (%d편)</h4>', esc_html( $title ), count( $groups[ $stage ] ) );
		if ( $help ) {
			echo '<p class="description">' . esc_html( $help ) . '</p>';
		}
		if ( 'indexed' === $stage ) {
			echo '<details><summary>목록 보기</summary>';
		}
		firstlab_stats_table( array( '글', '공개일', '구글이 마지막으로 읽은 날', '보강한 날', '요청한 날', '비고' ), $groups[ $stage ] );
		if ( 'indexed' === $stage ) {
			echo '</details>';
		}
	}
}

add_action(
	'admin_menu',
	static function () {
		add_menu_page( '사이트 성과', '사이트 성과', 'manage_options', FIRSTLAB_STATS_SLUG, 'firstlab_stats_render_page', 'dashicons-chart-bar', 3 );
	}
);

/** 숫자 칸 하나 (큰 숫자 + 설명). */
function firstlab_stats_card( string $label, string $value, string $note = '' ): string {
	return sprintf(
		'<div class="fl-card"><div class="fl-label">%s</div><div class="fl-value">%s</div><div class="fl-note">%s</div></div>',
		esc_html( $label ),
		esc_html( $value ),
		esc_html( $note )
	);
}

/** 주소(/글주소/ 또는 전체 URL)를 읽기 쉬운 글 제목 링크로. 글을 못 찾으면 주소를 그대로. */
function firstlab_stats_page_link( string $page, int $post_id = 0 ): string {
	if ( ! $post_id ) {
		$url     = 0 === strpos( $page, 'http' ) ? $page : home_url( $page );
		$post_id = url_to_postid( $url );
	}
	if ( $post_id ) {
		return sprintf( '<a href="%s" target="_blank">%s</a>', esc_url( get_permalink( $post_id ) ), esc_html( get_the_title( $post_id ) ) );
	}
	return esc_html( rawurldecode( $page ) );
}

/** 색인 안 된 글 한 줄: 전에 색인됐다가 빠졌는지, 구글이 다른 주소를 대표로 골랐는지 (bot/index_check.summarize). */
function firstlab_stats_waiting_row( array $w ): array {
	$note = array();
	if ( ! empty( $w['was_indexed'] ) ) {
		$note[] = esc_html( $w['was_indexed'] . ' 색인 확인 → 빠짐' );
	}
	if ( ! empty( $w['other_canonical'] ) ) {
		$note[] = '구글이 대표로 고른 주소: ' . firstlab_stats_page_link( (string) $w['other_canonical'] );
	}
	return array(
		firstlab_stats_page_link( (string) ( $w['url'] ?? '' ), (int) ( $w['post_id'] ?? 0 ) ),
		esc_html( (string) ( $w['published'] ?? '' ) ),
		esc_html( (string) ( $w['coverage'] ?? '' ) ),
		esc_html( (string) ( $w['last_crawl'] ?? '' ) ),
		implode( '<br>', $note ),
	);
}

/** 표 하나. $rows 는 이미 이스케이프된 칸 배열들. */
function firstlab_stats_table( array $head, array $rows ): void {
	echo '<table class="widefat striped fl-table"><thead><tr>';
	foreach ( $head as $h ) {
		echo '<th>' . esc_html( $h ) . '</th>';
	}
	echo '</tr></thead><tbody>';
	foreach ( $rows as $cells ) {
		echo '<tr><td>' . implode( '</td><td>', $cells ) . '</td></tr>';
	}
	echo '</tbody></table>';
}

function firstlab_stats_render_page(): void {
	$state  = (array) get_option( 'firstlab_bot_state', array() );
	$search = (array) ( $state['search'] ?? array() );
	$ga4    = (array) ( $state['ga4'] ?? array() );
	$index  = (array) ( $state['index'] ?? array() );
	$num    = static fn( $v ) => number_format( (float) $v );
	?>
	<div class="wrap">
	<h1>사이트 성과</h1>
	<style>
		.fl-cards{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0 24px}
		.fl-card{background:#fff;border:1px solid #dcdcde;border-radius:6px;padding:14px 18px;min-width:150px}
		.fl-label{color:#50575e;font-size:13px}
		.fl-value{font-size:28px;font-weight:600;margin:4px 0}
		.fl-note{color:#787c82;font-size:12px}
		.fl-table{max-width:860px;margin-bottom:24px}
	</style>
	<p class="description">서버가 매일 새벽 모은 값입니다. 숫자는 하루에 한 번 바뀝니다.</p>
	<?php
	if ( ! $search && ! $ga4 && ! $index ) {
		echo '<p>아직 모은 값이 없습니다.</p></div>';
		return;
	}

	// 구글 검색
	if ( $search ) {
		$l7 = (array) ( $search['last7'] ?? array() );
		$p7 = (array) ( $search['prev7'] ?? array() );
		printf( '<h2>구글 검색 <small>(최근 %d일, %s 기준)</small></h2>', (int) ( $search['days'] ?? 28 ), esc_html( (string) ( $search['date'] ?? '' ) ) );
		echo '<div class="fl-cards">';
		echo firstlab_stats_card( '노출', $num( $search['impressions'] ?? 0 ), '검색 결과에 보인 횟수' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '클릭', $num( $search['clicks'] ?? 0 ), '검색에서 들어온 횟수' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '평균 순위', ( $search['position'] ?? '-' ) . '위', '낮을수록 위' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '최근 7일 노출', $num( $l7['impressions'] ?? 0 ), '그 전 7일 ' . $num( $p7['impressions'] ?? 0 ) ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '노출된 글', (int) ( $search['pages_seen'] ?? 0 ) . '편', '공개 ' . (int) ( $search['published'] ?? 0 ) . '편 중' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo '</div>';

		$top = array_slice( (array) ( $search['top'] ?? array() ), 0, 10 );
		if ( $top ) {
			echo '<h3>검색에 많이 보인 글</h3>';
			firstlab_stats_table(
				array( '글', '노출', '클릭', '순위' ),
				array_map(
					static fn( $r ) => array( firstlab_stats_page_link( (string) ( $r['page'] ?? '' ) ), $num( $r['impressions'] ?? 0 ), $num( $r['clicks'] ?? 0 ), esc_html( (string) ( $r['position'] ?? '-' ) ) . '위' ),
					$top
				)
			);
		}
		$queries = array_slice( (array) ( $search['queries'] ?? array() ), 0, 10 );
		if ( $queries ) {
			echo '<h3>들어온 검색어</h3>';
			firstlab_stats_table(
				array( '검색어', '보인 글', '노출', '클릭', '순위' ),
				array_map(
					static fn( $q ) => array( esc_html( (string) ( $q['query'] ?? '' ) ), firstlab_stats_page_link( (string) ( $q['page'] ?? '' ) ), $num( $q['impressions'] ?? 0 ), $num( $q['clicks'] ?? 0 ), esc_html( (string) ( $q['position'] ?? '-' ) ) . '위' ),
					$queries
				)
			);
		}
	}

	// 방문 (GA4)
	if ( $ga4 ) {
		printf( '<h2>방문 <small>(GA4 최근 %d일)</small></h2>', (int) ( $ga4['days'] ?? 28 ) );
		echo '<div class="fl-cards">';
		echo firstlab_stats_card( '방문(세션)', $num( $ga4['sessions'] ?? 0 ), '검색·직접·다른 사이트 합계' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '평균 머문 시간', (int) ( $ga4['avg_seconds'] ?? 0 ) . '초', '방문 한 번당' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo firstlab_stats_card( '사이트 안 이동', $num( $ga4['internal_moves'] ?? 0 ) . '회', '다른 글로 넘어간 횟수' ); // phpcs:ignore WordPress.Security.EscapeOutput
		echo '</div>';
		$top = array_slice( (array) ( $ga4['top'] ?? array() ), 0, 10 );
		if ( $top ) {
			echo '<h3>많이 방문한 글</h3>';
			firstlab_stats_table(
				array( '글', '방문', '머문 시간' ),
				array_map(
					static fn( $r ) => array( firstlab_stats_page_link( (string) ( $r['page'] ?? '' ) ), $num( $r['sessions'] ?? 0 ), (int) ( $r['avg_seconds'] ?? 0 ) . '초' ),
					$top
				)
			);
		}
	}

	// 구글 색인
	if ( $index ) {
		$published = (int) ( $index['published'] ?? 0 );
		$indexed   = (int) ( $index['indexed'] ?? 0 );
		printf( '<h2>구글 색인 <small>(%s 점검)</small></h2>', esc_html( (string) ( $index['date'] ?? '' ) ) );
		echo '<div class="fl-cards">';
		echo firstlab_stats_card( '색인된 글', $indexed . '편', '공개 ' . $published . '편 중' ); // phpcs:ignore WordPress.Security.EscapeOutput
		foreach ( (array) ( $index['not_indexed'] ?? array() ) as $why => $count ) {
			echo firstlab_stats_card( (string) $why, (int) $count . '편', '색인 안 됨' ); // phpcs:ignore WordPress.Security.EscapeOutput
		}
		echo '</div>';
		$waiting = (array) ( $index['waiting'] ?? array() );
		if ( ! empty( $index['posts'] ) ) {
			firstlab_stats_index_groups( (array) $index['posts'] );
		} elseif ( $waiting ) {
			echo '<h3>색인 안 된 글</h3>';
			firstlab_stats_table(
				array( '글', '공개일', '상태', '구글이 마지막으로 읽은 날', '비고' ),
				array_map( 'firstlab_stats_waiting_row', $waiting )
			);
		}
		echo '<p class="description">\'구글이 아직 모름\'은 구글이 아직 안 읽은 글, \'읽어 갔지만 색인 안 함\'은 읽고도 검색에 안 넣은 글입니다. 검색 노출이 있는 글은 색인된 것입니다.</p>';
	}

	// 애드센스
	echo '<h2>애드센스 수익</h2><p>승인 뒤 연결합니다 (운영자 구글 계정으로 한 번 허가 필요).</p>';
	echo '</div>';
}
