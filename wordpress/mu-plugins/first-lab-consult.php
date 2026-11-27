<?php
/**
 * Plugin Name: First Lab - 법무사 상담 문의
 * Description: 정해 둔 글 끝에 법무사 사무실 상담 배너를 붙이고, 배너 안 문의 폼을 받아 비공개 글(fl_consult)로 저장한다.
 *
 * 11/27 운영자 '법무사 사무실 문의 만들려고 — 배너 눌러 문의 적으면 알림 가게'.
 * - 사무실 정보·붙일 글: 테마 consult.json (enabled=true 이고 이름이 있어야 받음 — 빈 값이면 아무것도 안 나옴). 전화는 있으면 배너에 표시.
 * - 11/27 디딤돌 계산기 웹판(사주데이 저장소 toss-didim, VITE_CONSULT_URL)의 문의창도 같은 주소로 보낸다. 글 배너는 match 가 비면 안 붙음.
 * - 알림: 서버 봇이 매분 새 문의를 텔레그램으로 보냄 (bot/consult_notify.py). 텔레그램 키는 워드프레스에 두지 않는다.
 * - 개인정보: 동의(필수) 받고, FIRSTLAB_CONSULT_RETAIN_DAYS 일 지나면 봇이 지움 (동의문에 적은 기간과 같게 — tests 가 확인).
 * - 스팸: 숨은 칸(사람은 못 봄)·너무 빠른 제출·같은 IP 1시간 3건·하루 전체 100건 제한.
 * 저장된 글 본문은 그대로, 화면에서만 붙인다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_CONSULT_TYPE        = 'fl_consult';
const FIRSTLAB_CONSULT_RETAIN_DAYS = 30;   // 퍼스트랩 보관 기간 (bot/consult_notify.RETAIN_DAYS 와 같아야 함)
const FIRSTLAB_CONSULT_IP_HOUR     = 3;    // 같은 IP 1시간 최대
const FIRSTLAB_CONSULT_DAY_MAX     = 100;  // 하루 전체 최대 (폭주 막기)
const FIRSTLAB_CONSULT_MIN_MS      = 3000; // 폼을 연 뒤 이보다 빨리 보내면 기계로 봄
const FIRSTLAB_CONSULT_TEXT_MAX    = 1000; // 문의 내용 최대 글자

/** consult.json → 설정. 문의를 받을 조건(켬 + 사무실 이름)이 안 되면 null. */
function firstlab_consult_spec(): ?array {
	static $spec = false;
	if ( false !== $spec ) {
		return $spec;
	}
	$spec = null;
	$file = function_exists( 'get_stylesheet_directory' ) ? get_stylesheet_directory() . '/consult.json' : '';
	$data = $file && is_file( $file ) ? json_decode( (string) file_get_contents( $file ), true ) : null;
	if ( is_array( $data ) && ! empty( $data['enabled'] ) && '' !== trim( (string) ( $data['office']['name'] ?? '' ) ) ) {
		$spec = $data;
	}
	return $spec;
}

/** 전화번호 → 숫자만 (0으로 시작, 9~11자리). 아니면 ''. */
function firstlab_consult_phone( string $raw ): string {
	$digits = preg_replace( '/\D/', '', $raw );
	return preg_match( '/^0\d{8,10}$/', (string) $digits ) ? (string) $digits : '';
}

/** 숫자 전화번호 → 010-1234-5678 꼴 (보여 주기용). */
function firstlab_consult_phone_view( string $digits ): string {
	if ( str_starts_with( $digits, '02' ) ) {
		return preg_replace( '/^(02)(\d{3,4})(\d{4})$/', '$1-$2-$3', $digits );
	}
	return preg_replace( '/^(\d{3})(\d{3,4})(\d{4})$/', '$1-$2-$3', $digits );
}

/** 이 글에 배너를 붙이나: all, 또는 태그 이름·제목에 정해 둔 말이 있으면. */
function firstlab_consult_matches( array $spec, string $title, array $tags ): bool {
	$m = (array) ( $spec['match'] ?? array() );
	if ( ! empty( $m['all'] ) ) {
		return true;
	}
	foreach ( (array) ( $m['tags'] ?? array() ) as $t ) {
		if ( '' !== (string) $t && in_array( (string) $t, $tags, true ) ) {
			return true;
		}
	}
	foreach ( (array) ( $m['keywords'] ?? array() ) as $k ) {
		if ( '' !== (string) $k && str_contains( $title, (string) $k ) ) {
			return true;
		}
	}
	return false;
}

