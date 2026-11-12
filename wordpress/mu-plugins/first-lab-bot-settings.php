<?php
/**
 * Plugin Name: First Lab - 자동 발행 봇 설정
 * Description: 관리자 > 설정 > 퍼스트랩 봇. 봇(Python)은 이 설정을 WP-CLI 로 읽는다 (옵션명: firstlab_bot).
 *
 * 필드를 추가하려면 firstlab_bot_fields() 에 한 줄만 추가하면 된다.
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_BOT_OPTION = 'firstlab_bot';

/**
 * 설정 필드 정의. type: checkbox | select | number | text | textarea | secret
 * secret 은 화면에 값을 보여주지 않고, 비워서 저장하면 기존 값을 유지한다.
 */
function firstlab_bot_fields(): array {
	return array(
		'write_mode'     => array(
			'label'   => '글 작성 방식',
			'type'    => 'select',
			'default' => 'direct',
			'options' => array( 'direct' => '월정액 (Claude 구독 예약 작업이 씀)', 'bot' => 'API 봇 (같은 작업을 서버가 Claude API 키로 실행)' ),
			'help'    => '두 방식은 쓰는 사람·지시서(automation/daily-writer.md)·검수 관문·발행 방식(초안/바로/예약)·글 작성 시각·연속 작성·지금 글 1개 만들기·매일 점검이 모두 같고, 요금만 다름. 월정액: Claude 구독 사용량 (API 요금 없음). API 봇: 서버가 같은 작업을 Claude Code 로 돌리고 아래 Claude API 키로 요금이 나감 — \'API 봇 사용\' 을 켜고 AI 제공사를 Anthropic Claude 로. 서버 메모리 때문에 한 번에 1편씩 (2번 작성자 없음). 저장소를 고쳐야 하는 일(대기 글 검수 결과 반영·계산기 새로 만들기·연초 기준값)은 서버 비밀값 GITHUB_PUSH_TOKEN(이 저장소만, Contents 읽기·쓰기 토큰 — 이 화면이 아니라 서버 set-secrets.sh 로만 넣음)이 있을 때 함: 검사를 통과해야 main 에 올라감. 토큰이 없으면 그 일은 건너뛰고 봇 기록에 하루 한 번 알림',
		),
		'maint_times'    => array( 'label' => '매일 점검 시각', 'type' => 'text', 'default' => '06:10', 'help' => '두 방식 모두: 이 시각에 Claude 가 서버 기록(실행 실패·연속 작성 자동 정지·검수 보류)을 점검. 월정액은 휴대폰 알림, API 봇은 봇 기록(/maintenance)에 남김. 비우면 안 함' ),
		'direct_enabled' => array( 'label' => '글 작성 시각 사용', 'type' => 'checkbox', 'default' => 1, 'help' => '끄면 아래 시각에 자동으로 쓰지 않음 (연속 작성·지금 글 1개 만들기는 그대로 동작)' ),
		'direct_times'   => array( 'label' => '글 작성 시각', 'type' => 'textarea', 'default' => '07:40', 'help' => '한 줄에 하나 (줄 수 = 하루 작성 편수, 예: 07:40 과 13:40 두 줄이면 하루 2편). 두 방식 모두 같은 글쓰기 작업 (월정액: 서버에 Claude 예약 작업 토큰 필요, API 봇: API 봇 사용 켜짐). API 봇이 앞 글을 쓰는 중이면 끝난 뒤 시작 (3시간 안). 연속 작성 중에는 쉼. 공개는 발행 방식을 따름' ),
		'enabled'        => array( 'label' => 'API 봇 사용', 'type' => 'checkbox', 'default' => 0, 'help' => '글 작성 방식이 API 봇일 때만 의미 있음 (요금 스위치): 켜야 글 작성 시각·연속 작성·지금 글 1개 만들기·매일 점검이 Claude API 키로 실행됨. 끄면 아무것도 쓰지 않고 봇 기록에 이유를 남김. 공개(발행 방식·예약 공개)와는 상관없음' ),
		'post_status'    => array(
			'label'   => '발행 방식',
			'type'    => 'select',
			'default' => 'draft',
			'options' => array(
				'draft'    => '초안으로 저장 (내가 검토 후 발행)',
				'publish'  => '바로 발행',
				'schedule' => '예약 발행 (초안으로 쌓고, 공개 시각마다 오래된 것부터 1편씩 자동 공개)',
			),
			'help'    => '직접 작성·봇 글 모두 적용. 사실 확인이 부족하거나 제목 숫자가 본문과 다른 글은 어느 방식이든 초안으로 남김',
		),
		'schedule_times' => array( 'label' => '예약 공개 시각', 'type' => 'textarea', 'default' => '09:00', 'help' => '예약 발행일 때만 사용. 한 줄에 하나 (줄 수 = 하루 공개 글 수, 예: 09:00 과 18:00 두 줄이면 하루 2편). 시각마다 공개 대기 초안 중 가장 오래된 것 1편을 공개. 확인이 필요한 초안과 예전 봇 초안은 대기열에 안 들어감' ),
		'daily_cost_cap' => array( 'label' => '하루 AI 비용 상한 (달러)', 'type' => 'number', 'default' => 3, 'min' => 0, 'max' => 100, 'help' => 'API 봇: 오늘 쓴 Claude API 비용이 이 금액에 닿으면 그날은 새 실행을 시작하지 않고 다음 날 다시 (실행마다 남은 금액을 예산으로 넘김). 글 1편 실행 약 3~8달러 추정 — 하루 편수 × 8 정도로 (0 = 상한 없음, 한 번 실행은 15달러까지). 월정액은 해당 없음' ),
		'refresh_per_week' => array( 'label' => '글 갱신 (주당 최대)', 'type' => 'number', 'default' => 5, 'min' => 0, 'max' => 20, 'help' => '순위 정체·하락 글 보강, 1·7월 제도 변경 재검증, 6개월 지난 글 재검증. 하루 최대 1개 (0 = 끔). 두 방식 모두 서버가 대상만 고르고 글쓰기 작업이 고쳐 갱신 파일로 올림 (관문 통과해야 반영)' ),
		'jitter_minutes' => array( 'label' => '공개 시각 랜덤 범위 (± 분)', 'type' => 'number', 'default' => 30, 'min' => 0, 'max' => 180, 'help' => '예약 공개 시각에 적용. 예: 30 이면 09:00 → 08:30~09:30 사이 무작위 (매일 달라짐)' ),
		'provider'       => array(
			'label'   => 'AI 제공사',
			'type'    => 'select',
			'default' => 'gemini',
			'options' => array( 'gemini' => 'Google Gemini', 'anthropic' => 'Anthropic Claude', 'openai' => 'OpenAI GPT' ),
		),
		'model'          => array( 'label' => '모델 이름', 'type' => 'text', 'default' => '', 'help' => 'API 봇 글쓰기 모델 (예: claude-opus-5-5). 비워두면 claude-opus-5-5' ),
		'gemini_key'     => array( 'label' => 'Gemini API 키', 'type' => 'secret' ),
		'anthropic_key'  => array( 'label' => 'Claude API 키', 'type' => 'secret' ),
		'openai_key'     => array( 'label' => 'OpenAI API 키', 'type' => 'secret' ),
		'cf_account_id'  => array( 'label' => 'Cloudflare 계정 ID (본문 AI 이미지)', 'type' => 'text', 'default' => '', 'help' => 'Cloudflare 대시보드 > Workers AI 화면의 Account ID' ),
		'cf_api_token'   => array( 'label' => 'Cloudflare API 토큰', 'type' => 'secret' ),
		'hint_keywords'  => array( 'label' => '우선 주제 (선택)', 'type' => 'textarea', 'default' => '', 'help' => '비워두면 분야 상관없이 자동 발굴. 입력하면 이 단어가 들어간 키워드를 먼저 씀 (한 줄에 하나)' ),
		'competition_checks' => array( 'label' => '하루 경쟁도 조회 수', 'type' => 'number', 'default' => 15, 'min' => 0, 'max' => 400, 'help' => 'Gemini 구글 검색 연동(무료 한도 내)으로 후보 키워드의 구글 상위 사이트를 확인. Gemini 키 필요' ),
		'discover_calls' => array( 'label' => '하루 키워드 수집량', 'type' => 'number', 'default' => 300, 'min' => 100, 'max' => 2000, 'help' => '네이버 키워드 도구 호출 수 (무료). 매일 급상승어 + 씨앗 단어 전부(약 80회) + 나머지는 롱테일 조합 탐색' ),
		'min_volume'     => array( 'label' => '최소 월간 검색량', 'type' => 'number', 'default' => 500, 'min' => 0, 'max' => 10000000 ),
		'max_volume'     => array( 'label' => '최대 월간 검색량', 'type' => 'number', 'default' => 30000, 'min' => 0, 'max' => 10000000 ),
		'blocked_words'  => array( 'label' => '추가 금지어', 'type' => 'textarea', 'default' => '', 'help' => '한 줄에 하나. 기본 금지어(의료·장소·쇼핑)에 추가됨' ),
		'target_length'  => array( 'label' => '글 분량 (자)', 'type' => 'number', 'default' => 3000, 'min' => 1000, 'max' => 10000 ),
		'persona'        => array( 'label' => '글쓴이 페르소나', 'type' => 'textarea', 'default' => '', 'help' => '예: 30대 직장인, 생활 정보를 꼼꼼히 비교해 정리하는 사람. 친근한 존댓말, 핵심부터 말함. (비우면 중립적인 정보 글. 경험담·후기는 지어내지 않음)' ),
		'extra_prompt'   => array( 'label' => '추가 작성 지시', 'type' => 'textarea', 'default' => '', 'help' => '예: 말투는 친근하게, 표를 꼭 넣어줘' ),
	);
}

