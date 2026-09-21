/**
 * 문서 마스킹 관련 유틸
 */

/**
 * 문서 콘텐츠에서 개인정보 패턴을 찾아 마스킹 처리하는 함수
 */
export function getMaskedContent(content: string): string {
  return content
    // 전화번호: 010-XXXX-XXXX 또는 02-XXXX-XXXX 등
    .replace(/(01[016789]-\d{3,4}-\d{4})|(0[2-9]-\d{3,4}-\d{4})/g, (match) => {
      return match.replace(/\d{3,4}(?=-\d{4})/, '****')
    })
    // 이메일
    .replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, '****@*****.***')
    // 주민등록번호
    .replace(/\b\d{6}[-]\d{7}\b/g, '******-*******')
    // 생년월일
    .replace(/\b(19|20)\d{2}[-.]\d{2}[-.]\d{2}\b/g, (match) => {
      return match.replace(/\d{2}[-.]\d{2}$/, '-**-**')
    })
    // 이름 패턴 (간단한 한글 2~4자) — 참고용
    .replace(/[가-힣]{2,4}/g, () => {
      return '***'
    })
}
