'use client'

import { useCallback, useState } from 'react'
import { Upload, FileText, File, AlertCircle } from 'lucide-react'
import type { FileValidation } from '@/types'

interface UploadZoneProps {
  onFileSelected: (file: File) => void
  onError: (error: string) => void
  maxSizeMB?: number
  maxSizeLabel?: string
}

const MAX_SIZE_BYTES = 50 * 1024 * 1024 // 50MB

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadZone({ 
  onFileSelected, 
  onError,
  maxSizeMB = 50,
  maxSizeLabel = '50MB',
}: UploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [, setDragCounter] = useState(0)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [validation, setValidation] = useState<FileValidation | null>(null)

  const validateFile = useCallback((file: File): FileValidation => {
    const ext = file.name.split('.').pop()?.toLowerCase() || ''
    
    // 확장자 체크
    if (!['pdf', 'docx', 'doc'].includes(ext)) {
      return {
        valid: false,
        fileType: null,
        fileSize: file.size,
        error: '지원하지 않는 파일 형식입니다. PDF 또는 DOCX 파일을 선택해주세요.',
        maxSizeMB,
      }
    }
    
    // 파일 크기 체크
    if (file.size > MAX_SIZE_BYTES) {
      return {
        valid: false,
        fileType: ext === 'pdf' ? 'pdf' : 'docx',
        fileSize: file.size,
        error: `파일 크기가 ${maxSizeLabel} 제한을 초과합니다. (${formatFileSize(file.size)} / ${maxSizeLabel})`,
        maxSizeMB,
      }
    }
    
    return {
      valid: true,
      fileType: ext === 'pdf' ? 'pdf' : 'docx',
      fileSize: file.size,
      maxSizeMB,
    }
  }, [maxSizeMB, maxSizeLabel])

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragCounter((prev) => prev + 1)
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragCounter((prev) => {
      const newCount = prev - 1
      if (newCount === 0) {
        setIsDragging(false)
      }
      return newCount
    })
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragCounter(0)
    setIsDragging(false)

    const files = e.dataTransfer.files
    if (files.length === 0) {
      onError('파일이 선택되지 않았습니다.')
      return
    }

    const file = files[0]
    const result = validateFile(file)
    setValidation(result)

    if (result.valid) {
      setSelectedFile(file)
      onFileSelected(file)
    } else {
      onError(result.error || '파일 검증에 실패했습니다.')
    }
  }, [validateFile, onFileSelected, onError])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) {
      return
    }

    const file = files[0]
    const result = validateFile(file)
    setValidation(result)

    if (result.valid) {
      setSelectedFile(file)
      onFileSelected(file)
    } else {
      onError(result.error || '파일 검증에 실패했습니다.')
    }
    
    // 파일 입력 요소 초기화 (같은 파일 다시 선택 가능하게)
    e.target.value = ''
  }, [validateFile, onFileSelected, onError])

  const handleClick = useCallback(() => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.pdf,.docx,.doc'
    input.onchange = (e: Event) => {
      const target = e.target as HTMLInputElement
      if (target.files && target.files.length > 0) {
        const file = target.files[0]
        const result = validateFile(file)
        setValidation(result)
        if (result.valid) {
          setSelectedFile(file)
          onFileSelected(file)
        } else {
          onError(result.error || '파일 검증에 실패했습니다.')
        }
      }
    }
    input.click()
  }, [validateFile, onFileSelected, onError])

  const handleReset = useCallback(() => {
    setSelectedFile(null)
    setValidation(null)
  }, [])

  const isValid = validation?.valid && selectedFile !== null

  return (
    <div className="w-full max-w-2xl mx-auto">
      {/* 파일 선택 영역 */}
      <div
        className={`relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-8 md:p-12 cursor-pointer transition-all duration-200 ${
          isDragging
            ? 'border-brand-400 bg-brand-50 scale-[1.01]'
            : isValid
            ? 'border-brand-300 bg-brand-50/30'
            : 'border-slate-300 bg-white hover:border-slate-400 hover:bg-slate-50'
        }`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onClick={isValid ? undefined : handleClick}
      >
        {isValid ? (
          // 파일 선택된 상태
          <div className="flex flex-col items-center gap-3">
            <div className="text-center">
              <p className="text-lg font-semibold text-slate-800">
                {selectedFile?.name}
              </p>
              <p className="text-sm text-slate-500 mt-1">
                {formatFileSize(selectedFile?.size || 0)} • {validation?.fileType === 'pdf' ? 'PDF' : 'DOCX'}
              </p>
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                handleReset()
              }}
              className="mt-2 text-sm text-slate-500 hover:text-slate-700 underline underline-offset-2"
            >
              다른 파일 선택
            </button>
          </div>
        ) : (
          // 파일 선택 전
          <div className="flex flex-col items-center gap-4">
            <div className={`w-16 h-16 rounded-full flex items-center justify-center transition-colors ${
              isDragging ? 'bg-brand-100' : 'bg-slate-100'
            }`}>
              <Upload className={`w-8 h-8 transition-colors ${
                isDragging ? 'text-brand-500' : 'text-slate-400'
              }`} />
            </div>
            <div className="text-center">
              <p className="text-lg font-semibold text-slate-800">
                {isDragging ? '파일을 여기에 놓으세요' : '문서 파일을 선택하세요'}
              </p>
              <p className="text-sm text-slate-500 mt-1">
                PDF 또는 DOCX • {maxSizeLabel} 이하
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <FileText className="w-4 h-4" />
              <span>PDF</span>
              <span className="text-slate-300">/</span>
              <File className="w-4 h-4" />
              <span>DOCX</span>
            </div>
          </div>
        )}

        {/* 숨겨진 파일 입력 */}
        <input
          type="file"
          accept=".pdf,.docx,.doc"
          className="hidden"
          onChange={handleFileInput}
          id="file-input"
        />
      </div>

      {/* 제한사항 안내 */}
      <div className="mt-4 flex items-start gap-2 text-xs text-slate-500 bg-white rounded-lg border border-slate-200 p-3">
        <AlertCircle className="w-4 h-4 text-slate-400 flex-shrink-0 mt-0.5" />
        <p>
          <span className="font-medium text-slate-600">지원 형식:</span> PDF, DOCX •{' '}
          <span className="font-medium text-slate-600">최대 크기:</span> {maxSizeLabel}
          <br />
          <span className="text-slate-400">
            암호가 걸린 문서, 손상된 파일, 스캔 이미지 PDF는 처리되지 않을 수 있습니다.
          </span>
        </p>
      </div>

      {/* 검증 결과 에러 표시 */}
      {validation && !validation.valid && validation.error && (
        <div className="mt-3 p-3 bg-redact-bg/10 border border-redact-bg/20 rounded-lg text-sm text-slate-700 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-redact-bg flex-shrink-0 mt-0.5" />
          <p>{validation.error}</p>
        </div>
      )}
    </div>
  )
}
