'use client';

import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type { Context } from '@/lib/service';

type Props = {
  value: Context;
  onChange: (value: Context) => void;
  onStart: (value: Context) => Promise<void>;
  disabled?: boolean;
  submitting?: boolean;
};

const RECIPIENTS = [
  { value: '내부동료', label: '내부 동료', detail: '같은 조직 안에서 공유' },
  { value: '외부협력기관', label: '외부 협력 기관', detail: '함께 일하는 외부 조직' },
  { value: '공공기관', label: '공공기관', detail: '제출·신고 등의 업무' },
  { value: '불특정다수', label: '누구나 볼 수 있도록 공개', detail: '웹 게시·공개 배포' },
  { value: '', label: '아직 정하지 않았어요', detail: '문서의 근거를 중심으로 판단' },
];
const TITLES = ['누구에게 공유하나요?', '어떤 목적으로 보내나요?', '꼭 보여야 하는 정보가 있나요?'];
const HELP = [
  '공유 대상을 알면 필요한 정보와 가릴 정보를 판단하는 데 도움이 됩니다.',
  '문서를 받는 사람이 무엇을 확인해야 하는지 알려주세요.',
  '가리면 업무에 지장이 생기는 항목이 있다면 알려주세요.',
];
const control = 'disabled:cursor-not-allowed disabled:opacity-50';

