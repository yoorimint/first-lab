<?php
/**
 * Plugin Name: First Lab - WebP 사진
 * Description: 사진의 화면용 크기(썸네일·중간·큰 사진·글 머리 사진)를 WebP 로 만들고, 화면에서는 WebP 만 고르게 함. 원본(JPG)은 그대로 — 공유 미리보기(og:image)는 JPG.
 *
 * 11/6 운영자 '워드프레스 꿀팁 2번(대표 사진 WebP) 해라': 같은 화질에 용량이 약 25~35% 작아 모바일 첫 화면이 빨라짐.
 * 원본을 JPG 로 두는 이유: 카카오톡 등 일부 공유 미리보기가 WebP 를 못 보여 줄 수 있고, 사진 정리 코드(bot/images, migrate_photos)가 원본 이름(.jpg)으로 찾음.
 * 서버 사진 편집기가 WebP 를 못 쓰면 워드프레스가 알아서 JPG 로 만든다 (아무것도 깨지지 않음).
 * 이미 올린 사진: 봇이 10분마다 몇 장씩 화면용 크기를 다시 만든다 (bot/webp.py, 원본·기존 파일은 지우지 않음).
 */

defined( 'ABSPATH' ) || exit;

const FIRSTLAB_HERO_SIZE = 'firstlab-hero';

// 새로 만드는 화면용 크기는 WebP 로
add_filter(
	'image_editor_output_format',
	static function ( $formats ) {
		$formats['image/jpeg'] = 'image/webp';
		return $formats;
	}
);

// 글 머리 사진 크기: 원본(1200)보다 1px 작아야 워드프레스가 따로 만든다 → 큰 화면에서도 WebP
add_action(
	'after_setup_theme',
	static function () {
		add_image_size( FIRSTLAB_HERO_SIZE, 1199, 0, false );
	}
);

/** srcset 후보 중 WebP 가 있으면 WebP 만 남김 (원본 JPG 를 큰 화면이 고르지 않게). */
function firstlab_webp_only( array $sources ): array {
	$webp = array_filter( $sources, static fn( $s ) => str_ends_with( strtolower( (string) ( $s['url'] ?? '' ) ), '.webp' ) );
	return $webp ? $webp : $sources;
}

add_filter( 'wp_calculate_image_srcset', static fn( $sources ) => is_array( $sources ) ? firstlab_webp_only( $sources ) : $sources );
