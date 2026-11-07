/* 고령자 계속고용장려금 계산기: 분기 지원 인원 한도(피보험자 평균 30%·최대 30명, 10명 미만 3명)와 분기 지원금(수도권 월 30만·비수도권 월 40만 원).
   기준은 고령자 고용안정지원금 지급 규정 제5조. 금액·한도는 data/senior-continued-employment.json */
(function () {
  'use strict';

  const REGION = [
    ['capital', '서울·인천·경기 (수도권)'],
    ['non_capital', '그 밖의 지역 (비수도권)'],
  ];

  FirstLabCalc.register({
    id: 'senior-continued-employment',
    title: '고령자 계속고용장려금 계산기',
    intro: '사업장 위치, 분기 석 달의 매월 말 피보험자 수, 정년 뒤 계속 고용한 직원 수를 넣으면 이번 분기 지원 인원 한도와 장려금이 나와요. 석 달을 모두 근무한 기준이에요.',
    inputs: [
      { name: 'region', label: '사업장 위치', type: 'select', default: 'capital', options: REGION },
      { name: 'm1', label: '분기 첫째 달 말 피보험자 수 (명)', type: 'number', min: 0, step: 1, default: 20, help: '그달 말일 기준 고용보험 피보험자 수예요 (예: 1분기면 1월 31일).' },
      { name: 'm2', label: '둘째 달 말 피보험자 수 (명)', type: 'number', min: 0, step: 1, default: 20 },
      { name: 'm3', label: '셋째 달 말 피보험자 수 (명)', type: 'number', min: 0, step: 1, default: 20 },
      { name: 'workers', label: '정년 뒤 계속 고용한 직원 수 (명)', type: 'number', min: 0, step: 1, default: 8, help: '지원 대상 요건(정년 전 피보험기간 2년 이상, 월 보수 124만 원 이상 등)을 갖춘 사람만 세요.' },
    ],

    compute(v, d, u) {
      const rule = d.regions[v.region];
      if (!rule) throw new u.InputError('사업장 위치를 골라 주세요.');
      if ([v.m1, v.m2, v.m3].some((x) => x === undefined || x === null || String(x).trim() === '')) throw new u.InputError('석 달의 매월 말 피보험자 수를 모두 넣어 주세요.'); // 빈칸을 0명으로 읽지 않게 (11/7 검수)
      const months = [v.m1, v.m2, v.m3].map(Number);
      if (months.some((n) => !(n >= 0) || !Number.isInteger(n))) throw new u.InputError('석 달의 매월 말 피보험자 수를 정수로 모두 넣어 주세요.');
      const sum = months[0] + months[1] + months[2];
      if (!(sum > 0)) throw new u.InputError('피보험자 수를 넣으면 결과가 나와요.');
      const workers = Number(v.workers);
      if (!(workers > 0)) throw new u.InputError('계속 고용한 직원 수를 1명 이상 넣어 주세요.');
      if (!Number.isInteger(workers)) throw new u.InputError('직원 수는 정수로 넣어 주세요.');
      if (Math.max(...months) > d.max_insured) throw new u.InputError('피보험자 수가 너무 커요. 1,000,000명 이하로 넣어 주세요.');

      const avg = sum / 3;
      // 평균 < 10명 ⇔ 합 < 30명. 평균의 30%(소수점 이하 버림) = 합 × 30 ÷ 300 을 정수로 계산 (평균을 반올림해 넣으면 1명 적게 나오던 문제, 11/7 검수)
      const small = sum < d.small_threshold * 3;
      const byRate = u.floor((sum * d.cap_rate_pct) / 300);
      const cap = small ? d.small_cap : Math.min(byRate, d.cap_max);
      const paid = Math.min(workers, cap);
      const monthly = rule.monthly;
      const quarterly = paid * monthly * 3;
      const perWorkerTotal = monthly * d.max_months;
      const values = { sum, cap, paid, monthly, quarterly, per_worker_total: perWorkerTotal, small };

      const rows = [
        ['1인당 월 지원액', `${u.won(monthly)} (${rule.label})`],
        ['이번 분기 인원 한도', small
          ? `${cap}명 (평균 ${d.small_threshold}명 미만 사업장)`
          : `${cap}명 (평균 ${(Math.round(avg * 100) / 100).toLocaleString('ko-KR')}명의 ${d.cap_rate_pct}%, 최대 ${d.cap_max}명)`],
        ['지원받는 인원', `${paid}명`],
        ['이번 분기 장려금', u.won(quarterly)],
        ['1인당 3년 최대', u.won(perWorkerTotal)],
      ];
      const notes = [];
      if (workers > cap) notes.push(`계속 고용한 직원이 ${workers}명이어도 이번 분기에는 한도인 ${cap}명까지만 지원돼요.`);
      if (small) notes.push(`평균 피보험자 수가 ${d.small_threshold}명 미만이면 30% 계산과 상관없이 ${d.small_cap}명까지 지원돼요.`);
      notes.push('달 중간에 입사·퇴직한 직원은 그달을 날짜만큼 나눠 계산해서 이 결과보다 적어요.');
      notes.push('지원 기간은 직원마다 처음 계속 고용된 날부터 최대 3년이에요.');
      notes.push('신청은 그 분기 마지막 날의 다음 날부터 1년 안에 고용24나 관할 고용센터에 해요.');
      return { values, rows, notes };
    },
  });
})();