/** 동의문 (개인정보 보호법 제15조·제17조 고지 항목: 항목·목적·받는 자·보유 기간·거부 권리와 불이익). */
function firstlab_consult_consent_text( string $office ): string {
	return '수집 항목: 이름(선택), 연락처, 문의 분야·내용 / 목적: 상담 연락 / 받는 곳: ' . $office
		. '(상담을 위해 문의 내용을 전달합니다) / 퍼스트랩 보관: 접수 후 ' . FIRSTLAB_CONSULT_RETAIN_DAYS . '일 뒤 자동 삭제'
		. ' / 동의하지 않을 수 있으며, 동의하지 않으면 문의를 보낼 수 없습니다.';
}

/** 배너 + 접히는 문의 폼. */
function firstlab_consult_box( array $spec ): string {
	$o        = (array) $spec['office'];
	$name     = (string) $o['name'];
	$phone    = firstlab_consult_phone( (string) $o['phone'] );
	$area     = trim( (string) ( $o['area'] ?? '' ) );
	$hours    = trim( (string) ( $o['hours'] ?? '' ) );
	$headline = trim( (string) ( $spec['headline'] ?? '' ) );
	$headline = '' !== $headline ? $headline : ( '' !== $area ? $area . ' ' : '' ) . $name . '에 물어보기';
	$services = array_values( array_filter( array_map( 'strval', (array) ( $spec['services'] ?? array() ) ) ) );
	$options  = '';
	foreach ( array_merge( $services, array( '기타' ) ) as $s ) {
		$options .= '<option>' . esc_html( $s ) . '</option>';
	}

	$html  = '<aside class="fl-consult" aria-label="상담 문의">';
	$html .= '<p class="fl-consult-title">' . esc_html( $headline ) . '</p>';
	$call  = '' !== $phone ? '전화 <a href="tel:' . esc_attr( $phone ) . '">' . esc_html( firstlab_consult_phone_view( $phone ) ) . '</a>'
		. ( '' !== $hours ? ' · ' . esc_html( $hours ) : '' ) : '';
	$html .= '<p class="fl-consult-sub">' . esc_html( implode( ' · ', $services ) ) . ( $services && $call ? '<br>' : '' ) . $call . '</p>';
	$html .= '<details class="fl-consult-form"><summary>글로 문의 남기기</summary>';
	$html .= '<form data-fl-consult novalidate>';
	$html .= '<label>이름 (선택)<input name="name" maxlength="20" autocomplete="name"></label>';
	$html .= '<label>연락처<input name="phone" type="tel" inputmode="tel" maxlength="20" required autocomplete="tel" placeholder="010-0000-0000"></label>';
	$html .= '<label>문의 분야<select name="service">' . $options . '</select></label>';
	$html .= '<label>문의 내용<textarea name="text" rows="4" maxlength="' . FIRSTLAB_CONSULT_TEXT_MAX . '" required></textarea></label>';
	$html .= '<label class="fl-consult-hp" aria-hidden="true">홈페이지<input name="website" tabindex="-1" autocomplete="off"></label>';
	$html .= '<label class="fl-consult-agree"><input type="checkbox" name="agree" required> 개인정보 수집·이용·제공에 동의합니다 (필수)</label>';
	$html .= '<p class="fl-consult-consent">' . esc_html( firstlab_consult_consent_text( $name ) ) . '</p>';
	$html .= '<button type="submit">문의 보내기</button><p class="fl-consult-msg" role="status"></p>';
	$html .= '</form></details>';
	$html .= '<p class="fl-consult-note">퍼스트랩은 상담을 하지 않으며, 문의는 ' . esc_html( $name ) . '에 전달됩니다.</p>';
	return $html . '</aside>';
}

