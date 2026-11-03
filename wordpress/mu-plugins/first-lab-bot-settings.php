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
		'fact_check'     => array( 'label' => '자동 사실 검증', 'type' => 'checkbox', 'default' => 1, 'help' => '글 완성 후 별도 AI 가 핵심 수치를 웹 검색으로 재확인·수정. 확인 못 한 주장이 많으면 자동 발행 대신 초안으로 남김' ),
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
		'cf_account_id'  => array( 'label' => 'Cloudflare 계정 ID (본문 AI 이미지)', 'type' => 'text', 'default' => '', 'help' => 'Cloudflare 대시보드 > Workers AI 화면의 Account ID' ),
		'cf_api_token'   => array( 'label' => 'Cloudflare API 토큰', 'type' => 'secret' ),
		'image_count'    => array( 'label' => '본문 이미지 수', 'type' => 'number', 'default' => 3, 'min' => 0, 'max' => 5, 'help' => '글 1개당 AI 이미지 수 (소제목 앞에 고르게 배치, 캡션 없음)' ),
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
				echo '요청됨 — 1~3분 안에 생성됩니다. 새로고침해서 아래 기록을 확인하세요.';
			} elseif ( ! empty( $state['manual_next'] ) ) {
				echo esc_html( 'AI 서버 혼잡으로 ' . substr( $state['manual_next'], 11 ) . '에 자동 재시도 예정입니다.' );
			} else {
				echo '설정의 발행 방식(초안/바로 발행)을 따릅니다.';
			}
			?>
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
