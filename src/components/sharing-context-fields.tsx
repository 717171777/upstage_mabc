'use client';

import type { Context } from '@/lib/service';

export const EMPTY_SHARING_CONTEXT: Context = { recipient: null, purpose: '', keepInfo: null };

export function SharingContextFields({ value, onChange, disabled = false }: {
  value: Context;
  onChange: (value: Context) => void;
  disabled?: boolean;
}) {
  const fieldClass = 'mt-2 w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50';
  return <div className="space-y-4">
    <label className="block text-sm font-medium text-slate-700">
      누구에게 공유하나요?
      <select value={value.recipient ?? ''} onChange={e => onChange({ ...value, recipient: e.target.value || null })} disabled={disabled} className={fieldClass}>
        <option value="">아직 정하지 않았어요</option>
        <option value="내부동료">내부 동료</option>
        <option value="외부협력기관">외부 협력 기관</option>
        <option value="공공기관">공공기관</option>
        <option value="불특정다수">누구나 볼 수 있도록 공개</option>
      </select>
    </label>
    <label className="block text-sm font-medium text-slate-700">
      어떤 목적으로 보내나요?
      <textarea value={value.purpose} onChange={e => onChange({ ...value, purpose: e.target.value })} disabled={disabled} maxLength={2000} rows={2} className={`${fieldClass} resize-y font-normal`} placeholder="예: 외부 협력사에 출장 결과와 지출 내역을 공유해요." />
    </label>
    <label className="block text-sm font-medium text-slate-700">
      꼭 보여야 하는 정보가 있나요?
      <textarea value={value.keepInfo ?? ''} onChange={e => onChange({ ...value, keepInfo: e.target.value || null })} disabled={disabled} maxLength={2000} rows={2} className={`${fieldClass} resize-y font-normal`} placeholder="예: 기관의 공용 문의 이메일은 보이게 해 주세요." />
    </label>
    <p className="text-xs leading-relaxed text-slate-500">모두 선택 사항입니다. 입력한 상황과 문서 안의 위치·역할을 함께 보고 가릴지 유지할지 판단합니다. 가릴 항목은 전체 가림이 기본입니다. 비워두면 문서의 근거를 중심으로 보수적으로 판단합니다.</p>
  </div>;
}
