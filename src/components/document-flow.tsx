export type DocumentFlowPhase =
  | 'select'
  | 'context'
  | 'analyzing'
  | 'analysis-failed'
  | 'review'
  | 'rendering'
  | 'copy-failed'
  | 'ready';

const STEPS = [
  { title: '문서 선택', description: '가릴 정보가 있는 파일을 골라요.' },
  { title: '공유 상황', description: '누구에게, 무엇을 위해 보내는지 정해요.' },
  { title: 'AI 분석·판단', description: '문서 근거와 공유 상황으로 가릴지 판단하고, 애매한 항목은 확인 질문을 만들어요.' },
  { title: '가림 검토', description: '전체 가림이 기본이에요. 필요하면 직접 방법을 바꿔요.' },
  { title: '공유본 받기', description: '실제 사본을 다시 검사한 뒤 내려받아요.' },
] as const;

const CURRENT_STEP: Record<DocumentFlowPhase, number> = {
  select: 0,
  context: 1,
  analyzing: 2,
  'analysis-failed': 2,
  review: 3,
  rendering: 4,
  'copy-failed': 4,
  ready: 4,
};

const CURRENT_LABEL: Record<DocumentFlowPhase, string> = {
  select: '현재 단계',
  context: '현재 단계',
  analyzing: '분석 중',
  'analysis-failed': '확인 필요',
  review: '검토 중',
  rendering: '사본 검사 중',
  'copy-failed': '확인 필요',
  ready: '검사 완료',
};

export function DocumentFlow({
  phase,
  reviewedCount,
  totalCount,
}: {
  phase: DocumentFlowPhase;
  reviewedCount?: number;
  totalCount?: number;
}) {
  const current = CURRENT_STEP[phase];
  const showReviewCount = phase === 'review'
    && reviewedCount !== undefined && totalCount !== undefined
    && Number.isInteger(reviewedCount) && Number.isInteger(totalCount)
    && reviewedCount >= 0 && totalCount > 0 && reviewedCount <= totalCount;

  return (
    <section aria-label="문서 처리 흐름" className="min-w-0">
      <h2 className="mb-4 text-sm font-semibold text-slate-800">이렇게 공유본을 만들어요</h2>
      <ol className="list-none p-0">
        {STEPS.map((step, index) => {
          const active = index === current;
          const completed = index < current;
          const needsAttention = active && (phase === 'analysis-failed' || phase === 'copy-failed');
          const status = active ? CURRENT_LABEL[phase] : completed ? '완료' : '대기';
          const statusStyle = needsAttention ? 'text-amber-800' : active || completed ? 'text-blue-800' : 'text-slate-500';

          return (
            <li key={step.title} aria-current={active ? 'step' : undefined} className="relative grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 pb-4 last:pb-0">
              {index < STEPS.length - 1 && (
                <span aria-hidden="true" className={`absolute bottom-0 left-4 top-8 w-px ${completed ? 'bg-blue-600' : 'bg-slate-300'}`} />
              )}
              <span aria-hidden="true" className={`relative flex h-8 w-8 items-center justify-center text-sm font-semibold ${statusStyle}`}>
                {index + 1}
              </span>
              <div className="min-w-0 pt-0.5">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <h3 className={`text-sm font-semibold ${active ? 'text-blue-800' : 'text-slate-800'}`}>{step.title}</h3>
                  <span className={`text-xs font-medium ${statusStyle}`}>{status}</span>
                </div>
                <p className={`mt-1 text-xs leading-relaxed text-slate-600 ${active ? '' : 'hidden lg:block'}`}>{step.description}</p>
                {index === 3 && showReviewCount && (
                  <p className="mt-1 text-xs font-medium text-blue-800">{reviewedCount} / {totalCount}곳 확인</p>
                )}
                {active && phase === 'analysis-failed' && (
                  <p className="mt-1 text-xs leading-relaxed text-amber-800">분석 문제를 확인하고 다시 시도해 주세요.</p>
                )}
                {active && phase === 'copy-failed' && (
                  <p className="mt-1 text-xs leading-relaxed text-amber-800">사본 생성·검사를 마치지 못했어요.</p>
                )}
                {active && phase === 'ready' && (
                  <p className="mt-1 text-xs font-medium text-blue-800">이제 공유본을 내려받을 수 있어요.</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
