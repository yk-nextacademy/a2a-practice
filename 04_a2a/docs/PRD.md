# PRD — Stage 4. A2A (Dev Agent ↔ Hub ↔ QA Agent)

## 1. 개요

| 항목 | 내용 |
| --- | --- |
| 목적 | 부서별 자율 에이전트가 A2A 프로토콜로 업무(Task)를 주고받고, Hub가 전체 흐름을 관제한다 |
| 핵심 특징 | Agent Card 기반 발견, Task 라이프사이클, Artifact, Streaming, `input-required`(사람 승인), 감사 로그 |
| 포트 | Hub(대시보드) 8400, Dev Agent 8401, QA Agent 8402 |

## 2. 업무 도메인

Stage 1과 동일한 시나리오·시뮬레이션 규칙을 사용한다 (요약).

- 12개 테스트케이스, 초기 결함 3건, `D-103` 수정 시 회귀 `D-201`
- 기대 결과: 1.0.0(9/3) → 1.1.0(11/1) → 1.2.0(12/0) → **사람 승인 후** APPROVED
- 판단의 분산: 이슈 분석은 QA Agent 내부 LLM, 수정·릴리즈 노트는 Dev Agent 내부 LLM, 다음 행동 결정은 Hub LLM

## 3. 서비스별 요구사항

### 3.1 Dev Agent (8401, `data/dev.db`)

- A2A Server. Agent Card 공개 (`/.well-known/agent-card.json`)
- Skill
  - `release-build`: 신규 사이클의 최초 버전 발행
  - `fix-and-release`: 이관된 이슈 수정 후 차기 버전 발행
- 입력: Message(TextPart 지시문 + DataPart `{skill, cycle_id, mode, issues?}`), `mode`는 `demo`/`manual`
- 출력: Artifact `release` (TextPart 릴리즈 노트 + DataPart `{cycle_id, version, build_url, features, fixed_issue_keys, release_note, fixes[]}`, `fixes`는 `fix-and-release`만)
- Task 진행 중 상태 메시지(`working`)로 "ISSUE-1 수정 중" 등 진행 상황 전달
- 일반 HTTP: `GET /builds/{cycle_id}/{version}` 빌드 산출물(사이클마다 같은 버전명이 반복되므로 cycle_id 포함), `GET /health`

### 3.2 QA Agent (8402, `data/qa.db`)

- A2A Server, Agent Card 공개
- Skill
  - `verify-release`: 빌드 검증, 수정 이슈 확인, 신규 이슈 분석·등록
- 입력: DataPart `{skill: "verify-release", cycle_id, version, build_url, fixed_issue_keys, mode}` (`skill`이 없거나 다르면 `rejected`)
- 처리
  - Streaming: 테스트 진행률을 `working` 상태 메시지로 전송 (예: "TC-05 FAIL (5/12)")
  - 실패 존재: Artifact `test-report` + `issues`(이관 대상) 반환 후 `completed`
  - 전체 PASS: Artifact `test-report` 반환 후 **`input-required`**("릴리즈 승인 여부를 회신해 주세요") → 같은 Task로 승인/반려 회신 수신 → `completed` (Artifact `approval`)
- 일반 HTTP: `GET /health`

### 3.3 Hub / Orchestrator (8400, `data/hub.db`)

| 기능 | 요구사항 |
| --- | --- |
| Agent Registry | 설정된 에이전트 URL에서 Agent Card를 수집·저장, 스킬 기반 라우팅, 상태(health) 표시 |
| Workflow Engine | 사이클 상태 머신(`RELEASING → VERIFYING → TRANSFERRING → FIXING → … → AWAITING_APPROVAL → APPROVED / REJECTED / FAILED / CANCELLED`) DB 저장. Hub 재시작 시 에이전트에 남은 Task를 GetTask·SubscribeToTask로 다시 연결해 이어 간다 |
| 판단 | QA 결과를 보고 다음 행동 결정(LLM 구조화 출력 / mock 규칙): `transfer_to_dev`, `request_approval`, `abort` |
| Human-in-the-loop | QA의 `input-required`를 승인함(Approvals)에 표시, 사용자 승인/반려를 같은 Task로 회신 |
| 관제 | 에이전트 Streaming 이벤트를 수신해 대시보드로 SSE 중계 |
| 감사 로그 | 모든 A2A 요청/응답(JSON-RPC 본문), Task 상태 전이를 기록 |
| 데이터 집계 | Release/Test Run/Issue 화면은 에이전트가 반환한 **Artifact만으로** 구성 (부서 DB 직접 접근 금지) |

- 화면: Overview, Releases, Test Runs, Issues, Timeline, **Agents**(Agent Card), **Message Trace**(A2A 송수신), **Approvals**(승인함)