function firstlab_bot_defaults(): array {
	$defaults = array();
	foreach ( firstlab_bot_fields() as $key => $field ) {
		$defaults[ $key ] = $field['default'] ?? '';
	}
	return $defaults;
}

/** '09:00', '9:00 20:30', '9시 30분' → ['09:00', '20:30'] — bot/settings.py _times 와 같은 규칙 (테스트 ScheduleSavedTest 가 둘을 비교). */
function firstlab_bot_schedule_times( string $text ): array {
	preg_match_all( '/(\d{1,2})\s*[:시]\s*(\d{1,2})?/u', $text, $m, PREG_SET_ORDER );
	$times = array();
	foreach ( $m as $x ) {
		$h = (int) $x[1];
		$i = (int) ( $x[2] ?? 0 );
		if ( $h < 24 && $i < 60 ) {
			$times[ sprintf( '%02d:%02d', $h, $i ) ] = true;
		}
	}
	$out = array_keys( $times );
	sort( $out );
	return $out;
}

function firstlab_bot_get(): array {
	return wp_parse_args( (array) get_option( FIRSTLAB_BOT_OPTION, array() ), firstlab_bot_defaults() );
}

function firstlab_bot_sanitize( $input ): array {
	$input = (array) $input;
	$old   = firstlab_bot_get();
	$clean = array();

	foreach ( firstlab_bot_fields() as $key => $field ) {
		$value = $input[ $key ] ?? '';
		switch ( $field['type'] ) {
			case 'checkbox':
				$clean[ $key ] = empty( $value ) ? 0 : 1;
				break;
			case 'select':
				$clean[ $key ] = array_key_exists( $value, $field['options'] ) ? $value : $field['default'];
				break;
			case 'number':
				$clean[ $key ] = min( $field['max'], max( $field['min'], (int) $value ) );
				break;
			case 'textarea':
				$clean[ $key ] = sanitize_textarea_field( $value );
				break;
			case 'secret':
				$value         = trim( sanitize_text_field( $value ) );
				$clean[ $key ] = '' === $value ? ( $old[ $key ] ?? '' ) : $value; // 빈칸 = 기존 값 유지
				if ( ! empty( $input[ $key . '__delete' ] ) ) {
					$clean[ $key ] = '';
				}
				break;
			default:
				$clean[ $key ] = sanitize_text_field( $value );
		}
	}
	return $clean;
}

