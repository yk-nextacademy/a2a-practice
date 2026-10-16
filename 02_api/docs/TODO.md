# TODO — Stage 2. API

> `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[-]` 보류

## 1. 구현

- [x] uv 프로젝트 구성, `run.py`(3개 프로세스 기동/종료)
- [x] common: 설정, SQLite 헬퍼, EventBus, LLM 클라이언트, 연동 HTTP 클라이언트(호출 로그·재시도)
- [x] Dev Server: 사이클 시작, 릴리즈/빌드 API, 티켓 수신 → 수정 → 차기 발행, 확정 Webhook 수신
- [x] QA Server: 발행 Webhook 수신 → 검증, 이슈 관리, 이관/확정 통지
- [x] 공통 API: health, events(+SSE), integration-logs, admin/reset
- [x] Hub(초기 이름 Portal): Dev/QA API를 조합하는 관제 화면 + Integration 화면
- [x] Hub 실시간: 두 서비스 SSE 병합 중계

## 2. 점검

- [x] 3개 프로세스 분리 기동, Swagger `/docs` 확인
- [x] mock 모드 시나리오 결과 일치, `cycle_id` 전파 확인
- [x] LLM 모드 사이클 완주
- [x] QA 서버 중지 상태에서 사이클 시작 시 오류 기록·표시 확인

## 3. 3개 웹 서비스 리팩토링 (추가 요청, 2026-10-15)

- [x] Portal → Hub 전환: 관제 전용(차례·판정 진행 표시, 집계, 병합 이력, 호출 로그), 편의 조작은 대리 호출
- [x] Dev Server 웹: 개발 작업대(발행, 티켓 착수·수정, AI 초안), 릴리즈, 티켓, 타임라인, 연동 로그
- [x] QA Server 웹: 검증 작업대(실행·판정·이슈 등록·수정 확인, Dev 이관 / 버전 확정), 검증 이력, 이슈, 타임라인, 연동 로그
- [x] 시스템별 부서원 명부, 데모/실습 모드(webhook `mode` 전파), DB 마이그레이션
- [x] 공통 웹 계층: 공통 레이아웃, 타임라인·연동 로그 템플릿, 작업대 JS, 입력 보호
- [x] 점검: 데모 완주, 실습 전 과정(API), 권한·조건, 전 화면 렌더링, 콘솔 오류 없음, 실습 발행 중 QA 장애 시 발행 취소

## 4. 알려진 이슈 수정 (2026-10-16)

- [x] Hub "실습 시작"·"사이클 중단" 버튼 무반응 수정 (`base.html`이 `workbench.js`를 항상 로드)
- [x] 사이클 중단을 QA에 통지: Dev → QA `POST /api/webhooks/cycle-cancelled`, QA는 자동 검증 취소 + 진행 중 실행 `CANCELLED`
- [x] Dev가 이관·확정 통지를 받을 때 사이클 상태 검사 (`VERIFYING`이 아니면 409)
- [x] 데모 검증 실행에도 QA 결정(`decision`, `decided_by='Demo'`) 기록
- [x] Hub 초기화를 2단계로 변경 (`dry_run`으로 양쪽 확인 후 실행)
- [x] QA 시작 시 끝나지 못한 데모 실행 정리, `BACKOFF` 미사용 값 제거
- [x] 실습 부분 수정 교착 해결: 직전 발행에 반영되지 않은 이월 이슈(TRANSFERRED)를 다시 이관 가능, Dev는 수정 중 티켓의 상태·담당자 유지
- [x] 점검: Edge 실제 브라우저로 실습 전 과정 클릭, 중단·초기화 직접 검증, LLM 모드 AI 초안 3종, 화면 캡처 재촬영

## 5. 문서화

- [x] `README.md`, `docs/PRD.md`, `docs/USER_SCENARIO.md`
- [x] 루트 `TODO.md` 갱신
- [x] 개발 완료 보고서 `docs/02_API_개발완료보고서.md` (루트)
