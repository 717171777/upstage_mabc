'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { FileText, Upload, ArrowRight, Shield } from 'lucide-react';
import { useApp } from '@/contexts/AppContext';
import { ServiceShell, AnalysisStatus, LegacyJobNotice } from '@/components/service-shell';
import { RecommendationProgress } from '@/components/recommendation-progress';
import { DocumentView } from '@/components/document-view';
import { DocumentFlow, type DocumentFlowPhase } from '@/components/document-flow';
import { UploadContextQuestions } from '@/components/upload-context-questions';
import { EMPTY_SHARING_CONTEXT } from '@/components/sharing-context-fields';
import { isUpstageComplete } from '@/lib/workspace';
import { DOCTYPE_LABELS, exampleFile, fetchHealth, type Context, type HealthResult, type Job } from '@/lib/service';

const ALLOWED_EXTS = new Set(['pdf', 'docx']);
const MAX_SIZE = 10 * 1024 * 1024;
const ANALYSIS_STEPS = [
  ['parse', '문서 구조 읽기'], ['classify', '문서 종류 알아보기'],
  ['extract', '개인정보 찾기'], ['hermes', '가림 여부 판단'],
] as const;
const PRIMARY_BUTTON = 'inline-flex min-h-11 items-center justify-center gap-2 bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50';
const SECONDARY_BUTTON = 'inline-flex min-h-11 items-center justify-center gap-2 border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50';

function validateFile(file: File | null): string | null {
  if (!file) return '파일이 없습니다.';
  if (file.size === 0) return '빈 파일은 업로드할 수 없습니다.';
  if (file.size > MAX_SIZE) return '파일 크기는 10MiB 이하여야 합니다.';
  if (!ALLOWED_EXTS.has(file.name.split('.').pop()?.toLowerCase() ?? '')) return 'PDF 또는 DOCX 파일을 선택해 주세요.';
  return null;
}

function copyReady(job: Job): boolean {
  return isUpstageComplete(job) && job.status === 'validated' && !!job.artifact
    && job.artifact.version === job.version && job.artifact.validationVersion === job.version
    && job.artifact.checks.every(check => check.passed);
}

function phaseForJob(job: Job): DocumentFlowPhase {
  if (job.status === 'analyzing') return 'analyzing';
  if (!isUpstageComplete(job)) return 'analysis-failed';
  if (job.status === 'rendering') return 'rendering';
  if (job.status === 'failed') return 'copy-failed';
  return copyReady(job) ? 'ready' : 'review';
}

