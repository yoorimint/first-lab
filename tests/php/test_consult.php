<?php
// 상담 문의 검사·배너 (tests/test_consult.py::ConsultPhpTest 가 실행). 워드프레스 없이 쓰는 함수만 흉내.
define( 'ABSPATH', '/' );
define( 'HOUR_IN_SECONDS', 3600 );
define( 'DAY_IN_SECONDS', 86400 );
function add_action() {}
function add_filter() {}
function wp_strip_all_tags( $s ) { return strip_tags( (string) $s ); }
function esc_html( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES ); }
function esc_attr( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES ); }
require $argv[1];

$fails = array();
function check( string $name, bool $ok ) { global $fails; if ( ! $ok ) { $fails[] = $name; } }

// 전화번호
check( '휴대폰 하이픈', '01012345678' === firstlab_consult_phone( '010-1234-5678' ) );
check( '서울 지역번호', '0212345678' === firstlab_consult_phone( '02) 1234-5678' ) );
check( '잘못된 번호', '' === firstlab_consult_phone( '12345' ) && '' === firstlab_consult_phone( '0101234567890' ) );
check( '보여 주기 꼴', '010-1234-5678' === firstlab_consult_phone_view( '01012345678' ) && '02-1234-5678' === firstlab_consult_phone_view( '0212345678' ) );

// 검사: 숨은 칸·빠른 제출·동의·전화·내용
$ok_in = array( 'name' => '<b>홍길동</b>', 'phone' => '010-1234-5678', 'service' => '상속', 'text' => '상속 등기 절차가 궁금합니다', 'agree' => true, 'elapsed' => 5000, 'website' => '' );
list( $row, $err ) = firstlab_consult_validate( $ok_in, array( '등기', '상속' ) );
check( '정상 접수', '' === $err && '01012345678' === $row['phone'] && '홍길동' === $row['name'] && '상속' === $row['service'] );
check( '숨은 칸 채움 = 기계', '' !== firstlab_consult_validate( array_merge( $ok_in, array( 'website' => 'x' ) ), array() )[1] );
check( '너무 빠름 = 기계', '' !== firstlab_consult_validate( array_merge( $ok_in, array( 'elapsed' => 500 ) ), array() )[1] );
check( '동의 안 함', '' !== firstlab_consult_validate( array_merge( $ok_in, array( 'agree' => false ) ), array() )[1] );
check( '전화 틀림', '' !== firstlab_consult_validate( array_merge( $ok_in, array( 'phone' => '123' ) ), array() )[1] );
check( '내용 너무 짧음', '' !== firstlab_consult_validate( array_merge( $ok_in, array( 'text' => '네' ) ), array() )[1] );
check( '목록 밖 분야는 기타', '기타' === firstlab_consult_validate( array_merge( $ok_in, array( 'service' => '해킹' ) ), array( '등기' ) )[0]['service'] );

// 붙일 글 고르기
$spec = array( 'match' => array( 'all' => false, 'tags' => array( '상속' ), 'keywords' => array( '등기' ) ) );
check( '태그로', firstlab_consult_matches( $spec, '아무 글', array( '상속' ) ) );
check( '제목으로', firstlab_consult_matches( $spec, '부동산 등기 비용', array() ) );
check( '해당 없음', ! firstlab_consult_matches( $spec, '연차수당 계산', array( '근로' ) ) );
check( '전체', firstlab_consult_matches( array( 'match' => array( 'all' => true ) ), '연차', array() ) );

// 배너: 전화 링크·동의문(보관 기간)·숨은 칸
$box = firstlab_consult_box( array( 'office' => array( 'name' => '가나법무사사무소', 'phone' => '02-123-4567', 'area' => '서울 강남', 'hours' => '평일 9~18시' ), 'services' => array( '등기', '상속' ) ) );
check( '전화 링크', str_contains( $box, 'href="tel:021234567"' ) );
check( '동의문 보관 기간', str_contains( $box, '접수 후 ' . FIRSTLAB_CONSULT_RETAIN_DAYS . '일 뒤 자동 삭제' ) && str_contains( $box, '받는 곳: 가나법무사사무소' ) );
check( '숨은 칸', str_contains( $box, 'name="website"' ) );
check( '퍼스트랩은 상담 안 함 안내', str_contains( $box, '퍼스트랩은 상담을 하지 않으며' ) );
$nophone = firstlab_consult_box( array( 'office' => array( 'name' => '가나법무사사무소', 'phone' => '' ), 'services' => array( '등기' ) ) );
check( '전화 없으면 전화 줄 없음', ! str_contains( $nophone, 'tel:' ) && str_contains( $nophone, 'data-fl-consult' ) );

echo $fails ? 'FAIL: ' . implode( ', ', $fails ) . "\n" : "OK\n";
exit( $fails ? 1 : 0 );
