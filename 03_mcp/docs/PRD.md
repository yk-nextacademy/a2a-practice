# PRD — Stage 3. MCP (중앙 AI 에이전트가 부서 시스템을 도구로 사용)

## 1. 개요

| 항목 | 내용 |
| --- | --- |
| 목적 | Dev/QA 시스템을 MCP 서버로 노출하고, AI Host(Claude)가 도구를 발견·판단·호출하여 사이클을 수행한다 |
| 핵심 특징 | MCP Streamable HTTP, `tools/list` 기반 도구 발견, Resources로 조회 데이터 제공, LLM이 흐름 결정 |
| 포트 | Host(대시보드) 8300, Dev MCP Server 8301, QA MCP Server 8302 |

## 2. 업무 도메인

Stage 1과 동일한 시나리오·시뮬레이션 규칙을 사용한다 (요약).

- 12개 테스트케이스, 초기 결함 3건, `D-103` 수정 시 회귀 `D-201`
- 기대 결과: 1.0.0(9/3) → 1.1.0(11/1) → 1.2.0(12/0, 승인)
- **판단 주체의 변화**: 이슈 심각도 분류, 제목·설명 작성, 수정 내역·릴리즈 노트 작성, 승인 판단을 **Host LLM**이 수행하고 MCP 서버는 데이터 처리만 담당

## 3. 서비스별 요구사항

모든 도구는 사이클 단위로 동작하므로 `cycle_id`를 받는다 (`ack_work`만 `work_id`).

### 3.1 Dev MCP Server (8301, `data/dev.db`, 서버명 `dev-department`)

| 구분 | 이름 | 설명 |
| --- | --- | --- |
| tool (데모) | `publish_initial_release(cycle_id)` | 사이클의 최초 버전 1.0.0 발행 → version, build_url, features |
| tool | `register_tickets(cycle_id, issues)` | QA 이관 데이터를 티켓으로 등록 (수정 완료·반영된 기존 키는 재접수, 아직 발행에 반영되지 않은 RECEIVED/IN_PROGRESS 티켓은 상태·담당자 유지) |
| tool (데모) | `fix_ticket(cycle_id, issue_key, fix_note)` | 티켓 수정 완료 처리 |
| tool (데모) | `publish_release(cycle_id, release_note)` | 수정 완료 티켓을 반영한 차기 버전 발행 → version, build_url, fixed_issue_keys |
| tool | `list_tickets(cycle_id)` | 티켓 목록·상태 조회 |
| tool (실습) | `request_release(cycle_id)` | 개발부서원에게 발행 요청(작업 지시) |
| tool (실습) | `list_completed_work(cycle_id)` | 부서원이 끝낸 미확인 작업 (Host 코드가 폴링) |
| tool (실습) | `ack_work(work_id)` | 완료 작업 전달 확인 (멱등) |
| tool (Host 전용) | `cancel_work(cycle_id)` | 사이클 중단 시 진행 중 발행 요청 취소. LLM에는 노출하지 않음 |
| resource | `dev://cycles/{cycle_id}/releases`, `dev://cycles/{cycle_id}/tickets`, `dev://cycles/{cycle_id}/work` | 조회용 데이터 |
| http | `GET /builds/{cycle_id}/{version}` | 빌드 산출물 다운로드 (MCP 외 일반 HTTP) |

### 3.2 QA MCP Server (8302, `data/qa.db`, 서버명 `qa-department`)

| 구분 | 이름 | 설명 |
| --- | --- | --- |
| tool (데모) | `run_tests(cycle_id, version, build_url)` | 빌드 내려받아 12개 테스트 실행 → run_id, 요약, 실패 목록(로그 포함) |
| tool (데모) | `verify_fixed_issues(cycle_id, version, run_id, fixed_issue_keys)` | 수정 주장 이슈 확인 → VERIFIED / REOPENED |
| tool (데모) | `create_issue(cycle_id, version, test_case_id, title, description, severity)` | 신규 이슈 등록 (활성 이슈가 있으면 기존 키) |
| tool | `transfer_issues(cycle_id)` | OPEN/REOPENED 이슈를 이관 상태로 변경하고, 직전 발행에 반영되지 않은 TRANSFERRED(이월) 이슈와 함께 이관용 데이터로 반환 (재시도 시에도 같은 목록) |
| tool | `approve_release(cycle_id, version, run_id, comment)` | 릴리즈 승인 기록 |
| tool | `list_issues(cycle_id)` | 이슈 목록·상태 조회 |
| tool (실습) | `request_verification(cycle_id, version, build_url, fixed_issue_keys)` | 검증부서원에게 검증 요청, 검증 항목 12건 생성 |
| tool (실습) | `list_completed_work(cycle_id)` | 이관/확정 결정 중 미확인 작업 (Host 코드가 폴링) |
| tool (실습) | `ack_work(work_id)` | 완료 작업 전달 확인 (멱등) |
| tool (Host 전용) | `cancel_work(cycle_id)` | 사이클 중단 시 진행 중 검증 요청·검증 실행 취소. LLM에는 노출하지 않음 |
| resource | `qa://test-cases`, `qa://cycles/{cycle_id}/test-runs`, `qa://test-runs/{run_id}`, `qa://cycles/{cycle_id}/issues`, `qa://status` | 조회용 데이터 |

### 3.3 Host App (8300, `data/host.db`)

