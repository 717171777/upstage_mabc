import type { Job } from '@/lib/service';

const ERRORS: Record<string, string> = {
  INTERRUPTED: '서버 재시작으로 중단됐습니다. 저장된 결과부터 이어갈 수 있어요.',
  UPSTREAM_TIMEOUT: '모델 응답 시간이 초과됐습니다.',
  NETWORK_ERROR: '모델 연결이 일시적으로 끊겼습니다.',
  RATE_LIMIT: '모델 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
  PROVIDER_UNAVAILABLE: '모델 제공 서버가 일시적으로 응답하지 않습니다.',
  AUTH_ERROR: '모델 연결 인증을 확인해야 합니다. 관리자에게 알려 주세요.',
  RESPONSE_INVALID: '추천의 형식 또는 원문 근거 검사를 통과하지 못했습니다.',
  WORKER_FAILED: '가림 여부 판단에 실패했습니다.',
};

export function RecommendationProgress({ job }: { job: Job }) {
  const stage = job.analysis.hermes;
  const total = stage.totalBatches ?? 0;
  const completed = stage.completedBatches ?? 0;
  const validCount = Number.isInteger(total) && total > 0 && Number.isInteger(completed) && completed >= 0 && completed <= total;
  const error = stage.errorCode ? ERRORS[stage.errorCode] : null;
  if (!validCount && !error) return null;
  return <div aria-live="polite" aria-atomic="true" className="space-y-1.5 text-sm text-slate-600">
    {validCount && <p>가림 여부 {total}묶음 중 {completed}묶음 완료{stage.status === 'running' && stage.currentBatch ? ` · ${stage.currentBatch}번째 처리 중` : ''}</p>}
    {stage.status === 'running' && (stage.attempt ?? 0) > 1 && <p className="text-xs">일시적인 연결 문제로 현재 묶음을 다시 시도하고 있어요. ({stage.attempt}/{stage.maxAttempts ?? 2}회)</p>}
    {error && <p className="text-xs text-amber-800">{error}</p>}
  </div>;
}
