/* 임원 퇴직소득 한도 계산기 (소득세법 제22조③④, 시행령 제42조의2⑥):
   2011년 말 이전분 = 퇴직소득금액 × 2011.12.31 이전 근무개월 ÷ 전체 근무개월(m_total, 없으면 세 구간 합) (먼저 뺌)
   한도 = 2019.12.31부터 소급 3년 연평균 총급여 × 1/10 × 2012~2019 근무개월/12 × 3
        + 퇴직일부터 소급 3년 연평균 총급여 × 1/10 × 2020년 이후 근무개월/12 × 2
   (2011년 말 이전분 뺀 금액 − 한도)가 양수면 근로소득. 근무기간은 개월 수(1개월 미만은 1개월, 제22조④1호).
   1원 미만은 버림. 배수·구간은 data/executive-severance-limit.json */
(function () {
  'use strict';

  const int = (raw, name, max, u) => {
    const n = raw === undefined || raw === null || raw === '' ? 0 : Number(raw);
    if (!(n >= 0) || !Number.isInteger(n)) throw new u.InputError(`${name}은 0 이상 정수로 넣어 주세요.`);
    if (n > max) throw new u.InputError(`${name}이 너무 커요.`);
    return n;
  };

  FirstLabCalc.register({
    id: 'executive-severance-limit',
    title: '임원 퇴직금 한도 계산기',
    intro: '퇴직금과 구간별 근무개월, 기준 급여를 넣으면 소득세법상 퇴직소득 한도와 근로소득이 되는 초과분이 나와요.',
    inputs: [
      { name: 'severance', label: '퇴직금 (원)', type: 'number', min: 0, step: 1000000, help: '받는 퇴직급여 전체예요. 비과세소득, 공적연금 일시금, 회사 비용 한도를 넘어 상여로 처분된 금액은 빼고 넣어요.' },
      { name: 'm_pre2012', label: '2011년 12월 31일까지 근무개월 (없으면 비워 둬요)', type: 'number', min: 0, step: 1, help: '1개월이 안 되는 기간은 1개월로 세요.' },
      { name: 'm_2012', label: '2012년 1월~2019년 12월 근무개월 (최대 96)', type: 'number', min: 0, step: 1 },
      { name: 'm_2020', label: '2020년 1월 이후 근무개월', type: 'number', min: 0, step: 1 },
      { name: 'm_total', label: '전체 근무개월 (2011년까지 근무가 있을 때만, 비워 두면 세 칸의 합)', type: 'number', min: 0, step: 1, help: '입사(취임)일부터 퇴직일까지를 통째로 센 개월 수예요. 시작일과 퇴직일이 달 중간이면 세 칸의 합보다 1~2개월 적을 수 있어요.' },
      { name: 'avg_2019', label: '2017~2019년 연평균 총급여 (원)', type: 'number', min: 0, step: 1000000, help: '2019년 12월 31일부터 거꾸로 3년 동안 받은 총급여의 연평균이에요. 그 기간 근무가 3년이 안 되면 근무기간 총급여를 1년으로 환산해요.' },
      { name: 'avg_last', label: '퇴직 전 3년 연평균 총급여 (원)', type: 'number', min: 0, step: 1000000, help: '퇴직한 날부터 거꾸로 3년 동안 받은 총급여의 연평균이에요. 근무가 3년이 안 되면 근무기간 총급여를 1년으로 환산해요.' },
    ],

    compute(v, d, u) {
      const severance = Number(v.severance);
      if (!(severance > 0)) throw new u.InputError('퇴직금을 넣으면 결과가 나와요.');
      if (!Number.isInteger(severance)) throw new u.InputError('금액은 1원 단위 정수로 넣어 주세요.');
      if (severance > d.max_input) throw new u.InputError('금액이 너무 커요. 1,000억 원 이하로 넣어 주세요.');
      const m0 = int(v.m_pre2012, '2011년까지 근무개월', d.max_months, u);
      const m1 = int(v.m_2012, '2012~2019년 근무개월', d.months_2012_2019, u);
      const m2 = int(v.m_2020, '2020년 이후 근무개월', d.max_months, u);
      const sum = m0 + m1 + m2;
      if (sum === 0) throw new u.InputError('근무개월을 하나 이상 넣어 주세요.');
      // 시행령 제42조의2⑥ 분모는 전체 근무기간을 통째로 센 개월 수 (구간별 올림 합보다 최대 2개월 적을 수 있음)
      const mt = int(v.m_total, '전체 근무개월', d.max_months, u);
      if (mt && (mt > sum || mt < sum - 2 || mt < m0)) throw new u.InputError('전체 근무개월은 세 칸의 합과 같거나 1~2개월 적어야 해요.');
      const total = mt || sum;
      const avg1 = int(v.avg_2019, '2017~2019년 연평균 총급여', d.max_input, u);
      const avg2 = int(v.avg_last, '퇴직 전 3년 연평균 총급여', d.max_input, u);
      const f = u.floor;
      const pre = f((severance * m0) / total);
      const subject = severance - pre;
      const lim1 = f((avg1 * m1 * d.multiple_2012_2019) / 120);
      const lim2 = f((avg2 * m2 * d.multiple_2020) / 120);
      const limit = lim1 + lim2;
      const excess = Math.max(0, subject - limit);
      const retirement = severance - excess;
      const values = { pre2012: pre, subject, limit_2012: lim1, limit_2020: lim2, limit, excess, retirement };
      const rows = [
        ['퇴직금', u.won(severance)],
        ['2011년 말까지 근무분 (한도 계산 전 뺌)', u.won(pre)],
        ['한도와 비교할 금액', u.won(subject)],
        [`2012~2019년분 한도 (${m1}개월 × ${d.multiple_2012_2019}배)`, u.won(lim1)],
        [`2020년 이후분 한도 (${m2}개월 × ${d.multiple_2020}배)`, u.won(lim2)],
        ['퇴직소득 한도 합계', u.won(limit)],
        ['퇴직소득으로 보는 금액', u.won(retirement)],
        ['근로소득이 되는 초과분', u.won(excess)],
      ];
      const notes = [];
      if (excess === 0) notes.push('한도 안이라 퇴직금 전액이 퇴직소득이에요.');
      else notes.push('초과분은 근로소득이라 그해 다른 종합소득과 합쳐 기본세율로 세금을 매겨요.');
      if (m1 > 0 && avg1 === 0) notes.push('2017~2019년 연평균 총급여를 비워 두면 2012~2019년분 한도가 0원으로 계산돼요.');
      if (m2 > 0 && avg2 === 0) notes.push('퇴직 전 3년 연평균 총급여를 비워 두면 2020년 이후분 한도가 0원으로 계산돼요.');
      if (m0 > 0) notes.push('2011년 말까지 근무분은 근무개월 비율로 뺐어요. 2011년 말에 정관이나 위임 규정이 있었다면 그 규정으로 받을 금액을 빼는 방법도 고를 수 있어요.');
      notes.push('1원 미만은 버렸어요. 회사 비용(법인세법 손금) 한도는 따로 계산해요.');
      return { values, rows, notes };
    },
  });
})();
