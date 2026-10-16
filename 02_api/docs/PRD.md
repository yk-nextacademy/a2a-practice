# PRD — Stage 2. API (부서별 시스템 REST 연동 + Hub 관제)

## 1. 개요

| 항목 | 내용 |
| --- | --- |
| 목적 | 부서 시스템을 독립 웹 서비스로 분리하고, 사전 합의된 REST API와 Webhook으로 업무를 연동한다. Hub는 전체를 관제한다 |
| 핵심 특징 | 3개 웹 서비스, 시스템별 DB·부서원 명부, Webhook 체인, Correlation ID(`cycle_id`), 데모/실습 모드 |
| 포트 | Hub 8200, Dev Server 8201, QA Server 8202 (모두 웹 화면 + REST API) |

## 2. 업무 도메인

Stage 1과 같은 시나리오·시뮬레이션 규칙을 사용한다.

- 기능 4개(login/search/payment/report) × 테스트케이스 3개 = 12건
- 초기 결함 `D-101`(TC-02), `D-102`(TC-05), `D-103`(TC-08), `D-103` 수정 시 회귀 `D-201`(TC-11)
- 기대 결과: 1.0.0(9/3) → 1.1.0(11/1) → 1.2.0(12/0, 확정)
- 빌드 산출물: Dev Server가 `GET /builds/{cycle}/{version}`으로 제공, QA는 `build_url`로 내려받아 검증

## 3. 서비스별 요구사항

### 3.1 Dev Server (8201, `data/dev.db`) — 발행과 접수된 문제 수정

| 구분 | 요구사항 |
| --- | --- |
| 웹 | 개발 작업대(발행 폼, 접수 티켓 착수·수정 완료, AI 초안), 릴리즈, 티켓·티켓 상세(이력), 타임라인, 연동 로그 |
| 부서원 | 최개발, 정개발 (Dev 시스템 명부) |
| 연동 API | `POST /api/cycles?mode=demo|manual`, `GET /api/cycles`, `GET /api/state`, `GET /api/releases`, `GET /builds/{cycle}/{version}`, `POST /api/tickets`(수신), `POST /api/webhooks/verification-completed`(수신), `POST /api/cycles/{id}/abort` |
| 실습 API | `POST /api/work/start`, `/api/work/abort`, `/api/work/release`, `/api/work/issues/{key}/start`, `/api/work/issues/{key}/fix`, `/api/work/draft/fix/{key}`, `/api/work/draft/release` |
| 규칙 | 사이클 상태(차례) 관리. 실습 발행 시 QA 통지가 실패하면 발행 취소(릴리즈 삭제). 일부 티켓만 수정해도 발행 가능(미완료 이월). 이관·확정 통지는 `VERIFYING` 사이클(확정은 현재 버전)에만 받고 그 외에는 409. 사이클 중단 시 QA에 `cycle-cancelled` 통지(실패해도 중단 유지) |

### 3.2 QA Server (8202, `data/qa.db`) — 문제 탐지, 이관 또는 확정

| 구분 | 요구사항 |
| --- | --- |
| 웹 | 검증 작업대(담당 항목 실행·판정, 이슈 등록 폼, 수정 확인/재현, Dev 이관 / 버전 확정), 검증 이력·상세, 이슈·이슈 상세(이력), 타임라인, 연동 로그 |
| 부서원 | 김검증(login·search), 이검증(payment), 박검증(report) (QA 시스템 명부, 테스트케이스 담당) |
| 연동 API | `POST /api/webhooks/release-published`(수신), `POST /api/webhooks/cycle-cancelled`(수신), `GET /api/test-cases`, `GET /api/test-runs`, `GET /api/issues`, `GET /api/state` |
| 실습 API | `POST /api/work/results/{id}/execute`, `/api/work/results/{id}/judge`, `/api/work/issues`, `/api/work/issues/{key}/verify`, `/api/work/transfer`, `/api/work/approve`, `/api/work/draft/issue/{id}` |
| 규칙 | 이관: 판정 대기 0 · 수정 확인 대기 0 · 이관 대상(OPEN/REOPENED + 직전 발행에 반영되지 않은 이월 TRANSFERRED) ≥ 1. 이월 이슈를 다시 이관해도 Dev의 수정 중 티켓(RECEIVED/IN_PROGRESS)은 상태·담당자를 유지. 확정: 판정 대기 0 · 수정 확인 대기 0 · 미해결 0. 이관/확정 통지가 실패하면 상태를 바꾸지 않고 작업 취소. 권한은 부서 단위(명부에 없는 작업자 403)이며, 담당자는 화면에서 안내만 한다(Stage 1과 동일). 중단 통지를 받으면 해당 사이클의 자동 검증을 멈추고 진행 중인 실행을 `CANCELLED`로 닫는다. 서버 재시작 시 끝나지 못한 데모 실행도 `CANCELLED`로 정리 |

