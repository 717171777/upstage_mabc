'use client';

import {useState} from 'react';
import {ShieldCheck} from 'lucide-react';
import {useApp} from '@/contexts/AppContext';

export function FullRedactionToggle() {
  const {job, busy, mutate, setError} = useApp();
  const [saving, setSaving] = useState(false);
  if (!job || !job.aiEnabled || job.upstageReanalysisRequired) return null;
  const enabled = job.fullRedaction === true;
  const disabled = busy || saving || ['analyzing', 'rendering'].includes(job.status) || job.aiReview?.status === 'running';
  const unresolved = job.candidates.filter(candidate => !candidate.locationResolved).length;
  const toggle = async () => {
    if (disabled) return;
    setSaving(true);
    try {
      await mutate('plan', {fullRedaction: !enabled});
    } catch (error) {
      setError(error instanceof Error ? error.message : '전체 가림 설정을 저장하지 못했습니다.');
    } finally {
      setSaving(false);
    }
  };

  return <section aria-label="전체 가림 설정" className={`mb-4 rounded-2xl border p-4 ${enabled ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-white'}`}>
    <div className="flex items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <ShieldCheck aria-hidden="true" className={`h-6 w-6 shrink-0 ${enabled ? 'text-blue-600' : 'text-slate-400'}`} />
        <div><p id="full-redaction-label" className="text-sm font-semibold text-slate-800">모든 개인정보 전체 가림</p>
          <p id="full-redaction-description" className="mt-1 text-xs leading-relaxed text-slate-600">{enabled
            ? '찾은 개인정보를 모두 가립니다. 유지·부분 가림보다 우선하며 문서 속성도 삭제합니다.'
            : '켜면 찾은 모든 개인정보를 전체 가림합니다. 끈 뒤에도 전체 가림은 유지되며 항목별로 변경할 수 있습니다.'}</p>
        </div>
      </div>
      <button type="button" role="switch" aria-checked={enabled} aria-labelledby="full-redaction-label" aria-describedby="full-redaction-description" disabled={disabled} onClick={() => void toggle()}
        className="flex min-h-11 shrink-0 items-center gap-2 rounded-lg px-1 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-600 disabled:opacity-50">
        <span className="text-xs font-medium text-slate-600">{saving ? '저장 중…' : enabled ? '켜짐' : '꺼짐'}</span>
        <span aria-hidden="true" className={`relative inline-block h-7 w-12 rounded-full transition-colors ${enabled ? 'bg-blue-600' : 'bg-slate-300'}`}>
          <span className={`absolute left-1 top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${enabled ? 'translate-x-5' : ''}`} />
        </span>
      </button>
    </div>
    {enabled && <p role="status" className="mt-3 text-xs text-blue-800">{unresolved
      ? `위치 확인이 필요한 개인정보 ${unresolved}곳이 있습니다. 원문 위치를 연결해야 내보낼 수 있습니다.`
      : `${job.candidates.length}곳 전체 가림 적용. 원문과 저장 사본을 비교해 누락된 정보도 확인해 주세요.`}
      {job.uninspected.length > 0 && ' 검사하지 못한 영역이 있어 내보내기가 차단됩니다.'}</p>}
  </section>;
}
