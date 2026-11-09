<?php
/**
 * Plugin Name: First Lab - 함께 보면 좋은 글
 * Description: 글 하단에 주제가 겹치는 다른 공개 글을 자동으로 연결 (새 글이 생기면 예전 글에도 자동 반영). 겹치는 글이 없으면 안 보임.
 *
 * 11/6 운영자 '자동차보험료 계산 글에 함께 보면 좋은 글이 왜 주식 평단가 계산기지?': 예전엔 같은 분류의 글을 제목 글자 비슷한 순으로 4개 채움
 * → 분류(금융·세금)에 공개 글이 2편뿐이라 '계산'만 겹치는 주식 글이 붙음.
 * 11/9 운영자: 제목이 아니라 글 내용으로 — 관련 글은 bot/related_posts.py 기록만. 아래 제목 글자쌍 함수는 계산기 고르기(first-lab-flow.php)에만 씀.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_RELATED_COUNT = 4;
// 주제가 아니라 글 형식을 말하는 흔한 말 — 이것만 겹치면 관련 글이 아님
const FIRSTLAB_RELATED_GENERIC = array( '계산기', '계산법', '계산', '방법', '신청', '조회', '양식', '기준', '비용', '요금', '가격', '총정리', '정리', '기간', '서류', '차이', '자격', '조건', '대상', '뜻', '하는', '까지', '부터', '이상', '이하', '최대', '최소', '평균', '발급', '수수료', '확인', '절차', '납부', '신고', '등록', '준비물', '리스트', '인터넷', '방문', '온라인', '이내', '이후', '이전', '기한', '시기' ); // 11/9 'ITQ 자격증 조회'에 '건물 등기부등본 발급'이 붙음 ('발급'만 겹침)

/** 제목의 주제 글자쌍 (한글 두 글자씩, 흔한 말·숫자·기호 뺌). */
function firstlab_related_bigrams( string $title ): array {
	// 숫자+단위('1,200만원'·'3개월'·'7칸과')는 주제가 아님 — 11/9 '중고차 할부 … 1,200만원'↔'퇴직금 … 5,000만원'이 '만원'으로 붙음
	$text = preg_replace( '/\d[\d,.]*\s*(?:만\s*원|억|만|원|개월|년|월|일|시간|칸|장|개|명|편|세|번|회|가지|종|점)/u', ' ', $title );
	$text = str_replace( FIRSTLAB_RELATED_GENERIC, ' ', (string) $text );
	$out  = array();
	foreach ( preg_split( '/[^가-힣]+/u', $text, -1, PREG_SPLIT_NO_EMPTY ) as $word ) {
		$len = mb_strlen( $word );
		for ( $i = 0; $i + 1 < $len; $i++ ) {
			$out[ mb_substr( $word, $i, 2 ) ] = true;
		}
	}
	return array_keys( $out );
}

/** 제목 맨 앞 한글 낱말 ('연차 개수 계산기'의 '연차', '주휴수당 뜻'의 '주휴수당'). */
function firstlab_related_lead( string $title ): string {
	$words = preg_split( '/[^가-힣]+/u', str_replace( FIRSTLAB_RELATED_GENERIC, ' ', $title ), -1, PREG_SPLIT_NO_EMPTY );
	return $words ? $words[0] : '';
}

/** 한 쌍만 겹칠 때 관련으로 볼지: 그 두 글자가 한쪽 맨 앞 낱말 전체('연차')이거나, 두 맨 앞 낱말의 끝('주휴수당'·'연차수당'의 '수당'). */
function firstlab_related_lead_hit( string $bigram, string $a, string $b ): bool {
	$la = firstlab_related_lead( $a );
	$lb = firstlab_related_lead( $b );
	return $bigram === $la || $bigram === $lb || ( mb_strlen( $la ) > 2 && mb_strlen( $lb ) > 2 && mb_substr( $la, -2 ) === $bigram && mb_substr( $lb, -2 ) === $bigram );
}

