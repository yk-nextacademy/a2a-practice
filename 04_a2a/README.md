# Stage 4. A2A — Dev Agent ↔ Hub ↔ QA Agent

개발부서와 검증부서가 각자 **자율 에이전트**를 운영하고, **Hub(Orchestrator)** 가 A2A 프로토콜로 업무를 위임·라우팅하며 전체 흐름을 관제하는 단계입니다.
Dev와 QA는 서로를 모릅니다. Hub도 에이전트 내부(LLM, DB, 테스트 방법)를 모르고, **Agent Card**에 공개된 스킬과 **Task / Artifact**로만 협업합니다.

![Overview](docs/images/overview.png)

## 실행

```powershell
cd 04_a2a
uv sync
uv run run.py                 # Hub http://localhost:8400
uv run run.py --mock          # LLM 없이 실행 (Hub 판단, Dev/QA 문서 작성 모두 규칙 기반)
uv run run.py --only qa_agent # 특정 서비스만 실행 (장애 실습용)
```

| 웹 서비스 | 주소 | 역할 |
| --- | --- | --- |
| Hub 웹 (Orchestrator) | http://localhost:8400 | 관제, 에이전트 레지스트리, 업무 위임·라우팅, 판단, 승인함, A2A 감사 로그 |
| Dev 웹 + Dev Agent | http://localhost:8401 (A2A `/a2a`, Agent Card) | 발행과 위임받은 결함 수정. 스킬 `release-build`, `fix-and-release` |
| QA 웹 + QA Agent | http://localhost:8402 (A2A `/a2a`, Agent Card) | 검증, 이관 또는 확정. 스킬 `verify-release` (Streaming, input-required) |

## 진행 방식

| 방식 | 시작 | 에이전트 내부 | Hub가 보는 것 |
| --- | --- | --- | --- |
| 데모 | Hub 웹 **데모 실행** | 에이전트가 LLM·규칙으로 스스로 처리 | Task, 진행 메시지, Artifact |
| 실습 | Hub 웹 **실습 시작** | 에이전트 뒤에서 **부서원**이 Dev 웹·QA 웹으로 처리 | **똑같이** Task, 진행 메시지, Artifact |

Hub는 데모와 실습을 구분하지 않습니다. 같은 스킬로 Task를 위임하고(`mode`만 전달), 결과를 같은 방식으로 받습니다. 에이전트 내부는 불투명(opaque)합니다.

### 실습 흐름

```text
Hub      Task [release-build] ───────────────▶ Dev Agent: Task working "개발부서원의 발행을 기다립니다"
Dev 웹   [최개발] v1.0.0 발행 ─────────────────▶ Task completed + Artifact release
Hub      Task [verify-release] (SSE) ────────▶ QA Agent: 검증 항목 12건 '대기', Task working
QA 웹    [김검증·이검증·박검증] 판정할 때마다 ──▶ 상태 메시지 "TC-07 PASS 판정 (이검증)" + metadata.progress (Hub 진행률)
         └ 이관 결정 → Task completed + Artifact test-report, issues
         └ 확정 결정 → Task input-required "릴리즈 승인 여부를 회신해 주세요"
Hub      (이관) 판단 transfer_to_dev → Task [fix-and-release] ─▶ Dev Agent: 티켓 접수, Task working
Dev 웹   착수·수정 완료 → 상태 메시지 "ISSUE-1 수정 완료 (최개발)" → 발행 → Task completed + Artifact release
Hub      (확정) 판단 request_approval → 승인함 → 릴리즈 매니저 승인 → 같은 Task로 회신 → Artifact approval
Hub      사이클 중단 → 진행 중인 Task에 CancelTask → 에이전트가 부서원 대기를 취소, 사이클 CANCELLED, 승인 대기도 CANCELLED
```

- 사람이 오래 걸리는 일도 **Task 하나**로 표현됩니다. 진행 상황은 에이전트가 **먼저 알려 줍니다**(스트리밍). Stage 3의 폴링과 비교해 보세요.
- Dev가 일부 티켓만 수정해 발행해도 됩니다. 미수정 티켓은 QA 검증 후 다시 이관(재위임)되어 다음 버전에서 처리합니다.
- 부서원 명부는 각 에이전트가 따로 가집니다. `http://localhost:8402/?as=김검증` 처럼 링크로 작업자를 지정할 수 있습니다.

| Hub: QA 차례 (진행 스트리밍) | Hub: Dev 차례 |
| --- | --- |
| ![Hub manual QA](docs/images/hub_manual_qa.png) | ![Hub manual Dev](docs/images/hub_manual_dev.png) |

| QA 웹 | Dev 웹 |
| --- | --- |
| ![QA web](docs/images/qa_web.png) | ![Dev web](docs/images/dev_web.png) |

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_MODE` | `auto` | `auto` / `llm` / `mock` (세 서비스 공통) |
| `AGENT_URLS` | `http://127.0.0.1:8401,http://127.0.0.1:8402` | Hub가 Agent Card를 수집할 주소. Hub가 에이전트에 대해 아는 유일한 정보 |
| `STEP_DELAY`, `TEST_DELAY` | `0.4`, `0.1` | 흐름 관찰용 지연(초) |

