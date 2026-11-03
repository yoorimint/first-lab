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
		'enabled'        => array( 'label' => '자동 발행 사용', 'type' => 'checkbox', 'default' => 0 ),
		'post_status'    => array(
			'label'   => '발행 방식',
			'type'    => 'select',
			'default' => 'draft',
			'options' => array( 'draft' => '초안으로 저장 (내가 검토 후 발행)', 'publish' => '바로 발행' ),
		),
		'publish_times'  => array( 'label' => '자동 발행 시각', 'type' => 'textarea', 'default' => "09:00\n20:00", 'help' => '한 줄에 하나 (예: 09:00). 줄 수 = 하루 글 개수' ),
		'jitter_minutes' => array( 'label' => '랜덤 범위 (± 분)', 'type' => 'number', 'default' => 30, 'min' => 0, 'max' => 180, 'help' => '예: 30 이면 09:00 → 08:30~09:30 사이 무작위 (매일 달라짐)' ),
		'provider'       => array(
			'label'   => 'AI 제공사',
			'type'    => 'select',
			'default' => 'gemini',
			'options' => array( 'gemini' => 'Google Gemini', 'anthropic' => 'Anthropic Claude', 'openai' => 'OpenAI GPT' ),
		),
		'model'          => array( 'label' => '모델 이름', 'type' => 'text', 'default' => '', 'help' => '비워두면 제공사별 기본 모델 사용' ),
		'gemini_key'     => array( 'label' => 'Gemini API 키', 'type' => 'secret' ),
		'anthropic_key'  => array( 'label' => 'Claude API 키', 'type' => 'secret' ),
		'openai_key'     => array( 'label' => 'OpenAI API 키', 'type' => 'secret' ),
		'hint_keywords'  => array( 'label' => '주제 힌트 키워드', 'type' => 'textarea', 'default' => '', 'help' => '선택 사항. 비워두면 기본 주제 풀(금융·제도, 자동차, 생활, IT, 요리, 여행 등)에서 자동으로 고름. 입력하면 이 주제를 우선' ),
		'use_trends'     => array( 'label' => '구글 급상승 검색어도 참고', 'type' => 'checkbox', 'default' => 0, 'help' => '켜면 글감 출발점 중 1개를 오늘의 구글 급상승 검색어로 사용. 뉴스·인물 주제가 섞일 수 있음' ),
		'min_volume'     => array( 'label' => '최소 월간 검색량', 'type' => 'number', 'default' => 500, 'min' => 0, 'max' => 10000000 ),
		'max_volume'     => array( 'label' => '최대 월간 검색량', 'type' => 'number', 'default' => 30000, 'min' => 0, 'max' => 10000000 ),
		'blocked_words'  => array( 'label' => '추가 금지어', 'type' => 'textarea', 'default' => '', 'help' => '한 줄에 하나. 기본 금지어(의료·장소·쇼핑)에 추가됨' ),
		'target_length'  => array( 'label' => '글 분량 (자)', 'type' => 'number', 'default' => 3000, 'min' => 1000, 'max' => 10000 ),
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
			<?php echo $pending ? '요청됨 — 1~3분 안에 생성됩니다. 새로고침해서 아래 기록을 확인하세요.' : '설정의 발행 방식(초안/바로 발행)을 따릅니다.'; ?>
		</span>
	</form>

	<h2>오늘 자동 발행 계획</h2>
	<p>
		<?php
		if ( ! $plan ) {
			echo '아직 계획이 없습니다 (봇이 1분 안에 만듭니다).';
		}
		foreach ( $plan as $i => $time ) {
			echo esc_html( $time ) . ( in_array( $i, $done, true ) ? ' ✅ ' : ' ⏳ ' ) . '&nbsp; ';
		}
		?>
	</p>

	<h2>최근 기록</h2>
	<ul>
		<?php foreach ( array_slice( (array) ( $state['log'] ?? array() ), 0, 10 ) as $entry ) : ?>
			<li><?php echo make_clickable( esc_html( ( $entry['ok'] ? '✅ ' : '❌ ' ) . $entry['time'] . '  ' . $entry['msg'] ) ); ?></li>
		<?php endforeach; ?>
		<?php if ( empty( $state['log'] ) ) : ?>
			<li>기록 없음</li>
		<?php endif; ?>
	</ul>
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
		<?php firstlab_bot_render_status(); ?>
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
