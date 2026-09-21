'use client'

import { Sparkles, Zap } from 'lucide-react'

interface AiToggleProps {
  enabled: boolean
  onToggle: (enabled: boolean) => void
}

export function AiToggle({ enabled, onToggle }: AiToggleProps) {
  return (
    <div className="flex items-center gap-3 p-3 bg-white rounded-xl border border-slate-200 shadow-sm">
      <div className="flex items-center gap-2 flex-1">
        {enabled ? (
          <Sparkles className="w-5 h-5 text-brand-500" />
        ) : (
          <Zap className="w-5 h-5 text-slate-400" />
        )}
        <div className="flex-1">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-800">
              {enabled ? 'AI 분석 사용' : 'AI 분석 끄기'}
            </span>
            <button
              type="button"
              onClick={() => onToggle(!enabled)}
              className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${
                enabled ? 'bg-brand-500' : 'bg-slate-300'
              }`}
              aria-label="AI 분석 토글"
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform duration-200 ${
                  enabled ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {enabled 
              ? 'Upstage Document Parse + Solar Pro 4로 문서 분석 및 개인정보 후보를 찾습니다.'
              : '기본 패턴 탐지(전화번호, 이메일, 주민등록번호 등)만 사용합니다.'}
          </p>
        </div>
      </div>
    </div>
  )
}