/** 글 끝에 배너 (한 번만). */
function firstlab_consult_insert( string $html, int $post_id ): string {
	$spec = firstlab_consult_spec();
	if ( null === $spec || str_contains( $html, 'fl-consult' ) ) {
		return $html;
	}
	$tags = function_exists( 'wp_get_post_tags' ) ? wp_list_pluck( wp_get_post_tags( $post_id ), 'name' ) : array();
	if ( ! firstlab_consult_matches( $spec, (string) get_the_title( $post_id ), array_map( 'strval', (array) $tags ) ) ) {
		return $html;
	}
	return $html . firstlab_consult_box( $spec );
}

add_filter(
	'the_content',
	static fn( $html ) => is_singular( 'post' ) && in_the_loop() && is_main_query() ? firstlab_consult_insert( (string) $html, (int) get_the_ID() ) : $html,
	16 // 글 흐름(15) 뒤
);

/** 받은 값 검사 → [저장할 값, 오류 문구]. 오류가 없으면 오류 문구 ''. */
function firstlab_consult_validate( array $in, array $services ): array {
	if ( '' !== trim( (string) ( $in['website'] ?? '' ) ) || (int) ( $in['elapsed'] ?? 0 ) < FIRSTLAB_CONSULT_MIN_MS ) {
		return array( null, '잠시 후 다시 보내 주세요.' );   // 숨은 칸이 채워졌거나 너무 빠름 — 기계로 봄
	}
	if ( empty( $in['agree'] ) ) {
		return array( null, '개인정보 수집·이용·제공에 동의해 주세요.' );
	}
	$phone = firstlab_consult_phone( (string) ( $in['phone'] ?? '' ) );
	if ( '' === $phone ) {
		return array( null, '연락처를 확인해 주세요. (예: 010-1234-5678)' );
	}
	$text = trim( wp_strip_all_tags( (string) ( $in['text'] ?? '' ) ) );
	if ( mb_strlen( $text ) < 5 ) {
		return array( null, '문의 내용을 조금 더 적어 주세요.' );
	}
	$service = (string) ( $in['service'] ?? '' );
	return array(
		array(
			'name'    => mb_substr( trim( wp_strip_all_tags( (string) ( $in['name'] ?? '' ) ) ), 0, 20 ),
			'phone'   => $phone,
			'service' => in_array( $service, array_merge( $services, array( '기타' ) ), true ) ? $service : '기타',
			'text'    => mb_substr( $text, 0, FIRSTLAB_CONSULT_TEXT_MAX ),
		),
		'',
	);
}

/** 같은 IP 1시간 횟수·하루 전체 횟수 확인 후 하나 올림. 넘으면 false. */
function firstlab_consult_rate_ok( string $ip ): bool {
	$ip_key  = 'fl_consult_ip_' . md5( $ip );
	$day_key = 'fl_consult_day_' . gmdate( 'Ymd' );
	$ip_n    = (int) get_transient( $ip_key );
	$day_n   = (int) get_transient( $day_key );
	if ( $ip_n >= FIRSTLAB_CONSULT_IP_HOUR || $day_n >= FIRSTLAB_CONSULT_DAY_MAX ) {
		return false;
	}
	set_transient( $ip_key, $ip_n + 1, HOUR_IN_SECONDS );
	set_transient( $day_key, $day_n + 1, DAY_IN_SECONDS );
	return true;
}

add_action(
	'init',
	static function () {
		register_post_type(
			FIRSTLAB_CONSULT_TYPE,
			array(
				'label'               => '상담 문의',
				'public'              => false,
				'show_ui'             => true,   // 관리자 화면에서 받은 문의를 볼 수 있게
				'show_in_menu'        => true,
				'show_in_rest'        => false,
				'exclude_from_search' => true,
				'menu_icon'           => 'dashicons-email-alt',
				'supports'            => array( 'title', 'editor' ),
				'capability_type'     => 'post',
			)
		);
	}
);