function AnalysisWorkspace({ job, busy, onRetry }: { job: Job; busy: boolean; onRetry: () => Promise<void> }) {
  const [showPreview, setShowPreview] = useState(false);
  const phase = phaseForJob(job);
  const running = phase === 'analyzing' || phase === 'rendering';
  const failed = phase === 'analysis-failed';
  const copyFailed = phase === 'copy-failed';
  const ready = phase === 'ready';
  const title = failed ? '분석을 마치지 못했어요' : copyFailed ? '사본 생성·검사를 마치지 못했어요' : phase === 'analyzing' ? '문서를 분석하고 있어요'
    : phase === 'rendering' ? '공유본을 만들고 검사하고 있어요' : ready ? '검사를 마친 공유본이 있어요' : '가릴 정보가 준비됐어요';
  const subtitle = failed ? (job.analysisResume?.completedStages.length === 3 ? '문서 분석 결과가 저장돼 있어요. 완료한 판단을 재사용하고 남은 항목의 가림 여부만 판단합니다.' : '공유 상황은 그대로 사용합니다. 저장된 결과가 없는 분석 단계는 다시 실행합니다.')
    : copyFailed ? '다운로드할 수 있는 사본이 아직 없습니다. 내보내기 화면에서 가림 선택을 확인하고 다시 만들어 주세요.'
    : phase === 'analyzing' ? '문서 근거와 공유 상황을 함께 보고 가림·유지를 판단해요. 애매한 항목은 확인 질문을 준비해요.'
    : phase === 'rendering' ? '선택을 적용한 파일을 저장한 뒤, 원문이 제거됐는지 다시 확인하고 있어요.'
    : ready ? '원본과 실제 저장한 사본을 나란히 확인하고 파일을 받을 수 있어요.'
    : '가릴 정보는 전체 가림으로 준비했어요. 직접 방법을 바꾸거나 기본 설정으로 파일을 받을 수 있어요.';
  return <div className="flex h-full flex-col">
    <div className="border-b border-slate-200 px-6 py-5 sm:px-8">
      <div className="flex min-w-0 items-center gap-3"><FileText className="h-6 w-6 shrink-0 text-blue-600" aria-hidden="true"/><div className="min-w-0"><p className="break-all text-sm font-semibold text-slate-800">{job.fileName}</p><p className="mt-1 text-xs text-slate-500">{job.format.toUpperCase()}{job.documentType && job.analysis.classify.status === 'completed' ? ` · ${DOCTYPE_LABELS[job.documentType]}` : ''}</p></div></div>
    </div>
    <div className="flex-1 space-y-6 px-6 py-7 sm:px-8">
      <div aria-live="polite" aria-atomic="true"><p className={`mb-3 text-xs font-semibold tracking-wide ${failed || copyFailed ? 'text-amber-800' : 'text-blue-600'}`}>{failed || copyFailed ? '확인이 필요해요' : running ? '문서 처리 중' : '다음 단계로 진행하세요'}</p><h2 className="text-xl font-semibold leading-snug tracking-tight text-slate-900 sm:text-2xl">{title}</h2><p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-600">{subtitle}</p></div>
      <ul aria-label="실제 분석 상태" className="flex flex-col gap-3">
        {ANALYSIS_STEPS.map(([key, label]) => {
          const status = job.analysis[key].status;
          const completed = status === 'completed';
          const notNeeded = key === 'hermes' && status === 'not_needed' && job.analysis.hermes.required === false;
          const active = status === 'running';
          const broken = status === 'failed' || (status === 'not_needed' && !notNeeded);
          return <li key={key} className={`flex items-center justify-between gap-3 border px-3 py-3 text-sm ${broken ? 'border-amber-200 bg-amber-50' : active ? 'border-blue-500 bg-blue-50' : 'border-slate-200 bg-white'}`}><span className="text-slate-700">{label}</span><span className={`inline-flex shrink-0 items-center gap-1.5 text-xs font-medium ${broken ? 'text-amber-800' : completed || active ? 'text-blue-600' : 'text-slate-500'}`}>{completed ? '완료' : notNeeded ? '추가 판단 불필요' : active ? '진행 중' : broken ? '다시 확인' : '대기'}</span></li>;
        })}
      </ul>
      <RecommendationProgress job={job}/>
      {job.context.recipient || job.context.purpose || job.context.keepInfo ? <div className="border-l-2 border-blue-500 pl-4 text-sm"><p className="font-medium text-slate-700">함께 반영한 공유 상황</p><dl className="mt-2 space-y-1.5 text-xs leading-relaxed text-slate-500">{job.context.recipient && <div className="flex gap-2"><dt className="shrink-0">공유 대상</dt><dd className="break-words">{job.context.recipient}</dd></div>}{job.context.purpose && <div className="flex gap-2"><dt className="shrink-0">공유 목적</dt><dd className="min-w-0 whitespace-pre-wrap break-words">{job.context.purpose}</dd></div>}{job.context.keepInfo && <div className="flex gap-2"><dt className="shrink-0">유지 희망</dt><dd className="min-w-0 whitespace-pre-wrap break-words">{job.context.keepInfo}</dd></div>}</dl></div> : <p className="text-xs text-slate-500">공유 상황을 따로 입력하지 않아 문서의 근거를 중심으로 판단합니다.</p>}
      {!running && !failed && <p className="text-sm text-slate-600">가림 후보 <strong className="text-slate-800">{job.candidates.length}곳</strong>{job.candidates.some(c => !c.locationResolved) ? ` · 위치 확인이 필요한 ${job.candidates.filter(c => !c.locationResolved).length}곳은 검토 화면에서 확인해 주세요.` : '을 검토할 수 있어요.'}</p>}
      <details className="border-t border-slate-200 pt-4 text-xs text-slate-500"><summary className="cursor-pointer">자세한 분석 내역</summary><div className="mt-3"><AnalysisStatus job={job}/></div></details>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-6 py-5 sm:px-8">
      {failed ? <button type="button" disabled={busy} onClick={() => void onRetry()} className={PRIMARY_BUTTON}>{busy ? '다시 시작하는 중…' : job.analysisResume?.completedStages.length === 3 ? '가림 여부만 다시 판단' : '중단된 분석 이어서 진행'}</button> : running ? <p role="status" className="text-sm text-slate-600">처리가 끝나면 다음 단계로 이동할 수 있어요.</p> : <Link className={PRIMARY_BUTTON} href={ready || copyFailed ? '/export' : '/decision'}>{ready ? '공유본 비교하고 받기' : copyFailed ? '내보내기에서 다시 확인' : '가림 검토 시작'}<ArrowRight className="h-4 w-4" aria-hidden="true"/></Link>}
      {!running && <Link href="/context" className="py-2 text-sm text-blue-600 hover:underline">공유 상황 수정</Link>}
    </div>
    <details className="border-t border-slate-200 px-6 py-4 text-xs text-slate-500 sm:px-8" onToggle={e => setShowPreview(e.currentTarget.open)}><summary className="cursor-pointer">선택한 문서 미리보기</summary>{showPreview && <div className="mt-4"><DocumentView job={job}/></div>}</details>
  </div>;
}