add_action(
	'admin_init',
	static function () {
		register_setting(
			'firstlab_bot',
			FIRSTLAB_BOT_OPTION,
			array( 'sanitize_callback' => 'firstlab_bot_sanitize', 'default' => firstlab_bot_defaults() )
		);
	}
);

add_action(
	'admin_menu',
	static function () {
		add_options_page( '퍼스트랩 봇', '퍼스트랩 봇', 'manage_options', 'firstlab-bot', 'firstlab_bot_render_page' );
	}
);

function firstlab_bot_render_field( string $key, array $field, array $values ): void {
	$name  = FIRSTLAB_BOT_OPTION . "[$key]";
	$value = $values[ $key ] ?? '';

	switch ( $field['type'] ) {
		case 'checkbox':
			printf( '<input type="checkbox" name="%s" value="1" %s>', esc_attr( $name ), checked( 1, $value, false ) );
			break;
		case 'select':
			printf( '<select name="%s">', esc_attr( $name ) );
			foreach ( $field['options'] as $opt => $label ) {
				printf( '<option value="%s" %s>%s</option>', esc_attr( $opt ), selected( $opt, $value, false ), esc_html( $label ) );
			}
			echo '</select>';
			break;
		case 'number':
			printf(
				'<input type="number" class="small-text" name="%s" value="%d" min="%d" max="%d">',
				esc_attr( $name ), (int) $value, (int) $field['min'], (int) $field['max']
			);
			break;
		case 'textarea':
			printf( '<textarea class="large-text" rows="4" name="%s">%s</textarea>', esc_attr( $name ), esc_textarea( $value ) );
			break;
		case 'secret':
			$status = $value ? '저장됨 (끝자리 ' . esc_html( substr( $value, -4 ) ) . ')' : '미입력';
			printf( '<input type="password" class="regular-text" name="%s" value="" autocomplete="new-password" placeholder="%s">', esc_attr( $name ), esc_attr( $status ) );
			if ( $value ) {
				printf( ' <label><input type="checkbox" name="%s"> 삭제</label>', esc_attr( FIRSTLAB_BOT_OPTION . "[{$key}__delete]" ) );
			}
			echo '<p class="description">바꿀 때만 입력하세요. 비워두면 기존 키가 유지됩니다.</p>';
			break;
		default:
			printf( '<input type="text" class="regular-text" name="%s" value="%s">', esc_attr( $name ), esc_attr( $value ) );
	}

	if ( ! empty( $field['help'] ) && 'secret' !== $field['type'] ) {
		printf( '<p class="description">%s</p>', esc_html( $field['help'] ) );
	}
}

