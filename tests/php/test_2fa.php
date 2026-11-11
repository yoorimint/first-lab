<?php
// 2단계 인증 계산이 RFC 6238 시험값과 같은지 (tests/test_bot.py::AttackSurfaceTest.test_two_factor 가 실행)
define( 'ABSPATH', '/' );
function add_action() {}
function add_filter() {}
require $argv[1];
$key = '12345678901234567890';
$b32 = firstlab_2fa_b32_encode( $key );
$ok  = firstlab_2fa_b32_decode( $b32 ) === $key
	&& '287082' === firstlab_2fa_code( $key, intdiv( 59, 30 ) )
	&& '081804' === firstlab_2fa_code( $key, intdiv( 1111111109, 30 ) )
	&& '279037' === firstlab_2fa_code( $key, intdiv( 2000000000, 30 ) )
	&& 1 === firstlab_2fa_check( $b32, '287082', 59 )
	&& 1 === firstlab_2fa_check( $b32, '287 082', 89 )        // 앞뒤 30초 허용
	&& null === firstlab_2fa_check( $b32, '287082', 59, 1 )   // 한 번 쓴 코드는 다시 못 씀
	&& null === firstlab_2fa_check( $b32, '123456', 59 )
	&& null === firstlab_2fa_check( $b32, '28708', 59 );
echo $ok ? "OK\n" : "FAIL\n";
exit( $ok ? 0 : 1 );
