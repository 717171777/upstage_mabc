# Claude Sonnet 전환 — 2026-09-20

로컬 소스 변경 완료. 실제 Claude 호출과 운영 종단 간 검증은 배포 후 별도 확인이 필요합니다.

- Upstage: Parse, Classify, Information Extract 및 사본 재추출 유지.
- Hermes: 상황·가림 여부·예외·확인 질문·사본 AI 검토를 Claude Sonnet 5로 실행.
- 외부 판단 목적지: https://api.anthropic.com/v1/messages
- 모델: claude-sonnet-5. 다른 모델 자동 대체 없음.
- Hermes의 내부 통신은 로컬 제한 게이트를 거침. 게이트가 Anthropic 원생 Messages API로 변환.
- Anthropic에는 서비스 판단 지침과 필요한 문서 근거·공유 상황만 전달. Hermes 호스트 메타데이터는 전송 지침에서 제외.
- UI 전송 안내를 Anthropic 포함으로 갱신.
- 키·모델 미설정, 잘린 응답, 도구 호출, 잘못된 모델 응답은 완료 처리하지 않음.
- 기존 SP4 완료 작업을 Claude 완료로 간주하지 않음. 전환 뒤 새 문서 작업으로 검증.

## 제출용 폴더

이 폴더에는 Claude 전환 코드가 이미 반영되어 있습니다. 패치를 다시 적용하지 마세요.
Render는 Dockerfile.backend, Vercel은 기존 Next.js 설정을 사용합니다.
Dockerfile.backend는 기존 Render 배포 설정을 유지하고 Claude 모델 환경변수와 설명만 갱신했습니다.
이번 릴리스에서 Docker 이미지 빌드는 재실행하지 않았습니다.
현재 운영 사이트에 반영하려면 저장소 변경 검토·push와 재배포가 필요합니다.

## Render 설정

- ANTHROPIC_API_KEY: Anthropic API 키를 비밀 환경변수로 직접 입력. 채팅·저장소·Vercel에 넣지 않음.
- GARIMI_CLAUDE_MODEL=claude-sonnet-5
- UPSTAGE_API_KEY 및 기존 문서 API 설정은 유지.
- 키에 작업 공간 지정이 필요하면 ANTHROPIC_WORKSPACE_ID 설정.

설정 후 변경 코드 push와 Render/Vercel 재배포가 필요합니다.
키와 지원 모델 설정 존재 여부만 health의 aiConfigured로 표시하며, 실제 인증·추론 성공을 뜻하지 않습니다.

## 검증 결과

관련 pytest 77개 통과, TypeScript tsc --noEmit 통과.
실제 로컬 Hermes와 합성 입력 + 가짜 Anthropic 서버로 1회 판단 완료 확인.
이 검사에서는 외부 네트워크를 차단했습니다. 실제 Claude API 및 운영 배포 종단 간 검증은 미실행입니다.
배포 후 합성 DOCX/PDF로 분석 → 확인 질문 → 사용자 수정 → 저장 사본 검사 → 다운로드를 검증하세요.

공식 모델 목록: https://platform.claude.com/docs/en/models/overview
공식 API: https://platform.claude.com/docs/en/api/overview