## 구조

```text
04_a2a/
├─ run.py
├─ docs/
├─ data/                       # dev.db, qa.db, hub.db
└─ src/
   ├─ common/                  # 설정, SQLite(+마이그레이션), EventBus, LLM, A2A 파트 헬퍼, 실습 공통, 웹 공통 계층
   ├─ dev_agent/
   │  ├─ catalog.py            # 제품·결함·빌드 산출물
   │  ├─ agent.py              # DevWork(데모 처리·부서원 작업·Task 대기 연결) + DevAgentExecutor
   │  ├─ server.py             # Agent Card + JSON-RPC(/a2a) + Dev 웹 + /builds
   │  └─ templates/            # layout, desk(개발 작업대), tickets, ticket_detail
   ├─ qa_agent/
   │  ├─ catalog.py            # 테스트케이스, 판정 규칙
   │  ├─ agent.py              # QAWork(데모 검증·부서원 작업·진행률 스트리밍) + QAAgentExecutor(input-required 승인)
   │  ├─ server.py             # Agent Card + JSON-RPC(/a2a) + QA 웹
   │  └─ templates/            # layout, desk(검증 작업대), runs, test_run_detail, issues, issue_detail
   └─ hub/
      ├─ registry.py           # Agent Card 수집(15초 주기), 스킬 → 에이전트 라우팅
      ├─ audit.py              # httpx 전송 계층 래퍼: A2A 원문(JSON-RPC, SSE) 감사 로그
      ├─ workflow.py           # 사이클 상태 머신, 위임(_delegate), Artifact → 현황 반영, 승인 대기
      ├─ decider.py            # 다음 행동 판단 (Claude 구조화 출력 / 규칙)
      ├─ store.py              # hub.db 스키마
      ├─ app.py                # 대시보드, 승인 API
      └─ templates/            # + approvals, agents, messages
```

## 협업 흐름

```text
Hub  : Agent Card 수집 → 스킬 라우팅 (release-build, fix-and-release → Dev / verify-release → QA)
Hub → Dev : Task "사이클 N의 최초 버전을 발행해 주세요"           → Artifact release {cycle_id, version, build_url, features, fixed_issue_keys, release_note}
Hub → QA  : Task "v1.0.0을 검증해 주세요"  (SSE 스트리밍)
            status: working "TC-05 FAIL (5/12)" ...             → Artifact test-report, issues
Hub  : 판단 transfer_to_dev (Claude가 근거 기록)
Hub → Dev : Task "이슈 3건을 수정하고 차기 버전을 발행해 주세요"   → Artifact release {fixes[]}
...  (v1.1.0 회귀 1건 → 반복)
Hub → QA  : Task "v1.2.0을 검증해 주세요"                          → status: input-required "승인 여부를 회신해 주세요"
Hub  : 판단 request_approval + 승인자용 요약 → 승인함에 표시
사람 : 승인 / 반려
Hub → QA  : 같은 Task(taskId, contextId)로 회신 "approve"          → Artifact approval → completed
```

## A2A 개념과 구현 위치

| 개념 | 구현 |
| --- | --- |
| Agent Card | `dev_agent/server.py`, `qa_agent/server.py` 의 `agent_card()` — 스킬, 엔드포인트, `streaming=true` |
| 발견·라우팅 | `hub/registry.py` — 주소만 알고 카드를 읽어 스킬 ID로 에이전트 선택 |
| Task 라이프사이클 | `submitted → working → completed / input-required / failed / rejected / canceled` (`TaskUpdater`). rejected는 지원하지 않는 skill·빈 이슈, canceled는 CancelTask |
| Task 재구독 | Hub 재시작 시 `GetTask`로 마지막 Task를 확인하고, 진행 중이면 `SubscribeToTask`로 스트림에 다시 붙음 (`hub/workflow.py` `_resume`) |
| Message / Part | TextPart(지시문) + DataPart(구조화 입력) — `common/a2a_parts.py` |
| Artifact | `release`, `test-report`, `issues`, `approval` |
| Streaming | QA 테스트 진행률을 상태 메시지 + metadata로 전송, Hub가 SSE로 받아 대시보드에 중계 |
| input-required | QA가 승인 여부를 되묻고 Task를 멈춤 → Hub 승인함 → 같은 Task로 회신하면 재개 |
| contextId | 사이클 단위로 `cycle-N`을 지정해 관련 Task를 묶음 |

![Approvals](docs/images/approvals.png)

## Hub의 책임