/** 두 제목의 주제 겹침 (겹치는 글자쌍 수, 0 이면 관련 없음).
 * 한 쌍만 겹치면 그 두 글자가 어느 한쪽 제목 맨 앞 낱말 전체일 때만 관련 — 11/9 'ITQ 자격증 … 발급비'↔'건물 등기부등본 발급'(발급),
 * '상속재산분할협의서'↔'해촉증명서'(증명)처럼 뒤쪽 낱말 하나만 겹치는 글이 붙었음. '연차 개수'↔'연차수당'은 그대로. */
function firstlab_related_score( string $a, string $b ): int {
	$hit = array_intersect( firstlab_related_bigrams( $a ), firstlab_related_bigrams( $b ) );
	if ( 1 === count( $hit ) && ! firstlab_related_lead_hit( (string) reset( $hit ), $a, $b ) ) {
		return 0;
	}
	return count( $hit );
}

/** 후보 [['id', 'title', 'same_cat']] 중 관련 글 id (겹침 많은 순, 같으면 같은 분류 먼저, 최대 $count). */
function firstlab_related_pick( string $title, array $rows, int $count = FIRSTLAB_RELATED_COUNT ): array {
	$scored = array();
	foreach ( $rows as $row ) {
		$s = firstlab_related_score( $title, (string) $row['title'] );
		if ( $s > 0 ) {
			$scored[] = array( (int) $row['id'], $s, empty( $row['same_cat'] ) ? 1 : 0 );
		}
	}
	usort( $scored, static fn( $x, $y ) => array( $y[1], $x[2] ) <=> array( $x[1], $y[2] ) );
	return array_slice( array_column( $scored, 0 ), 0, $count );
}

/** 기록된 관련 글 id (JSON {"ids":[…]}) 중 지금 공개 상태인 것. */
function firstlab_related_from_meta( string $raw, callable $is_public ): array {
	$m = json_decode( $raw, true );
	return array_values( array_filter( array_map( 'intval', (array) ( $m['ids'] ?? array() ) ), $is_public ) );
}

/** 공개 글 중 이 글과 내용이 비슷한 글 id (비슷한 순). 본문 중간 연결(first-lab-flow.php)과 하단 목록이 같이 씀.
 * 11/9 운영자 '제목으로 판단할 게 아니고 글 내용으로' — 서버(bot/related_posts.py)가 본문 전체를 비교해 메타에 저장한 것만 씀.
 * 기록이 없으면 관련 글 없음 (제목 글자로 짐작해 엉뚱한 글을 붙이지 않음). */
function firstlab_related_ids( int $post_id, int $count = FIRSTLAB_RELATED_COUNT + 1 ): array {
	static $cache = array();
	if ( ! isset( $cache[ $post_id ] ) ) {
		$cache[ $post_id ] = firstlab_related_from_meta(
			(string) get_post_meta( $post_id, '_firstlab_related_ids', true ),
			static fn( $id ) => $id > 0 && 'publish' === get_post_status( $id )
		);
	}
	return array_slice( $cache[ $post_id ], 0, $count );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		// 본문 중간 '이어서 보면 좋은 글'에 이미 보여 준 글은 빼고 4개 (first-lab-flow.php)
		$shown = $GLOBALS['firstlab_flow_shown'] ?? array();
		$ids   = array_slice( array_values( array_diff( firstlab_related_ids( get_the_ID() ), $shown ) ), 0, FIRSTLAB_RELATED_COUNT );
		if ( ! $ids ) {
			return $content; // 관련 글이 없으면 엉뚱한 글을 채우지 않음
		}
		$items = '';
		foreach ( $ids as $id ) {
			$items .= sprintf( '<li><a href="%s">%s</a></li>', esc_url( get_permalink( $id ) ), esc_html( get_the_title( $id ) ) );
		}
		return $content . '<section class="firstlab-related"><h2>함께 보면 좋은 글</h2><ul>' . $items . '</ul></section>';
	},
	20
);
