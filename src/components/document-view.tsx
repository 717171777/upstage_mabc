/* Authenticated PDF previews use local blob URLs; do not send them to an image optimizer. */
/* eslint-disable @next/next/no-img-element */
'use client';

import {useState, useEffect, useRef, useMemo, type Ref} from 'react';
import type {Job, Unit, Candidate, PreviewResult, DocumentBlock} from '@/lib/service';
import {useApp} from '@/contexts/AppContext';
import {selectedTextRange} from '@/lib/masking';

type SelectionRange = {unitId: string; start: number; end: number; value: string};
type Props = {
  job: Job; variant?: 'original' | 'copy'; active?: Candidate | null;
  onSelect?: (range: SelectionRange) => void; onCandidateSelect?: (id: string) => void;
  pageNumber?: number; onPageChange?: (page: number) => void;
  compact?: boolean; readOnlyOriginal?: boolean;
  viewportRef?: Ref<HTMLDivElement>; zoomPercent?: number;
};
const partLabel = (part = '') => part.includes('header') ? '머리말' : part.includes('footer') ? '꼬리말' : '본문';
const isLocated = (c: Candidate) => c.locationResolved && c.start !== null && c.end !== null && !!c.unitId;
function hides(c: Candidate, start: number, end: number) {
  if (c.start === null || c.end === null || start < c.start || end > c.end) return false;
  return c.method === 'full' || c.method === 'delete' || (c.method === 'partial' && c.mask.some(([a,b]) => start >= c.start! + a && end <= c.start! + b));
}

