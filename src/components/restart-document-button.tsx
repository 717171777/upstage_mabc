'use client';

import {useId, useRef, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useApp} from '@/contexts/AppContext';

export function RestartDocumentButton({disabled = false}: {disabled?: boolean}) {
  const {job, busy, erase} = useApp();
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const lock = useRef(false);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState('');
  const titleId = useId();
  const descriptionId = useId();
  const blocked = disabled || busy || restarting || job?.status === 'analyzing'
    || job?.status === 'rendering' || job?.aiReview?.status === 'running';

  const restart = async () => {
    if (blocked || lock.current) return;
    lock.current = true; setRestarting(true); setError('');
    try {
      await erase();
      dialog.current?.close();
      router.replace('/');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '작업을 정리하지 못했어요. 다시 시도해 주세요.');
    } finally { lock.current = false; setRestarting(false); }
  };

  return <>
    <button type="button" disabled={blocked} onClick={()=>{setError('');dialog.current?.showModal();}}
      className="min-h-10 shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
      ← 처음으로
    </button>
    <dialog ref={dialog} aria-labelledby={titleId} aria-describedby={descriptionId}
      onCancel={event=>{if(restarting)event.preventDefault();}}
      className="fixed inset-0 m-auto w-[calc(100%-2rem)] max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl backdrop:bg-slate-900/40">
      <h2 id={titleId} className="text-lg font-semibold text-slate-900">새 문서를 선택할까요?</h2>
      <div id={descriptionId} className="mt-3 space-y-2 text-sm leading-relaxed text-slate-600">
        <p>현재 문서의 분석 결과, 가림 설정과 저장 사본은 삭제됩니다.</p>
        <p>컴퓨터의 원본과 이미 다운로드한 파일은 그대로 남아요.</p>
      </div>
      {error&&<p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" autoFocus disabled={restarting} onClick={()=>dialog.current?.close()}
          className="min-h-11 rounded-lg border border-slate-300 px-4 text-sm text-slate-700 disabled:opacity-40">계속 검토하기</button>
        <button type="button" disabled={blocked} onClick={()=>void restart()}
          className="min-h-11 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white disabled:opacity-40">{restarting?'정리 중…':'새 문서 선택'}</button>
      </div>
    </dialog>
  </>;
}
