'use client';

import { Candidate, Method, PII_LABELS, Suggestion, METHOD_LABELS } from '@/lib/service';
import type { Mask } from '@/lib/masking';
import { presetsFor, maskedText, validPartial, partialDisclosureHint } from '@/lib/masking';
import { followingTargets, previousChoice } from '@/lib/repeat-decisions';
import { PartialEditor } from '@/components/partial-editor';
import { useState, useRef, useEffect } from 'react';

function isNativeKeyTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return (target instanceof HTMLElement && target.isContentEditable) || !!target.closest('input, textarea, select, [contenteditable="true"], [data-native-keys="true"]');
}

export function CandidateEditor({
  candidate,
  all,
  busy,
  onSave,
  onClose,
  onNavigate,
  suggestion,
}: {
  candidate: Candidate;
  all: Candidate[];
  busy: boolean;
  onSave: (decisions: { id: string; method: Method; mask: Mask; confirmed: boolean }[]) => Promise<unknown>;
  onClose: () => void;
  onNavigate?: (kind: 'occurrence' | 'group', reverse: boolean) => void;
  suggestion?: Suggestion;
}) {
  const inherited = previousChoice(candidate, all);
  const initial = inherited ?? candidate;
  const [method, setMethod] = useState<Method>(initial.method);
  const [mask, setMask] = useState<Mask>(initial.mask);
  const [presetId, setPresetId] = useState<string | null>(() => initial.method === 'partial' ? presetsFor(candidate.type, candidate.value).find(p => JSON.stringify(p.mask) === JSON.stringify(initial.mask))?.id ?? null : null);
  const [scope, setScope] = useState<'following' | 'one' | 'sameValue' | 'sameType'>('following');
  const [partialOpen, setPartialOpen] = useState(initial.method === 'partial' && !inherited);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [keyboardMode, setKeyboardMode] = useState(true);
  const [keyboardNotice, setKeyboardNotice] = useState('');

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
    if (!candidate.locationResolved) return '선택한 후보의 위치가 확인되지 않아 저장할 수 없습니다.';
    if (method === 'partial' && presetId) {
      for (const t of targets.included) {
        const ps = presetsFor(t.type, t.value);
        const p = ps.find(p => p.id === presetId);
        if (!p) return `대상 값 "${t.value}"(타입: ${t.type})에 프리셋 "${presetId}"가 없습니다.`;
        if (!validPartial(t.value, p.mask)) return `대상 값 "${t.value}"에 프리셋 마스크가 유효하지 않습니다.`;
      }
    }
    if (method === 'partial' && !presetId && !validPartial(candidate.value, mask)) return '프리셋을 선택하거나 일부 글자를 가려 주세요';
    if (method === 'partial' && !presetId) {
      for (const t of targets.included) {
        if (t.value !== candidate.value) return `값이 다른 대상 "${t.value}"에는 선택한 마스크 범위를 복사할 수 없습니다. 프리셋을 선택하거나 같은 값의 모든 위치으로 맞춰주세요.`;
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
    return !saved.confirmed || saved.method !== decision.method || JSON.stringify(saved.mask) !== JSON.stringify(decision.mask);
  });

  const saveDecisions = async (): Promise<boolean> => {
    if (!canSave || savingLockRef.current) return false;
    if (!hasUnconfirmedChanges) {
      setKeyboardNotice('이미 확정한 선택입니다. ↓ 또는 Tab으로 이동하세요.');
      return true;
    }
    savingLockRef.current = true;
    setSaving(true);
    setError(null);
    setKeyboardNotice('');
    try {
      await onSave(decisions);
      setKeyboardNotice('선택을 확정했습니다. Tab으로 같은 값의 반복 위치부터 차례로 확인하세요.');
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
      if (hasUnconfirmedChanges || !canSave) {
        setKeyboardNotice('먼저 Enter로 현재 선택을 확정해 주세요. 선택은 그대로 유지됩니다.');
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
        setMethod(num === 0 ? 'keep' : 'full');
        setMask([]);
        setPresetId(null);
        setPartialOpen(false);
      } else if (num >= 2 && num <= 1 + maxPresets) {
        const idx = num - 2;
        const p = presets[idx];
        if (p) {
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
      setMethod(choice); setMask([]); setPresetId(null); setPartialOpen(false);
      setKeyboardNotice('답변을 저장했습니다. 이미 직접 확정한 다른 위치의 선택은 보존됩니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '답변을 저장하지 못했습니다. 다시 선택해 주세요.');
    } finally { savingLockRef.current = false; setSaving(false); }
  };

  const handleSave = async () => {
    if (await saveDecisions()) rootRef.current?.focus({preventScroll:true});
  };

  const renderMaskPreview = (value: string, m: Mask, methodName: Method) => {
    const codePoints: string[] = [];
    for (const ch of value) codePoints.push(ch);
    const spans: React.ReactNode[] = [];
    for (let i = 0; i < codePoints.length; i++) {
      const masked = methodName === 'full' || methodName === 'delete' || (methodName === 'partial' && m.some(r => i >= r[0] && i < r[1]));
      if (masked) {
        spans.push(<span key={i} className="inline-block" style={{ display: 'inline-block', backgroundColor: 'var(--ui-mask-edit-sample)', color: 'var(--ui-mask-solid)' }}>{codePoints[i]}</span>);
      } else {
        spans.push(<span key={i} className="text-slate-800">{codePoints[i]}</span>);
      }
    }
    return (
      <div className="space-y-1">
        <div className="text-[10px] text-slate-400">편집 중 · 원문 확인</div><div className="text-sm leading-relaxed">{spans}</div>
        <div className="text-xs text-slate-600 font-mono break-all">저장 결과 · {methodName === 'delete' ? '(값 삭제)' : maskedText(value, methodName, m)}</div>
      </div>
    );
  };

  const grouped = (() => {
    const map = new Map<string, Candidate[]>();
    for (const t of targets.included) {
      const arr = map.get(t.value) || [];
      arr.push(t);
      map.set(t.value, arr);
    }
    return map;
  })();

  const disabledAll = busy || saving;

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="bg-white rounded-lg border border-slate-200 p-4 space-y-4 outline-none focus:ring-2 focus:ring-blue-500/40"
      aria-label="개인정보 편집"
      aria-keyshortcuts={keyboardMode ? 'Enter ArrowLeft ArrowRight ArrowDown Tab 0 1 2 3 4 5 Escape' : undefined}
    >
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-sm font-semibold text-slate-800 capitalize">{PII_LABELS[candidate.type]}</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            data-native-keys="true"
            onClick={() => { setKeyboardMode(true); if (rootRef.current) rootRef.current.focus({preventScroll:true}); }}
            className={`text-xs px-2 py-1 rounded border ${keyboardMode ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-white text-slate-500 border-slate-300 hover:bg-slate-50'}`}
          >
            {keyboardMode ? '키보드 모드 켜짐' : '키보드 모드 켜기'}
          </button>
          <button data-native-keys="true" onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">닫기</button>
        </div>
      </div>

      <div className="text-base font-medium text-slate-900 break-all">{candidate.value}</div>

      {suggestion && <div className="border border-blue-100 bg-blue-50/60 p-3 text-sm">
        {suggestion.question ? <section aria-label="AI 확인 질문">
          <p className="text-xs font-semibold text-blue-800">문서 근거와 공유 상황을 보고 확인이 필요해요</p>
          <p className="mt-2 font-medium leading-relaxed text-slate-900">{suggestion.question}</p>
          {suggestion.evidence && <blockquote className="mt-3 border-l-2 border-blue-300 pl-2 text-xs leading-relaxed text-slate-600"><span className="block font-medium">문서 근거</span>{suggestion.evidence}</blockquote>}
          {candidate.confirmed ? <p role="status" className="mt-3 text-xs font-medium text-blue-800">선택 확정됨 · {METHOD_LABELS[candidate.method]} · 아래에서 다시 수정할 수 있어요.</p> : <>
            <p className="mt-3 text-xs text-slate-600">답하지 않으면 전체 가림합니다. {scope === 'one' ? '답변은 이 위치에만 적용됩니다.' : '같은 값의 미확정 위치에도 답변을 적용합니다. 이미 직접 확정한 위치는 보존합니다.'}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" data-native-keys="true" disabled={disabledAll || !candidate.locationResolved} onClick={() => void answerQuestion('full')} className="min-h-11 border border-blue-600 bg-blue-600 px-3 text-xs font-medium text-white disabled:opacity-40">아니요, 전체 가림</button>
              <button type="button" data-native-keys="true" disabled={disabledAll || !candidate.locationResolved} onClick={() => void answerQuestion('keep')} className="min-h-11 border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 disabled:opacity-40">네, 그대로 유지</button>
            </div>
          </>}
        </section> : <>
          <div className="flex items-start justify-between gap-3"><span className="font-medium text-blue-800">{suggestion.recommendation ? `${suggestion.source === 'local_rules' ? '이전 규칙 제안' : 'AI 판단'} · ${METHOD_LABELS[suggestion.recommendation]}` : 'AI 검토'}</span>
            {suggestion.type === 'recommendation' && (suggestion.recommendation === 'full' || suggestion.recommendation === 'keep') && <button type="button" data-native-keys="true" disabled={disabledAll || !candidate.locationResolved} className="shrink-0 border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 disabled:opacity-40" onClick={() => {setMethod(suggestion.recommendation!); setMask([]); setPresetId(null); setPartialOpen(false);}}>판단 적용</button>}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">{suggestion.reason || suggestion.content}</p>
          {suggestion.recommendation === 'partial' && <p className="mt-2 text-xs text-amber-800">이전 방식의 일부 가림 추천입니다. 가림 방법은 아래에서 직접 선택해 주세요. 바로 받기에서는 미확정 항목을 전체 가림합니다.</p>}
          {suggestion.evidence && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">판단에 사용한 원문</summary><blockquote className="mt-2 border-l-2 border-blue-200 pl-2">{suggestion.evidence}</blockquote></details>}
        </>}
      </div>}

      <div className="flex flex-wrap gap-1.5">
        <button
          key="1"
          onClick={() => { setMethod('full'); setPresetId(null); setPartialOpen(false); }}
          disabled={disabledAll}
          className={`max-w-full min-w-0 px-3 py-1.5 text-sm rounded-md border flex items-center gap-1.5 ${
            method === 'full' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
          } ${disabledAll ? 'opacity-50 cursor-not-allowed' : ''}`}
          aria-pressed={method === 'full' && presetId === null}
        >
          <span className="inline-flex w-5 h-5 justify-center bg-slate-100 text-slate-700 rounded text-xs font-mono font-bold">1</span>
          전체 가림
        </button>
        {presets.slice(0, 4).map((p, idx) => {
          const num = idx + 2;
          const preview = maskedText(candidate.value, 'partial', p.mask);
          return (
            <button
              key={p.id}
              onClick={() => { setMethod('partial'); setPresetId(p.id); setMask(p.mask); setPartialOpen(false); }}
              disabled={disabledAll}
              className={`max-w-full min-w-0 px-3 py-1.5 text-sm rounded-md border flex items-center gap-1.5 ${
                presetId === p.id && method === 'partial' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
              } ${disabledAll ? 'opacity-50 cursor-not-allowed' : ''}`}
              aria-pressed={presetId === p.id && method === 'partial'}
            >
              <span className="inline-flex w-5 h-5 justify-center bg-slate-100 text-slate-700 rounded text-xs font-mono font-bold">{num}</span>
              <span className="min-w-0 text-left break-all"><span className="block text-xs opacity-80">{p.label}</span><span className="block font-mono text-sm">{preview}</span></span>
            </button>
          );
        })}
      </div>

      <p className="text-xs leading-relaxed text-slate-500">{partialDisclosureHint(candidate.type)}{presets.length === 0 && ' 자동 일부 가림 대신 직접 범위를 선택할 수 있습니다.'}</p>

      {scope === 'following' && <p className="text-xs leading-relaxed text-blue-700">확정하면 같은 종류·같은 값의 미확정 위치에도 적용합니다. 직접 확정한 다른 위치는 보존합니다.</p>}
      {inherited && !candidate.confirmed && <p className="text-xs leading-relaxed text-slate-600">이전에 같은 정보에 확정한 방법을 불러왔습니다. Enter로 적용하세요.</p>}

      <details className="group">
        <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-700 select-none">적용 범위</summary>
        <div className="flex flex-wrap gap-4 pt-2 border-t border-slate-100" data-native-keys="true">
          {(['following', 'one', 'sameValue', 'sameType'] as const).map(key => (
            <label key={key} className="flex items-center gap-1.5 text-sm text-slate-700">
              <input type="radio" name="scope" value={key} checked={scope === key} onChange={() => setScope(key)} className="text-blue-600" disabled={disabledAll} />
              {key === 'following' ? '같은 값의 미확정 위치 (기본)' : key === 'one' ? '이 위치만' : key === 'sameValue' ? '같은 값의 모든 위치' : '같은 개인정보 종류'}
            </label>
          ))}
        </div>
      </details>

      <div className="text-xs text-slate-500">
        영향받을 위치: {targets.included.length}개
        {targets.excluded > 0 && <span className="text-slate-400"> (위치 미확인 제외: {targets.excluded})</span>}
      </div>

      {targets.included.length > 0 ? (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {Array.from(grouped.entries()).map(([value, cts]) => {
            const t = cts[0];
            const tMask: Mask = (() => {
              if (method === 'partial') {
                if (presetId) {
                  const ps = presetsFor(t.type, t.value);
                  const p = ps.find(p => p.id === presetId);
                  return p ? p.mask : [];
                }
                return t.value === candidate.value ? mask : [];
              }
              return [];
            })();
            return (
              <div key={value} className="text-xs bg-slate-50 rounded px-2 py-1">
                <div className="text-slate-400">{cts.length}곳에 적용</div>
                {renderMaskPreview(value, tMask, method)}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-slate-400">영향받을 위치가 없습니다.</p>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => { setMethod('delete'); setPresetId(null); setPartialOpen(false); }}
          disabled={disabledAll}
          className={`px-3 py-1.5 text-sm rounded-md border ${
            method === 'delete' ? 'bg-slate-600 text-white border-slate-600' : 'bg-white text-slate-500 border-slate-300 hover:bg-slate-50'
          } ${disabledAll ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          삭제
        </button>
        <button
          onClick={() => { setMethod('keep'); setPresetId(null); setPartialOpen(false); }}
          disabled={disabledAll}
          aria-pressed={method === 'keep'}
          className={`max-w-full min-w-0 px-3 py-1.5 text-sm rounded-md border flex items-center gap-1.5 ${
            method === 'keep' ? 'bg-slate-600 text-white border-slate-600' : 'bg-white text-slate-500 border-slate-300 hover:bg-slate-50'
          } ${disabledAll ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          <span className="inline-flex w-5 h-5 justify-center bg-slate-100 text-slate-700 rounded text-xs font-mono font-bold">0</span>
          유지
        </button>
      </div>

      {method === 'partial' && partialOpen && (
        <div data-native-keys="true">
          <PartialEditor
            candidate={{ ...candidate, mask }}
            disabled={busy || saving}
            onApply={(m, pid) => { setMask(m); setPresetId(pid); setPartialOpen(false); }}
            onCancel={() => setPartialOpen(false)}
          />
        </div>
      )}

      {!partialOpen && (
        <button onClick={() => { setMethod('partial'); setPartialOpen(true); }} className="text-sm text-blue-600 hover:text-blue-700" disabled={disabledAll}>
          직접 선택하여 수정
        </button>
      )}

      {invalidReason && <p className="text-xs text-red-600">{invalidReason}</p>}

      <div className="text-xs text-slate-400 bg-slate-50 rounded px-2 py-1 text-center">
        <p>← → 방법 선택 · Enter 확정</p><p className="mt-1">Tab 다음 위치 · ↓ 같은 종류</p>
        <p className="mt-1">0 유지 · 1 전체 가림{maxPresets > 0 && <> · 2{maxPresets > 1 ? `–${maxPresets + 1}` : ''} 일부 가림</>}</p>
        <p className="mt-1">Shift+↓ / Shift+Tab 이전 · Esc 키보드 모드 해제</p>
      </div>

      <button
        data-native-keys="true"
        onClick={handleSave}
        disabled={!canSave || disabledAll}
        className={`w-full py-2 text-sm rounded-md ${
          canSave && !disabledAll ? 'bg-blue-600 text-white hover:bg-blue-700' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
        }`}
      >
        {saving ? '저장 중...' : '선택 확정 · Enter'}
      </button>

      <p aria-live="polite" className="min-h-4 text-xs text-slate-500">{hasUnconfirmedChanges && keyboardNotice ? '먼저 Enter로 현재 선택을 확정해 주세요. 선택은 그대로 유지됩니다.' : keyboardNotice}</p>
      {error && !saving && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