### 3.3 Hub (8200, DB 없음) — 전체 관제

| 구분 | 요구사항 |
| --- | --- |
| 웹 | Overview(파이프라인, 실습 차례·판정 진행, 버전별 결과, 이슈 현황, 병합 이벤트, 사이클 이력), Releases, Test Runs, Issues(QA 이슈 + Dev 티켓), Timeline(병합), Integration(서비스 상태, 연동 계약, 호출 로그) |
| 동작 | 흐름에 개입하지 않음. 매 요청마다 Dev/QA API 조회·조합, 두 시스템 SSE 병합 중계 |
| 편의 조작 | 데모 실행 / 실습 시작 / 사이클 중단 / 초기화 — Dev/QA API 대리 호출. 초기화는 양쪽에 `dry_run`으로 가능 여부를 먼저 확인한 뒤 실행 |
| 연결 | 사이드바에서 Dev 웹, QA 웹으로 이동 (차례 표시) |

### 3.4 공통 (Dev/QA 각각)

`GET /health`, `GET /api/events`·`/api/events/stream`, `GET /api/integration-logs`, `POST /api/admin/reset`(`?dry_run=true`면 확인만), `GET /docs`

## 4. 기능 요구사항 요약

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-01 | 3개 웹 서비스가 독립 프로세스로 동작, Dev/QA는 독립 DB | 필수 |
| F-02 | 데모: Webhook 체인으로 사람 개입 없이 완주 | 필수 |
| F-03 | 실습: 개발부서원은 Dev 웹, 검증부서원은 QA 웹에서 진행 (부서별 명부·권한) | 필수 |
| F-04 | QA는 검증 후 Dev 이관 또는 버전 확정을 선택 | 필수 |
| F-05 | 모든 시스템 간 HTTP 호출을 integration log로 기록, `cycle_id` 전파 | 필수 |
| F-06 | 상대 시스템 장애 시 재시도 3회, 데모는 실패 처리, 실습은 작업 취소·재시도 안내 | 필수 |
| F-07 | Hub는 관제 전용(차례, 집계, 병합 이력, 호출 로그) | 필수 |
| F-08 | LLM(이슈 분석, 수정 내역, 릴리즈 노트) / mock, 실습은 AI 초안 | 필수 |
| F-09 | 실시간 갱신 중 입력 폼 보호, 이모지 미사용 모노톤 UI | 필수 |

## 5. 아키텍처

```text
              ┌──────────── Hub 웹 (8200, 관제, DB 없음) ────────────┐
              │  조회: /api/state, releases, tickets, test-runs,     │
              │        issues, events(SSE), integration-logs         │
              ▼                                                      ▼
 개발부서원 ─▶ Dev 웹 / Dev Server (8201) ── dev.db     QA Server / QA 웹 (8202) ── qa.db ◀─ 검증부서원
              │  POST /api/webhooks/release-published ───────────────▶│
              │◀──────────────────────────── GET /builds/{c}/{v} ─────│
              │◀──────────────────────────── POST /api/tickets ───────│ (Dev 이관)
              │◀──────────── POST /api/webhooks/verification-completed │ (버전 확정)
              │  POST /api/webhooks/cycle-cancelled ─────────────────▶│ (사이클 중단)
```

## 6. 학습 포인트 / 한계

- 시스템 분리로 독립 운영·장애 격리. 사람도 자기 부서 시스템에서만 일한다.
- 상대의 URL·스키마를 알아야 하는 강결합, 경로는 코드에 고정.
- 전체 그림은 Hub가 매번 양쪽을 조회해 조합해야 보인다.
- 다음 단계(MCP): AI 에이전트가 표준 프로토콜로 기능을 발견하고 판단하여 호출한다.
