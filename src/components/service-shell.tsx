'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import { Shield, FileText } from 'lucide-react';
import { useApp } from '@/contexts/AppContext';
import { RecommendationProgress } from './recommendation-progress';
import type { Job } from '@/lib/service';
import {RestartDocumentButton} from './restart-document-button';

const STATUS_LABELS: Record<string, string> = {
  pending: '대기',
  running: '처리 중',
  completed: '성공',
  failed: '실패',
  disabled: '사용 안 함',
  not_needed: '판단 불필요',
  unknown: '알 수 없음',
};

const STAGE_KEYS = ['parse', 'classify', 'extract', 'hermes'] as const;
const STAGE_LABELS: Record<string, string> = {
  parse: '문서 구조',
  classify: '문서 분류',
  extract: '정보 추출',
  hermes: '가림 여부 판단',
};

export function ServiceShell({
  step,
  title,
  children,
  showSteps = true,
  actionsDisabled = false,
}: {
  step: 1 | 2 | 3;
  title: string;
  children: React.ReactNode;
  showSteps?: boolean;
  actionsDisabled?: boolean;
}) {
  const { job, busy, error, setError, erase } = useApp();
  const router=useRouter();
  const [showDelete, setShowDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  const steps = [
    { n: 1, label: '문서 선택' },
    { n: 2, label: '가림 검토' },
    { n: 3, label: '내보내기' },
  ];

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    setDeleteErr(null);
    try {
      await erase();
      router.push('/');
    } catch (e) {
      setDeleteErr(e instanceof Error ? e.message : '삭제 중 오류가 발생했습니다.');
    } finally {
      setDeleting(false);
      if (/* success */ true) {
        setShowDelete(false);
      }
    }
  }, [erase, router]);

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-3 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <Shield className="h-6 w-6 text-brand-500" />
            <span className="text-lg font-semibold text-slate-800">가리미</span>
          </Link>
          <p className="text-sm text-slate-500">문서 개인정보 마스킹</p>
        </div>
      </header>

      {showSteps && <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-2">
          <div className="flex items-center justify-center gap-5 sm:gap-8">
            {steps.map((s) => (
              <div key={s.n} className="flex items-center gap-2">
                <div
                  className={`flex h-7 w-7 items-center justify-center text-sm font-medium ${
                    step === s.n
                      ? 'text-brand-500'
                      : step > s.n
                      ? 'text-brand-500'
                      : 'text-slate-400'
                  }`}
                >
                  {s.n}
                </div>
                <span
                  className={`text-sm ${
                    step === s.n
                      ? 'text-brand-500 font-medium'
                      : step > s.n
                      ? 'text-slate-600'
                      : 'text-slate-400'
                  }`}
                >
                  {s.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>}

      <main className="mx-auto max-w-[1440px] px-4 sm:px-6 py-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h1 className={`font-semibold text-slate-800 ${showSteps ? 'text-xl' : 'text-2xl tracking-tight sm:text-3xl'}`}>{title}</h1>
          {job&&step>=2&&<RestartDocumentButton disabled={actionsDisabled}/>}
        </div>

        {job && <details className="mb-4 text-xs text-slate-500"><summary className="cursor-pointer"><span className="font-medium text-slate-600">{job.fileName}</span><span className="ml-3">작업 정보</span></summary><div className="mt-3 flex flex-wrap items-center gap-4 rounded-xl bg-white p-3"><span>작업 공간 삭제 예정: {new Date(job.expiresAt).toLocaleString('ko-KR')}</span><Link href="/context" className="text-blue-600">공유 상황 수정</Link><button disabled={busy || deleting} onClick={()=>setShowDelete(true)} className="text-red-600">작업 즉시 삭제</button></div></details>}

        {error && (
          <div
            role="alert"
            className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          >
            {error}
            <button
              className="ml-3 text-red-500 underline"
              onClick={() => setError(null)}
            >
              닫기
            </button>
          </div>
        )}

        {children}


        {showDelete && (
          <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="mb-2 text-sm font-medium text-slate-800">
              정말 삭제하시겠습니까?
            </h3>
            <p className="mb-3 text-sm text-slate-600">
              이 작업은 되돌릴 수 없습니다. 계속하려면 확인을 눌러주세요.
            </p>
            {deleteErr && (
              <p className="mb-3 text-sm text-red-600">{deleteErr}</p>
            )}
            <div className="flex items-center justify-end gap-2">
              <button
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                disabled={deleting}
                onClick={() => {
                  setShowDelete(false);
                  setDeleteErr(null);
                }}
              >
                취소
              </button>
              <button
                className="rounded-lg bg-red-500 px-3 py-1.5 text-sm text-white hover:bg-red-600 disabled:opacity-50"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? '삭제 중...' : '확인'}
              </button>
            </div>
          </div>
        )}
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-3 text-xs text-slate-400">
          <p>우리 작업 공간은 업로드 후 1시간 만료됩니다.</p>
          <p>외부 AI 제공사의 보관 조건은 별도입니다.</p>
        </div>
      </footer>
    </div>
  );
}

export function AnalysisStatus({ job }: { job: Job }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-medium text-slate-700">분석 상태</h2>
      <div className="space-y-2">
        {STAGE_KEYS.map((key) => {
          const stage = job.analysis[key];
          const status = stage?.status ?? 'unknown';
          const model = stage?.model;

          return (
            <div key={key} className="flex items-center justify-between">
              <span className="text-sm text-slate-600">{STAGE_LABELS[key]}</span>
              <span className="text-sm">
                {STATUS_LABELS[status] ?? STATUS_LABELS.unknown}
                {model && status === 'completed' && (
                  <span className="ml-2 text-xs text-slate-400">({model})</span>
                )}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-3"><RecommendationProgress job={job}/></div>
      {job.analysis.warnings.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-medium text-amber-600">경고</p>
          {job.analysis.warnings.map((w, i) => (
            <p key={i} className="text-xs text-amber-700">
              {w}
            </p>
          ))}
        </div>
      )}

      <div className="mt-3 pt-3 border-t border-slate-100">
        <p className="text-xs text-slate-500">
          Upstage에 문서 파일과 공유 상황을, Hermes·Claude(Anthropic)에 필요한 원문 근거와 공유 상황을 보냅니다.
        </p>
      </div>
    </div>
  );
}

export function LegacyJobNotice() {
  const {busy, erase, setError} = useApp();
  const router = useRouter();
  const [clearing, setClearing] = useState(false);
  const restart = async () => {
    if (busy || clearing) return;
    setClearing(true);
    try {await erase(); router.push('/');}
    catch (error) {setError(error instanceof Error ? error.message : '기존 작업을 삭제하지 못했습니다.');}
    finally {setClearing(false);}
  };
  return <ServiceShell step={1} title="Upstage 분석으로 다시 시작해 주세요">
    <section className="max-w-2xl rounded-2xl border border-blue-200 bg-white p-6">
      <p className="text-sm leading-relaxed text-slate-700">이 작업은 이전 방식으로 분석한 문서입니다. 기존 파일을 자동으로 전송하지 않습니다. 문서를 다시 올리면 Upstage 분석을 거쳐 가림 방법을 검토할 수 있습니다.</p>
      <p className="mt-3 text-xs leading-relaxed text-slate-500">아래 버튼은 현재 작업과 선택 내용을 삭제합니다. 컴퓨터에 있는 원본 파일은 그대로 유지됩니다.</p>
      <button type="button" disabled={busy || clearing} onClick={restart} className="mt-5 rounded-xl bg-blue-600 px-4 py-3 text-sm font-medium text-white disabled:opacity-40">{clearing ? '작업 정리 중…' : '기존 작업 삭제하고 새로 올리기'}</button>
    </section>
  </ServiceShell>;
}

export function EmptyJob() {
  const { restoring } = useApp();

  if (restoring) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
        <FileText className="mx-auto h-12 w-12 text-slate-300" />
        <p className="mt-3 text-sm text-slate-500">저장된 작업을 확인하고 있습니다</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
      <FileText className="mx-auto h-12 w-12 text-slate-300" />
      <p className="mt-3 text-sm text-slate-500">선택된 문서가 없습니다</p>
      <Link
        href="/"
        className="mt-3 inline-block rounded-lg bg-brand-500 px-4 py-2 text-sm text-white hover:bg-brand-600"
      >
        문서 선택
      </Link>
    </div>
  );
}