add_action(
	'rest_api_init',
	static function () {
		register_rest_route(
			'firstlab/v1',
			'/consult',
			array(
				'methods'             => 'POST',
				'permission_callback' => '__return_true', // 손님이 보내는 문의 (검사는 아래에서)
				'callback'            => static function ( WP_REST_Request $request ) {
					$spec = firstlab_consult_spec();
					if ( null === $spec ) {
						return new WP_REST_Response( array( 'ok' => false, 'error' => '지금은 문의를 받지 않아요.' ), 404 );
					}
					list( $row, $error ) = firstlab_consult_validate( (array) $request->get_json_params(), array_map( 'strval', (array) ( $spec['services'] ?? array() ) ) );
					if ( '' !== $error ) {
						return new WP_REST_Response( array( 'ok' => false, 'error' => $error ), 400 );
					}
					if ( ! firstlab_consult_rate_ok( (string) ( $_SERVER['REMOTE_ADDR'] ?? '' ) ) ) {
						return new WP_REST_Response( array( 'ok' => false, 'error' => '잠시 후 다시 보내 주세요. 급하면 전화 주세요.' ), 429 );
					}
					$who  = '' !== $row['name'] ? $row['name'] : '이름 없음';
					$body = "분야: {$row['service']}\n이름: {$who}\n연락처: " . firstlab_consult_phone_view( $row['phone'] ) . "\n\n{$row['text']}";
					$id   = wp_insert_post(
						array(
							'post_type'    => FIRSTLAB_CONSULT_TYPE,
							'post_status'  => 'private',
							'post_title'   => "[{$row['service']}] {$who}",
							'post_content' => $body,
						),
						true
					);
					if ( is_wp_error( $id ) ) {
						return new WP_REST_Response( array( 'ok' => false, 'error' => '저장하지 못했어요. 전화로 문의해 주세요.' ), 500 );
					}
					return array( 'ok' => true );
				},
			)
		);
	}
);

/** 폼 보내기 (배너가 있는 글에서만). 화면이 캐시돼도 되게 토큰 없이 — 스팸은 서버 검사로. */
add_action(
	'wp_footer',
	static function () {
		if ( ! is_singular( 'post' ) || null === firstlab_consult_spec() ) {
			return;
		}
		$url = esc_url_raw( rest_url( 'firstlab/v1/consult' ) );
		?>
<style>
.fl-consult{margin:32px 0 8px;padding:18px;border:1px solid #d7dde4;border-radius:12px;background:#f7f9fb}
.fl-consult-title{font-weight:700;font-size:1.1em;margin:0 0 6px}
.fl-consult-sub,.fl-consult-note,.fl-consult-consent{font-size:.9em;color:#4b5563;margin:4px 0}
.fl-consult-form summary{cursor:pointer;font-weight:600;margin-top:10px;color:#1d4ed8}
.fl-consult-form label{display:block;margin:10px 0 0;font-size:.95em}
.fl-consult-form input,.fl-consult-form select,.fl-consult-form textarea{display:block;width:100%;margin-top:4px;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;box-sizing:border-box}
.fl-consult-agree input{display:inline;width:auto;margin-right:6px}
.fl-consult-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.fl-consult-form button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:8px;background:#1d4ed8;color:#fff;font:inherit;font-weight:700}
.fl-consult-msg{min-height:1.2em;font-size:.95em}
</style>
<script>
(function(){
  document.querySelectorAll('form[data-fl-consult]').forEach(function(f){
    var opened = Date.now();
    var box = f.closest('details'); if (box) box.addEventListener('toggle', function(){ if (box.open) opened = Date.now(); });
    f.addEventListener('submit', function(e){
      e.preventDefault();
      var msg = f.querySelector('.fl-consult-msg'), btn = f.querySelector('button');
      var el = f.elements;   // f.name 은 폼 자체의 name 속성이라 입력 칸은 elements 로 읽음
      var d = {name:el['name'].value, phone:el['phone'].value, service:el['service'].value, text:el['text'].value, website:el['website'].value, agree:el['agree'].checked, elapsed:Date.now()-opened};
      if (!d.agree) { msg.textContent = '개인정보 수집·이용·제공에 동의해 주세요.'; return; }
      btn.disabled = true; msg.textContent = '보내는 중…';
      fetch(<?php echo wp_json_encode( $url ); ?>, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(d)})
        .then(function(r){ return r.json(); })
        .then(function(j){ if (j && j.ok) { f.reset(); msg.textContent = '문의를 보냈어요. 사무실에서 연락드릴 거예요.'; } else { msg.textContent = (j && j.error) || '보내지 못했어요. 전화로 문의해 주세요.'; btn.disabled = false; } })
        .catch(function(){ msg.textContent = '보내지 못했어요. 전화로 문의해 주세요.'; btn.disabled = false; });
    });
  });
})();
</script>
		<?php
	},
	50
);
