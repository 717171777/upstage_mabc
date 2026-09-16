// 개인정보 유형 12종
export type InfoType =
  | 'name'          // 이름
  | 'phone'         // 전화번호
  | 'email'         // 이메일
  | 'address'       // 주소
  | 'birthDate'     // 생년월일
  | 'ssn'           // 주민등록번호
  | 'foreignId'     // 외국인등록번호
  | 'passport'      // 여권번호
  | 'driverLicense' // 운전면허번호
  | 'account'       // 계좌번호
  | 'creditCard'    // 카드번호
  | 'personalId'    // 개인 관리번호

// 문서 유형 6종
export type DocumentType =
  | 'report-meeting'   // 보고·회의
  | 'contract-agreement' // 계약·합의
  | 'transaction-settlement' // 거래·정산
  | 'application-list-personnel' // 신청·명단·인사
  | 'consultation-complaint-interview' // 상담·민원·인터뷰
  | 'other'            // 기타

// 가림 처리 방법 4종
export type MaskingMethod = 'full' | 'partial' | 'delete' | 'keep'

// 마스킹 후보 정보
export interface MaskingCandidate {
  id: string
  type: InfoType
  value: string             // 원본 값
  maskedValue: string       // 현재 적용된 마스킹 결과
  positions: Position[]     // 문서 내 등장 위치 (여러 곳 가능)
  method: MaskingMethod
  customReplacement?: string
  suggestionReason?: string  // AI 제안 이유
  confirmed: boolean        // 사용자가 확인했는지
  skipped: boolean          // 건너뛴 항목인지
}

// 문서 속 위치 (페이지 번호 + 오프셋)
export interface Position {
  page: number              // 1-based 페이지 번호
  start: number             // 페이지 텍스트 내 시작 오프셋
  end: number               // 페이지 텍스트 내 종료 오프셋
  context?: string          // 주변 맥락 (선택)
}

// 문서 정보
export interface DocumentInfo {
  fileName: string
  fileType: 'pdf' | 'docx' | 'txt'
  fileSize: number          // 바이트
  pageCount: number         // 페이지 수
  content: string           // 파싱된 전체 텍스트
  pages: PageContent[]      // 페이지별 내용
  detectedInfos: MaskingCandidate[]
  documentType: DocumentType | null
}

// 페이지별 내용
export interface PageContent {
  pageNumber: number
  text: string
  hasImage?: boolean        // 이미지 포함 여부
}

// 공유 상황 (3가지 질문)
export interface SharingContext {
  recipient: RecipientType | null
  purpose: string           // 한 줄 목적 (비어있을 수 있음)
  keepInfo: KeepInfoOption | null
}

// 받는 사람 유형
export type RecipientType =
  | 'external-partner'   // 외부 협력사
  | 'submission-agency'  // 제출 기관
  | 'customer'           // 고객
  | 'internal-team'      // 다른 내부 팀
  | 'other'              // 기타

// 남길 정보 옵션
export type KeepInfoOption =
  | 'contact-window'     // 문의 창구
  | 'department-name'    // 부서명
  | 'contact-person'     // 담당자 이름
  | 'none'               // 남길 정보 없음

// 앱 전체 상태
export type AppPhase = 'upload' | 'context' | 'review' | 'decision' | 'export'

export interface AppState {
  phase: AppPhase
  document: DocumentInfo | null
  sharingContext: SharingContext
  aiEnabled: boolean
  isProcessing: boolean
  error: string | null
  processingStep: string | null  // 처리 중 표시용 단계명
}

// AI 분석 결과 (문서 Parse + Classify + Extract 통합)
export interface AnalysisResult {
  documentType: DocumentType | null
  candidates: MaskingCandidate[]
  suggestions: Suggestion[]  // 공유 상황에 따른 제안/질문
}

// AI 제안/질문
export interface Suggestion {
  id: string
  candidateId: string
  type: 'question' | 'exception' | 'recommendation'
  content: string
  recommendation?: MaskingMethod
}

// 파일 검증 결과
export interface FileValidation {
  valid: boolean
  fileType: 'pdf' | 'docx' | 'txt' | null
  fileSize: number
  error?: string
  maxSizeMB: number
}

// 규칙 기반 탐지 결과 (AI 꺼졌을 때)
export interface PatternDetectionResult {
  candidates: MaskingCandidate[]
}