const FIRSTLAB_BOT_REQUEST = 'firstlab_bot_request'; // 서버 봇(1분마다 실행)이 확인 후 지움
const FIRSTLAB_BOT_STATE   = 'firstlab_bot_state';   // 봇이 기록하는 오늘 계획·최근 기록

// '지금 글 1개 만들기' → 요청만 남기고, 실제 생성은 서버 봇이 처리 (PHP 에서 외부 명령 실행 금지 정책)
add_action(
	'admin_post_firstlab_bot_run_now',
	static function () {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( '권한이 없습니다.' );
		}
		check_admin_referer( 'firstlab_bot_run_now' );
		update_option( FIRSTLAB_BOT_REQUEST, (string) time(), false );
		wp_safe_redirect( admin_url( 'options-general.php?page=firstlab-bot&requested=1' ) );
		exit;
	}
);

const FIRSTLAB_BOT_CONTINUOUS = 'firstlab_bot_continuous'; // 연속 작성 켜짐(1)/꺼짐(0) — 서버 봇이 1분마다 확인 (bot/run.py continuous_step)

// '연속 작성 시작·정지' → 값만 바꾸고, 실제 실행은 서버 봇이 처리
add_action(
	'admin_post_firstlab_bot_continuous',
	static function () {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( '권한이 없습니다.' );
		}
		check_admin_referer( 'firstlab_bot_continuous' );
		update_option( FIRSTLAB_BOT_CONTINUOUS, empty( $_POST['start'] ) ? '0' : '1', false );
		wp_safe_redirect( admin_url( 'options-general.php?page=firstlab-bot' ) );
		exit;
	}
);