export function DocumentView({job, variant = 'original', active, onSelect, onCandidateSelect, pageNumber: controlledPage, onPageChange, compact = false, readOnlyOriginal = false, viewportRef, zoomPercent}: Props) {
  const {preview, page} = useApp();
  const [loaded, setLoaded] = useState<{key:string;data?:PreviewResult;error?:string}|null>(null);
  const [structure, setStructure] = useState(false);
  const [mode, setMode] = useState<'edit' | 'masked'>('edit');
  const [pageChoice, setPageChoice] = useState<{key:string;number:number}|null>(null);
  const [localZoom, setZoom] = useState(100);
  const zoom = zoomPercent ?? localZoom;
  const [loadedImage, setLoadedImage] = useState<{key:string;url?:string;error?:string}|null>(null);
  const unitsRef = useRef(new Map<string, HTMLDivElement>());
  const pageKey=`${job.id}:${active?.id??''}`;
  const number=controlledPage??(pageChoice?.key===pageKey?pageChoice.number:active?.page??1);
  const isCopy = variant === 'copy';
  const editable = !isCopy && !readOnlyOriginal;
  const copyVersion = isCopy ? `${job.version}:${job.artifact?.sha256 ?? ''}` : '';
  const requestKey=`${job.id}:${variant}:${copyVersion}`;
  const data=loaded?.key===requestKey?loaded.data:null;
  const error=loaded?.key===requestKey?loaded.error:'';
  const imageKey=`${requestKey}:${number}:${structure}`;
  const imageUrl=loadedImage?.key===imageKey?loadedImage.url:null;
  const imageError=loadedImage?.key===imageKey?loadedImage.error:'';
  const totalPages = Object.keys(data?.pageSizes ?? {}).length;
  const setPage=(n:number)=>{setPageChoice({key:pageKey,number:n});onPageChange?.(n);};

  useEffect(() => {
    let cancelled = false;
    preview(variant).then(result => {if (!cancelled) setLoaded({key:requestKey,data:result});}).catch(e => {if (!cancelled) setLoaded({key:requestKey,error:e instanceof Error ? e.message : '문서를 불러오지 못했습니다.'});});
    return () => {cancelled = true;};
  }, [requestKey, variant, preview]);

  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    if (data?.format === 'pdf' && !structure) {
      page(number, variant).then(blob => {
        if (cancelled) return;
        url = URL.createObjectURL(blob); setLoadedImage({key:imageKey,url});
      }).catch(e => {if (!cancelled) setLoadedImage({key:imageKey,error:e instanceof Error ? e.message : '페이지를 불러오지 못했습니다.'});});
    }
    return () => {cancelled = true; if (url) URL.revokeObjectURL(url);};
  }, [data?.format, imageKey, number, variant, structure, page]);

  useEffect(() => {
    if (active?.unitId && data) unitsRef.current.get(active.unitId)?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }, [active?.id, active?.unitId, data, structure]);

  const units = useMemo(() => new Map((data?.units ?? []).map(u => [u.id, u])), [data]);
  const candidatesByUnit = useMemo(() => {
    const map = new Map<string, Candidate[]>();
    if (editable) for (const c of job.candidates) if (isLocated(c)) map.set(c.unitId!, [...(map.get(c.unitId!) ?? []), c]);
    return map;
  }, [job.candidates, editable]);

  const selectText = (el: HTMLDivElement, unit: Unit) => {
    if (!editable || !onSelect) return;
    const selected = selectedTextRange(el, window.getSelection());
    if (!selected) return;
    const chars = Array.from(unit.text);
    if (selected.end > chars.length) return;
    onSelect({unitId: unit.id, ...selected, value: chars.slice(selected.start, selected.end).join('')});
  };

  const renderText = (unit: Unit) => {
    const cs = candidatesByUnit.get(unit.id) ?? [];
    if (!cs.length) return unit.text;
    const chars = Array.from(unit.text);
    const bounds = new Set([0, chars.length]);
    for (const c of cs) {
      bounds.add(c.start!); bounds.add(c.end!);
      if (c.method === 'partial') for (const [a,b] of c.mask) {bounds.add(c.start!+a); bounds.add(c.start!+b);}
    }
    const sorted = [...bounds].filter(n => n >= 0 && n <= chars.length).sort((a,b) => a-b);
    return sorted.slice(0,-1).map((start,i) => {
      const end = sorted[i+1];
      const covering = cs.filter(c => c.start! <= start && c.end! >= end);
      const chosen = covering.find(c => c.id === active?.id) ?? covering[0];
      const hidden = covering.some(c => hides(c,start,end));
      return <span key={start} data-candidate-id={chosen?.id} data-masked={hidden || undefined}
        onClick={e => {if (chosen && !window.getSelection()?.toString()) {e.stopPropagation(); onCandidateSelect?.(chosen.id);}}}
        style={{backgroundColor: hidden ? mode === 'edit' ? 'var(--ui-mask-edit)' : 'var(--ui-mask-solid)' : undefined,
          color: hidden && mode === 'masked' ? 'transparent' : undefined,
          boxShadow: chosen?.id === active?.id ? 'inset 0 -2px var(--ui-primary)' : undefined,
          cursor: chosen ? 'pointer' : undefined, borderRadius: 2}}>{chars.slice(start,end).join('')}</span>;
    });
  };
  const renderUnit = (unit: Unit, block?: Extract<DocumentBlock,{kind:'paragraph'}>) => <div key={unit.id} data-scroll-anchor={unit.id}
    ref={el => {if (el) unitsRef.current.set(unit.id,el); else unitsRef.current.delete(unit.id);}}
    className={structure ? 'border-b border-slate-100 px-4 py-3' : `${block?.region === 'header' || block?.region === 'footer' ? 'text-[.875em] text-slate-500' : ''} ${block?.heading ? 'text-[1.25em] font-semibold my-5' : 'my-2'}`}>
    {structure && <p className="mb-1 text-xs text-slate-400">{partLabel(unit.locator?.part)}{unit.page ? ` · ${unit.page}쪽` : ''}</p>}
    <div data-unit-id={unit.id} onMouseUp={e => selectText(e.currentTarget, unit)}
      className="min-h-[1.5em] whitespace-pre-wrap break-words leading-[1.9] select-text"
      style={{textAlign: block?.align ?? 'left'}}>{renderText(unit)}</div>
  </div>;
  const renderBlocks = (blocks: DocumentBlock[], depth = 0): React.ReactNode => depth > 24 ? null : blocks.map((b,i) => b.kind === 'paragraph'
    ? units.has(b.unitId) ? renderUnit(units.get(b.unitId)!, b) : null
    : <table key={`table-${depth}-${i}`} className="w-full table-fixed border-collapse my-4 text-[.875em]"><tbody>{b.rows.map((row,r) => <tr key={r}>{row.map((cell,c) => <td key={c} colSpan={cell.colSpan} className="border border-slate-300 px-3 py-1 align-top">{renderBlocks(cell.blocks,depth+1)}</td>)}</tr>)}</tbody></table>);

  const size = data?.pageSizes?.[String(number)];
  const pdfOverlays = editable && size ? (data?.units ?? []).filter(u => u.page === number).flatMap(u => (candidatesByUnit.get(u.id) ?? []).flatMap(c =>
    (u.chars ?? []).slice(c.start!,c.end!).map((ch,i) => {
      const hidden = hides(c,c.start!+i,c.start!+i+1);
      if (!hidden && c.id !== active?.id) return null;
      const [x0,y0,x1,y1] = ch.bbox;
      return <button key={`${c.id}-${i}`} type="button" tabIndex={-1} aria-label="이 정보 선택" onClick={() => onCandidateSelect?.(c.id)}
        className="absolute p-0 border-0" style={{left:`${x0/size.width*100}%`,top:`${y0/size.height*100}%`,width:`${(x1-x0)/size.width*100}%`,height:`${(y1-y0)/size.height*100}%`,
          backgroundColor: hidden ? mode === 'edit' ? 'var(--ui-mask-edit-pdf)' : 'var(--ui-mask-solid)' : 'transparent', boxShadow:c.id === active?.id ? 'inset 0 -2px var(--ui-primary)' : undefined}}/>;
    }))) : null;

  return <div className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-800">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
      {editable ? <div className="flex rounded-lg bg-slate-100 p-0.5 text-xs">
        {(['edit','masked'] as const).map(m => <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)} className={`rounded-md px-3 py-2 ${mode === m ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500'}`}>{m === 'edit' ? '원문 보며 편집' : '가림 미리보기'}</button>)}
      </div> : <span className="text-sm font-medium">{isCopy ? '실제 저장된 사본' : '원본 문서'}</span>}
      {!compact && <button type="button" aria-pressed={structure} onClick={() => setStructure(v => !v)} className={`rounded-lg border px-3 py-2 text-xs ${structure ? 'bg-blue-50 border-blue-200 text-blue-700' : 'border-slate-200 text-slate-500'}`}>구조 보기 {structure ? '켜짐' : '꺼짐'}</button>}
      {data?.format === 'pdf' && <div className="flex items-center gap-2 text-xs"><button aria-label="이전 페이지" disabled={number <= 1} onClick={() => setPage(number-1)} className="p-2 disabled:opacity-30">←</button><span>{number} / {totalPages}</span><button aria-label="다음 페이지" disabled={number >= totalPages} onClick={() => setPage(number+1)} className="p-2 disabled:opacity-30">→</button></div>}
    </div>
    {editable && <p className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">{mode === 'edit' ? '반투명 부분이 가려질 범위입니다. 원문을 보며 수정하세요.' : '저장 전 예상 결과입니다. 실제 저장 사본은 내보내기에서 확인합니다.'}</p>}
    <div ref={viewportRef} data-testid={`document-viewport-${variant}`} role="region" aria-label={`${isCopy ? '저장 사본' : '원본'} 문서 스크롤 영역`} tabIndex={0}
      className="max-h-[72vh] min-h-64 overflow-auto overscroll-contain bg-[var(--ui-document-canvas)] p-3 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 sm:p-5" style={{scrollbarGutter: 'stable'}}>
      {error ? <p role="alert" className="p-5 text-sm text-red-600">{error}</p> : !data ? <p className="p-5 text-sm text-slate-500">문서를 불러오는 중…</p> : structure ? <div className="bg-white rounded-lg">{data.units.filter(u => data.format !== 'pdf' || u.page === number).map(u => renderUnit(u))}</div>
        : data.format === 'docx' ? <div data-testid="document-paper" className="mx-auto min-h-[520px] bg-white px-6 py-8 shadow-sm sm:px-10" style={{width:`${zoom}%`, maxWidth:794*zoom/100, fontSize:16*zoom/100}}>{data.blocks?.length ? renderBlocks(data.blocks) : data.units.map(u => renderUnit(u))}</div>
        : imageError ? <p role="alert" className="text-sm text-red-600">{imageError}</p> : imageUrl ? <div className="relative mx-auto bg-white shadow-sm" style={{width:`${zoom}%`}}><img src={imageUrl} alt={`${isCopy ? '저장 사본' : '원본'} ${number}쪽`} className="block w-full"/>{pdfOverlays}</div> : <p className="text-sm text-slate-500">페이지를 불러오는 중…</p>}
    </div>
    <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
      <span>{job.format === 'docx' ? '본문·표를 문서 형태로 표시합니다. Word의 실제 페이지 배치와는 다를 수 있습니다.' : editable && !structure ? '정보를 눌러 선택하세요. 텍스트를 직접 선택하려면 구조 보기를 켜세요.' : isCopy ? '저장 파일의 실제 페이지입니다.' : '원본 문서의 실제 페이지입니다.'}</span>
      {!compact && zoomPercent === undefined && !structure && <div className="flex shrink-0 items-center gap-1 text-xs"><button type="button" aria-label="문서 축소" disabled={zoom <= 80} className="h-9 w-9 rounded-lg hover:bg-slate-100 disabled:opacity-30" onClick={() => setZoom(z => Math.max(80,z-20))}>−</button><button type="button" aria-label="문서를 너비에 맞추기" className="rounded-lg px-2 py-2 hover:bg-slate-100" onClick={() => setZoom(100)}>{zoom}%</button><button type="button" aria-label="문서 확대" disabled={zoom >= 200} className="h-9 w-9 rounded-lg hover:bg-slate-100 disabled:opacity-30" onClick={() => setZoom(z => Math.min(200,z+20))}>＋</button></div>}
    </div>
  </div>;
}