export function UploadContextQuestions({ value, onChange, onStart, disabled = false, submitting = false }: Props) {
  const [step, setStep] = useState(0);
  const [starting, setStarting] = useState(false);
  const latest = useRef(value);
  const startLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const answer = useRef<HTMLTextAreaElement>(null);
  // This panel mounts after the user chooses a file; move focus to the new task.
  const previousStep = useRef<number | null>(null);
  const id = useId();
  const unavailable = disabled || submitting || starting;
  const sending = submitting || starting;

  useEffect(() => { latest.current = value; }, [value]);
  useEffect(() => {
    if (previousStep.current !== step) {
      previousStep.current = step;
      if (step > 0 && answer.current) {
        answer.current.focus();
        const end = answer.current.value.length;
        answer.current.setSelectionRange(end, end);
      } else heading.current?.focus();
    }
  }, [step]);

  function change(next: Context) {
    if (disabled || submitting || startLock.current) return;
    latest.current = next;
    onChange(next);
  }

  function move(next: number) {
    if (disabled || submitting || startLock.current) return;
    setStep(Math.max(0, Math.min(2, next)));
  }

  async function start(next: Context) {
    if (disabled || submitting || startLock.current) return;
    startLock.current = true;
    setStarting(true);
    try {
      // Pass the current answers directly; do not wait for the parent's state update.
      await onStart({ ...next });
    } catch {
      // The parent displays the error. Keep the question and answers for retry.
    } finally {
      startLock.current = false;
      setStarting(false);
    }
  }

  function answerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (event.repeat || unavailable || startLock.current) return;
    if (step < 2) move(step + 1);
    else void start(latest.current);
  }

  function skipCurrent() {
    if (disabled || submitting || startLock.current) return;
    const next = { ...latest.current };
    if (step === 0) next.recipient = null;
    else if (step === 1) next.purpose = '';
    else next.keepInfo = null;
    change(next);
    if (step < 2) move(step + 1);
    else void start(next);
  }

  return <section aria-labelledby={`${id}-heading`} aria-busy={sending} className="flex min-w-0 flex-col">
    <div className="mb-6 flex items-center justify-between gap-3">
      <p className="text-sm font-semibold text-blue-700">공유 상황</p>
      <p className="text-xs text-slate-500"><span className="font-semibold text-blue-700">{step + 1} / 3</span><span className="ml-3">모두 선택 사항</span></p>
    </div>

    <h2 ref={heading} id={`${id}-heading`} tabIndex={-1} className="text-xl font-semibold leading-snug text-slate-900">{TITLES[step]}</h2>
    <p id={`${id}-help`} className="mb-5 mt-3 text-sm leading-relaxed text-slate-600">{HELP[step]}</p>

    <div className="min-w-0 flex-1">
      {step === 0 ? <fieldset disabled={unavailable} aria-describedby={`${id}-help`}>
        <legend className="sr-only">공유 대상</legend>
        <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
          {RECIPIENTS.map(option => {
            const selected = (value.recipient ?? '') === option.value;
            return <label key={option.value} className={`flex min-w-0 items-start gap-3 border p-3.5 ${unavailable ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'} ${selected ? 'border-blue-600 bg-blue-50' : 'border-slate-300 bg-white hover:bg-slate-50'}`}>
              <input type="radio" name={`${id}-recipient`} value={option.value} checked={selected}
                onChange={() => change({ ...latest.current, recipient: option.value || null })}
                className="peer sr-only" />
              <span aria-hidden="true" className={`mt-1 flex h-4 w-4 shrink-0 items-center justify-center border text-[11px] font-bold peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-blue-600 ${selected ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-300 bg-white'}`}>{selected ? '✓' : ''}</span>
              <span className="min-w-0"><span className="block text-sm font-medium leading-relaxed text-slate-800">{option.label}</span><span className="mt-1 block text-xs leading-relaxed text-slate-500">{option.detail}</span></span>
            </label>;
          })}
        </div>
      </fieldset> : <div>
        <label htmlFor={`${id}-answer`} className="sr-only">{TITLES[step]}</label>
        <textarea ref={answer} id={`${id}-answer`} aria-describedby={`${id}-help ${id}-optional`} disabled={unavailable} maxLength={2000} rows={5}
          onKeyDown={answerKeyDown}
          value={step === 1 ? value.purpose : value.keepInfo ?? ''}
          onChange={event => change(step === 1 ? { ...latest.current, purpose: event.target.value } : { ...latest.current, keepInfo: event.target.value || null })}
          placeholder={step === 1 ? '예: 외부 협력사에 출장 결과와 지출 내역을 공유해요.' : '예: 기관의 공용 문의 이메일은 보이게 해 주세요.'}
          className={`block w-full min-w-0 resize-y border border-slate-300 bg-white px-3 py-3 text-sm leading-relaxed text-slate-800 focus-visible:ring-2 focus-visible:ring-blue-500 ${control}`} />
        <div className="mt-2 flex flex-wrap items-start justify-between gap-2 text-xs text-slate-500">
          <p id={`${id}-optional`} className="max-w-full leading-relaxed">비워두어도 됩니다. 최대 2,000자까지 입력할 수 있어요. Enter로 {step < 2 ? '다음 질문' : '분석 시작'} · Shift+Enter로 줄바꿈</p>
          <span aria-hidden="true" className="ml-auto shrink-0">{(step === 1 ? value.purpose : value.keepInfo ?? '').length.toLocaleString()} / 2,000</span>
        </div>
      </div>}
    </div>

    <div className="mt-7 border-t border-slate-200 pt-5">
      <p className="mb-4 text-xs leading-relaxed text-slate-500">분석 시작 시 문서·공유 상황은 Upstage로, 필요한 원문 근거·상황은 Hermes·SP4로 전송됩니다.</p>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={unavailable || step === 0} onClick={() => move(step - 1)} className={`min-h-11 border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 ${control}`}>이전</button>
        <button type="button" disabled={unavailable} onClick={() => step < 2 ? move(step + 1) : void start(latest.current)}
          className={`ml-auto min-h-11 bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 ${control}`}>{sending ? '분석 시작 중…' : step < 2 ? '다음' : '문서 분석 시작'}</button>
      </div>
      <div className="mt-4 flex flex-col items-start gap-3 text-sm">
        <button type="button" disabled={unavailable} onClick={skipCurrent} className={`min-h-9 text-left text-slate-600 underline decoration-slate-300 underline-offset-4 hover:text-slate-800 ${control}`}>{step === 2 ? '이 질문 건너뛰고 분석 시작' : '이 질문 건너뛰기'}</button>
        {step < 2 && <button type="button" disabled={unavailable} onClick={() => void start(latest.current)} className={`min-h-9 text-left text-blue-600 underline underline-offset-4 hover:text-blue-700 ${control}`}>남은 질문 건너뛰고 분석 시작</button>}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-500">현재 질문을 건너뛰면 해당 답변만 비웁니다. 남은 질문을 건너뛰어도 이미 입력한 내용은 유지합니다.</p>
    </div>
  </section>;
}
