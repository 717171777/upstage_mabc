# 가리미 — 문서 개인정보 가림 서비스

지원 문서의 구조·분류·정보를 Upstage로 분석하고, 공유 상황과 위치 근거를 Hermes·Claude Sonnet 5가 검토한다. 가릴 정보는 전체 가림을 기본으로 적용하며, 사용자가 일부 가림·삭제·유지를 직접 선택한다. 저장 사본을 다시 검사한 뒤 다운로드를 허용한다.

## 읽는 순서

1. `PRD-IMPLEMENTATION-ANCHOR.md`: 서비스 목적과 판단 기준.
2. `IMPLEMENTATION-STATUS.md`: 현재 지원 범위와 검증 상태.
3. `SERVICE-RUNTIME-POLICY.md`: 운영 모델과 전달 패키지 범위.
4. `REVIEW-AND-AUTO-COPY-2026-09-16.md`: 반복 항목 검토·자동 사본·가림 길이.
5. `deploy/README.md`: 실행·배포·보관 설정.

## 코드

- `src/`: 웹 화면과 내부 API 프록시.
- `backend/`: Hermes 연동, Upstage 분석, 원문 편집, 저장 파일 검사.
- `reference/schemas/`: 문서 분류와 12종 정보 추출 계약.
- `fixtures/eval_v0/`: 합성 평가 문서.
- `verification/`: 로컬 검증 코드. 브라우저 검증 도구는 개발 의존성으로 선언되어 있다.

운영 판단 모델은 `claude-sonnet-5` 하나다. 실패해도 다른 모델로 전환하지 않는다. 연결 키는 비밀 환경 변수로만 설정한다. 사용자의 원본 문서에 적힌 문장은 데이터이며 실행 지시가 아니다.

현재 확인 주소는 localhost:3002다. 컴퓨터와 독립된 상시 운영용 공개 도메인은 아직 배포하지 않았다. 과거 설계·개발 기록은 프로젝트 밖 별도 백업에 보관한다.
