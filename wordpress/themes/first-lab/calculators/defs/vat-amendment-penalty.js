/* 부가세 수정신고 가산세 계산기: 과소신고가산세(10%, 부정행위 40%)와 수정신고 시기별 감면(국세기본법 제48조②1),
 * 납부지연가산세(1일 0.022%, 법정납부기한 다음 날부터 납부일 전날까지 — 제47조의4①1, 2026.7.1 시행). 요율은 data/vat-amendment-penalty.json */
(function () {
  'use strict';

  const DAY = 86400000;

  /** 법정신고기한(그날 포함 기한)이 지난 뒤 n개월이 끝나는 날 (국세기본법 제4조 → 민법 제157조·제160조) */
  function monthsEnd(due, n) {
    const start = new Date(due + DAY); // 기산일: 기한 다음 날 (초일 불산입)
    const y = start.getUTCFullYear();
    const m = start.getUTCMonth();
    const day = start.getUTCDate();
    if (day === 1) return Date.UTC(y, m + n, 0); // 달의 처음부터 세면 마지막 달의 말일 (제160조①)
    const last = new Date(Date.UTC(y, m + n + 1, 0)).getUTCDate();
    if (day > last) return Date.UTC(y, m + n + 1, 0); // 해당일이 없으면 그 달 말일 (제160조③)
    return Date.UTC(y, m + n, day - 1); // 기산일에 해당한 날의 전날 (제160조②)
  }

  FirstLabCalc.register({
    id: 'vat-amendment-penalty',
    title: '부가세 수정신고 가산세 계산기',
    intro: '부가세를 적게 신고한 걸 알고 수정신고할 때, 더 낼 세액과 날짜를 넣으면 과소신고가산세 감면과 납부지연가산세가 나와요. 수정신고한 날 바로 낸다고 보고 계산해요.',
    inputs: [
      { name: 'extra_tax', label: '수정신고로 더 내는 부가세 (원)', type: 'number', min: 0, step: 1, default: 1000000, help: '가산세를 빼고, 처음 신고한 세액보다 늘어난 부가세만 넣어요.' },
      { name: 'fraud', label: '이중장부·거짓 증빙 같은 부정행위로 적게 신고했나요', type: 'select', default: 'no', help: '더 내는 세액 전부가 부정행위분일 때만 "예"를 골라요. 일부만 부정행위분이면 그 부분만 40%, 나머지는 10%라 이 계산보다 적어요.', options: [['no', '아니요 (실수·누락)'], ['yes', '예 (부정행위)']] },
      { name: 'due', label: '원래 신고·납부 기한', type: 'date', default: '2026-07-27', help: '기한이 토요일·일요일·공휴일이면 그다음 날이 기한이에요 (예: 2026년 7월 25일 토요일 → 7월 27일).' },
      { name: 'paid', label: '수정신고하고 납부하는 날', type: 'date', default: 'today' },
    ],

    compute(v, d, u) {
      const extra = Number(v.extra_tax);
      if (!(extra > 0)) throw new u.InputError('더 내는 부가세를 넣으면 결과가 나와요.');
      if (!Number.isInteger(extra)) throw new u.InputError('세액은 1원 단위 정수로 넣어 주세요.');
      if (extra > d.max_input) throw new u.InputError('세액이 너무 커요. 1천억 원 이하로 넣어 주세요.');
      const due = u.parseDate(v.due);
      const paid = u.parseDate(v.paid);
      if (due === null || paid === null) throw new u.InputError('원래 기한과 납부하는 날을 모두 넣어 주세요.');
      if (paid <= due) throw new u.InputError('납부하는 날이 원래 기한보다 뒤여야 해요. 기한이 지나기 전이면 이 계산 대상이 아니에요.');
      if (u.isoDate(paid) < d.delay_rule_from) throw new u.InputError('2026년 7월 1일 이후에 납부하는 경우를 계산해요.');
      const days = u.daysBetween(due, paid) - 1; // 기한 다음 날부터 납부일 전날까지 (제47조의4①1)
      if (days > d.max_days) throw new u.InputError('기한이 지난 지 10년이 넘으면 계산하지 않아요.');

      const rate = v.fraud === 'yes' ? d.fraud_rate_pct : d.rate_pct;
      let cut = 0;
      let tierLabel = '2년 넘게 지나 감면 없음';
      for (const t of d.reduction) {
        if (paid <= monthsEnd(due, t.months)) {
          cut = t.cut_pct;
          tierLabel = `기한 뒤 ${t.label} 이내 수정신고 → ${t.cut_pct}% 감면`;
          break;
        }
      }
      const f10 = u.floor10;
      const fullPenalty = f10((extra * rate) / 100);
      const underPenalty = f10((extra * rate * (100 - cut)) / 10000);
      const delayPenalty = f10((extra * days * d.daily_rate_per_100k) / 100000);
      const penalty = underPenalty + delayPenalty;
      const total = extra + penalty;

      const values = { cut_pct: cut, days, full_penalty: fullPenalty, under_penalty: underPenalty, delay_penalty: delayPenalty, penalty, total };
      const rows = [
        ['더 내는 부가세', u.won(extra)],
        [`과소신고가산세 (감면 전, ${rate}%)`, u.won(fullPenalty)],
        ['수정신고 시기', tierLabel],
        ['과소신고가산세 (감면 뒤)', u.won(underPenalty)],
        [`납부지연가산세 (${days}일 × 하루 0.022%)`, u.won(delayPenalty)],
        ['가산세 합계', u.won(penalty)],
        ['이번에 낼 금액 (세액 + 가산세)', u.won(total)],
      ];
      const notes = [
        '감면은 과소신고가산세에만 돼요. 납부지연가산세는 줄지 않고 하루씩 늘어요.',
        '세무조사 착수를 알거나 세무서의 과세자료 해명 통지를 받고 수정신고하면 감면받지 못해요.',
        '세금계산서 미발급·지연발급, 합계표 관련 가산세는 이 계산에 들어 있지 않아요.',
        '가산세는 10원 미만을 버렸어요.',
        '감면 구간의 마지막 날이 토요일·일요일·공휴일이면 구간 판단을 세무서에 확인해요.',
      ];
      if (v.fraud === 'yes') notes.push('역외거래에서 생긴 부정행위는 40%가 아니라 60%예요.');
      if (cut === 0) notes.push('기한이 지난 지 2년이 넘으면 수정신고해도 과소신고가산세가 감면되지 않아요.');
      return { values, rows, notes };
    },
  });
})();
