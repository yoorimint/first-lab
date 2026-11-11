/* 법정이자 계산기: 원금·이율 종류(민사 5%·상사 6%·임금 지연이자 20%·약정 이율)·이자 첫날·마지막 날·소장 송달일 → 일수, 구간별 이자, 합계.
 * 이자 = 원금 × 연 이율 × 일수 ÷ 365 (구간마다 원 미만 버림, 첫날·마지막 날 모두 포함해 셈).
 * 이율은 data/legal-interest.json (민법 제379조·제397조, 상법 제54조, 소송촉진법 제3조와 법정이율 규정 연 12%, 근로기준법 제37조·시행령 제17조 연 20%) */
(function () {
  'use strict';

  /** 원금 A(원, 정수) × 연 이율 R(만분율 정수, 5% → 500) × 일수 n ÷ 365 의 원 미만 버림 — 정수로 정확히 */
  function interestOf(A, R, n) {
    if (n <= 0) return 0;
    return Number((BigInt(A) * BigInt(R) * BigInt(n)) / (365n * 10000n));
  }

  FirstLabCalc.register({
    id: 'legal-interest',
    title: '법정이자 계산기 (지연이자·지연손해금)',
    intro: '받을 돈(원금), 이율 종류, 이자가 붙는 첫날과 마지막 날을 넣으면 일수와 이자가 나와요. 소송을 냈다면 소장 송달일을 넣어 그다음 날부터 연 12%로 나눠 계산해요.',
    inputs: [
      { name: 'principal', label: '원금 (원)', type: 'number', min: 0, step: 10000, help: '받지 못한 돈이에요. 이자는 빼고 넣어요.' },
      { name: 'kind', label: '이율 종류', type: 'select', default: 'civil', options: [
        ['civil', '민사 법정이율 연 5% (개인끼리 빌린 돈 등)'],
        ['commercial', '상사 법정이율 연 6% (상행위로 생긴 빚)'],
        ['wage', '임금·퇴직금 지연이자 연 20% (근로기준법)'],
        ['custom', '약정 이율 직접 넣기'],
      ] },
      { name: 'rate', label: '약정 이율 (연 %, 직접 넣기일 때만)', type: 'number', min: 0, step: 0.01, help: '계약서에 적은 연 이율(연체 이율)이에요.' },
      { name: 'start', label: '이자가 붙는 첫날', type: 'date', default: '', help: '판결문·지급명령에 적힌 "○○부터"의 날짜예요. 이 날도 일수에 넣어요.' },
      { name: 'end', label: '마지막 날 (갚는 날)', type: 'date', default: 'today', help: '다 갚는 날(완제일)이에요. 이 날도 일수에 넣어요.' },
      { name: 'served', label: '소장 송달일 (소송을 냈을 때만)', type: 'date', default: '', help: '소장이 상대에게 도착한 날이에요. 그다음 날부터 연 12%로 계산해요. 소송 전이면 비워 두세요.' },
    ],

    compute(v, d, u) {
      const A = Number(v.principal);
      if (!(A > 0)) throw new u.InputError('원금을 넣으면 결과가 나와요.');
      if (!Number.isInteger(A)) throw new u.InputError('원금은 1원 단위 정수로 넣어 주세요.');
      if (A > d.max_amount) throw new u.InputError('원금이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const kind = ['civil', 'commercial', 'wage', 'custom'].includes(v.kind) ? v.kind : 'civil';
      let ratePct;
      if (kind === 'custom') {
        ratePct = Number(v.rate);
        if (v.rate === '' || v.rate === undefined || !(ratePct >= 0) || ratePct > d.max_rate) throw new u.InputError(`약정 이율을 0~${d.max_rate}% 사이로 넣어 주세요.`);
      } else {
        ratePct = d.rates[kind];
      }
      const R = Math.round(ratePct * 100);
      if (Math.abs(ratePct * 100 - R) > 1e-6) throw new u.InputError('이율은 소수 둘째 자리까지 넣어 주세요 (예: 5.25).');

      const s = u.parseDate(v.start);
      const e = u.parseDate(v.end);
      if (s === null) throw new u.InputError('이자가 붙는 첫날을 넣어 주세요.');
      if (e === null) throw new u.InputError('마지막 날(갚는 날)을 넣어 주세요.');
      if (e < s) throw new u.InputError('마지막 날이 첫날보다 앞이에요. 날짜를 확인해 주세요.');
      const days = u.daysBetween(s, e) + 1;
      if (days > d.max_days) throw new u.InputError('기간이 너무 길어요. 100년 이하로 넣어 주세요.');

      const served = v.served ? u.parseDate(v.served) : null;
      if (v.served && served === null) throw new u.InputError('소장 송달일 형식을 확인해 주세요.');
      const useLitigation = served !== null && kind !== 'wage';
      const R12 = Math.round(d.rates.litigation * 100);

      let days1 = days;
      let days2 = 0;
      if (useLitigation) {
        // 송달일까지는 원래 이율, 송달일 다음 날부터 연 12% (소송촉진법 제3조①)
        days1 = Math.max(0, Math.min(days, u.daysBetween(s, served) + 1));
        days2 = days - days1;
      }
      const i1 = interestOf(A, R, days1);
      const i2 = interestOf(A, R12, days2);
      const total = i1 + i2;

      const pct = (r) => `연 ${(r / 100).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}%`;
      const rows = [
        ['원금', u.won(A)],
        ['기간', `${u.korDate(s)} ~ ${u.korDate(e)} (${days.toLocaleString('ko-KR')}일, 첫날·마지막 날 포함)`],
      ];
      if (useLitigation && days2 > 0 && days1 > 0) {
        rows.push([`송달일까지 (${pct(R)}, ${days1.toLocaleString('ko-KR')}일)`, u.won(i1)]);
        rows.push([`송달 다음 날부터 (${pct(R12)}, ${days2.toLocaleString('ko-KR')}일)`, u.won(i2)]);
      } else if (useLitigation && days1 === 0) {
        rows.push([`이율`, `${pct(R12)} (송달 다음 날 이후 기간)`]);
      } else {
        rows.push(['이율', pct(R)]);
      }
      rows.push(['이자 합계', u.won(total)], ['원금+이자', u.won(A + total)]);

      const notes = [
        '이자 = 원금 × 연 이율 × 일수 ÷ 365 로, 구간마다 원 미만은 버렸어요. 중간에 일부를 갚았다면 그 돈은 비용·이자·원금 순서로 채워지니, 남은 원금을 구해 날짜를 나눠 다시 계산해요.',
        '첫날을 일수에 넣을지, 윤년을 366일로 나눌지는 판결·계약마다 다를 수 있어요. 판결문이 있으면 거기 적힌 기간과 이율이 기준이에요.',
      ];
      if (useLitigation) notes.push('연 12%는 판결로 돈을 갚으라고 할 때 송달 다음 날부터 붙어요. 다만 상대가 갚을 의무나 금액을 다투는 것이 타당하다고 법원이 보면, 그 범위에서는 12%가 적용되지 않을 수 있어요.');
      if (useLitigation && v.served < d.litigation_from) notes.push('연 12%는 2019년 6월 1일부터 쓰는 이율이에요. 그 전부터 법원에 걸려 있던 사건은 기간별로 옛 이율이 쓰일 수 있으니 판결문을 확인하세요.');
      if (served !== null && kind === 'wage') notes.push('임금·퇴직금 지연이자 연 20%를 고르면 소장 송달일은 계산에 쓰지 않았어요.');
      if (useLitigation && R > R12) notes.push('약정 이율이 연 12%보다 높은데 송달 다음 날부터 12%로 계산했어요. 이 경우 어떤 이율이 붙는지는 판결문을 확인하세요.');
      if (kind === 'wage') notes.push('퇴직·사망하면 14일 안에, 재직 중이면 정해진 임금 지급일에 주지 않은 임금에 붙어요. 천재지변이나 법원·노동위원회에서 다투는 것이 적절한 경우 등은 그 기간 동안 연 20%가 적용되지 않아요.');
      if (kind === 'custom' && ratePct > d.loan_cap) notes.push(`개인끼리처럼 돈을 빌려주는 계약의 이자는 연 ${d.loan_cap}%를 넘는 부분이 무효예요(이자제한법). 다만 원금 10만 원 미만이면 이 상한이 적용되지 않고, 인가·허가·등록을 마친 금융회사·대부업자는 각자 해당 법률을 따라요.`);
      return { values: { days, days1, days2, interest1: i1, interest2: i2, interest: total, total: A + total }, rows, notes };
    },
  });
})();
