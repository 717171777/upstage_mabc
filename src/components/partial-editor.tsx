'use client';

import {useState,useRef,useEffect,useCallback} from 'react';
import type {Candidate} from '@/lib/service';
import type {Mask} from '@/lib/masking';
import {graphemes,maskFromIndices,presetsFor,validPartial} from '@/lib/masking';

export type {Mask};

type Props = {
  candidate: Candidate;
  disabled?: boolean;
  onApply: (mask: Mask, presetId: string | null) => void;
  onCancel: () => void;
  onPreview?: (mask: Mask, presetId: string | null) => void;
};

export function PartialEditor({candidate, disabled, onApply, onCancel, onPreview}: Props) {
  const dragHidesRef = useRef(true);
  const [selected, setSelected] = useState<Set<number>>(() => {
    const chars = graphemes(candidate.value);
    const set = new Set<number>();
    for (let i = 0; i < chars.length; i++) {
      if (candidate.mask.some(([a, b]) => a <= chars[i].start && chars[i].end <= b)) {
        set.add(i);
      }
    }
    return set;
  });
  const [presetId, setPresetId] = useState<string | null>(() => presetsFor(candidate.type, candidate.value)
    .find(p => JSON.stringify(p.mask) === JSON.stringify(candidate.mask))?.id ?? null);
  const [dragStart, setDragStart] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragSnapshotRef = useRef<Set<number> | null>(null);

  const chars = graphemes(candidate.value);
  const selectedArray = [...selected];
  const mask = maskFromIndices(candidate.value, selectedArray);
  const valid = validPartial(candidate.value, mask);
  const fullSelection = selected.size === chars.length;
  const emptySelection = selected.size === 0;
  const previewKey = JSON.stringify(mask);
  useEffect(() => {
    onPreview?.(JSON.parse(previewKey) as Mask, presetId);
  }, [previewKey, presetId, onPreview]);

  const clearSelection = useCallback(() => {
    setSelected(new Set());
    setPresetId(null);
  }, []);

  const toggleChip = useCallback((i: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
    setPresetId(null);
  }, []);

  const handlePointerDown = useCallback((i: number, e: React.PointerEvent) => {
    if (disabled) return;
    if (e.buttons !== 1) return;
    e.preventDefault();
    setDragStart(i);
    setIsDragging(true);
    dragSnapshotRef.current = new Set(selected);
    dragHidesRef.current = !selected.has(i);
    if (dragHidesRef.current) {
      setSelected(prev => {
        const next = new Set(prev);
        if (!next.has(i)) next.add(i);
        return next;
      });
    } else {
      setSelected(prev => {
        const next = new Set(prev);
        next.delete(i);
        return next;
      });
    }
    setPresetId(null);
  }, [selected, disabled]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDragging || dragStart === null) return;
    if (disabled) return;
    if (e.buttons !== 1) return;
    e.preventDefault();
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (el instanceof HTMLElement) {
      const chip = el.closest('.chip');
      if (chip instanceof HTMLElement && containerRef.current && containerRef.current.contains(chip)) {
        const index = parseInt(chip.dataset.index || '-1');
        if (index >= 0) {
          const start = Math.min(dragStart, index);
          const end = Math.max(dragStart, index);
          const base = dragSnapshotRef.current || new Set();
          setSelected(() => {
            const next = new Set(base);
            for (let j = start; j <= end; j++) {
              if (dragHidesRef.current) next.add(j);
              else next.delete(j);
            }
            return next;
          });
        }
      }
    }
  }, [isDragging, dragStart, disabled]);

  useEffect(() => {
    const handleGlobal = () => {
      setIsDragging(false);
      setDragStart(null);
      dragSnapshotRef.current = null;
    };
    window.addEventListener('pointerup', handleGlobal);
    window.addEventListener('pointercancel', handleGlobal);
    return () => {
      window.removeEventListener('pointerup', handleGlobal);
      window.removeEventListener('pointercancel', handleGlobal);
    };
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent, i: number) => {
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      toggleChip(i);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      const prev = i > 0 ? i - 1 : chars.length - 1;
      chipRefs.current[prev]?.focus();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      const next = i < chars.length - 1 ? i + 1 : 0;
      chipRefs.current[next]?.focus();
    }
  }, [chars, toggleChip]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && containerRef.current && containerRef.current.contains(document.activeElement)) {
        onCancel();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onCancel]);

  return (
    <div className="space-y-3" onPointerMove={handlePointerMove}>
      <p className="text-xs leading-relaxed text-slate-600">가릴 글자를 눌러 주세요. 다시 누르면 가림이 풀려요. 이어서 드래그할 수도 있어요.</p>
      <div>

        <div
          ref={containerRef}
          className="flex flex-wrap gap-1 max-h-32 overflow-y-auto p-2 border border-slate-200 rounded-md bg-white"
          style={{touchAction: 'none'}}
        >
          {chars.map((ch, i) => {
            const isSelected = selected.has(i);
            return (
              <button
                key={i}
                ref={el => { chipRefs.current[i] = el; }}
                data-index={i}
                type="button"
                disabled={disabled}
                className={`chip px-2 py-1 min-w-[2rem] min-h-[2rem] text-sm rounded border focus:outline-none focus:ring-2 focus:ring-blue-500 ${isSelected ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-700 border-slate-300'}`}
                onPointerDown={(e) => handlePointerDown(i, e)}
                onKeyDown={e => handleKeyDown(e, i)}
                aria-pressed={isSelected}
                title={isSelected ? `가릴 문자 ${ch.text}` : `남길 문자 ${ch.text}`}
                aria-label={isSelected ? `가릴 문자 ${ch.text}` : `남길 문자 ${ch.text}`}>
                {ch.text}
              </button>
            );
          })}
        </div>
        <div className="flex gap-3 mt-2 text-xs text-slate-500">
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-blue-600"></span>파랑: 가림</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 rounded bg-white border border-slate-300"></span>흰색: 남김</span>
        </div>
      </div>

      <div className="flex gap-2 mt-4">
        <button
          type="button"
          onClick={clearSelection}
          disabled={disabled}
          className="max-w-full break-all px-3 py-1.5 rounded-md text-sm border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          다시 선택
        </button>
        <button
          type="button"
          onClick={() => onApply(mask, presetId)}
          disabled={disabled || !valid || emptySelection || fullSelection}
          className="max-w-full break-all px-3 py-1.5 rounded-md text-sm border border-blue-600 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          글자 선택 완료
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="max-w-full break-all px-3 py-1.5 rounded-md text-sm border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
        >
          취소
        </button>
      </div>

      <div aria-live="polite">
        <p className="text-xs text-amber-600">
          {fullSelection
            ? "모두 가리려면 '전체 가림'을 선택해 주세요."
            : emptySelection
            ? "가릴 글자를 선택해 주세요."
            : ""}
        </p>
      </div>
    </div>
  );
}