| 기능 | 내용 |
| --- | --- |
| Agent Registry | Agent Card 수집·상태 표시 (**Agents** 화면) |
| Workflow | 사이클 상태 머신(`RELEASING → VERIFYING → TRANSFERRING → FIXING → … → AWAITING_APPROVAL → APPROVED / REJECTED / FAILED / CANCELLED`)을 hub.db에 저장. Hub 재시작 시 에이전트에 남은 Task에 다시 연결해 이어 가고, 에이전트 재시작으로 Task가 사라졌으면 사유를 남기고 FAILED |
| 판단 | 각 검증 결과 후 다음 행동을 Claude가 결정. 선택지는 상태에 맞게 제한 (QA가 묻지 않았는데 승인 요청 불가) |
| Human-in-the-loop | 승인함에서 승인/반려 + 의견 → QA Task 재개 |
| 감사 로그 | 모든 A2A 요청/응답 원문 (**Message Trace** 화면) |
| 업무 현황 | Release / Test Run / Issue 화면은 에이전트 Artifact만으로 구성 (부서 DB 직접 접근 없음) |

![Message Trace](docs/images/message_trace.png)

## 점검 결과

| 항목 | 결과 |
| --- | --- |
| A2A 직접 호출 (Hub 없이 클라이언트로) | Agent Card 조회, Task, 스트리밍, Artifact, input-required 재개 모두 정상 |
| mock 모드 | 9/3 → 11/1 → 12/0, 이슈 4건 VERIFIED, Task 6개, 승인 후 APPROVED |
| LLM 모드 (`claude-opus-5-5`) | 동일 결과, 약 50초. Hub 판단 3회 + 에이전트 문서 작성 모두 Claude |
| 반려 | 사이클 REJECTED, QA의 approval Artifact와 test run에 반려·의견 기록 |
| QA Agent 중지 후 시작 | Agent Card 수집 실패 → "verify-release 스킬을 제공하는 에이전트가 없습니다"로 FAILED |
| 실습 (mock / LLM, 부서원 작업은 API로 수행) | v1.0.0 → v1.1.0(회귀) → v1.2.0, QA 결정 이관 → 이관 → 확정 → Hub 승인 → APPROVED. Task 6개 모두 completed, LLM 모드는 Hub 판단 3회 Claude |
| 실습 중 차례·권한 | 위임받지 않은 부서의 작업 409, 타 부서원 403 |
| 실습 중 사이클 중단 | Hub가 `CancelTask` 전송 → Dev Agent의 부서원 대기 취소, 이후 발행 시도 409 |
| 화면 | Hub 8개, Dev 4개(+Agent Card), QA 6개(작업대, 검증 이력, 검증 상세, 이슈, 이슈 상세, 타임라인 +Agent Card) 정상 |
| 결함 수정 (2026-10-16) | 부분 수정 발행 후 교착 해소, 승인 대기 중 중단 시 승인 CANCELLED, 중단은 항상 CANCELLED, Hub 재시작 시 사이클 이어 가기 — 재현·검증 결과는 `docs/04_A2A_개발완료보고서.md` 12장 |

Hub(Claude)가 승인자에게 남긴 요약 예:
> 통과 테스트 수는 v1.0.0 9/12, v1.1.0 11/12를 거쳐 v1.2.0에서 12/12가 되었고, ISSUE-1~4는 모두 수정 후 검증을 마쳤습니다.
> 다만 두 Critical 이슈가 모두 금액 계산 정합성 문제이고 ISSUE-4는 이번 회차에서야 해소되었으므로, 운영 반영 후 결제·환불 금액을 대사로 모니터링할 것을 권고합니다.

## 학습 포인트

- **MCP Host vs A2A Hub**: Stage 3의 Host는 `run_tests`, `create_issue`처럼 **세부 함수**를 하나씩 호출했다(도구 호출 21회).
  Stage 4의 Hub는 "v1.1.0 검증해 줘"처럼 **업무 목표**만 위임한다(Task 6개). 테스트 방법, 이슈 분석, 수정 방식은 각 에이전트가 결정한다.
- **불투명성(opacity)**: Hub는 QA가 LLM을 쓰는지, 어떤 DB를 쓰는지 모른다. QA Agent를 다른 구현으로 바꿔도 같은 Agent Card 스킬만 제공하면 Hub 코드는 그대로다.
- **장시간 작업과 사람 개입이 표준**: 진행 상황은 Task 상태 스트리밍으로, 승인 요청은 `input-required` 상태로 표현된다. Stage 2였다면 Webhook과 별도 API를 직접 설계해야 했다.
- **사람이 일해도 같은 프로토콜**: 실습에서 부서원이 몇 분 동안 판정해도 Hub 입장에서는 working 상태의 Task 하나다. Stage 3은 같은 상황에서 Host가 2초마다 폴링해야 했다.
- **관찰의 한계도 보인다**: Hub는 진행 메시지는 실시간으로 받지만, 업무 데이터(검증 결과, 이슈)는 Artifact가 도착해야 화면에 반영된다.
- **허브 구조의 트레이드오프**: 관제·감사·승인을 한곳에서 할 수 있지만, Hub가 단일 장애점이고 흐름 지식이 Hub에 모인다.
  A2A 자체는 토폴로지를 강제하지 않으므로 Dev ↔ QA 직접 협업(Peer) 구성도 가능하다.

![Agents](docs/images/agents.png)