const FIRSTLAB_BOT_WRITER2 = 'firstlab_writer2'; // 2번 작성자 켜짐(1, 기본)/꺼짐(0) — 서버 봇이 1분마다 확인 (bot/run.py continuous_step)

// '2번 작성자 켜기·끄기' (11/7 운영자) → 값만 바꿈. 끄면 2번이 쓰던 한 편은 마치고 다음부터 1번만 씀
add_action(
	'admin_post_firstlab_bot_writer2',
	static function () {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( '권한이 없습니다.' );
		}
		check_admin_referer( 'firstlab_bot_writer2' );
		update_option( FIRSTLAB_BOT_WRITER2, empty( $_POST['on'] ) ? '0' : '1', false );
		wp_safe_redirect( admin_url( 'options-general.php?page=firstlab-bot' ) );
		exit;
	}
);

/** 예약 발행 공개 대기 초안 수 (bot/schedule.py 와 같은 메타). */
function firstlab_bot_ready_count(): int {
	return count(
		get_posts(
			array(
				'post_status' => 'draft',
				'meta_key'    => '_firstlab_queue',
				'meta_value'  => 'ready',
				'numberposts' => -1,
				'fields'      => 'ids',
			)
		)
	);
}

const FIRSTLAB_BOT_EXCLUDED = 'firstlab_bot_excluded'; // 제외할 키워드 (봇이 반영 후 지움)

add_action(
	'admin_post_firstlab_bot_exclude',
	static function () {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( '권한이 없습니다.' );
		}
		check_admin_referer( 'firstlab_bot_exclude' );
		$keyword = sanitize_text_field( wp_unslash( $_POST['keyword'] ?? '' ) );
		if ( '' !== $keyword ) {
			$list   = (array) get_option( FIRSTLAB_BOT_EXCLUDED, array() );
			$list[] = $keyword;
			update_option( FIRSTLAB_BOT_EXCLUDED, array_values( array_unique( $list ) ), false );
		}
		wp_safe_redirect( admin_url( 'options-general.php?page=firstlab-bot&excluded=1#candidates' ) );
		exit;
	}
);

