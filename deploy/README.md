# 가리미 배포 안내

공개 배포는 아직 하지 않았다. 아래 구성은 컴퓨터와 독립된 서버에서 실행하기 위한 배포 후보이며 실제 빌드·실행 결과는 ../IMPLEMENTATION-STATUS.md에 기록한다.

## 실행 구조

공개 HTTPS → Next.js 웹/허용된 API 프록시 → 컨테이너 내부 127.0.0.1:8000 → 실제 Hermes registry → Upstage 문서 API → 필요한 Solar Pro 4 예외 검토 → 결정적 원문 제거·저장 사본 재검사. 사용자의 2026-09-16 후속 승인에 따라 모든 지원 문서를 Upstage로 전달한다. 원문 파일은 웹 정적 경로에 저장하지 않는다.

- 노출 포트: 환경 변수 PORT, 기본 3000. 플랫폼 HTTPS 종료 뒤 이 포트로 연결한다.
- GARIMI_PUBLIC_ORIGIN: 실제 접속 주소의 origin (예: https://운영도메인). 교차 출처 쓰기 요청은 거부한다. 개발용 값을 운영에 복사하지 않는다.
- GARIMI_BACKEND_URL: http://127.0.0.1:8000 (이미지 기본값).
- GARIMI_DATA_DIR: /data/jobs. /data를 UID 10001이 쓸 수 있어야 한다. 지속 볼륨 사용 시 자동 백업을 켜기 전에 보관 정책을 따로 검토한다.
- HERMES_SOURCE와 HERMES_PYTHON은 이미지가 지정한다. Hermes 커밋은 337ef8f8ce4106231b72f6ee7a83d5094dab0059로 고정한다.
- UPSTAGE_API_KEY는 배포 플랫폼의 비밀 환경 변수로만 설정한다. 저장소·브라우저·빌드 인수·이 문서에 넣지 않는다. 키가 없으면 문서 분석을 시작하지 못한다.
- GARIMI_REQUIRE_UPSTAGE=1: 모든 신규 작업과 내보내기에 Upstage 분석을 요구한다. 이미지 기본값이다. 외부 단계 실패를 로컬 처리 완료로 대체하지 않는다.
- GARIMI_ALLOW_USER_DOCUMENTS=1: 승인된 합성 예시만 허용하던 제한을 해제하고 지원되는 사용자 문서를 처리한다. 사용자 전송 승인에 따라 이미지 기본값으로 반영했다.
- 현재 로컬 미리보기는 화면/분석 컨테이너를 분리한다. `deploy/update_preview.py`는 기본적으로 화면만 교체하고, `--backend`에서만 분석 작업 종료를 기다린 뒤 분석 서버를 교체한다. 자세한 내용은 `RESUMABLE-ANALYSIS-2026-09-16.md`를 참고한다. 분석 서버 복제본은 1개이며 여러 복제본의 동시 파일 편집을 지원하지 않는다.

아래는 단일 컨테이너의 수동 실행 예시다. 현재 로컬 미리보기 갱신에는 위 안전 배포 스크립트를 사용한다:

```sh
docker build -t garimi:release .
docker run --name garimi --init -p 127.0.0.1:3002:3000 \
  -e GARIMI_PUBLIC_ORIGIN=http://localhost:3002 \
  -e UPSTAGE_API_KEY \
  garimi:release
```

위 예시는 로컬 검증이다. 상시 운영 서버에 배포하면 플랫폼의 공개 HTTPS 주소를 GARIMI_PUBLIC_ORIGIN에 넣고 그 주소에서 같은 검사를 수행한다. 특정 플랫폼의 계정 생성·결제·도메인 구입은 아직 승인받지 않았다.

## 공개 전 검증

1. /api/service/health에서 aiConfigured=true, upstageRequired=true, syntheticOnly=false 및 고정 모델 확인.
2. 합성 DOCX/PDF 업로드 → 후보 검토 → 프리셋/직접 선택 → 속성 검토 → 저장 사본 검사 → 같은 사본 확인 → 다운로드.
3. 다운로드 파일을 다시 읽어 가린 원문 제거와 나머지 내용 보존 확인.
4. 잘못된 토큰·지난 버전·확인 전 다운로드·선택 변경 후 예전 사본 요청 거부 확인.
5. 작업 즉시 삭제 및 만료 확인. 웹 프로세스 또는 백엔드가 종료될 때 컨테이너도 실패 종료하고 하위 프로세스가 정리되는지 확인.
6. 실제 공개 origin으로 쓰기 요청이 성공하고 다른 origin은 거부되는지 확인.

## 현재 서비스 범위

- 단일 DOCX 또는 텍스트층 PDF, 최대 10MiB. 스캔 PDF는 지원하지 않는다.
- 일반 문서도 Parse·Classify·Information Extract를 거친다. 필요한 원문 근거·위치·공유 상황만 Hermes·SP4 예외 판단에 보낸다. 소스 코드·개발 지시는 운영 요청에 넣지 않는다.
- 로컬 탐지는 후보와 위치를 보완하지만 필수 외부 분석을 대신하지 않는다. 이전 로컬 작업은 자동 전송하지 않으며 새로 올려 분석하도록 안내한다.
- 자체 작업 공간의 1시간 삭제와 외부 API 제공사의 보관은 별개다.
- 저장 사본 검사와 사용자의 같은 사본 확인이 완료되기 전에는 다운로드를 허용하지 않는다. 탐지 후보 0건은 개인정보 0건을 뜻하지 않는다.
