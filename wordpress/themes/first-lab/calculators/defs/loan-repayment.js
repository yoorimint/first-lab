/* 할부·대출 상환 계산기: 원금·연 금리·기간·상환 방식 → 월 납입금, 총 이자, 총 상환액. 표준 수학 공식 (data/loan-repayment.json 은 기본값·출처) */
(function () {
  'use strict';

  const won = (n) => Math.round(n);

  /** 상환 방식별 계산. r = 월이율, n = 개월 수 */
  const METHODS = {
    // 원리금균등: 매달 같은 금액 = P·r·(1+r)^n / ((1+r)^n − 1), 금리 0%면 P/n
    equal_payment(P, r, n) {
      // 같은 공식을 아주 작은 금리에서도 정밀도를 잃지 않는 형태로: P·r / (1 − (1+r)^−n)
      const monthly = r === 0 ? P / n : (P * r) / -Math.expm1(-n * Math.log1p(r));
      // 금리 0%면 이자는 0 — P/n·n 의 소수 오차로 '-0원'이 보이지 않게 (11/11 외부 검수, 1억·0%·360개월)
      return { first: monthly, last: monthly, interest: r === 0 ? 0 : Math.max(0, monthly * n - P) };
    },
    // 원금균등: 원금은 매달 P/n 씩, 이자는 남은 원금에 → 이자 합계 = P·r·(n+1)/2
    equal_principal(P, r, n) {
      return { first: P / n + P * r, last: (P / n) * (1 + r), interest: (P * r * (n + 1)) / 2 };
    },
    // 만기일시: 매달 이자만, 마지막 달에 원금까지
    bullet(P, r, n) {
      return { first: P * r, last: P * r + P, interest: P * r * n };
    },
  };

  FirstLabCalc.register({
    id: 'loan-repayment',
    title: '할부·대출 상환 계산기',
    intro: '빌리는 금액, 연 금리, 기간을 넣으면 매달 낼 돈과 이자 총액을 계산합니다.',
    inputs: [
      { name: 'principal', label: '할부·대출 원금 (원)', type: 'number', min: 0, step: 10000, help: '차값이라면 선수금을 뺀 금액이에요.' },
      { name: 'rate', label: '연 금리 (%)', type: 'number', min: 0, step: 0.1, help: '계약서·견적서에 적힌 연 이율' },
      { name: 'months', label: '기간 (개월)', type: 'number', min: 1, step: 1 },
      {
        name: 'method', label: '상환 방식', type: 'select', default: 'equal_payment',
        options: [['equal_payment', '원리금균등 (매달 같은 금액)'], ['equal_principal', '원금균등 (점점 줄어듦)'], ['bullet', '만기일시 (이자만 내다가 마지막에 원금)']],
        help: '자동차 할부는 대부분 원리금균등이에요.',
      },
    ],

    compute(v, d, u) {
      const P = Number(v.principal);
      const ratePct = Number(v.rate);
      const n = Number(v.months);
      if (!(P > 0)) throw new u.InputError('원금을 넣으면 결과가 나와요.');
      if (!(ratePct >= 0) || v.rate === '' || v.rate === undefined) throw new u.InputError('연 금리를 넣어 주세요 (무이자면 0).');
      if (!Number.isInteger(n) || n < 1 || n > d.max_months) throw new u.InputError(`기간은 1~${d.max_months}개월로 넣어 주세요.`);
      const calc = METHODS[v.method];
      if (!calc) throw new u.InputError('상환 방식을 골라 주세요.');

      const res = calc(P, ratePct / 100 / 12, n);
      const total = P + res.interest;
      const same = won(res.first) === won(res.last);
      const rows = [
        same ? ['월 납입금', u.won(res.first)] : ['월 납입금', `첫 달 ${u.won(res.first)} → 마지막 달 ${u.won(res.last)}`],
        ['이자 총액', u.won(res.interest)],
        ['총 상환액', u.won(total)],
      ];
      return {
        values: { first: won(res.first), last: won(res.last), interest: won(res.interest), total: won(total) },
        rows,
        notes: [
          '금리가 기간 내내 같다고 보고 계산했어요 (변동금리면 달라져요).',
          '실제 청구액은 납입일·월별 일수와 금융사의 원 단위 처리 방식에 따라 몇 원~몇백 원 다를 수 있어요.',
          '중도상환수수료, 보증료, 취급수수료 같은 비용은 들어 있지 않아요.',
        ],
      };
    },
  });
})();