function firstlab_bot_render_keywords( array $state ): void {
	$kw       = (array) ( $state['keywords'] ?? array() );
	$by       = (array) ( $kw['by_status'] ?? array() );
	$pending  = (array) get_option( FIRSTLAB_BOT_EXCLUDED, array() );
	$labels   = array( 'new' => '검사 전', 'ok' => '✅ 통과' );
	?>
	<h2>키워드 창고</h2>
	<p>
		전체 <strong><?php echo number_format( (int) ( $kw['total'] ?? 0 ) ); ?></strong>개
		(오늘 새로 <?php echo number_format( (int) ( $kw['new_today'] ?? 0 ) ); ?>개) ·
		미검사 <?php echo number_format( (int) ( $by['new'] ?? 0 ) ); ?> ·
		통과 <?php echo number_format( (int) ( $by['ok'] ?? 0 ) ); ?> ·
		부적합 <?php echo number_format( (int) ( $by['rejected'] ?? 0 ) ); ?> ·
		글 작성 <?php echo number_format( (int) ( $by['used'] ?? 0 ) ); ?> ·
		제외 <?php echo number_format( (int) ( $by['excluded'] ?? 0 ) ); ?>
		<br><span class="description">매일 새로 수집한 후보에서 고릅니다. 이미 쓴 키워드 기록(중복 방지) + 최근 8일치(상승세 비교용)만 보관합니다. 현황은 1시간마다 갱신. 마지막 갱신: <?php echo esc_html( $state['dashboard_at'] ?? '-' ); ?></span>
	</p>

	<details id="candidates" style="margin:1em 0" <?php echo isset( $_GET['excluded'] ) ? 'open' : ''; // 제외 누른 뒤에는 펼친 상태 유지 ?>>
		<summary style="cursor:pointer;font-weight:600;font-size:1.1em">다음 글감 후보 보기 (점수순 <?php echo count( (array) ( $state['candidates'] ?? array() ) ); ?>개) ▾</summary>
	<div style="overflow-x:auto"><table class="widefat striped" style="max-width:900px">
		<thead><tr><th>키워드</th><th>월간 검색</th><th>롱테일의 원 키워드</th><th>구글 상위 경쟁 (난이도 0~1)</th><th>점수</th><th>AI 안전검사</th><th></th></tr></thead>
		<tbody>
		<?php foreach ( (array) ( $state['candidates'] ?? array() ) as $c ) : ?>
			<?php $is_pending = in_array( $c['keyword'], $pending, true ); ?>
			<tr>
				<td><?php echo esc_html( $c['keyword'] ); ?></td>
				<td><?php echo number_format( (int) $c['total'] ); ?></td>
				<td><?php echo esc_html( $c['head'] ?? '' ); ?><?php echo ! empty( $c['gsc'] ) ? '<br><strong>' . esc_html( $c['gsc'] ) . '</strong>' : ''; ?></td>
				<td>
					<?php
					if ( isset( $c['difficulty'] ) && null !== $c['difficulty'] ) {
						printf( '<strong>%s</strong> <span class="description">%s</span>', esc_html( $c['difficulty'] ), esc_html( $c['comp_info'] ?? '' ) );
					} else {
						echo '<span class="description">조회 전</span>';
					}
					?>
				</td>
				<td><?php echo esc_html( $c['score'] ); ?></td>
				<td><?php echo esc_html( $labels[ $c['status'] ] ?? $c['status'] ); ?></td>
				<td>
					<?php if ( $is_pending ) : ?>
						제외 예정
					<?php else : ?>
						<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" style="margin:0">
							<input type="hidden" name="action" value="firstlab_bot_exclude">
							<input type="hidden" name="keyword" value="<?php echo esc_attr( $c['keyword'] ); ?>">
							<?php wp_nonce_field( 'firstlab_bot_exclude' ); ?>
							<button class="button button-small">제외</button>
						</form>
					<?php endif; ?>
				</td>
			</tr>
		<?php endforeach; ?>
		<?php if ( empty( $state['candidates'] ) ) : ?>
			<tr><td colspan="7">아직 후보가 없습니다. 키워드 수집이 끝나면 표시됩니다.</td></tr>
		<?php endif; ?>
		</tbody>
	</table></div>
	<p class="description">'검사 전' 후보는 하루 1번 사전 검토 또는 글을 쓰기 직전에 AI 안전 검사(인물·정치·종교·의료 등 제외)를 거칩니다. 구글 상위 경쟁은 Gemini 무료 한도 안에서 조회합니다. 경쟁 난이도는 구글 상위 출처 중 대형 사이트(정부·위키·언론·대기업) 비율이며, 0.8 이상은 글감에서 제외됩니다.</p>
	</details>
	<?php
}

