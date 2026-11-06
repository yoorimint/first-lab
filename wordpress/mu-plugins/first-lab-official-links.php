<?php
/**
 * Plugin Name: First Lab - 공식 사이트 링크
 * Description: 본문에 글자로만 적힌 공식 사이트(위택스·서울 이택스·정부24·홈택스 등)를 화면에 보일 때 링크로 바꿈. 정해 둔 목록만, 사이트마다 첫 곳만, 글 하나에 최대 FIRSTLAB_OFFICIAL_MAX 개.
 *
 * 11/6 운영자 '공식사이트 링크 만들어라': 자동차세 납부 조회 글에 'wetax.go.kr' 이 글자로만 있어 독자가 바로 못 들어감.
 * 본문 외부 링크는 서버가 지움(bot/related.clean_links — 지어낸 주소 방지)이라, 링크는 저장 본문이 아니라 화면에서만 이 목록으로 붙인다.
 * 이미 그 사이트로 링크가 있거나, 제목·링크·코드 안의 글자는 건드리지 않는다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_OFFICIAL_MAX = 3;

/** [주소, 본문에 적힌 주소(도메인), 이름들] — 공공기관 공식 서비스만 (새로 넣을 때는 공식 주소를 직접 확인하고). */
function firstlab_official_sites(): array {
	return array(
		array( 'https://www.wetax.go.kr', 'wetax.go.kr', array( '위택스' ) ),
		array( 'https://etax.seoul.go.kr', 'etax.seoul.go.kr', array( '서울시 이택스', '서울시 ETAX', '이택스', 'ETAX' ) ),
		array( 'https://www.gov.kr', 'gov.kr', array( '정부24' ) ),
		array( 'https://www.hometax.go.kr', 'hometax.go.kr', array( '홈택스' ) ),
		array( 'https://www.bokjiro.go.kr', 'bokjiro.go.kr', array( '복지로' ) ),
		array( 'https://www.work24.go.kr', 'work24.go.kr', array( '고용24' ) ),
		array( 'https://www.nhis.or.kr', 'nhis.or.kr', array( '국민건강보험공단' ) ),
		array( 'https://www.nps.or.kr', 'nps.or.kr', array( '국민연금공단' ) ),
		array( 'https://www.ecar.go.kr', 'ecar.go.kr', array( '자동차민원 대국민포털' ) ),
		array( 'https://www.efine.go.kr', 'efine.go.kr', array( '교통민원24' ) ),
		array( 'https://www.q-net.or.kr', 'q-net.or.kr', array( '큐넷' ) ),
		array( 'https://www.iros.go.kr', 'iros.go.kr', array( '인터넷등기소' ) ),
	);
}

/** 본문 HTML → 공식 사이트 이름·주소 첫 곳을 링크로 (글자 부분만, 링크·제목·코드 안은 그대로). */
function firstlab_official_autolink( string $html ): string {
	$parts = preg_split( '/(<[^>]+>)/', $html, -1, PREG_SPLIT_DELIM_CAPTURE );
	$skip  = 0; // <a>·<h1~6>·<code>·<script>·<style> 안
	$done  = 0;
	$sites = array_values(
		array_filter(
			firstlab_official_sites(),
			static fn( $s ) => false === stripos( $html, '//' . $s[1] ) && false === stripos( $html, '//www.' . $s[1] ) // 이미 링크가 있으면 뺌
		)
	);
	foreach ( $parts as $i => $part ) {
		if ( '' === $part ) {
			continue;
		}
		if ( '<' === $part[0] ) {
			if ( preg_match( '#^<(a|h[1-6]|code|script|style)\b#i', $part ) ) {
				$skip++;
			} elseif ( preg_match( '#^</(a|h[1-6]|code|script|style)>#i', $part ) ) {
				$skip = max( 0, $skip - 1 );
			}
			continue;
		}
		if ( $skip || $done >= FIRSTLAB_OFFICIAL_MAX ) {
			continue;
		}
		foreach ( $sites as $k => $site ) {
			if ( $done >= FIRSTLAB_OFFICIAL_MAX ) {
				break;
			}
			list( $url, $host, $names ) = $site;
			// 주소(www. 있든 없든)를 먼저, 없으면 이름 — 다른 주소의 일부(…gov.kr 가 아닌 'xgov.kr')나 낱말 중간은 빼고
			$patterns = array_merge(
				array( '/(?<![\w.\/-])(?:www\.)?' . preg_quote( $host, '/' ) . '(?![\w\/-])/i' ),
				array_map( static fn( $n ) => '/(?<![\p{L}\p{N}])' . preg_quote( $n, '/' ) . '(?![\p{L}\p{N}])/u', $names )
			);
			foreach ( $patterns as $p ) {
				if ( preg_match( $p, $part, $m, PREG_OFFSET_CAPTURE ) ) {
					$link = sprintf( '<a href="%s" target="_blank" rel="noopener" class="firstlab-official">%s</a>', esc_url( $url ), $m[0][0] );
					$part = substr_replace( $part, $link, $m[0][1], strlen( $m[0][0] ) );
					unset( $sites[ $k ] );
					$done++;
					break;
				}
			}
		}
		$parts[ $i ] = $part;
	}
	return implode( '', $parts );
}

add_filter(
	'the_content',
	static function ( $content ) {
		if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
			return $content;
		}
		return firstlab_official_autolink( (string) $content );
	},
	7 // 목차(8)·운영 정보(9)·함께 보면 좋은 글(20) 전 — 본문 글자에만
);
