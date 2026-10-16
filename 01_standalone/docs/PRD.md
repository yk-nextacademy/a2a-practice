# PRD — Stage 1. Standalone (모놀리식 웹 서비스)

## 1. 개요

| 항목 | 내용 |
| --- | --- |
| 목적 | 릴리즈·검증·결함 이관 업무 흐름 자체를 하나의 웹 서비스로 구현하여 도메인을 이해한다 |
| 핵심 특징 | 단일 프로세스, 단일 DB, Dev/QA 서비스 간 **함수 직접 호출** |
| 대상 사용자 | 릴리즈 매니저(데모 실행·실습 시작/중단·초기화·관제), 개발/검증 부서원(실습 작업대에서 업무 수행) |
| 포트 | 8100 |

## 2. 업무 도메인

### 2.1 기능 및 테스트케이스

| 기능 | 테스트케이스 |
| --- | --- |
| login | TC-01 정상 로그인, TC-02 세션 유지, TC-03 비밀번호 오류 처리 |
| search | TC-04 키워드 검색, TC-05 페이지네이션, TC-06 필터 조합 |
| payment | TC-07 카드 결제, TC-08 환불 금액 계산, TC-09 결제 취소 |
| report | TC-10 일별 리포트, TC-11 결제 합계 리포트, TC-12 CSV 내보내기 |

### 2.2 시뮬레이션 규칙 (결정적)

- 빌드(build)는 버전별 "숨은 결함 목록"을 가진다. 결함이 영향을 주는 테스트케이스는 FAIL 한다.
- 초기 결함(v1.0.0): `D-101`(TC-02), `D-102`(TC-05), `D-103`(TC-08)
- 회귀 규칙: `D-103` 수정 시 `D-201`(TC-11) 발생. 그 외 수정은 회귀 없음.
- 결함 수정: Dev가 이관받은 이슈의 테스트케이스에 영향을 주는 결함을 다음 빌드에서 제거한다.
- 버전 규칙: 최초 `1.0.0`, 이후 minor 증가(`1.1.0`, `1.2.0` …)
- 종료: 전체 PASS → 승인(APPROVED) / 최대 반복 5회 초과 → 실패(FAILED)

### 2.3 기대 결과

| 버전 | 결과 | 처리 |
| --- | --- | --- |
| 1.0.0 | 9 PASS / 3 FAIL | ISSUE-1,2,3 생성 → 이관 |
| 1.1.0 | 11 PASS / 1 FAIL | ISSUE-1~3 VERIFIED, ISSUE-4 생성 → 이관 |
| 1.2.0 | 12 PASS | ISSUE-4 VERIFIED, 사이클 APPROVED |

### 2.4 이슈 상태

`OPEN → TRANSFERRED → IN_PROGRESS → FIXED → VERIFIED`, 재현 시 `FIXED → REOPENED → TRANSFERRED`

### 2.5 LLM 활용 (mock 대체 가능)

| 주체 | 작업 | mock 동작 |
| --- | --- | --- |
| QA | 실패 로그 분석 → 이슈 제목·설명·심각도 | 템플릿 문구, 기능별 고정 심각도 (payment=Critical, login/report=Major, search=Minor) |
| Dev | 이슈별 수정 내역 작성 | 템플릿 문구 |
| Dev | 릴리즈 노트 작성 | 반영 이슈 목록 기반 템플릿 |

LLM 결과는 문서화 품질에만 영향을 주며, PASS/FAIL·버전·이슈 수는 시뮬레이터가 결정한다.

## 3. 기능 요구사항

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-01 | 상단 "데모 실행" 버튼으로 릴리즈 사이클(데모)을 백그라운드 실행 | 필수 |
| F-02 | Dev 서비스: 버전 발행(빌드 생성), 이슈 수신, 수정 후 차기 버전 발행 | 필수 |
| F-03 | QA 서비스: 빌드 검증(12개 테스트 실행), 이슈 생성/재오픈/검증 완료 처리, Dev 이관 | 필수 |
| F-04 | 사이클 상태 머신: `RELEASING → VERIFYING → TRANSFERRING → FIXING → … → APPROVED / FAILED` | 필수 |
| F-05 | 모든 업무 이벤트를 `events` 테이블에 기록하고 SSE로 대시보드에 실시간 전송 | 필수 |
| F-06 | 화면: Overview, Releases, Test Runs(상세 포함), Issues(상세·이력 포함), Timeline | 필수 |
| F-07 | 데이터 초기화(Reset) 기능. 진행 중인 사이클(데모·실습)이 있으면 거부(409)하고 버튼도 비활성화 | 필수 |
| F-08 | LLM 모드 표시(llm/mock) 및 LLM 오류 시 mock 자동 대체 | 필수 |
| F-09 | 진행 중인 사이클이 있으면 데모·실습 시작 거부(409) | 필수 |
| F-10 | 데모(자동 루틴)와 실습(사람이 진행) 두 가지 모드 제공. 동시 진행 불가 | 필수 |
| F-11 | 실습: 부서원 선택(작업자), 부서별 권한 검사 (검증 작업은 QA, 개발 작업은 Dev) | 필수 |
| F-12 | 실습: 검증 작업대 - 담당 항목 실행(참고 결과)·PASS/FAIL 판정, FAIL 시 이슈 등록 폼, 수정 이슈 확인/재현 | 필수 |
| F-13 | 실습: 검증 완료 - 모든 항목 판정 후 미해결 이슈가 있으면 Dev 이관, 없으면 버전 확정 | 필수 |
| F-14 | 실습: 개발 작업대 - 이슈 착수·수정 완료(수정 내역 폼), 수정 완료 이슈를 반영해 버전 발행 | 필수 |
| F-15 | 실습: 이슈·수정 내역·릴리즈 노트 AI 초안 (LLM / mock). 해당 부서 작업자만 요청 가능 (이슈 = QA, 수정 내역·릴리즈 노트 = Dev) | 선택 |
| F-16 | 실시간 갱신 중 입력 중인 폼 내용 보호 | 필수 |