function firstlab_bot_render_status(): void {
	$state   = (array) get_option( FIRSTLAB_BOT_STATE, array() );
	$today   = wp_date( 'Y-m-d' );
	$plan    = $state['plan'][ $today ] ?? array();
	$done    = $state['done'][ $today ] ?? array();
	$pending = get_option( FIRSTLAB_BOT_REQUEST );
	?>
	<h2>지금 발행</h2>
	<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
		<input type="hidden" name="action" value="firstlab_bot_run_now">
		<?php wp_nonce_field( 'firstlab_bot_run_now' ); ?>
		<?php submit_button( '지금 글 1개 만들기', 'secondary', 'submit', false, $pending ? array( 'disabled' => 'disabled' ) : array() ); ?>
		<span class="description">
			<?php
			if ( $pending ) {
				echo '요청됨 — 1분 안에 처리를 시작합니다. 새로고침해서 아래 기록을 확인하세요.';
			} elseif ( ! empty( $state['manual_next'] ) ) {
				echo esc_html( 'AI 서버 혼잡으로 ' . substr( $state['manual_next'], 11 ) . '에 자동 재시도 예정입니다.' );
			} elseif ( 'bot' !== ( firstlab_bot_get()['write_mode'] ?? 'direct' ) ) {
				echo '월정액 방식: Claude 예약 작업을 바로 실행합니다 (글은 20~40분 뒤 배포와 함께 올라옴). 발행 방식(초안/바로/예약)을 따릅니다.';
			} else {
				echo 'API 봇 방식: 서버가 월정액과 같은 글쓰기 작업을 바로 시작합니다 (Claude API 키 요금, 글은 20~60분 뒤 같은 관문을 거쳐 올라옴, 앞 글을 쓰는 중이면 끝난 뒤). 발행 방식(초안/바로/예약)을 따릅니다.';
			}
			?>
		</span>
	</form>

	<h2>연속 작성</h2>
	<?php
	$running    = '1' === get_option( FIRSTLAB_BOT_CONTINUOUS, '0' );
	$continuous = (array) ( $state['continuous'] ?? array() );
	?>
	<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
		<input type="hidden" name="action" value="firstlab_bot_continuous">
		<?php wp_nonce_field( 'firstlab_bot_continuous' ); ?>
		<?php if ( $running ) : ?>
			<?php submit_button( '정지', 'secondary', 'stop', false ); ?>
		<?php else : ?>
			<?php submit_button( '시작', 'primary', 'start', false ); ?>
		<?php endif; ?>
		<span class="description">
			<?php
			if ( $running ) {
				printf(
					'진행 중 — 이번에 %d편 완료. 한 편씩 쓰고(20~40분), 올라오면 바로 다음 편. 정지를 누르면 쓰던 한 편까지만 마치고 멈춤.',
					(int) ( $continuous['count'] ?? 0 )
				);
			} else {
				echo '멈춤. 시작을 누르면 Claude 가 정지할 때까지 한 편씩 계속 씁니다 (월정액: 구독 사용량, API 봇: API 키 요금·하루 비용 상한까지 — 한도에 닿거나 3번 연속 실패하면 스스로 멈춤).';
			}
			?>
		</span>
	</form>
	<?php $writer2 = '0' !== (string) get_option( FIRSTLAB_BOT_WRITER2, '1' ); ?>
	<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" style="margin-top:8px">
		<input type="hidden" name="action" value="firstlab_bot_writer2">
		<?php wp_nonce_field( 'firstlab_bot_writer2' ); ?>
		<strong>2번 작성자:</strong>
		<?php if ( $writer2 ) : ?>
			<?php submit_button( '끄기', 'secondary', 'off', false ); ?>
			<span class="description">켜짐 — 두 편을 동시에 씀 (구독 사용량 2배, 월정액만 — API 봇은 서버에서 한 번에 1편). 끄면 2번이 쓰던 한 편까지 마치고 1번만 씀.</span>
		<?php else : ?>
			<?php submit_button( '켜기', 'primary', 'on', false ); ?>
			<span class="description">꺼짐 — 1번 작성자만 한 편씩 씀 (후보 전부를 1번이 받음).</span>
		<?php endif; ?>
	</form>
	<p>예약 발행 공개 대기 초안: <strong><?php echo (int) firstlab_bot_ready_count(); ?>편</strong> (발행 방식이 '예약 발행'일 때 공개 시각마다 오래된 것부터 1편씩 공개)</p>
	<?php
	// 검수 관문에서 막힌 초안 (bot/review_gate.py) — 이유를 보고 고치거나, 잘못 막힌 것이면 알려 주세요
	$held = array_filter(
		get_posts( array( 'post_status' => 'draft', 'meta_key' => '_firstlab_hold', 'numberposts' => 20 ) ),
		static fn( $p ) => 'ready' !== get_post_meta( $p->ID, '_firstlab_queue', true ) // 확인 후 대기열에 넣은 글은 제외
	);
	if ( $held ) :
		?>
		<p><strong>검수 보류 초안 <?php echo count( $held ); ?>편</strong> (공개 대기열에 안 들어감)</p>
		<ul>
			<?php foreach ( $held as $p ) : ?>
				<li>
					<a href="<?php echo esc_url( get_edit_post_link( $p->ID ) ); ?>"><?php echo esc_html( $p->post_title ); ?></a>
					— <?php echo esc_html( (string) get_post_meta( $p->ID, '_firstlab_hold', true ) ); ?>
				</li>
			<?php endforeach; ?>
		</ul>
	<?php endif; ?>

	<h2>오늘 공개 계획 (예약 발행)</h2>
	<p>
		<?php
		if ( ! $plan ) {
			echo '예약 발행이 아니거나 아직 계획이 없습니다 (봇이 1분 안에 만듭니다).';
		}
		// 저장한 시각이 아직 계획에 안 들어갔으면 알림 (봇이 1분마다 다시 만듦 — 11/6 운영자 '저장해도 바로 반영이 안 되네')
		$saved = firstlab_bot_schedule_times( (string) ( firstlab_bot_get()['schedule_times'] ?? '' ) );
		$from  = (array) ( $state['plan_from'] ?? array() );
		if ( $plan && $saved && $saved !== $from ) {
			echo '<br><strong>저장한 공개 시각 ' . count( $saved ) . '개(' . esc_html( implode( ', ', $saved ) ) . ')는 봇이 1분 안에 오늘 계획으로 바꿉니다 — 잠시 뒤 새로고침하세요.</strong><br>';
		}
		foreach ( $plan as $i => $time ) {
			echo esc_html( $time ) . ( in_array( $time, $done, true ) ? ' ✅ ' : ' ⏳ ' ) . '&nbsp; ';  // 완료 기록 = 예정 시각
		}
		?>
	</p>

	<?php firstlab_bot_render_keywords( $state ); ?>

	<?php $logs = (array) ( $state['log'] ?? array() ); ?>
	<details style="margin:1em 0">
		<summary style="cursor:pointer;font-weight:600;font-size:1.1em">
			최근 기록 ▾
			<?php if ( $logs ) : ?>
				<span style="font-weight:normal"> — <?php echo esc_html( ( $logs[0]['ok'] ? '✅ ' : '❌ ' ) . $logs[0]['time'] . ' ' . mb_substr( $logs[0]['msg'], 0, 40 ) . '…' ); ?></span>
			<?php endif; ?>
		</summary>
		<ul>
			<?php foreach ( array_slice( $logs, 0, 15 ) as $entry ) : ?>
				<li><?php echo make_clickable( esc_html( ( $entry['ok'] ? '✅ ' : '❌ ' ) . $entry['time'] . '  ' . $entry['msg'] ) ); ?></li>
			<?php endforeach; ?>
			<?php if ( ! $logs ) : ?>
				<li>기록 없음</li>
			<?php endif; ?>
		</ul>
	</details>
	<hr>
	<?php
}

