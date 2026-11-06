/* 자동차검사 계산기: 만료일 → 검사 가능 기간, 공단 수수료, 늦었을 때 과태료. 요율은 data/car-inspection.json */
(function () {
  'use strict';

  /**
   * 검사 지연 과태료 (자동차관리법 시행령 과태료 기준 문구):
   * 지연 30일 이내 4만원 / 31일째부터 계산해 3일 초과할 때마다 2만원 추가 / 115일 이상 60만원
   */
  function fineFor(days, f) {
    if (days <= 0) return 0;
    if (days <= f.base_days) return f.base;
    if (days >= f.max_from_day) return f.max;
    return Math.min(f.max, f.base + f.step_amount * FirstLabCalc.util.floor((days - f.step_from_day) / f.step_days));
  }

  FirstLabCalc.register({
    id: 'car-inspection',
    title: '자동차검사 계산기',
    intro: '검사 유효기간 만료일을 넣으면 검사 받을 수 있는 기간, 공단 검사소 수수료, 늦었을 때 과태료를 계산합니다.',
    inputs: [
      {
        name: 'expiry', label: '검사 유효기간 만료일', type: 'date',
        help: '자동차등록증, 검사 안내문, 한국교통안전공단 사이버검사소에서 확인할 수 있어요.',
      },
      { name: 'on', label: '검사 받을 날', type: 'date', default: 'today' },
      {
        name: 'kind', label: '검사 종류', type: 'select', default: 'periodic',
        options: [['periodic', '정기검사'], ['comp_no_load', '종합검사 (무부하)'], ['comp_load', '종합검사 (부하)']],
        help: '검사 안내문에 적힌 종류를 고르세요. 종합검사의 부하·무부하 방식은 검사소에서 차에 맞춰 정해요.',
      },
      {
        name: 'size', label: '차종', type: 'select', default: 'small',
        options: [
          ['small', '승용차 (10인 이하 승합 포함)'],
          ['light', '경형 승합·화물'],
          ['small', '소형 승합·화물'],
          ['medium', '중형 승합·화물'],
          ['large', '대형 승합·화물'],
        ],
      },
    ],

    compute(v, d, u) {
      const expiry = u.parseDate(v.expiry);
      if (expiry === null) throw new u.InputError('검사 유효기간 만료일을 넣으면 결과가 나와요.');
      const on = u.parseDate(v.on);
      if (on === null) throw new u.InputError('검사 받을 날을 넣어 주세요.');
      const fee = (d.fee[v.kind] || {})[v.size];
      if (fee === undefined) throw new u.InputError('검사 종류와 차종을 골라 주세요.');

      const start = u.addDays(expiry, -d.window.before_days);
      const end = u.addDays(expiry, d.window.after_days);
      const lateDays = Math.max(0, u.daysBetween(end, on));
      const fine = fineFor(lateDays, d.fine);

      let status;
      if (on < start) status = `아직 기간 전이에요 (${u.daysBetween(on, start)}일 뒤부터 기간이 시작돼요. 기간 전에 받으면 다음 만료일이 검사일 기준으로 당겨져요)`;
      else if (on <= end) status = `검사 기간 안이에요 (마감까지 ${u.daysBetween(on, end)}일 남음)`;
      else status = `검사 기간이 ${lateDays}일 지났어요`;

      const notes = [
        '기간 안에 합격하면 만료일에 검사받은 것으로 봐요. 일찍 받아도 다음 만료일은 그대로예요.',
        '수수료는 한국교통안전공단 검사소 기준이에요. 민간 지정 검사소(지정정비사업자)는 금액이 다를 수 있어요.',
        '재검사 기간 안에 받는 재검사는 수수료가 면제돼요.',
      ];
      if (fine) {
        notes.push('검사유효기간 만료일이 지난 뒤 소유권이 바뀐 차는 이전등록한 날부터 31일 안에 받으면 돼요 (자동차관리법 시행규칙 제77조③). 이 경우 과태료는 위 계산과 다를 수 있어요.');
        notes.push('과태료는 법령 기준으로 계산한 예상액이에요. 실제 금액은 고지서를 따르고, 의견제출 기한 안에 내면 최대 20%까지 줄어요.');
      }

      return {
        values: { start: u.isoDate(start), end: u.isoDate(end), fee, late_days: lateDays, fine },
        rows: [
          ['검사 받을 수 있는 기간', `${u.korDate(start)} ~ ${u.korDate(end)}`],
          ['검사 받을 날 기준', status],
          ['공단 검사소 수수료', u.won(fee)],
          ['예상 과태료', fine ? u.won(fine) : '없음'],
        ],
        notes,
      };
    },
  });
})();
