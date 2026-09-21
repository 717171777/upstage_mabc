'use client';

import { Candidate, Method, PII_LABELS, Suggestion, METHOD_LABELS } from '@/lib/service';
import type { Mask } from '@/lib/masking';
import { presetsFor, maskedText, validPartial, partialDisclosureHint } from '@/lib/masking';
import { followingTargets } from '@/lib/repeat-decisions';
import {isUserDecision} from '@/lib/review-flow';
import { PartialEditor } from '@/components/partial-editor';
import { useState, useRef, useEffect, useCallback } from 'react';
import type {PreviewDecision} from '@/lib/review-preview';

function isNativeKeyTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return (target instanceof HTMLElement && target.isContentEditable) || !!target.closest('input, textarea, select, [contenteditable="true"], [data-native-keys="true"]');
}

export function CandidateEditor({
  candidate,
  all,
  busy,
  onSave,
  onNavigate,
  onDone,
  keyboardMode,
  onKeyboardModeChange: setKeyboardMode,
  suggestion,
  onPreviewChange,
  onPreviewStart,
}: {
  candidate: Candidate;
  all: Candidate[];
  busy: boolean;
  onSave: (decisions: { id: string; method: Method; mask: Mask; confirmed: boolean }[]) => Promise<unknown>;
  onDone: () => void;
  keyboardMode: boolean;
  onKeyboardModeChange: (enabled: boolean) => void;
  onNavigate?: (kind: 'occurrence' | 'group', reverse: boolean) => void;
  suggestion?: Suggestion;
  onPreviewChange?: (decisions: PreviewDecision[], valid: boolean) => void;
  onPreviewStart?: () => void;
}) {
  const initial = candidate;
  const [method, setMethod] = useState<Method>(initial.method);
  const [mask, setMask] = useState<Mask>(initial.mask);
  const [presetId, setPresetId] = useState<string | null>(() => initial.method === 'partial' ? presetsFor(candidate.type, candidate.value).find(p => JSON.stringify(p.mask) === JSON.stringify(initial.mask))?.id ?? null : null);
  const [scope, setScope] = useState<'following' | 'one' | 'sameValue' | 'sameType'>('following');
  const [partialOpen, setPartialOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [keyboardNotice, setKeyboardNotice] = useState('');
  const savedKey = JSON.stringify([initial.method, initial.mask, candidate.confirmed, candidate.decisionSource]);
  const [previousSavedKey, setPreviousSavedKey] = useState(savedKey);
  // External updates (including bulk actions) replace the saved baseline without resetting the user's scope.
  if (savedKey !== previousSavedKey) {
    setPreviousSavedKey(savedKey);
    setMethod(initial.method); setMask(initial.mask);
    setPresetId(initial.method === 'partial' ? presetsFor(candidate.type, candidate.value).find(p => JSON.stringify(p.mask) === JSON.stringify(initial.mask))?.id ?? null : null);
    setPartialOpen(false);
  }
  const partialSnapshot = useRef<{method: Method; mask: Mask; presetId: string | null}>({method: initial.method, mask: initial.mask, presetId});
  const previewPartial = useCallback((nextMask: Mask, nextPreset: string | null) => {
    setMask(nextMask); setPresetId(nextPreset);
  }, []);

  const rootRef = useRef<HTMLDivElement>(null);
  const savingLockRef = useRef(false);

  useEffect(() => {
    if (keyboardMode && rootRef.current) rootRef.current.focus({preventScroll:true});
  }, [keyboardMode]);

  const targets = (() => {
    let list: Candidate[] = [];
    if (scope === 'following') list = followingTargets(candidate, all);
    else if (scope === 'one') list = [candidate];
    else if (scope === 'sameValue') list = all.filter(c => c.type === candidate.type && c.value === candidate.value);
    else list = all.filter(c => c.type === candidate.type);
    const excluded = list.filter(c => !c.locationResolved).length;
    const included = list.filter(c => c.locationResolved);
    return { included, excluded };
  })();

  const presets = presetsFor(candidate.type, candidate.value);
  const maxPresets = Math.min(presets.length, 4);

  const canSave = !busy && !saving && candidate.locationResolved && targets.included.length > 0
    && (method !== 'partial' || (validPartial(candidate.value, mask) && ((() => {
      if (presetId) {
        for (const t of targets.included) {
          const ps = presetsFor(t.type, t.value);
          const p = ps.find(p => p.id === presetId);
          if (!p || !validPartial(t.value, p.mask)) return false;
        }
      } else {
        for (const t of targets.included) {
          if (t.value !== candidate.value) return false;
        }
      }
      return true;
    })())));

  const invalidReason = (() => {
    if (!candidate.locationResolved) return '먼저 이 정보가 있는 원문 위치를 연결해 주세요.';
    if (method === 'partial' && presetId) {
      for (const t of targets.included) {
        const ps = presetsFor(t.type, t.value);
        const p = ps.find(p => p.id === presetId);
        if (!p) return `“${t.value}”에는 이 방법을 쓸 수 없어요. 적용 위치를 줄이거나 다른 방법을 골라 주세요.`;
        if (!validPartial(t.value, p.mask)) return `“${t.value}”에 맞는 가림 방법을 다시 골라 주세요.`;
      }
    }
    if (method === 'partial' && !presetId && !validPartial(candidate.value, mask)) return '가릴 방법이나 글자를 선택해 주세요.';
    if (method === 'partial' && !presetId) {
      for (const t of targets.included) {
        if (t.value !== candidate.value) return `직접 고른 글자는 같은 내용에만 적용할 수 있어요. 적용 위치를 바꿔 주세요.`;
      }
    }
    return null;
  })();

  const decisions = targets.included.map(t => {
    const targetMask = method !== 'partial' ? [] : presetId
      ? presetsFor(t.type, t.value).find(p => p.id === presetId)?.mask ?? []
      : t.value === candidate.value ? mask : [];
    return { id: t.id, method, mask: targetMask as Mask, confirmed: true };
  });
  const hasUnconfirmedChanges = decisions.some((decision, index) => {
    const saved = targets.included[index];
    return saved.method !== decision.method || JSON.stringify(saved.mask) !== JSON.stringify(decision.mask);
  });
  const previewJSON = JSON.stringify(candidate.locationResolved ? decisions.filter((decision, index) => {
    if (method !== 'partial') return true;
    const target = targets.included[index];
    return presetId ? validPartial(target.value, decision.mask) : target.value === candidate.value;
  }) : []);
  const previewValid = candidate.locationResolved && !invalidReason && targets.included.length > 0;
  useEffect(() => {
    onPreviewChange?.(JSON.parse(previewJSON) as PreviewDecision[], previewValid);
  }, [previewJSON, previewValid, onPreviewChange]);

  const saveDecisions = async (): Promise<boolean> => {
    if (!canSave || savingLockRef.current) return false;
    if (!hasUnconfirmedChanges) {
      setKeyboardNotice('현재 설정을 그대로 사용합니다.');
      return true;
    }
    savingLockRef.current = true;
    setSaving(true);
    setError(null);
    setKeyboardNotice('');
    try {
      await onSave(decisions);
      setKeyboardNotice('변경을 적용했습니다. Tab으로 다음 위치를 볼 수 있어요.');
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : '저장 중 오류가 발생했습니다.');
      return false;
    } finally {
      setSaving(false);
      savingLockRef.current = false;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!keyboardMode) return;
    if (isNativeKeyTarget(e.target)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (window.getSelection()?.isCollapsed === false) return;
    if (Array.from(document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]')).some(el => el.getClientRects().length > 0)) return;

    const shortcut = ['Enter', 'Tab', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Escape'].includes(e.key) || /^[0-5]$/.test(e.key);
    if (!shortcut) return;
    e.preventDefault();
    if (e.repeat || busy || saving || savingLockRef.current) return;

    if (e.key === 'Escape') {
      e.stopPropagation();
      setKeyboardMode(false);
      if (rootRef.current) rootRef.current.blur();
      return;
    }

    if (e.key === 'Tab' || e.key === 'ArrowDown') {
      if (!canSave) {
        setKeyboardNotice('일부 가림 범위를 완성하거나 다른 방법을 선택해 주세요.');
        return;
      }
      onNavigate?.(e.key === 'Tab' ? 'group' : 'occurrence', e.shiftKey);
      return;
    }

    if (e.key === 'Enter') {
      void saveDecisions();
      return;
    }

    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || /^[0-5]$/.test(e.key)) {
      // Numeric options are drafts: arrows never confirm or move to another item.
      const partialIndex = presets.findIndex(p => JSON.stringify(p.mask) === JSON.stringify(mask));
      const current = method === 'keep' ? 0 : method === 'full' ? 1
        : method === 'partial' && partialIndex >= 0 ? partialIndex + 2 : 1;
      const num = e.key === 'ArrowLeft' ? Math.max(0, current - 1)
        : e.key === 'ArrowRight' ? Math.min(1 + maxPresets, current + 1) : parseInt(e.key, 10);
      setKeyboardNotice('');
      if (num === 0 || num === 1) {
        onPreviewStart?.();
        setMethod(num === 0 ? 'keep' : 'full');
        setMask([]);
        setPresetId(null);
        setPartialOpen(false);
      } else if (num >= 2 && num <= 1 + maxPresets) {
        const idx = num - 2;
        const p = presets[idx];
        if (p) {
          onPreviewStart?.();
          setMethod('partial');
          setPresetId(p.id);
          setMask(p.mask);
          setPartialOpen(false);
        }
      }
    }
  };

  const answerQuestion = async (choice: 'full' | 'keep') => {
    if (busy || savingLockRef.current || !candidate.locationResolved) return;
    savingLockRef.current = true;
    setSaving(true); setError(null);
    try {
      // Reuse the user's answer only for this exact type/value, respecting explicit overrides.
      const peers = scope === 'one' ? [candidate] : followingTargets(candidate, all);
      await onSave(peers.filter(c => c.locationResolved).map(c => ({ id: c.id, method: choice, mask: [], confirmed: true })));
      setMethod(choice); setMask([]); setPresetId(null); setPartialOpen(false); onPreviewStart?.();
      setKeyboardNotice('답변을 저장했습니다. 이미 직접 확정한 다른 위치의 선택은 보존됩니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '답변을 저장하지 못했습니다. 다시 선택해 주세요.');
    } finally { savingLockRef.current = false; setSaving(false); }
  };

  const disabledAll = busy || saving;
  const chooseMethod = (choice: Method) => {
    onPreviewStart?.(); setError(null); setPartialOpen(false);
    if (choice === 'partial') {
      if (method !== 'partial' || !validPartial(candidate.value, mask)) {
        const preset = presets[0];
        setMask(preset?.mask ?? []); setPresetId(preset?.id ?? null);
        setPartialOpen(!preset);
        partialSnapshot.current = {method, mask, presetId};
      }
    } else { setMask([]); setPresetId(null); }
    setMethod(choice);
  };
  const sample = method === 'delete' ? '이 내용이 삭제돼요' : maskedText(candidate.value, method, mask);
  const options = [
    {method:'full' as const, label:'모두 가리기', hint:'내용을 알아볼 수 없게 가려요'},
    {method:'partial' as const, label:'일부만 가리기', hint:'필요한 글자만 남겨요'},
    {method:'keep' as const, label:'그대로 두기', hint:'원문을 그대로 보여줘요'},
    {method:'delete' as const, label:'내용 삭제하기', hint:'가림 표시 없이 이 내용을 없애요'},
  ];

  return <div ref={rootRef} tabIndex={0} onKeyDown={handleKeyDown}
    className="rounded-xl border border-slate-200 bg-white p-4 space-y-4 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
    aria-label="개인정보 편집" aria-keyshortcuts={keyboardMode ? 'Enter ArrowLeft ArrowRight ArrowDown Tab 0 1 2 3 4 5 Escape' : undefined}>
    <header>
      <p className="text-xs text-slate-500">{PII_LABELS[candidate.type]} 가림 설정</p>
      <h2 className="mt-1 text-base font-medium text-slate-900 break-all">{candidate.value}</h2>
      <p className="mt-1 text-sm text-slate-600">어떻게 보여줄까요?</p>
    </header>

    <div className="space-y-2" role="group" aria-label="가림 방법">
      {options.map(option=><button key={option.method} type="button" disabled={disabledAll}
        aria-pressed={method===option.method} onClick={()=>chooseMethod(option.method)}
        className={`flex min-h-14 w-full items-center gap-3 rounded-lg border px-3 py-2 text-left disabled:opacity-50 ${method===option.method?'border-blue-600 bg-blue-50':'border-slate-200 hover:bg-slate-50'}`}>
        <span aria-hidden="true" className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-xs ${method===option.method?'border-blue-600 bg-blue-600 text-white':'border-slate-300 text-transparent'}`}>✓</span>
        <span><span className="block text-sm font-semibold text-slate-900">{option.label}</span><span className="block text-xs text-slate-500">{option.hint}</span></span>
      </button>)}
    </div>

    {method==='partial'&&<section aria-label="일부 가림 방법" className="space-y-3 border-l-2 border-blue-100 pl-3">
      {presets.length>0&&<div className="flex flex-wrap gap-2">{presets.slice(0,4).map(p=><button key={p.id} type="button" disabled={disabledAll}
        aria-pressed={presetId===p.id} onClick={()=>{setMask(p.mask);setPresetId(p.id);setPartialOpen(false);onPreviewStart?.();}}
        className={`min-w-0 flex-1 rounded-lg border p-2 text-left disabled:opacity-50 ${presetId===p.id?'border-blue-500 bg-blue-50':'border-slate-200 bg-white'}`}>
        <span className="block text-xs text-slate-600">{p.label.split(' · ')[0]}</span>
        <span className="mt-1 block break-all text-sm font-semibold text-slate-900">{maskedText(candidate.value,'partial',p.mask)}</span>
      </button>)}</div>}
      {!partialOpen&&<button type="button" disabled={disabledAll} className="min-h-9 text-xs font-medium text-blue-700" onClick={()=>{
        partialSnapshot.current={method,mask,presetId};setPartialOpen(true);onPreviewStart?.();
      }}>가릴 글자 직접 고르기</button>}
      {partialOpen&&<div data-native-keys="true"><PartialEditor candidate={{...candidate,mask}} disabled={disabledAll} onPreview={previewPartial}
        onApply={(m,pid)=>{setMask(m);setPresetId(pid);setPartialOpen(false);}}
        onCancel={()=>{const prior=partialSnapshot.current;setMethod(prior.method);setMask(prior.mask);setPresetId(prior.presetId);setPartialOpen(false);}}/></div>}
    </section>}

    <section aria-label="선택한 가림 결과" aria-live="polite" className="rounded-lg bg-slate-50 px-3 py-3">
      <p className="text-xs text-slate-500">이렇게 보여요</p>
      <p className="mt-1 break-all text-lg font-semibold text-slate-900">{sample}</p>
      {method==='partial'&&<p className="mt-2 text-xs leading-relaxed text-slate-500">{partialDisclosureHint(candidate.type)}</p>}
    </section>

    {invalidReason&&<p role="status" className="text-xs text-amber-700">{invalidReason}</p>}
    {error&&<p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="sticky bottom-0 z-10 space-y-2 border-t border-white bg-white py-2">
      <button type="button" data-native-keys="true" onClick={onDone} disabled={disabledAll||!previewValid}
        className="min-h-11 w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40">{disabledAll?'저장 중…':'완료 · 목록으로'}</button>
      <p className="text-center text-xs text-slate-500">{targets.included.length}곳에 적용 · 목록으로 돌아가도 저장돼요</p>
    </div>

    <details className="border-t border-slate-100 pt-3">
      <summary className="cursor-pointer text-xs text-slate-600">적용 위치 바꾸기 · {targets.included.length}곳</summary>
      <div className="mt-3 space-y-3" data-native-keys="true">
        {(['following','one','sameValue','sameType'] as const).map(key=><label key={key} className="flex items-start gap-2 text-xs leading-relaxed text-slate-700">
          <input type="radio" name="scope" className="mt-0.5" checked={scope===key} disabled={disabledAll} onChange={()=>{setScope(key);onPreviewStart?.();}}/>
          {key==='following'?'같은 정보에 함께 적용 (기본)':key==='one'?'이 위치만':key==='sameValue'?'같은 정보의 모든 위치':`모든 ${PII_LABELS[candidate.type]}에 적용`}
        </label>)}
        <p className="text-xs text-slate-500">{scope==='following'?'다른 위치에서 직접 정한 설정은 바꾸지 않아요.':scope==='sameValue'||scope==='sameType'?'직접 정한 설정도 선택한 방법으로 바뀝니다.':'선택한 한 곳만 바꿔요.'}</p>
        {targets.excluded>0&&<p className="text-xs text-amber-700">위치가 연결되지 않은 {targets.excluded}곳은 제외했어요.</p>}
        {scope==='sameType'&&<ul className="max-h-32 space-y-1 overflow-auto text-xs text-slate-600">{decisions.map(d=>{
          const target=all.find(c=>c.id===d.id)!;
          return <li key={d.id} className="break-all">{target.value} → {d.method==='delete'?'삭제':maskedText(target.value,d.method,d.mask)}</li>;
        })}</ul>}
      </div>
    </details>

    {suggestion&&<details className="border-t border-slate-100 pt-3">
      <summary className="cursor-pointer text-xs text-slate-600">{suggestion.question?'AI의 추가 질문 · 선택 사항':'AI 판단 살펴보기'}</summary>
      <section aria-label="AI 확인 질문" className="mt-3 space-y-3 rounded-lg bg-blue-50 p-3 text-xs leading-relaxed text-slate-700">
        <p>{suggestion.question||suggestion.reason||suggestion.content}</p>
        {suggestion.evidence&&<blockquote className="border-l-2 border-blue-200 pl-2 text-slate-500">{suggestion.evidence}</blockquote>}
        {suggestion.question&&!isUserDecision(candidate)?<>
          <p>답변은 선택 사항이에요. 현재 선택한 가림 방법으로도 진행할 수 있어요.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" data-native-keys="true" disabled={disabledAll||!candidate.locationResolved} onClick={()=>void answerQuestion('full')} className="min-h-10 rounded border border-blue-300 bg-white px-3 disabled:opacity-40">아니요, 모두 가리기</button>
            <button type="button" data-native-keys="true" disabled={disabledAll||!candidate.locationResolved} onClick={()=>void answerQuestion('keep')} className="min-h-10 rounded border border-blue-300 bg-white px-3 disabled:opacity-40">네, 그대로 두기</button>
          </div>
        </>:<p>{isUserDecision(candidate)?'직접 설정':'AI 제안'} · {METHOD_LABELS[isUserDecision(candidate)?candidate.method:suggestion.recommendation??candidate.method]}</p>}
      </section>
    </details>}

    <details className="border-t border-slate-100 pt-3">
      <summary className="cursor-pointer text-xs text-slate-600">추가 기능</summary>
      <div className="mt-3 space-y-4" data-native-keys="true">
        <label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={keyboardMode} onChange={e=>setKeyboardMode(e.target.checked)}/>키보드 단축키 사용</label>
        {keyboardMode&&<div className="space-y-1 text-xs text-slate-500"><p>0 그대로 두기 · 1 모두 가리기{maxPresets>0&&<> · 2{maxPresets>1?`–${1+maxPresets}`:''} 일부 가림</>}</p><p>← → 방법 선택 · Enter 변경 저장</p><p>Tab 다음 위치 · ↓ 같은 종류 · Shift와 함께 누르면 이전</p><p>Esc 단축키 끄기</p><button type="button" onClick={()=>rootRef.current?.focus({preventScroll:true})} className="min-h-9 font-medium text-blue-700">단축키로 편집하기</button></div>}
      </div>
    </details>
    {keyboardNotice&&<p aria-live="polite" className="text-xs text-slate-500">{keyboardNotice}</p>}
  </div>;
}
