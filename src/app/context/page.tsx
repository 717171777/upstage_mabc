'use client';

import { useApp } from '@/contexts/AppContext';
import { ServiceShell, AnalysisStatus, EmptyJob, LegacyJobNotice } from '@/components/service-shell';
import { SharingContextFields } from '@/components/sharing-context-fields';
import { DOCTYPE_LABELS, type Context, type Job } from '@/lib/service';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

function ContextForm({ job }: { job: Job }) {
  const { busy, mutate, setError } = useApp();
  const router = useRouter();
  const [context, setContext] = useState<Context>(job.context);
  const [documentType, setDocumentType] = useState(job.documentType ?? '');
  const disabled = busy || job.status === 'analyzing' || job.status === 'rendering';

  async function save() {
    if (disabled) return;
    try {
      await mutate('context', { context, documentType: documentType || null });
      router.push('/decision');
    } catch (error) {
      setError(error instanceof Error ? error.message : '상황을 저장하지 못했습니다. 다시 시도해 주세요.');
    }
  }

  return <ServiceShell step={2} title="공유 상황 수정">
    <AnalysisStatus job={job}/>
    <section className="mt-5 max-w-3xl border border-slate-200 bg-white p-5">
      <p className="mb-5 text-sm leading-relaxed text-slate-600">바뀐 상황으로 가릴지 유지할지 다시 판단합니다. 직접 확정한 가림 방법은 유지합니다.</p>
      <SharingContextFields value={context} onChange={setContext} disabled={disabled}/>
      <details className="mt-5 border-t border-slate-200 pt-4">
        <summary className="cursor-pointer text-sm text-slate-600">문서 종류 수정 <span className="text-slate-500">· {DOCTYPE_LABELS[job.documentType ?? 'other']}</span></summary>
        <label className="mt-4 block text-sm text-slate-700">문서 종류
          <select value={documentType} onChange={e => setDocumentType(e.target.value)} disabled={disabled} className="mt-2 w-full border border-slate-300 bg-white px-3 py-2.5">
            <option value="">자동 분류</option>
            {Object.entries(DOCTYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
      </details>
      <div className="mt-6 flex flex-wrap items-center gap-4">
        <button type="button" onClick={() => void save()} disabled={disabled} className="bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:opacity-50">상황 반영해 다시 분석</button>
        <a href="/decision" className="text-sm text-slate-600 hover:underline">변경 없이 돌아가기</a>
      </div>
    </section>
  </ServiceShell>;
}

export default function ContextPage() {
  const { job } = useApp();
  if (!job) return <EmptyJob/>;
  if (!job.aiEnabled || job.upstageReanalysisRequired) return <LegacyJobNotice/>;
  return <ContextForm key={job.id} job={job}/>;
}
