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
		'posts_per_day'  => array( 'label' => '하루 글 개수', 'type' => 'number', 'default' => 2, 'min' => 1, 'max' => 5 ),
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
		'hint_keywords'  => array( 'label' => '주제 힌트 키워드', 'type' => 'textarea', 'default' => '', 'help' => '한 줄에 하나. 이 키워드들의 연관 키워드 중에서 글감을 고름' ),
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

function firstlab_bot_render_page(): void {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	$values = firstlab_bot_get();
	?>
	<div class="wrap">
		<h1>퍼스트랩 자동 발행 봇</h1>
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