export default function HomePage() {
  const { job, busy, restoring, setError, start, mutate } = useApp();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sharingContext, setSharingContext] = useState<Context>(EMPTY_SHARING_CONTEXT);
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [loadingExample, setLoadingExample] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const startLock = useRef(false);
  const exampleLock = useRef(false);

  const checkHealth = useCallback(async () => {
    try { setHealth(await fetchHealth()); setHealthError(null); }
    catch { setHealth(null); setHealthError('분석 연결을 확인하지 못했습니다. 다시 확인해 주세요.'); }
  }, []);
  useEffect(() => {
    let cancelled = false;
    fetchHealth().then(result => { if (!cancelled) { setHealth(result); setHealthError(null); } })
      .catch(() => { if (!cancelled) setHealthError('분석 연결을 확인하지 못했습니다. 다시 확인해 주세요.'); });
    return () => { cancelled = true; };
  }, []);

  const analysisAvailable = !!health?.ok && !!health.aiConfigured && health.upstageRequired === true;
  const blocked = !!job || busy || restoring || starting || loadingExample || !analysisAvailable;
  const handleFile = useCallback((file: File | null) => {
    if (startLock.current) return;
    const error = validateFile(file);
    if (error) { setError(error); return; }
    setSelectedFile(file); setError(null);
  }, [setError]);
  const onDrop = (event: React.DragEvent) => {
    event.preventDefault(); setDragActive(false);
    if (blocked) return;
    if (event.dataTransfer.files.length !== 1) { setError('파일은 한 번에 1개만 선택해 주세요.'); return; }
    handleFile(event.dataTransfer.files[0]);
  };
  const onExample = async (format: 'docx' | 'pdf') => {
    if (blocked || exampleLock.current) return;
    exampleLock.current = true; setLoadingExample(true);
    try { handleFile(await exampleFile(format)); }
    catch { setError('예시 파일을 불러오지 못했습니다.'); }
    finally { exampleLock.current = false; setLoadingExample(false); }
  };
  const onStart = async (context: Context) => {
    if (!selectedFile || blocked || startLock.current) return;
    startLock.current = true; setStarting(true); setError(null);
    try { await start(selectedFile, context); }
    catch (error) { setError(error instanceof Error ? error.message : '분석을 시작하지 못했습니다.'); }
    finally { startLock.current = false; setStarting(false); }
  };
  const retry = async () => {
    if (!job || busy || startLock.current) return;
    startLock.current = true;
    try { await mutate('retry-analysis', {}); }
    catch (error) { setError(error instanceof Error ? error.message : '분석을 다시 시작하지 못했습니다.'); }
    finally { startLock.current = false; }
  };
  const clearFile = () => { if (!blocked) { setSelectedFile(null); setError(null); if (fileInputRef.current) fileInputRef.current.value = ''; } };
  if (job && (!job.aiEnabled || job.upstageReanalysisRequired)) return <LegacyJobNotice/>;
  const phase = job ? phaseForJob(job) : selectedFile ? 'context' : 'select';

  return <ServiceShell step={1} title="공유할 문서, 함께 준비해요" showSteps={false}>
    <p className="mb-7 max-w-2xl text-sm leading-relaxed text-slate-600 sm:text-base">문서를 고르고 공유 상황을 알려주시면, 가릴 정보를 판단해요. 가리는 방법은 전체 가림이 기본이에요.</p>
    <div className="grid items-stretch border border-slate-200 bg-white lg:grid-cols-[330px_minmax(0,1fr)]">
      <aside data-testid="document-flow-panel" className="min-w-0 border-b border-slate-200 bg-blue-50/30 px-6 py-6 lg:border-b-0 lg:border-r lg:px-7 lg:py-8">
        <DocumentFlow phase={phase} reviewedCount={job?.candidates.filter(c => c.confirmed).length} totalCount={job?.candidates.length}/>
        <div className="mt-6 hidden border-t border-slate-200 pt-5 text-xs leading-relaxed text-slate-500 lg:block"><p className="flex items-center gap-2 font-medium text-slate-700"><Shield className="h-4 w-4 text-blue-600" aria-hidden="true"/>원본은 그대로 보관하세요</p><p className="mt-2">가림 선택을 적용한 공유용 사본을 만들고, 저장된 파일을 다시 검사해요.</p><p className="mt-4 font-medium">모든 문서를 Upstage로 분석합니다</p></div>
      </aside>
      <section data-testid="home-workspace" aria-label="문서 선택과 공유 상황" className="min-w-0">
        {job ? <AnalysisWorkspace key={job.id} job={job} busy={busy} onRetry={retry}/> : <>
          <input ref={fileInputRef} id="file-input" type="file" accept=".pdf,.docx" className="sr-only" tabIndex={-1} onChange={e => { if (!blocked) handleFile(e.target.files?.[0] ?? null); }} disabled={blocked} aria-label="공유할 문서 파일"/>
          {!analysisAvailable || restoring ? <div role="status" className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-800"><p>{restoring ? '저장된 작업을 확인하고 있어요.' : healthError || (health ? '분석 연결을 준비하고 있어요. 연결 후 파일을 선택할 수 있습니다.' : '분석 연결을 확인하고 있어요.')}</p>{!restoring && (healthError || health) && <button type="button" onClick={() => void checkHealth()} className="min-h-11 border border-amber-300 bg-white px-3 py-2 text-xs font-medium">연결 다시 확인</button>}</div> : null}
          {selectedFile ? <>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-6 py-5 sm:px-8"><div className="flex min-w-0 flex-1 items-center gap-3"><FileText className="h-6 w-6 shrink-0 text-blue-600" aria-hidden="true"/><div className="min-w-0"><p className="break-all text-sm font-semibold text-slate-800">{selectedFile.name}</p><p className="mt-1 text-xs text-slate-500">{selectedFile.name.split('.').pop()?.toUpperCase()} · {Math.max(1, Math.round(selectedFile.size / 1024))} KB · 분석 시작 전</p></div></div><button type="button" onClick={clearFile} disabled={blocked} className="min-h-11 px-2 text-sm text-blue-600 hover:underline disabled:opacity-50">다른 문서 선택</button></div>
            <div className="px-6 py-7 sm:px-8"><UploadContextQuestions key={`${selectedFile.name}-${selectedFile.size}-${selectedFile.lastModified}`} value={sharingContext} onChange={setSharingContext} onStart={onStart} disabled={blocked} submitting={starting || busy}/></div>
          </> : <div className="flex min-h-[580px] flex-col px-6 py-7 sm:px-8">
            <div><p className="mb-3 text-xs font-semibold tracking-wide text-blue-600">문서부터 선택해 주세요</p><h2 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">어떤 문서를 공유하나요?</h2><p className="mt-3 text-sm leading-relaxed text-slate-600">파일을 고르면 공유 상황을 차례로 여쭤볼게요.<br className="hidden sm:block"/>답변은 선택 사항이고, 입력한 내용은 분석에 함께 반영돼요.</p></div>
            <div onDragEnter={e => { e.preventDefault(); if (!blocked) setDragActive(true); }} onDragOver={e => { e.preventDefault(); if (!blocked) setDragActive(true); }} onDragLeave={e => { if (!(e.relatedTarget instanceof Node) || !e.currentTarget.contains(e.relatedTarget)) setDragActive(false); }} onDrop={onDrop} className={`my-7 flex min-h-56 flex-col items-center justify-center border-2 border-dashed px-5 py-8 text-center transition-colors ${dragActive ? 'border-blue-500 bg-blue-50' : 'border-slate-300 bg-slate-50/50'}`}>
              <Upload className="mb-4 h-8 w-8 text-blue-600" strokeWidth={1.5} aria-hidden="true"/>
              <p className="mb-4 text-sm font-medium text-slate-700">파일을 이곳에 끌어다 놓으세요</p>
              <button type="button" onClick={() => fileInputRef.current?.click()} disabled={blocked} className={PRIMARY_BUTTON}>파일 선택<ArrowRight className="h-4 w-4" aria-hidden="true"/></button>
              <p className="mt-4 text-xs leading-relaxed text-slate-500">텍스트가 있는 PDF · DOCX · 최대 10MiB<br/>스캔 PDF는 지원하지 않습니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs"><span className="mr-1 text-slate-500">먼저 둘러볼까요?</span><button type="button" className={SECONDARY_BUTTON} onClick={() => void onExample('docx')} disabled={blocked}>합성 DOCX 예시</button><button type="button" className={SECONDARY_BUTTON} onClick={() => void onExample('pdf')} disabled={blocked}>합성 PDF 예시</button></div>
            <p className="mt-auto pt-6 text-xs leading-relaxed text-slate-500">파일 선택만으로는 외부에 전송하지 않아요. 공유 상황을 입력한 뒤 분석을 시작할 때 문서를 전송합니다.</p>
          </div>}
        </>}
      </section>
    </div>
  </ServiceShell>;
}