- 두 MCP 서버에 클라이언트 세션 연결, `tools/list`로 도구 자동 발견 → Claude `tools` 형식으로 변환
- Agent 루프: Claude가 시스템 프롬프트(업무 규정)에 따라 도구 호출 순서·인자를 결정 (최대 80턴)
- mock 모드: 규칙 기반 플래너가 동일한 도구 호출 순서를 재현
- 대시보드 데이터는 MCP **resources**를 읽어 구성
- 화면: Overview, Releases, Test Runs, Issues, Timeline, **Agent Trace**(LLM 응답 텍스트 → tool_use → tool_result), **Tools**(발견된 도구·스키마 목록)

## 4. 기능 요구사항 요약

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-01 | MCP 서버 2개가 Streamable HTTP로 독립 기동 | 필수 |
| F-02 | Host가 도구를 하드코딩하지 않고 `tools/list`로 발견 | 필수 |
| F-03 | LLM 모드에서 Claude가 스스로 사이클 완주 | 필수 |
| F-04 | mock 모드에서 동일 결과 | 필수 |
| F-05 | 모든 LLM 턴과 도구 호출을 host.db에 기록하고 Agent Trace 화면 제공 | 필수 |
| F-06 | 잘못된 도구 인자/오류는 `is_error` tool_result로 LLM에 반환 | 필수 |
| F-07 | Stage 1과 동일한 화면 구성 | 필수 |

## 5. 비기능 요구사항

- `uv run run.py`로 3개 프로세스 동시 기동
- LLM: `.env`의 `CLAUDE_MODEL`(기본 `claude-opus-5-5`), 키가 없으면 mock
- UI: 모노톤(shadcn 스타일), 이모지 금지

## 6. 아키텍처

```text
Browser ─▶ Host App (8300) ── Claude API
             │  MCP Client (Streamable HTTP)
             ├──────────▶ Dev MCP Server (8301) ── dev.db
             └──────────▶ QA MCP Server  (8302) ── qa.db
QA MCP ──GET /builds/{cycle_id}/{version}──▶ Dev MCP Server
```

## 6.1 3개 웹 서비스와 실습 모드 (추가 요구, 2026-10-15)

| 서비스 | 요구사항 |
| --- | --- |
| Hub 웹 = MCP Host (8300) | 관제(파이프라인, 실습 차례, 폴링·전달 횟수, Host+Dev+QA 병합 타임라인), Agent Trace(실행 라벨), MCP Tools, 데모 실행 / 실습 시작 / 중단 / 초기화 |
| Dev 웹 + Dev MCP (8301) | 개발 작업대(Host 발행 요청 처리, 티켓 착수·수정, 발행, AI 초안), 티켓, 타임라인. 조정 도구 `request_release`, `list_completed_work`, `ack_work`, Host 전용 `cancel_work` |
| QA 웹 + QA MCP (8302) | 검증 작업대(Host 검증 요청 처리, 실행·판정, 이슈 등록, 수정 확인, 이관/확정 결정, AI 초안), 검증 이력, 이슈, 타임라인. 조정 도구 `request_verification`, `list_completed_work`, `ack_work`, Host 전용 `cancel_work` |

| ID | 요구사항 | 우선순위 |
| --- | --- | --- |
| F-08 | 실습: Dev/QA는 서로를 모르고, 부서원이 끝낸 작업은 '완료 작업'으로 남는다 | 필수 |
| F-09 | 실습: Host 조정자가 2초마다 완료 작업을 폴링하고, 발견 시 AI(조정 프롬프트, 조정용 도구만)가 상대 시스템에 전달 후 ack | 필수 |
| F-10 | 실습: 차례가 아닌 부서의 작업 409, 타 부서원 403, Host 재시작 시 조정 재개 | 필수 |
| F-11 | 관찰: Dev/QA 이벤트를 Hub에 실시간 중계(업무 연결과 분리된 관찰 채널) | 필수 |
| F-12 | 실습: 필수 전달 도구가 모두 성공한 경우에만 완료 작업을 ack (실패 시 다음 폴링에서 재전달, 3회 실패 시 사이클 FAILED) | 필수 |
| F-13 | 실습: Host에서 사이클을 중단하면 `cancel_work`로 Dev/QA의 진행 중 작업 지시를 즉시 취소 (Host 종료·재시작 시에는 취소하지 않고 재개) | 필수 |
| F-14 | 실습: Dev가 일부 티켓만 수정해 발행해도 진행 가능. QA는 반영되지 않은 이관 이슈를 다시 이관할 수 있고(이관 버튼 활성), Dev는 해당 티켓의 상태·담당자를 유지 | 필수 |

## 7. 학습 포인트 / 한계

- 도구 **자동 발견**과 스키마 공유: Host는 엔드포인트를 몰라도 된다.
- 흐름 결정 주체가 코드 → **LLM 판단**으로 이동했다.
- 그러나 구조는 **에이전트 1개 → 도구 N개(수직)**. Dev/QA 시스템은 스스로 판단하지 않는 수동적 도구다.
  중앙 LLM이 모든 세부 단계(테스트 실행, 이슈 하나하나 등록)를 직접 지시해야 한다.
- 다음 단계(A2A): 각 부서가 자기 에이전트를 갖고, 내부를 숨긴 채 **업무 단위**로 서로 위임한다.