## 4. 기능 요구사항 요약

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-01 | Dev/QA Agent가 A2A Server로 독립 기동, Agent Card 공개 | 필수 |
| F-02 | Hub가 Agent Card로 에이전트를 발견하고 스킬로 라우팅 | 필수 |
| F-03 | Dev와 QA는 서로를 모르고 Hub를 통해서만 협업 | 필수 |
| F-04 | Task/Message/Artifact(TextPart + DataPart)로 업무 데이터 교환 | 필수 |
| F-05 | QA 검증 Streaming 진행률이 대시보드에 실시간 표시 | 필수 |
| F-06 | `input-required` → 사용자 승인 → Task 재개 | 필수 |
| F-07 | 모든 A2A 트래픽 감사 로그 + Message Trace 화면 | 필수 |
| F-08 | LLM / mock 모드 (Hub, Dev, QA 각각) | 필수 |
| F-09 | (선택) Hub 자체 Agent Card 공개(`run-release-cycle`) | 선택 |
| F-10 | (선택) Peer 모드: Dev ↔ QA 직접 협업 | 선택 |

## 4.1 3개 웹 서비스와 실습 모드 (추가 요구, 2026-10-15)

| 서비스 | 요구사항 |
| --- | --- |
| Hub 웹 (8400) | 관제(실습 차례, 진행 중 Task의 최근 진행 메시지, 승인 대기 배너), Approvals, Agents, Message Trace, 데모 실행 / 실습 시작 / 사이클 중단(CancelTask) / 초기화 |
| Dev 웹 + Dev Agent (8401) | 개발 작업대(위임 업무 처리, 티켓 착수·수정, 발행, AI 초안), 티켓, 타임라인 |
| QA 웹 + QA Agent (8402) | 검증 작업대(위임 검증 처리, 실행·판정, 이슈 등록, 수정 확인, 이관/확정 결정, AI 초안), 검증 이력, 이슈, 타임라인 |

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-11 | 실습: Hub는 데모와 같은 스킬로 Task를 위임하고 DataPart에 `mode: manual`만 추가한다 | 필수 |
| F-12 | 실습: 에이전트는 Task를 working으로 유지한 채 부서원 작업을 기다리고, 작업마다 상태 메시지(QA 판정은 metadata.progress 포함)를 스트리밍한다 | 필수 |
| F-13 | 실습: Dev 발행 → release Artifact로 완료, QA 이관 → test-report·issues Artifact로 완료, QA 확정 → input-required로 Hub 승인 요청 | 필수 |
| F-14 | 실습: Hub 사이클 중단 시 진행 중 Task에 CancelTask, 에이전트는 대기 취소, 사이클은 항상 CANCELLED, 승인 대기는 CANCELLED로 닫음 | 필수 |
| F-16 | 실습: Dev가 일부 티켓만 수정해 발행해도 QA는 미수정 이슈(TRANSFERRED)를 다시 이관할 수 있어 사이클이 막히지 않는다 | 필수 |
| F-17 | Hub 재시작 시 진행 중 사이클을 이어 가고, 에이전트 재시작으로 Task가 사라지면 사유를 남기고 FAILED | 필수 |
| F-15 | 실습: 위임받지 않은 부서 작업 409, 타 부서원 403, 부서원 명부는 에이전트별 | 필수 |

## 5. 비기능 요구사항

- `uv run run.py`로 3개 프로세스 동시 기동
- 공식 `a2a-sdk` 사용
- UI: 모노톤(shadcn 스타일), 이모지 금지

## 6. 아키텍처

```text
Browser ─▶ Hub (8400) ─ hub.db ─ Claude(판단)
            │ Registry · Workflow · Audit · Approvals
            │ A2A Client
            ├──────────▶ Dev Agent (8401) ─ dev.db ─ Claude(수정/노트)
            └──────────▶ QA Agent  (8402) ─ qa.db  ─ Claude(이슈 분석)
QA Agent ──GET /builds/{cycle_id}/{version}──▶ Dev Agent   (build_url, 산출물 다운로드)
```

## 7. 학습 포인트

- **MCP Host vs A2A Hub**: MCP Host는 세부 함수를 하나씩 호출한다. A2A Hub는 "1.1.0 검증해줘" 같은 **업무 목표**를 위임하고,
  방법(어떤 테스트를 어떻게 실행할지)은 QA Agent가 결정한다. Hub는 QA 내부(DB, LLM, 도구)를 모른다(opaque).
- 장시간 작업은 Task 상태와 Streaming으로, 사람 개입은 `input-required` 표준 상태로 표현된다.
- 허브 구조의 장점(관제·감사·승인)과 단점(단일 장애점, 흐름 지식 집중)을 함께 이해한다.