## 4. 비기능 요구사항

- Python 3.12, uv 관리, `uv run run.py` 한 줄 실행
- SQLite 단일 파일(`data/app.db`), 기동 시 스키마 자동 생성
- 단계 진행이 눈에 보이도록 단계 간 지연(기본 0.4초, 테스트당 0.1초) 설정 가능
- UI: 모노톤(shadcn 스타일), 이모지 사용 금지, 아이콘 최소화, 다크 모드 대응

## 5. 아키텍처

```text
Browser ──HTTP/SSE──▶ FastAPI (8100)
                        ├─ web (routes, templates)
                        ├─ CycleRunner ──call──▶ DevService ─┐
                        │              ──call──▶ QAService  ─┤
                        ├─ Workbench (실습) ──call──▶ Dev/QA ─┤
                        ├─ EventBus (DB + SSE)               │
                        └─ SQLite app.db ◀───────────────────┘
```

## 6. 데이터 모델 (app.db)

| 테이블 | 주요 컬럼 |
| --- | --- |
| cycles | id, status, iteration, current_version, llm_mode(실행 당시 LLM 라벨), started_at, finished_at |
| releases | id, cycle_id, version, features, defects(숨은 결함), fixed_issue_keys, release_note, released_at |
| test_cases | id, name, feature |
| test_runs | id, cycle_id, version, total, passed, failed, status, started_at, finished_at |
| test_results | id, run_id, test_case_id, passed, log |
| issues | id, cycle_id, key, test_case_id, version_found, title, description, severity, status, fixed_in_version, fix_note, created_at, updated_at |
| issue_history | id, issue_id(`issues.id`), from_status, to_status, actor, note, ts |
| events | id, ts, cycle_id, actor, type, message, payload |
| members | name, dept(QA/Dev), role, features(담당 기능) |

실습 모드 추가 컬럼: `cycles.mode`(demo/manual), `releases.released_by`, `test_cases.assignee`,
`test_results.result(PENDING/PASS/FAIL)·assignee·sim_log·checked_by·checked_at`, `issues.reporter·assignee`

### 6.1 실습 모드 단계 규칙

| 상태 | 차례 | 가능한 작업 | 다음 상태 |
| --- | --- | --- | --- |
| RELEASING | Dev | 최초 버전 발행 | VERIFYING |
| VERIFYING | QA | 항목 실행·판정, 이슈 등록, 수정 확인/재현 → Dev 이관 / 버전 확정 | FIXING / APPROVED |
| FIXING | Dev | 이슈 착수·수정 완료, 수정 완료 이슈가 1건 이상이면 버전 발행 | VERIFYING |

- Dev 이관 조건: 판정 대기 0건, 수정 확인 대기 0건, 이관 대상 1건 이상 (OPEN/REOPENED 이슈 또는 일부 수정 발행으로 개발부서에 남은 TRANSFERRED/IN_PROGRESS 이슈)
- 이관 시 OPEN/REOPENED는 TRANSFERRED로 바꾸고, 개발부서 미완료 이슈는 상태·담당자를 그대로 둔 채 차례만 개발부서로 넘긴다
- 버전 확정 조건: 판정 대기 0건, 수정 확인 대기 0건, 미해결 이슈 0건
- FAIL 이슈 등록 시 같은 테스트케이스에 미해결 이슈(OPEN/TRANSFERRED/IN_PROGRESS/REOPENED)가 있으면 새 이슈 대신 그 이슈에 연결한다. 수정 확인 대기(FIXED) 이슈는 연결 대상이 아니며, 수정이 안 되었으면 "재현"으로 재오픈한다.

### 6.2 권한 규칙

| 작업 | 검사 |
| --- | --- |
| 실습 시작·중단, 데모 실행, 초기화 | 작업자 검사 없음 (릴리즈 매니저 조작. 인증이 없는 학습용 범위) |
| 검증 작업 (실행·판정·이슈 등록·수정 확인·이관·확정), 이슈 AI 초안 | 검증부서(QA) 작업자 |
| 개발 작업 (착수·수정 완료·발행), 수정 내역·릴리즈 노트 AI 초안 | 개발부서(Dev) 작업자 |

## 7. 학습 포인트 / 한계

- 업무 흐름이 코드에 하드코딩되어 있다 (데모 `CycleRunner`, 실습 단계 규칙 `Workbench`).
- Dev와 QA가 같은 프로세스·같은 DB를 공유한다 → 부서별 독립 운영·배포 불가.
- 다음 단계(API): 부서 시스템을 분리하고 HTTP로 통신한다.