function firstlab_bot_render_page(): void {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	$values = firstlab_bot_get();
	?>
	<div class="wrap">
		<h1>퍼스트랩 자동 발행 봇</h1>
		<?php
		$providers = firstlab_bot_fields()['provider']['options'];
		$key_field = $values['provider'] . '_key';
		printf(
			'<p style="font-size:1.1em">현재 글쓰기 AI: <strong>%s</strong> (모델: %s) · 키: %s</p>',
			esc_html( $providers[ $values['provider'] ] ?? $values['provider'] ),
			esc_html( $values['model'] ? $values['model'] : '자동 선택' ),
			! empty( $values[ $key_field ] ) ? '✅ 있음' : '❌ 없음'
		);
		firstlab_bot_render_status();
		?>
		<h2>설정</h2>
		<p>여기서 바꾼 설정은 다음 실행부터 바로 적용됩니다.</p>
		<form method="post" action="options.php">
			<?php settings_fields( 'firstlab_bot' ); ?>
			<table class="form-table" role="presentation">
				<?php foreach ( firstlab_bot_fields() as $key => $field ) : ?>
					<tr>
						<th scope="row"><?php echo esc_html( $field['label'] ); ?></th>
						<td><?php firstlab_bot_render_field( $key, $field, $values ); ?></td>
					</tr>
				<?php endforeach; ?>
			</table>
			<?php submit_button( '저장' ); ?>
		</form>
	</div>
	<?php
}

// 사이트 화면 상단 관리자 바에 '퍼스트랩 봇' 바로가기 (로그인한 관리자에게만 보임)
add_action(
	'admin_bar_menu',
	static function ( WP_Admin_Bar $bar ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$bar->add_node(
			array(
				'id'    => 'firstlab-bot',
				'title' => '⚙️ 퍼스트랩 봇',
				'href'  => admin_url( 'options-general.php?page=firstlab-bot' ),
			)
		);
	},
	100
);
