'use client';

import type { Context } from '@/lib/service';

export const EMPTY_SHARING_CONTEXT: Context = { recipient: null, purpose: '', keepInfo: null };

// 잘 모르는 경우를 위한 보기. 고르면 그대로 채워지고, 직접 고쳐 쓸 수도 있다.
const RECIPIENT_CHOICES = [
  '내부 동료',
  '외부 협력 기관',
  '공공기관',
  '금융기관',
  '고객센터·서비스 제출',
  '누구나 볼 수 있도록 공개',
] as const;

export function SharingContextFields({ value, onChange, disabled = false }: {
  value: Context;
  onChange: (value: Context) => void;
  disabled?: boolean;
}) {
  const fieldClass = 'mt-2 w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50';
  return <div className="space-y-4">
    <div className="block text-sm font-medium text-slate-700">
      <label htmlFor="garimi-recipient">어디에 제출하거나 공유하나요?</label>
      <div className="mt-2 flex flex-wrap gap-2">
        {RECIPIENT_CHOICES.map(choice => {
          const active = value.recipient === choice;
          return <button
            key={choice}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange({ ...value, recipient: active ? null : choice })}
            className={`border px-3 py-1.5 text-xs font-normal disabled:opacity-50 ${active ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'}`}
          >{choice}</button>;
        })}
      </div>
      <input
        id="garimi-recipient"
        type="text"
        value={value.recipient ?? ''}
        onChange={e => onChange({ ...value, recipient: e.target.value || null })}
        disabled={disabled}
        maxLength={100}
        className={`${fieldClass} font-normal`}
        placeholder="위에서 고르거나, 제출처를 직접 적어 주세요 (예: 카페24, 카카오 고객센터)"
      />
    </div>
    <label className="block text-sm font-medium text-slate-700">
      어떤 목적으로 보내나요?
      <textarea value={value.purpose} onChange={e => onChange({ ...value, purpose: e.target.value })} disabled={disabled} maxLength={2000} rows={2} className={`${fieldClass} resize-y font-normal`} placeholder="예: 카페24 입점 심사에 제출할 사업자 서류예요." />
    </label>
    <label className="block text-sm font-medium text-slate-700">
      꼭 보여야 하는 정보가 있나요?
      <textarea value={value.keepInfo ?? ''} onChange={e => onChange({ ...value, keepInfo: e.target.value || null })} disabled={disabled} maxLength={2000} rows={2} className={`${fieldClass} resize-y font-normal`} placeholder="예: 기관의 공용 문의 이메일은 보이게 해 주세요." />
    </label>
    <p className="text-xs leading-relaxed text-slate-500">제출처와 사유를 적으면 가림 정도까지 함께 판단합니다. 비워두면 전체 가림만 제안합니다. 신분증 번호는 언제나 직접 고르셔야 합니다. 모두 선택 사항입니다. 입력한 상황과 문서 안의 위치·역할을 함께 보고 가릴지 유지할지 판단합니다. 가릴 항목은 전체 가림이 기본입니다. 비워두면 문서의 근거를 중심으로 보수적으로 판단합니다.</p>
  </div>;
}
