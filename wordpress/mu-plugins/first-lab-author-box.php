<?php
/**
 * Plugin Name: First Lab - 글 운영 정보
 * Description: 글 본문 끝에 운영 방식·마지막 업데이트 날짜·오류 제보 안내를 표시 (누가·어떻게 쓴 글인지 밝혀 신뢰도 보강).
 *
 * 11/7 운영자 '확인 방식 한 줄 — 하자, AI 사용은 빼고': 서버 근거 대조 기록(_firstlab_source_check, bot/source_check.py)에서
 * 실제로 확인한 근거(법령·기관 페이지)와 마지막 확인일(검수 통과·갱신·공개 중 가장 늦은 날)로
 * "이 글은 법제처 법령 원문과 고용노동부 공식 페이지로 확인했어요 · 마지막 확인 2026년 11월 7일" 을 붙인다. 기록이 없으면 이 줄은 없음.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_CHECKED_MAX = 3; // 기관 이름은 최대 이만큼, 넘으면 '외 N곳'

/** 근거 주소의 기관 이름 [주소 끝, 이름] — 위에서부터 처음 맞는 것. 목록에 없는 .go.kr·.or.kr 은 '공공기관'. */
function firstlab_source_names(): array {
	return array(
		array( 'law.go.kr', '법제처' ), array( 'moel.go.kr', '고용노동부' ), array( 'work24.go.kr', '고용24' ), array( 'ei.go.kr', '고용보험' ),
		array( 'kcomwel.or.kr', '근로복지공단' ), array( 'minimumwage.go.kr', '최저임금위원회' ), array( 'nts.go.kr', '국세청' ),
		array( 'hometax.go.kr', '국세청 홈택스' ), array( 'wetax.go.kr', '위택스' ), array( 'mohw.go.kr', '보건복지부' ),
		array( 'bokjiro.go.kr', '복지로' ), array( 'gov.kr', '정부24' ), array( 'nhis.or.kr', '국민건강보험공단' ),
		array( 'longtermcare.or.kr', '노인장기요양보험' ), array( 'nps.or.kr', '국민연금공단' ), array( '4insure.or.kr', '4대사회보험 정보연계센터' ),
		array( 'kdca.go.kr', '질병관리청' ), array( 'molit.go.kr', '국토교통부' ), array( 'mois.go.kr', '행정안전부' ),
		array( 'kotsa.or.kr', '한국교통안전공단' ), array( 'fsc.go.kr', '금융위원회' ), array( 'fss.or.kr', '금융감독원' ),
		array( 'kinfa.or.kr', '서민금융진흥원' ), array( 'hf.go.kr', '한국주택금융공사' ), array( 'q-net.or.kr', '큐넷' ),
		array( 'kfsi.or.kr', '한국소방안전원' ), array( 'iros.go.kr', '인터넷등기소' ), array( 'korea.kr', '정책브리핑' ),
		array( 'mogef.go.kr', '여성가족부' ), array( 'mss.go.kr', '중소벤처기업부' ), array( 'semas.or.kr', '소상공인시장진흥공단' ),
		array( 'police.go.kr', '경찰청' ), array( 'koroad.or.kr', '도로교통공단' ), array( 'kepco.co.kr', '한국전력' ),
	);
}

/** 주소 하나 → 기관 이름 ('' 이면 공식 출처 아님). */
function firstlab_source_name( string $url ): string {
	$host = strtolower( (string) wp_parse_url( $url, PHP_URL_HOST ) );
	foreach ( firstlab_source_names() as $row ) {
		if ( $host === $row[0] || str_ends_with( $host, '.' . $row[0] ) ) {
			return $row[1];
		}
	}
	return ( str_ends_with( $host, '.go.kr' ) || str_ends_with( $host, '.or.kr' ) ) ? '공공기관' : '';
}

/** 근거 대조 기록(배열) + 날짜들 → 확인 한 줄 글자 ('' 이면 표시 안 함). $dates: 'Y-m-d…' 문자열들 (가장 늦은 날을 씀). */
function firstlab_checked_line( array $record, array $dates ): string {
	$urls = array();
	foreach ( (array) ( $record['laws'] ?? array() ) as $law ) {
		$urls[] = (string) ( is_array( $law ) ? ( $law[0] ?? '' ) : $law );
	}
	foreach ( (array) ( $record['found'] ?? array() ) as $item ) {
		foreach ( (array) ( $item['urls'] ?? array() ) as $u ) {
			$urls[] = (string) $u;
		}
	}
	$names = array_values( array_unique( array_filter( array_map( 'firstlab_source_name', $urls ) ) ) );
	if ( ! $names ) {
		return '';
	}
	$law    = in_array( '법제처', $names, true );
	$others = array_values( array_diff( $names, array( '법제처' ) ) );
	$extra  = count( $others ) - FIRSTLAB_CHECKED_MAX;
	$others = array_slice( $others, 0, FIRSTLAB_CHECKED_MAX );
	$list   = implode( '·', $others ) . ( $extra > 0 ? " 외 {$extra}곳" : '' );
	if ( $law && $others ) {
		$what = "법제처 법령 원문과 {$list} 공식 페이지로";
	} elseif ( $law ) {
		$what = '법제처 법령 원문으로';
	} else {
		$what = "{$list} 공식 페이지로";
	}
	$days = array_filter( array_map( static fn( $d ) => substr( (string) $d, 0, 10 ), $dates ), static fn( $d ) => (bool) preg_match( '/^\d{4}-\d{2}-\d{2}$/', $d ) );
	if ( ! $days ) {
		return "이 글은 {$what} 확인했어요";
	}
	$last = max( $days );
	return sprintf( '이 글은 %s 확인했어요 · 마지막 확인 %d년 %d월 %d일', $what, (int) substr( $last, 0, 4 ), (int) substr( $last, 5, 2 ), (int) substr( $last, 8, 2 ) );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		$updated = get_the_modified_date( 'Y년 n월 j일' );
		$post_id = get_the_ID();
		$record  = json_decode( (string) get_post_meta( $post_id, '_firstlab_source_check', true ), true );
		$checked = firstlab_checked_line(
			is_array( $record ) ? $record : array(),
			array( get_post_meta( $post_id, '_firstlab_verified_at', true ), get_post_meta( $post_id, '_firstlab_refreshed', true ), get_the_date( 'Y-m-d' ) )
		);
		$box     = sprintf(
			'<aside class="firstlab-author-box" style="margin-top:2.5em;padding:1em 1.2em;border-top:1px solid #ddd;font-size:0.9em;line-height:1.7;color:#555">'
			. '%s'
			. '<p style="margin:0"><strong>퍼스트랩</strong> · 정부·공공기관 안내와 법령 원문을 바탕으로 정리하고, 공개 전에 따로 검수합니다. <a href="%s">검수 원칙</a></p>'
			. '<p style="margin:0.4em 0 0">마지막 업데이트: %s · 잘못된 내용 제보: <a href="%s">문의하기</a></p>'
			. '</aside>',
			$checked ? '<p class="firstlab-checked" style="margin:0 0 0.4em">' . esc_html( $checked ) . '</p>' : '',
			esc_url( home_url( '/editorial-policy/' ) ),
			esc_html( $updated ),
			esc_url( home_url( '/contact/' ) )
		);
		return $content . $box;
	},
	9 // '함께 보면 좋은 글'(우선순위 10)보다 먼저 → 본문 바로 아래
);
