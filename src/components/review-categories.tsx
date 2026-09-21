'use client';

import type {Candidate, PiiType} from '@/lib/service';
import {PII_LABELS, METHOD_LABELS} from '@/lib/service';
import {reviewCategorySections, isUserDecision} from '@/lib/review-flow';

type Props = {
  candidates: Candidate[];
  activeType: PiiType;
  selected: Candidate | null;
  visibleGroups: Candidate[][];
  onTypeChange: (type: PiiType) => void;
  onSelect: (id: string) => void;
  disabled: boolean;
};

export function ReviewCategories({candidates, activeType, selected, visibleGroups, onTypeChange, onSelect, disabled}: Props) {
  const {categories, primary, additional} = reviewCategorySections(candidates);
  const active = categories.find(category => category.type === activeType)!;
  const categoryCard = (category: typeof active) => <button key={category.type} type="button" disabled={disabled}
    aria-label={`${PII_LABELS[category.type]} ${category.count}곳`} aria-pressed={category.type===activeType}
    onClick={()=>onTypeChange(category.type)}
    className={`flex min-h-9 min-w-0 items-center justify-between gap-1 rounded-md border px-1.5 py-1.5 text-left text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 ${category.type===activeType?'border-blue-600 bg-blue-50 text-blue-800':'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>
    <span className="min-w-0 break-words font-medium">{PII_LABELS[category.type]}</span>
    <span className={`shrink-0 tabular-nums ${category.type===activeType?'font-semibold text-blue-700':category.count?'text-slate-600':'text-slate-400'}`}>{category.count}</span>
  </button>;
  return <>
    <div className="grid grid-cols-3 gap-1.5 px-3 pb-3" role="group" aria-label="개인정보 기본 6종">
      {primary.map(categoryCard)}
    </div>
    <div className="px-3 pb-3">
      <p className="mb-1.5 text-[11px] text-slate-500">추가정보</p>
      <div role="group" aria-label="개인정보 추가 6종" className="flex flex-wrap gap-1.5">
        {additional.map(category=><button key={category.type} type="button" disabled={disabled}
          aria-label={`${PII_LABELS[category.type]} ${category.count}곳`} aria-pressed={category.type===activeType}
          onClick={()=>onTypeChange(category.type)}
          className={`inline-flex min-h-7 items-center gap-1 rounded-full border px-2 py-1 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1 disabled:opacity-50 ${category.type===activeType?'border-blue-600 bg-blue-600 text-white':category.count?'border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100':'border-slate-100 bg-slate-50 text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
          <span>{PII_LABELS[category.type]}</span>
          {category.count>0&&<span className="font-semibold tabular-nums">{category.count}</span>}
        </button>)}
      </div>
    </div>
    <div className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500">{PII_LABELS[activeType]} · {active.count>0?`${active.groups.length}개 정보 · 눌러서 설정`:'탐지된 정보 없음'}</div>
    <div className="max-h-80 overflow-auto" role="region" aria-label={`${PII_LABELS[activeType]} 정보 목록`}>
      {visibleGroups.map(group => {
        const unresolved = group.filter(candidate => !candidate.locationResolved).length;
        const manual = group.filter(isUserDecision).length;
        const selectedGroup = selected?.type === activeType && selected.value === group[0].value;
        return <button type="button" key={group[0].value} disabled={disabled}
          onClick={() => onSelect((group.find(candidate => !candidate.locationResolved) ?? group[0]).id)}
          className={`flex w-full items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-left text-sm disabled:opacity-50 ${selectedGroup ? 'bg-blue-50' : 'hover:bg-slate-50'}`}>
          <span className="min-w-0 break-all font-medium">{group[0].value}</span>
          <span className="shrink-0 text-right text-xs text-slate-500">
            <span className="block">{group.length}곳 · {new Set(group.map(candidate => candidate.method)).size > 1 ? '위치마다 다름' : METHOD_LABELS[group[0].method]}</span>
            <span className="mt-1 block">{manual ? `설정 적용 ${manual}곳` : '기본 설정'}</span>
            {unresolved > 0 && <span className="mt-1 block text-amber-700">위치 연결 필요 {unresolved}곳</span>}
          </span>
          <span aria-hidden="true" className="text-lg text-slate-400">›</span>
        </button>;
      })}
      {!visibleGroups.length && <p className="px-4 py-5 text-sm text-slate-500">{active.count ? '검색·필터에 맞는 정보가 없습니다.' : '이번 문서에서 탐지된 항목이 없습니다.'}</p>}
    </div>
  </>;
}
