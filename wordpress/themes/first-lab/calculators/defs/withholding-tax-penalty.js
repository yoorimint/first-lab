/* 원천세 가산세 계산기: 원천징수 등 납부지연가산세 = 미납세액 3% + 미납세액 × 날수 × 하루 0.022%,
 * 두 금액 합계는 미납세액의 10% 한도 (국세기본법 제47조의5①1·2, 2026.7.1 시행 문구: 기한 다음 날부터 납부일 전날까지).
 * 세무서 납부고지 전에 스스로 낸 경우만 계산 (고지 뒤 제3호·제4호는 고지서대로). 요율은 data/withholding-tax-penalty.json */
(function () {
  'use strict';

  FirstLabCalc.register({
    id: 'withholding-tax-penalty',
    title: '원천세 가산세 계산기',
    intro: '원천세를 기한보다 늦게 낼 때, 안 낸 세액과 날짜를 넣으면 납부지연가산세가 나와요. 세무서 고지를 받기 전에 스스로 내는 경우를 계산해요.',
    inputs: [
      { name: 'unpaid_tax', label: '늦게 내는 원천세 (원)', type: 'number', min: 0, step: 1, default: 1000000, help: '지방소득세는 빼고, 기한까지 안 냈거나 덜 낸 소득세(원천세)만 넣어요.' },
      { name: 'due', label: '원래 납부 기한', type: 'date', default: '2026-11-10', help: '보통 지급한 달의 다음 달 10일이에요. 그날이 토요일·일요일·공휴일이면 그다음 날이 기한이에요.' },
      { name: 'paid', label: '실제로 내는 날', type: 'date', default: 'today' },
    ],

    compute(v, d, u) {
      const tax = Number(v.unpaid_tax);
      if (!(tax > 0)) throw new u.InputError('늦게 내는 원천세를 넣으면 결과가 나와요.');
      if (!Number.isInteger(tax)) throw new u.InputError('세액은 1원 단위 정수로 넣어 주세요.');
      if (tax > d.max_input) throw new u.InputError('세액이 너무 커요. 1천억 원 이하로 넣어 주세요.');
      const due = u.parseDate(v.due);
      const paid = u.parseDate(v.paid);
      if (due === null || paid === null) throw new u.InputError('원래 기한과 내는 날을 모두 넣어 주세요.');
      if (u.isoDate(due) < d.rule_from) throw new u.InputError('원래 기한이 2026년 7월 1일 이후인 경우를 계산해요.');
      if (paid <= due) throw new u.InputError('내는 날이 원래 기한보다 뒤여야 해요. 기한 안에 내면 가산세가 없어요.');

      const days = u.daysBetween(due, paid) - 1; // 기한 다음 날부터 납부일 전날까지 (제47조의5①2)
      const basePart = (tax * d.base_pct) / 100;
      const dayPart = (tax * days * d.daily_rate_per_100k) / 100000;
      const cap = (tax * d.cap_pct) / 100;
      const capped = basePart + dayPart > cap;
      const penalty = u.floor10(capped ? cap : basePart + dayPart);
      const total = tax + penalty;

      const values = { days, capped, penalty, total };
      const rows = [
        ['늦게 내는 원천세', u.won(tax)],
        [`처음 붙는 가산세 (${d.base_pct}%)`, u.won(u.floor(basePart))],
        [`늦은 날수만큼 (${days}일 × 하루 0.022%)`, u.won(u.floor(dayPart))],
        ['가산세', capped ? `${u.won(penalty)} (${d.cap_pct}% 한도에 걸림)` : u.won(penalty)],
        ['이번에 낼 금액 (세액 + 가산세)', u.won(total)],
      ];
      const notes = [
        '가산세는 10원 미만을 버렸어요.',
        '세무서 납부고지를 받기 전에 스스로 내는 경우예요. 고지를 받았다면 고지서 금액과 기한을 따라요.',
        '지방소득세는 위택스에서 따로 내고, 늦으면 가산세도 따로 붙어요.',
        '지급명세서를 늦게 내거나 안 낸 가산세는 이 계산에 들어 있지 않아요.',
      ];
      if (capped) notes.push('3%와 날수만큼 붙는 가산세를 합쳐 안 낸 세액의 10%를 넘지 않아요. 그래서 더 늦어져도 고지 전까지는 이 금액이에요.');
      return { values, rows, notes };
    },
  });
})();
