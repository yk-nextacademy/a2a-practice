# Stage 3. MCP 개발 완료 보고서

> AI Host가 부서 시스템을 MCP 도구로 연결하는 단계 — Hub 웹(MCP Host) + Dev MCP 서버 + QA MCP 서버

---

## 0. 문서 정보

| 항목 | 내용 |
| --- | --- |
| 단계 | Stage 3 — MCP (Model Context Protocol) |
| 폴더 | `03_mcp/` |
| 포트 | Hub 웹 = MCP Host `8300`, Dev 웹 + Dev MCP 서버 `8301` (MCP 엔드포인트 `/mcp`), QA 웹 + QA MCP 서버 `8302` (MCP 엔드포인트 `/mcp`). 환경변수 `HOST_PORT` / `DEV_PORT` / `QA_PORT`로 변경 가능 |
| 작성일 | 2026-10-16 |
| 상태 | 완료 (구현·점검 완료. 2026-10-16 결함 수정·알려진 이슈 정리·재점검 반영) |
| 실행 환경 | Python 3.12, uv, `mcp` SDK 2.2.0 (`MCPServer`, `Client`, Streamable HTTP), `anthropic` 1.14.0, 모델 `claude-opus-5-5` (effort `low`) |
| 관련 문서 | [`03_mcp/README.md`](../03_mcp/README.md), [`03_mcp/docs/PRD.md`](../03_mcp/docs/PRD.md), [`03_mcp/docs/TODO.md`](../03_mcp/docs/TODO.md), [`03_mcp/docs/USER_SCENARIO.md`](../03_mcp/docs/USER_SCENARIO.md), 이전 단계 [`02_api/README.md`](../02_api/README.md), 다음 단계 [`04_a2a/README.md`](../04_a2a/README.md) |

본 보고서의 기술 내용은 **소스 코드**(`03_mcp/src/...`)를 기준으로 작성했습니다. 2026-10-16에 단계 문서(README, PRD, TODO, USER_SCENARIO)를 코드에 맞게 갱신했으며, 정리 내역은 13.3절에 있습니다.

---

## 1. 요약

Stage 3는 개발부서(Dev)·검증부서(QA) 시스템을 **MCP 서버**로 노출하고, **Hub 웹이 MCP Host**가 되어 Claude(또는 규칙 기반 mock)가 두 서버의 도구를 **자동 발견(`tools/list`)** 하고 **호출(`tools/call`)** 해 릴리즈 사이클을 진행하는 단계입니다.

핵심 결과는 다음과 같습니다.

- **세 개의 독립 웹 서비스**: Hub(8300), Dev(8301), QA(8302). 각각 자기 SQLite DB(`data/host.db`, `dev.db`, `qa.db`)를 가집니다.
- **Dev와 QA는 서로를 모릅니다.** 두 시스템을 잇는 유일한 주체는 Host의 AI입니다. (QA가 Dev에 접근하는 유일한 지점은 Host가 넘겨준 `build_url`로 빌드 산출물을 내려받는 일반 HTTP GET뿐입니다.)
- **Dev/QA 프로세스는 이중 얼굴**을 가집니다. 같은 DB 위에 **사람을 위한 웹 화면**(작업대)과 **AI를 위한 MCP 도구**(`/mcp`)를 함께 제공합니다.
- **두 가지 진행 방식**
  - **데모**: Host의 Claude가 시스템 프롬프트(업무 절차)를 읽고 21회의 MCP 도구 호출로 발행 → 테스트 → 이슈 작성 → 이관 → 수정 → 재발행 → 승인까지 모두 직접 수행합니다. (mock 모드에서는 `MockPlanner`가 같은 순서를 코드로 재현)
  - **실습**: 사람이 Dev 웹·QA 웹에서 실제 업무를 합니다. Host는 2초마다 양쪽의 `list_completed_work`를 **코드로 폴링**합니다. 완료 작업을 발견하면 **조정자 Claude**(조정 프롬프트 + 조정용 도구 9개만 노출) 또는 `MockCoordinator`가 상대 시스템에 결과를 전달하고, 필수 도구가 모두 성공했을 때만 `ack_work`로 확인 처리합니다.
- **결과 재현성**: 판정(PASS/FAIL)은 결정적 시뮬레이터가 내리고, LLM은 이슈 제목·설명, 수정 내역, 릴리즈 노트 같은 **내용만** 작성합니다. 따라서 LLM/mock 어느 쪽이든 v1.0.0 9/3 → v1.1.0 11/1 → v1.2.0 12/0 → 승인, ISSUE-1~4 VERIFIED라는 같은 결과가 나옵니다.
- **측정치**(2026-10-15, 지연 0 기준): mock 데모 약 1초, Claude 데모 약 48초(15턴), 도구 호출 21회. Claude 조정자 실습은 전달 7회 · 22턴으로 완주했습니다.
- **2026-10-16 수정과 재점검**: Hub "실습 시작" 버튼 무반응, 중단 후 남는 작업 지시, 전달 실패와 무관한 ack, 일부 티켓만 수정해 발행하면 생기는 교착 등 13.2절의 코드 이슈를 수정했습니다. 격리 사본에서 mock 데모, 실제 브라우저(Playwright + Edge) 클릭 실습, 중단 직후 작업대 상태, Host 재시작 재개, 부분 수정 시나리오를 모두 시험해 기대 결과를 확인했습니다(12장).

학습 관점의 결론은 **"MCP는 에이전트 → 도구(수직) 연결이다"** 입니다. 도구 발견과 스키마 공유로 통합 비용은 크게 줄었지만, Dev/QA는 스스로 판단하지 않는 수동적 도구이고, 사람의 장시간 작업은 요청-응답 단위의 도구 호출로 표현할 수 없어 Host가 폴링해야 합니다. 이 한계가 Stage 4(A2A)의 동기가 됩니다.

---

## 2. 기술 배경 — MCP 입문

### 2.1 MCP란 무엇인가

**MCP(Model Context Protocol)** 는 LLM 애플리케이션이 외부 시스템(파일, DB, 업무 시스템, SaaS 등)의 **기능과 데이터**를 표준 방식으로 사용할 수 있게 하는 개방형 프로토콜입니다. 메시지 형식은 **JSON-RPC 2.0**이며, "AI 애플리케이션을 위한 USB-C 포트"라는 비유로 자주 설명됩니다. 한 번 MCP 서버로 만들어 두면 MCP를 지원하는 어떤 AI 애플리케이션에서도 그 기능을 쓸 수 있습니다.

### 2.2 왜 필요한가 — N×M 통합 문제

MCP 이전에는 AI 애플리케이션마다, 외부 시스템마다 전용 연동 코드를 따로 작성해야 했습니다.

```text
[MCP 이전]  AI 앱 N개 x 외부 시스템 M개 = N x M 개의 전용 연동 코드

  AI 앱 A ──┬── 연동 A-1 ──▶ 시스템 1
            ├── 연동 A-2 ──▶ 시스템 2
            └── 연동 A-3 ──▶ 시스템 3
  AI 앱 B ──┬── 연동 B-1 ──▶ 시스템 1
            ├── 연동 B-2 ──▶ 시스템 2
            └── ...

[MCP 이후]  AI 앱은 MCP 클라이언트 1개, 시스템은 MCP 서버 1개 = N + M

  AI 앱 A (MCP Client) ─┐            ┌─ MCP Server ── 시스템 1
  AI 앱 B (MCP Client) ─┼── MCP ─────┼─ MCP Server ── 시스템 2
  AI 앱 C (MCP Client) ─┘  (표준)    └─ MCP Server ── 시스템 3
```

각 도구의 이름·설명·입력 스키마를 서버가 **스스로 설명(self-describing)** 하므로, 클라이언트는 엔드포인트나 파라미터를 하드코딩할 필요가 없습니다.

### 2.3 구성 요소: Host / Client / Server

| 구성 요소 | 역할 | 이 프로젝트에서 |
| --- | --- | --- |
| **Host** | 사용자와 LLM이 있는 AI 애플리케이션. 하나 이상의 Client를 만들고, LLM에 도구를 제공하며, 도구 호출을 중계·통제한다 | Hub 웹(8300) — `host/app.py`, `host/agent.py` |
| **Client** | Host 안에서 **서버 하나당 하나씩** 생성되는 연결 객체. 세션 수립, 능력 협상, 요청 송수신 | `host/mcp_hub.py`의 `McpConnection` (서버별 `mcp.Client` 장기 세션) |
| **Server** | 기능(tools)·데이터(resources)·프롬프트(prompts)를 노출하는 프로그램 | Dev MCP(`dev_mcp/server.py`), QA MCP(`qa_mcp/server.py`) |

### 2.4 서버가 제공하는 세 가지 기본 요소

| 요소 | 제어 주체 | 의미 | 대표 메서드 | 이 프로젝트 |
| --- | --- | --- | --- | --- |
| **Tools** | 모델(LLM)이 호출 결정 | 부수 효과가 있을 수 있는 **동작**. 이름, 설명, JSON Schema 입력 | `tools/list`, `tools/call` | Dev 9개, QA 10개 (Host 전용 `cancel_work` 포함, 7장) |
| **Resources** | 애플리케이션이 읽기 결정 | URI로 식별되는 **읽기 전용 데이터** (URI 템플릿 지원) | `resources/list`, `resources/templates/list`, `resources/read` | `dev://cycles/{id}/releases` 등 8종. Hub 대시보드가 사용 |
| **Prompts** | 사용자가 선택 | 재사용 가능한 프롬프트 템플릿 | `prompts/list`, `prompts/get` | 사용하지 않음 (시스템 프롬프트는 Host가 보유) |

반대 방향(서버 → 클라이언트)의 기능으로 sampling(서버가 Host의 LLM 사용 요청), elicitation(사용자 입력 요청), roots 등도 있으나 본 단계에서는 사용하지 않았습니다.

### 2.5 기본 메시지 흐름

```text
Client                                         Server
  │  initialize {protocolVersion, capabilities}  │
  │─────────────────────────────────────────────▶│
  │  result {serverInfo, capabilities,           │
  │          instructions}                       │
  │◀─────────────────────────────────────────────│
  │  notifications/initialized                   │
  │─────────────────────────────────────────────▶│
  │  tools/list                                  │
  │─────────────────────────────────────────────▶│
  │  result {tools: [{name, description,         │
  │                   inputSchema}, ...]}        │
  │◀─────────────────────────────────────────────│
  │  tools/call {name, arguments}                │
  │─────────────────────────────────────────────▶│
  │  result {content: [...], structuredContent,  │
  │          isError}                            │
  │◀─────────────────────────────────────────────│
```

- `tools/list`: 서버가 가진 도구 목록과 각 도구의 입력 스키마(JSON Schema)를 돌려줍니다.
- `tools/call`: 도구 이름과 인자로 실행을 요청합니다. 결과는 `content`(텍스트·이미지 등 블록 배열), `structuredContent`(구조화 결과, 선택), `isError`(도구 수준 오류 여부)로 구성됩니다.
- **오류의 두 층위**: 프로토콜 오류(알 수 없는 메서드 등)는 JSON-RPC `error`로, **업무 오류는 `isError: true`인 정상 결과**로 돌려줍니다. 후자는 LLM이 읽고 스스로 복구할 수 있다는 점이 중요합니다.

### 2.6 전송 방식 (Transports)

| 전송 | 방식 | 장점 | 단점 / 용도 |
| --- | --- | --- | --- |
| **stdio** | Host가 서버를 **자식 프로세스로 실행**하고 표준입출력으로 JSON-RPC를 주고받음 | 설정이 단순, 네트워크 불필요, 로컬 도구에 적합 | 같은 머신에 한정, 여러 Host가 공유 불가. 데스크톱 AI 앱의 로컬 도구에 주로 사용 |
| **Streamable HTTP** | 하나의 HTTP 엔드포인트(예: `/mcp`)에 POST로 요청, 응답은 JSON 또는 SSE 스트림. 세션 ID 헤더로 세션 유지 | 원격·다중 클라이언트, 기존 웹 인프라(프록시, 인증)와 결합 쉬움 | 세션·재연결 관리 필요. 사내 시스템을 서비스로 노출할 때 적합 |

본 단계는 각 부서 시스템이 **독립 서버 프로세스**라는 설정이므로 **Streamable HTTP**를 사용했습니다. FastAPI 앱에 `mcp.streamable_http_app()`을 마운트해 같은 포트에서 웹 화면과 MCP 엔드포인트(`/mcp`)를 함께 제공합니다.

### 2.7 장점과 단점

| 장점 | 단점 / 주의점 |
| --- | --- |
| 도구 자동 발견: 클라이언트는 엔드포인트·파라미터를 하드코딩하지 않는다 | 기본 단위가 **요청-응답**이라, 사람이 몇 시간 걸리는 작업처럼 장시간 작업을 표현하기 어렵다 (이 프로젝트의 폴링 문제) |
| 스키마가 LLM 도구 정의로 거의 그대로 변환된다 | 서버는 판단하지 않는 **수동적 도구**다. 업무 절차는 모두 Host(LLM 프롬프트)에 있어야 한다 |
| 한 번 만든 서버를 여러 AI 앱에서 재사용 (N+M) | 도구가 많아지면 LLM 컨텍스트(토큰)와 선택 정확도에 부담 |
| 업무 오류를 `isError`로 LLM에 돌려줘 자가 복구 가능 | 도구 설명이 곧 "API 계약"이라 설명 품질이 결과 품질을 좌우 |
| stdio/HTTP 등 전송 선택 가능, SDK 성숙 | 인증·권한·감사는 별도로 설계해야 한다 (이 단계는 로컬 무인증) |

### 2.8 전형적인 구현 방법

1. **서버**: SDK의 서버 클래스(여기서는 `MCPServer`)를 만들고, 함수에 `@mcp.tool()` 데코레이터를 붙입니다. 함수 시그니처의 타입 힌트(Pydantic 모델 포함)가 입력 스키마로, docstring이 도구 설명으로 변환됩니다. 읽기 데이터는 `@mcp.resource("scheme://path/{param}")`로 노출합니다.
2. **전송**: `streamable_http_app()`(HTTP) 또는 stdio 실행기를 선택합니다.
3. **클라이언트(Host)**: 서버별 세션을 열고 `list_tools()`로 도구를 받아 LLM의 도구 형식으로 변환합니다.
4. **에이전트 루프**: LLM이 `tool_use`를 내면 해당 서버에 `call_tool()`을 보내고, 결과를 `tool_result`로 돌려줍니다. LLM이 종료(`end_turn`)할 때까지 반복합니다.

### 2.9 이 프로젝트에의 대응

| MCP 개념 | 구현 위치 |
| --- | --- |
| Host | Hub 웹 `03_mcp/src/host/app.py` + 에이전트 `host/agent.py` |
| Client (서버당 1개) | `host/mcp_hub.py` `McpConnection` (`async with Client(url)`) |
| Server | `dev_mcp/server.py` `create_mcp()` (`MCPServer(name="dev-department")`), `qa_mcp/server.py` (`MCPServer(name="qa-department")`) |
| Transport | Streamable HTTP, `http://127.0.0.1:8301/mcp`, `http://127.0.0.1:8302/mcp` (`common/config.py` `MCP_SERVERS`) |
| `tools/list` | `McpConnection._run()`에서 연결 때마다 `client.list_tools()` |
| `tools/call` | `McpHub.call_tool()` → `client.call_tool(tool, arguments)` |
| `resources/read` | `McpHub.read_json()` → `client.read_resource(uri)` (Hub 화면 구성) |
| 도구 오류 | 서버: `WorkError` → `ToolError` 변환 → `isError: true`. Host: `tool_result.is_error`로 LLM에 전달 |
| 서버 instructions | `MCPServer(..., instructions="개발부서 시스템. ...")` |

---

## 3. 목표와 범위

### 3.1 목표

| ID | 목표 | 근거 |
| --- | --- | --- |
| G-1 | Dev/QA 시스템을 MCP 서버(Streamable HTTP)로 독립 기동 | PRD F-01 |
| G-2 | Host가 도구를 하드코딩하지 않고 `tools/list`로 발견해 Claude `tools`로 변환 | PRD F-02, `mcp_hub.py` `anthropic_tools()` |
| G-3 | 데모: Claude가 스스로 사이클을 완주 (mock은 동일 결과) | PRD F-03, F-04 |
| G-4 | 모든 LLM 턴과 도구 호출을 `host.db`에 기록, Agent Trace 화면 제공 | PRD F-05, `agent.py` `_step()` |
| G-5 | 도구 오류를 `is_error` tool_result로 LLM에 반환 | PRD F-06 |
| G-6 | 실습: Dev/QA는 서로를 모르고, 사람의 완료 작업을 Host가 폴링해 AI가 전달 | PRD F-08, F-09 |
| G-7 | 실습: 차례가 아닌 부서 작업 409, 타 부서원 403, Host 재시작 시 조정 재개 | PRD F-10 |
| G-8 | Dev/QA 이벤트를 Hub에 실시간 중계(업무 연결과 분리된 관찰 채널) | PRD F-11, `host/relay.py` |

### 3.2 범위

| 포함 | 제외 |
| --- | --- |
| MCP tools / resources, Streamable HTTP | MCP prompts, sampling, elicitation, stdio 전송 |
| Claude 수동 도구 루프(직접 구현), 프롬프트 캐싱, 서버측 fallback | Agent SDK·tool runner 등 상위 프레임워크 |
| Stage 2와 같은 웹 공통 계층(작업대, SSE, AI 초안) | 인증·권한(로컬 무인증), 다중 사용자 동시성 제어 |
| 결정적 시뮬레이터 기반 판정 | 실제 빌드·테스트 실행 |

### 3.3 공통 업무 도메인 (전 단계 공통)

- Dev가 버전을 발행 → QA가 검증하고 결과를 관리, 결함을 이관 → Dev가 수정해 재발행 → QA가 승인할 때까지 반복.
- 테스트케이스 12건 TC-01~12 (login / search / payment / report 각 3건, `qa_mcp/catalog.py`).
- 시드 결함 (`dev_mcp/catalog.py`): D-101 → TC-02, D-102 → TC-05, D-103 → TC-08. D-103을 고치면 회귀 결함 D-201 → TC-11 발생 (`REGRESSIONS = {"D-103": "D-201"}`).
- 기대 결과: v1.0.0 9 PASS / 3 FAIL → v1.1.0 11/1 → v1.2.0 12/0 → 승인. ISSUE-1~4 모두 VERIFIED.
- 빌드 산출물(매니페스트)은 Dev의 `GET /builds/{cycle}/{version}`.
- 부서원: QA 김검증(login·search = TC-01~06), 이검증(payment = TC-07~09), 박검증(report = TC-10~12) / Dev 최개발(백엔드), 정개발(프론트엔드).

---

## 4. 시스템 구성

### 4.1 전체 구성도

```text
                          Browser (릴리즈 매니저)
                                   │ HTTP / SSE
                                   ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │ Hub 웹 = MCP Host  :8300   (host.db: cycles, agent_steps, events)     │
 │                                                                       │
 │  AgentRunner ─────────── Claude Messages API (claude-opus-5-5)        │
 │   ├ 데모: _agent_loop(SYSTEM_PROMPT, 전체 도구) / MockPlanner          │
 │   └ 실습: _coordinate() 2초 폴링 → _handoff()                           │
 │            (COORDINATOR_PROMPT, MANUAL_TOOLS 9개) / MockCoordinator    │
 │                                                                       │
 │  McpHub  ── McpConnection("dev")  ── McpConnection("qa")               │
 │             (MCP Client 장기 세션)     (MCP Client 장기 세션)          │
 │  EventRelay (관찰 채널: Dev/QA SSE 구독 → Hub SSE로 병합)               │
 └──────────┬───────────────────────────────────────┬────────────────────┘
            │ MCP Streamable HTTP                   │ MCP Streamable HTTP
            │ tools/list, tools/call,               │ tools/list, tools/call,
            │ resources/read  (POST /mcp)           │ resources/read (POST /mcp)
            ▼                                       ▼
 ┌─────────────────────────────┐        ┌─────────────────────────────┐
 │ Dev 프로세스 :8301           │        │ QA 프로세스 :8302            │
 │  ┌───────────┐ ┌──────────┐ │        │  ┌───────────┐ ┌──────────┐ │
 │  │ MCP /mcp  │ │ Dev 웹 / │ │        │  │ MCP /mcp  │ │ QA 웹 /  │ │
 │  │ (AI 용)   │ │ (사람 용)│ │        │  │ (AI 용)   │ │ (사람 용)│ │
 │  └─────┬─────┘ └────┬─────┘ │        │  └─────┬─────┘ └────┬─────┘ │
 │        └─ DevSystem ┘       │        │        └─ QASystem ─┘       │
 │              │              │        │              │              │
 │           dev.db            │        │            qa.db            │
 │  GET /builds/{cycle}/{ver}  │◀───────┼── httpx GET build_url       │
 │  GET /api/events/stream     │        │  GET /api/events/stream     │
 └─────────────────────────────┘        └─────────────────────────────┘
        ▲  개발부서원 (최개발, 정개발)            ▲  검증부서원 (김검증, 이검증, 박검증)
        │  Browser                               │  Browser

 Dev ◀──x──▶ QA : 업무 통신 없음 (서로의 존재를 모름)
```

### 4.2 Dev/QA 프로세스의 이중 얼굴

각 부서 프로세스는 **하나의 서비스 객체(`DevSystem` / `QASystem`)와 하나의 DB**를 공유하면서 두 개의 입구를 가집니다.

```text
                    ┌──────────────── Dev 프로세스 (FastAPI, :8301) ────────────────┐
  Host(AI) ──MCP──▶ │  /mcp  : create_mcp()                                          │
                    │    publish_initial_release / register_tickets / fix_ticket ... │
                    │    request_release / list_completed_work / ack_work            │──┐
                    │    cancel_work (Host 코드 전용)                                │  │
 개발부서원 ─HTTP─▶ │  /     : desk.html, /tickets, /timeline                        │  ├─▶ DevSystem ─▶ dev.db
                    │  /api/work/release, /api/work/issues/{key}/start|fix ...       │──┘
                    │  /api/work/draft/fix/{key}, /api/work/draft/release (AI 초안)  │
                    └────────────────────────────────────────────────────────────────┘
```

| 입구 | 사용자 | 인증/식별 | 예 |
| --- | --- | --- | --- |
| MCP `/mcp` | Host의 AI(또는 mock) | 없음 (Host만 접속한다는 가정) | `request_release(cycle_id)` |
| 웹/REST `/api/work/*` | 부서원(사람) | 요청 본문의 `actor`가 **그 시스템 명부**에 있어야 함 (`Members.require`, 없으면 403) | `POST /api/work/release {"actor":"최개발", ...}` |

같은 데이터를 두 입구가 공유하므로, AI가 도구로 넣은 "발행 요청"이 사람의 작업대에 카드로 보이고, 사람이 끝낸 작업이 AI의 `list_completed_work` 결과로 보입니다. 이 **작업 지시(work order) 테이블이 사람과 AI의 접점**입니다.

### 4.3 연결 관계 요약

| From → To | 방식 | 용도 | 코드 |
| --- | --- | --- | --- |
| Hub → Dev/QA | MCP tools/call | 업무 수행(데모), 결과 전달(실습), 완료 작업 폴링 | `mcp_hub.py` `call_tool` |
| Hub → Dev/QA | MCP resources/read | 대시보드 조회 | `mcp_hub.py` `read_json`, `app.py` |
| Hub → Dev/QA | HTTP SSE `GET /api/events/stream` | 관찰 채널(실시간 표시) | `relay.py` `EventRelay._follow` |
| Hub → Dev/QA | HTTP `GET /api/events` | 타임라인 병합 조회 | `relay.py` `department_events` |
| Hub → Dev/QA | HTTP `POST /admin/reset` | 초기화 | `app.py` `/api/reset` |
| QA → Dev | HTTP `GET {build_url}` | 빌드 매니페스트 다운로드 (주소는 Host가 전달) | `qa_mcp/service.py` `_download` |
| Dev ↔ QA | 없음 | — | — |

---

## 5. 소스 구조

### 5.1 디렉터리 트리

```text
03_mcp/
├─ run.py                     # 3개 프로세스(dev_mcp → qa_mcp → host) 기동/종료, --mock, --only
├─ pyproject.toml             # fastapi, uvicorn, jinja2, httpx, mcp>=2.2, anthropic>=1.0, python-dotenv
├─ data/                      # dev.db, qa.db, host.db (WAL)
├─ docs/                      # PRD, TODO, USER_SCENARIO, images/
└─ src/
   ├─ common/
   │  ├─ config.py            # 포트, URL, MCP_SERVERS, MODEL, STEP/TEST_DELAY, MAX_AGENT_TURNS, llm_mode()
   │  ├─ db.py                # SQLite 헬퍼, 공통 스키마(events, members), JSON 컬럼, migrate, reset
   │  ├─ events.py            # EventBus: events 테이블 기록 + SSE 구독자 전달
   │  ├─ llm.py               # LLMClient.json(): 구조화 출력, 실패 시 mock fallback (AI 초안용)
   │  ├─ work.py              # WorkError(status), Members(명부 seed/require/actor)
   │  └─ web/
   │     ├─ setup.py          # /static 마운트, 공통+서비스 템플릿 ChoiceLoader, 필터
   │     ├─ ui.py             # 상태 배지 톤, 상태 라벨, 시간 포맷
   │     ├─ static/js/app.js  # SSE 연결, 부분 갱신, 토스트, 테마, 데모 시작/초기화
   │     ├─ static/js/workbench.js  # 작업자 선택, 작업대 버튼, 대화상자, AI 초안, 실습 시작/중단
   │     ├─ static/css/app.css
   │     └─ templates/        # base.html, _macros.html, timeline.html
   ├─ dev_mcp/
   │  ├─ catalog.py           # 제품·기능, 결함 정의, 회귀 규칙, next_version, apply_fixes, build_manifest
   │  ├─ service.py           # DevSystem: 데모 도구 동작 + 작업 지시 + 부서원 작업 + AI 초안
   │  ├─ server.py            # create_mcp(): MCP 도구·리소스 / create_app(): Dev 웹·/builds·/api
   │  ├─ __main__.py          # uvicorn 127.0.0.1:DEV_PORT
   │  └─ templates/           # layout, desk, tickets, ticket_detail
   ├─ qa_mcp/
   │  ├─ catalog.py           # 테스트케이스 12건, run_test() 판정 규칙
   │  ├─ service.py           # QASystem: 데모 도구 동작 + 작업 지시 + 판정·이슈·이관/확정 결정 + AI 초안
   │  ├─ server.py            # create_mcp() / create_app()
   │  ├─ __main__.py
   │  └─ templates/           # layout, desk, runs, test_run_detail, issues, issue_detail
   └─ host/
      ├─ mcp_hub.py           # McpConnection(서버별 장기 세션·재연결), McpHub(도구 변환·호출·리소스)
      ├─ agent.py             # AgentRunner: 데모 루프, 실습 조정자, 상태 추적, 트레이스 기록, 프롬프트
      ├─ planner.py           # MockPlanner(데모), MockCoordinator(실습)
      ├─ relay.py             # EventRelay(관찰 SSE 병합), department_events
      ├─ app.py               # Hub 웹 라우트 (MCP resources로 조합)
      ├─ __main__.py
      └─ templates/           # layout, overview, releases, release_detail, test_runs, test_run_detail,
                              # issues, issue_detail, trace, tools
```

### 5.2 McpHub — 연결 관리와 도구 이름 한정 (`host/mcp_hub.py`)

**서버별 장기 세션 (`McpConnection`)**

```text
McpConnection._run()  (전담 asyncio 태스크, 무한 루프)
  while True:
    try:
      async with Client(url) as client:          # MCP initialize 협상
          tools = await client.list_tools()        # tools/list (연결될 때마다 새로)
          server_info = client.server_info
          self.client = client; _ready.set()
          await _broken.wait()                     # 고장 신호가 올 때까지 세션 유지
    except BaseException:                          # 연결 실패는 ExceptionGroup으로 올 수 있음
      last_error = "..."                           # MCP Tools 화면에 표시
    _ready.clear(); client = None
    await asyncio.sleep(2.0)                       # 2초 후 재연결
```

- 세션을 **연 태스크가 닫아야 한다**는 anyio 규칙 때문에, 세션 수명은 전담 태스크가 관리하고 다른 코드는 `get()`으로 준비된 클라이언트를 빌려 씁니다.
- `call(fn)`: 최대 2회 시도. 첫 시도에서 예외가 나면 `_broken`을 세워 세션을 재수립시키고, 새 세션으로 한 번 더 시도합니다.
- `get(timeout=5.0)`: `_ready` 이벤트를 기다리며, 시간 초과 시 `asyncio.TimeoutError`.

**도구 이름 한정 (qualification)**

두 서버가 같은 이름의 도구(`list_completed_work`, `ack_work`)를 갖기 때문에, Claude에 노출할 때 `<server>__<tool>` 형식으로 이름을 한정합니다 (`SEP = "__"`).

```python
# host/mcp_hub.py
def anthropic_tools(self) -> list[dict]:
    for conn in self.connections.values():
        for t in conn.tools:
            tools.append({"name": f"{conn.name}{SEP}{t.name}", "description": t.description or "",
                          "input_schema": t.input_schema})
```

- MCP `Tool.inputSchema`(JSON Schema)를 Claude `input_schema`로 **그대로** 옮깁니다. 변환 로직이 사실상 이름 접두사뿐이라는 점이 MCP 스키마와 LLM 도구 정의의 친화성을 보여 줍니다.
- 호출 시에는 `call_tool("qa__ack_work", {...})` → `partition("__")`로 서버 `qa`, 도구 `ack_work`를 분리해 해당 세션에 보냅니다.
- 결과 해석: `content` 중 `TextContent`의 텍스트를 이어 붙이고, `structured_content`가 없으면 텍스트를 JSON으로 파싱해 구조화 결과를 만듭니다. 반환값은 `(is_error, text, structured)` 튜플입니다.
- 알 수 없는 서버/도구, 연결 시간 초과, 호출 예외는 모두 **예외를 던지지 않고** `is_error=True`와 한국어 메시지로 돌려줍니다. 이 메시지가 그대로 LLM의 `tool_result`가 됩니다.

### 5.3 AgentRunner — 도구 루프 (`host/agent.py`)

`AgentRunner`는 데모와 실습 모두의 실행기입니다. 주요 구성은 다음과 같습니다.

| 메서드 | 역할 |
| --- | --- |
| `start(mode)` | 진행 중 사이클이 있으면 `CycleConflict`(→ 409). `cycles`에 `RELEASING`으로 삽입 후 데모는 `_run`, 실습은 `_coordinate` 태스크 생성 |
| `resume()` | Host 기동 시, 활성 상태의 **실습** 사이클이 있으면 `_coordinate(resumed=True)` 재개 |
| `stop()` | 태스크를 취소하고 종료를 기다림 (Host 종료 시 사용. 작업 지시는 그대로 두어 재시작 후 재개) |
| `abort()` | 사용자 중단: `stop()` 후 실습 사이클이면 `dev__cancel_work`·`qa__cancel_work`로 진행 중 작업 지시 취소 |
| `execute_tool()` | LLM/플래너 공통 도구 실행: 상태 갱신, 검증 횟수 제한, ack 가드, `hub.call_tool`, 카운터, 트레이스, 이벤트 |
| `_agent_loop()` | Claude 수동 도구 루프 |
| `_run()` | 데모 사이클 |
| `_coordinate()` / `_handoff()` | 실습 조정자 |

**`_agent_loop()` 흐름**

```text
messages = [user: "릴리즈 사이클을 진행하세요. cycle_id=N"]     (데모)
         | [user: "<라벨>\n\n상황 데이터:\n{event JSON}"]      (실습 전달)
for _ in range(max_turns):                      # 데모 MAX_AGENT_TURNS=80, 전달 1회당 12
    response = client.beta.messages.create(
        model, max_tokens=16000, system, tools, messages,
        output_config={"effort": "low"}, cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    cycles.turns += 1, input/output_tokens 누적 (cache_read 포함)
    text 블록 → agent_steps(kind=text), tool_use 블록 → agent_steps(kind=tool_use)
    messages += assistant(response.content)
    stop_reason == "end_turn"  → 종료
    stop_reason == "pause_turn" → 그대로 계속
    stop_reason != "tool_use"  → error 기록 후 종료
    각 tool_use에 대해 execute_tool() → tool_result{tool_use_id, content, is_error}
    messages += user(tool_results)
return 마지막 text (요약)
```

- 한 응답에 여러 `tool_use`가 있으면(병렬 호출) **순서대로** 실행하고 결과를 한 번에 돌려줍니다. 시스템 프롬프트가 "서로 독립적인 호출은 한 번에 함께 요청해도 된다"고 허용하므로, 21회 도구 호출이 15턴에 끝납니다.
- `execute_tool()`이 하는 일:
  1. `TOOL_STATUS` 매핑으로 사이클 상태 갱신(대시보드 파이프라인용): 예) `qa__run_tests` → `VERIFYING`, `qa__transfer_issues` → `TRANSFERRING`, `dev__register_tickets` → `FIXING`.
  2. `qa__run_tests`는 `MAX_VERIFICATIONS = 5` 초과 시 서버를 호출하지 않고 오류 결과("검증 횟수 제한(5회)을 초과했습니다...")를 돌려줌. 아니면 `iteration` +1, `current_version` 갱신.
  3. **ack 가드**(실습 전달 중): `*__ack_work` 호출 시 이번 전달의 필수 도구(`REQUIRED_CALLS`) 중 성공하지 않은 것이 있으면 서버를 호출하지 않고 오류 결과("전달이 끝나지 않아 확인 처리하지 않습니다...")를 돌려줌.
  4. `hub.call_tool()` 호출과 소요 시간 측정, `tool_calls` +1, `tool_result` 트레이스(주체 `DEV MCP` / `QA MCP`). 성공한 도구는 이번 전달의 성공 목록에 기록.
  5. `_emit_tool_event()`로 사람이 읽을 이벤트 생성(예: "v1.1.0 검증 완료: 11 PASS / 1 FAIL").
  6. 성공 시 특수 상태 전이: `qa__approve_release` → `APPROVED`, `qa__request_verification` → `VERIFYING`(+iteration, 기존 요청을 돌려받은 재전달은 제외), `dev__request_release` → `RELEASING`(initial) / `FIXING`(fix).
  7. 데모일 때만 `STEP_DELAY`(기본 0.4초) 대기 — 사람이 흐름을 눈으로 따라갈 수 있게 하는 관찰용 지연.

### 5.4 MockPlanner / MockCoordinator (`host/planner.py`)

- **MockPlanner**: LLM 모드의 `SYSTEM_PROMPT` 업무 절차를 코드로 옮긴 것입니다. `call(name, **args)`가 `cycle_id`를 자동으로 넣고(`ack_work` 제외), `tool_use` 트레이스를 남긴 뒤 같은 `execute_tool()`을 거칩니다. 즉 **LLM과 mock은 도구 호출 경로가 완전히 같고**, "다음에 무엇을 호출할지"를 결정하는 주체만 다릅니다. 문서 내용은 템플릿(`"[{feature}] {name} 실패"`, 기능별 심각도 `FEATURE_SEVERITY`)으로 채웁니다. 도구 오류가 나면 `RuntimeError`로 사이클을 실패시킵니다(LLM처럼 복구하지 않음).
- **MockCoordinator**: `MockPlanner`를 상속해, 실습 이벤트 3종(`cycle_started`, `dev_work_completed`, `qa_work_completed`)을 `COORDINATOR_PROMPT`의 상황별 처리와 같은 순서로 처리합니다. 턴 번호는 `_next_turn()`에서 이어 받아 트레이스가 연속되게 합니다.

### 5.5 조정자 폴링 루프 (`AgentRunner._coordinate`)

```text
_coordinate(cycle_id, resumed=False)
  hub.wait_ready(timeout=30)
  if not resumed:
      emit "실습 사이클 #N 시작"
      missing = _handoff("사이클 시작", {event: cycle_started})   # → dev__request_release
      missing이 있으면 사이클 FAILED
  while cycle.status in ACTIVE (RELEASING|VERIFYING|TRANSFERRING|FIXING):
      for server in ("dev", "qa"):
          hub.call_tool(f"{server}__list_completed_work", {cycle_id})   # 코드가 직접 호출
          cycles.polls += 1
          for work in completed:
              label = "Dev 발행 완료 전달 (v1.1.0)" | "QA 이관 결정 전달 (v1.0.0)" | "QA 확정 결정 전달 (v1.2.0)"
              emit "완료 작업 발견: <label>"
              missing = _handoff(label, {event: "<server>_work_completed", cycle_id, work})
              if not missing:
                  hub.call_tool(f"{server}__ack_work", {work_id})   # LLM의 ack 누락 대비(멱등)
              else:                                                 # ack하지 않음 → 다음 폴링에서 재전달
                  같은 work 3회 실패면 사이클 FAILED, 아니면 emit "전달 미완료 ..."
      if status not in ACTIVE: break
      sleep(POLL_SECONDS = 2.0)
  APPROVED면 emit "실습 사이클 #N 완료"
```

`_handoff`는 전달 패턴별 필수 도구(`REQUIRED_CALLS`) 가운데 성공하지 못한 도구 목록을 돌려줍니다.

| 패턴 | 필수 도구 |
| --- | --- |
| 사이클 시작 | `dev__request_release` |
| 발행 완료 (Dev → QA) | `qa__request_verification` |
| 이관 (QA → Dev) | `qa__transfer_issues`, `dev__register_tickets`, `dev__request_release` |
| 확정 | `qa__approve_release` |

설계상 중요한 점:

- **폴링은 LLM이 아니라 코드가 합니다.** 2초마다 LLM을 부르면 비용과 지연이 커지기 때문입니다. 조정자 Claude에게는 `list_completed_work`를 노출하지 않고(`MANUAL_TOOLS`에 없음), 발견한 내용을 상황 데이터로 주입합니다.
- **LLM은 발견 시에만** 호출됩니다(전달 1회 = `_handoff` 1회 = 짧은 에이전트 루프 1회, 최대 12턴).
- **ack는 전달이 끝난 뒤에만** 합니다. 전달이 일부만 수행되면 완료 작업을 남겨 두고 다음 폴링에서 다시 전달합니다. 재전달이 안전하도록 `transfer_issues`는 언제나 TRANSFERRED 이슈 전체를 돌려주고, `register_tickets`는 아직 발행에 반영되지 않은 티켓을 건드리지 않으며, `request_*`는 같은 사이클의 OPEN 요청을 그대로 돌려줍니다.
- `_handoff`마다 `run_label`을 바꿔, Agent Trace에서 "사이클 시작", "Dev 발행 완료 전달 (v1.0.0)" 같은 실행 단위로 묶어 보여 줍니다.
- 폴링 호출과 보험용 `ack_work`는 `execute_tool`을 거치지 않으므로 `tool_calls`·트레이스에 포함되지 않고, `polls` 카운터만 증가합니다.
- mock 조정자는 도구 오류가 나면 재전달하지 않고 즉시 사이클을 FAILED로 처리합니다(`MockPlanner.call`의 `RuntimeError`). 재전달은 주로 LLM 조정자에 해당합니다.

### 5.6 Relay — 관찰 채널 (`host/relay.py`)

- `EventRelay.start()`가 Dev·QA의 `GET /api/events/stream`(SSE)을 각각 구독하고, 받은 이벤트에 `source`("Dev"/"QA")를 붙여 Hub 구독자 큐에 넣습니다. 끊기면 2초 후 재연결.
- `app.py`에서 `bus.publish`를 감싸 Host 자신의 이벤트도 `source: "Host"`로 같은 스트림에 넣습니다. 그래서 Hub 화면 한 곳에서 "QA 김검증 TC-04 → PASS" 같은 **사람의 작업**과 "Host 작업 #7 전달 완료 확인" 같은 **AI의 작업**이 실시간으로 섞여 보입니다.
- `department_events()`는 타임라인 페이지를 위해 두 시스템의 `/api/events`를 조회하고, `app.py` `events()`가 Host 이벤트와 시간순(`ts`, `id`)으로 병합합니다.
- 이 채널은 **업무 연결이 아닙니다.** 업무 데이터의 이동은 오직 MCP 도구 호출로만 일어나고, 관찰 채널은 사람이 보는 화면용입니다. (PRD F-11)

### 5.7 Hub 웹 (`host/app.py`)

- 업무 데이터는 **MCP resources**로 읽어 조합합니다. 예) `releases_with_runs()`는 `dev://cycles/{id}/releases`와 `qa://cycles/{id}/test-runs`를 동시에 읽어 버전 기준으로 결합, `issues_with_tickets()`는 QA 이슈와 Dev 티켓을 이슈 키 기준으로 결합.
- 에이전트 기록(`cycles`, `agent_steps`)은 자기 DB(`host.db`)에서 읽습니다.
- `turn_of(cycle)`: 실습 사이클에서 `VERIFYING`이면 QA 차례, 그 외 활성 상태면 Dev 차례. 사이드바 "차례" 배지와 Overview 배너에 사용합니다.

### 5.8 부서 시스템 서비스 (`dev_mcp/service.py`, `qa_mcp/service.py`)

| 영역 | DevSystem | QASystem |
| --- | --- | --- |
| 데모 도구 동작 | `publish_initial_release`, `register_tickets`, `fix_ticket`, `publish_release` | `run_tests`, `verify_fixed_issues`, `create_issue`, `transfer_issues`, `approve_release` |
| 작업 지시 | `request_release`, `open_order`, `completed_work`, `ack_work` | `request_verification`, `open_order`, `completed_work`, `ack_work` |
| 부서원 작업 | `release`, `start_fix`, `fix` | `execute`, `judge`, `staff_issue`, `verify_issue`, `transfer`, `approve` (`_decide`) |
| 차례 검사 | `_order()` — OPEN 발행 요청 없으면 409 | `_run()` — OPEN 검증 요청 없으면 409 |
| AI 초안 | `draft_fix`, `draft_release` (LLMClient.json) | `draft_issue` |
| 시뮬레이션 | `catalog.apply_fixes` → 회귀 발생 시 `Simulator` 이벤트 | `catalog.run_test(build, tc)` |

---

## 6. 데이터 모델

모든 DB에는 공통 테이블 `events`(id, ts, cycle_id, actor, type, message, payload)와 `members`(name PK, dept, role, features)가 있습니다 (`common/db.py`). Stage 2에서 넘어온 `integration_logs` 테이블은 쓰이지 않아 2026-10-16 스키마에서 제거했습니다(기존 DB 파일에 남은 빈 테이블은 동작에 영향이 없습니다). `features`, `fixed_issue_keys`, `payload`, `build`, `outcome` 컬럼은 JSON 문자열로 저장되고 읽을 때 자동 디코딩됩니다(`JSON_COLUMNS`). 실습 모드에서 추가된 컬럼은 `Database.migrate()`가 기존 DB 파일에 `ALTER TABLE ADD COLUMN`으로 보강합니다.

### 6.1 Dev (`data/dev.db`, `dev_mcp/service.py`)

| 테이블 | 주요 컬럼 | 설명 |
| --- | --- | --- |
| `releases` | cycle_id, version, features(JSON), build(JSON 매니페스트), fixed_issue_keys(JSON), release_note, released_by(+), released_at. UNIQUE(cycle_id, version) | 발행 이력. `build`가 `/builds/{cycle}/{version}` 응답 본문 |
| `tickets` | cycle_id, key(=QA 이슈 키), test_case_id, title, description, severity, version_found, status, fix_note, fixed_in_version, assignee(+), received_at, updated_at. UNIQUE(cycle_id, key) | 개발 티켓 |
| `ticket_history` | ticket_id, from_status, to_status, actor(+), note, ts | 티켓 상태 이력 |
| `work_orders` | cycle_id, **kind**(initial/fix), **status**(OPEN/DONE/ACKED/CANCELLED), **outcome**(JSON), requested_at, done_at, acked_at | Host의 발행 요청 |

(+) 표시는 `MIGRATIONS`로 추가되는 컬럼입니다.

빌드 매니페스트 예 (`catalog.build_manifest`):

```json
{
  "product": "Commerce Web", "version": "1.1.0",
  "features": ["login", "search", "payment", "report"],
  "behaviors": [
    {"id": "D-201", "breaks": "TC-11",
     "log": "AssertionError: report total 1,250,000 != payment ledger 1,249,990\n  at report/summary.py:73 refund rounding mismatch"}
  ]
}
```

티켓 상태 전이:

```text
           register_tickets (신규)                start_fix (사람)          fix (사람) / fix_ticket (데모)
 (없음) ─────────────────────────▶ RECEIVED ───────────────────▶ IN_PROGRESS ───────────────▶ FIXED
                                     ▲                                                           │
                                     │ register_tickets (같은 키 재이관: "재이관 수신 (재현)",     │ publish_release
                                     │                  fixed_in_version=NULL)                    ▼
                                     └─────────────────────────────────────────── FIXED + fixed_in_version=v
```

데모의 `fix_ticket`은 `IN_PROGRESS`와 `FIXED`를 연속 기록하고, 이미 `FIXED`면 "이미 수정 완료된 티켓입니다"로 멱등 응답합니다. `register_tickets`는 아직 발행에 반영되지 않은 티켓(`RECEIVED`, `IN_PROGRESS`, 미발행 `FIXED`)을 다시 받으면 상태·담당자를 그대로 두고 이력도 남기지 않습니다. 부분 수정 후 재이관된 이슈와 Host의 재전달이 이 경우에 해당합니다. 반영까지 끝난 티켓(`FIXED` + `fixed_in_version`)만 "재이관 수신 (재현)"으로 다시 접수합니다.

### 6.2 QA (`data/qa.db`, `qa_mcp/service.py`)

| 테이블 | 주요 컬럼 | 설명 |
| --- | --- | --- |
| `test_cases` | id(TC-xx), feature, name, assignee(+) | 12건, 기능별 담당자 시드 |
| `test_runs` | cycle_id, version, build_url, total, passed, failed, status(RUNNING/DONE/CANCELLED), approved, approval_comment, mode(+, demo/manual), build(+, 캐시), decision(+, TRANSFERRED/APPROVED), decided_by(+), started_at, finished_at | 검증 실행 |
| `test_results` | run_id, test_case_id, passed, log, result(+, PENDING/PASS/FAIL), assignee(+), sim_log(+), checked_by(+), checked_at(+) | 항목별 결과 |
| `issues` | cycle_id, key(ISSUE-n), test_case_id, version_found, title, description, severity, status, fixed_in_version, reporter(+), created_at, updated_at | 결함 |
| `issue_history` | issue_id, from_status, to_status, actor(+), note, ts | 이슈 상태 이력 |
| `work_orders` | cycle_id, **run_id**, **status**(OPEN/DONE/ACKED/CANCELLED), **outcome**(JSON), requested_at, done_at, acked_at | Host의 검증 요청 |

이슈 상태 전이:

```text
 create_issue / staff_issue          transfer_issues            request_verification / verify_fixed_issues
 ────────────────────────▶ OPEN ──────────────────▶ TRANSFERRED ──────────────────────────────▶ FIXED
                            ▲                                    (Dev가 vX에 반영, fixed_in_version=vX)
                            │                                                                    │
                            │      transfer_issues                    verify_issue(ok=false)     │ verify_issue(ok=true)
                REOPENED ───┴──────────────────────▶ TRANSFERRED      / 테스트 재현              ▼
                   ▲                                                                         VERIFIED
                   └──────────────────────────────── FIXED ─────────────────────────────────────┘
```

- `ACTIVE = (OPEN, TRANSFERRED, REOPENED)`: 같은 테스트케이스에 활성 이슈가 있으면 새 이슈 대신 기존 키를 반환(중복 방지).
- **이월 이슈**: Dev가 일부 티켓만 수정해 발행하면, 반영된 이슈만 `request_verification`에서 FIXED가 되고 나머지는 TRANSFERRED로 남습니다. 다음 검증에서 이 이슈들의 테스트가 FAIL이면 기존 키에 연결되며, `transfer_issues`가 이들을 새 OPEN/REOPENED 이슈와 함께 다시 이관합니다(상태는 TRANSFERRED 유지).
- `UNRESOLVED = ACTIVE + FIXED`: 확정 가능 여부 판단에 사용.

### 6.3 Host (`data/host.db`, `host/agent.py`)

| 테이블 | 주요 컬럼 | 설명 |
| --- | --- | --- |
| `cycles` | status, iteration(검증 횟수), current_version, llm_mode(모델명 또는 mock), summary(마지막 보고), error, turns, tool_calls, input_tokens, output_tokens, mode(+, demo/manual), polls(+), handoffs(+), started_at, finished_at | 사이클과 계측치 |
| `agent_steps` | cycle_id, turn, actor(Claude/Planner/DEV MCP/QA MCP/Host), kind(text/tool_use/tool_result/error), tool, input, output, is_error, duration_ms, run_label(+), ts(ms) | Agent Trace 원본 |

사이클 상태 전이:

```text
                 ┌──────────────── (도구 호출에 따라 TOOL_STATUS로 이동) ────────────────┐
 start ─▶ RELEASING ⇄ VERIFYING ⇄ TRANSFERRING ⇄ FIXING ──── qa__approve_release 성공 ──▶ APPROVED
              │            │             │            │
              └────────────┴──── abort / 오류 / 승인 없이 종료 ──▶ CANCELLED | FAILED
 Host 재시작 시: 활성 데모 사이클 → CANCELLED("서버 재시작으로 중단"), 활성 실습 사이클 → 조정 재개
```

### 6.4 작업 지시(work order) — 사람과 AI의 접점

```text
 Host(AI)                       부서 시스템 work_orders                      부서원(사람)
   │ request_release /              │                                          │
   │ request_verification ─────────▶│ INSERT status=OPEN                       │
   │                                │ (작업대에 "Host 요청" 카드, "차례" 배지)  │
   │                                │◀───────── release / transfer / approve ──│
   │                                │ UPDATE status=DONE, outcome=JSON, done_at│
   │ list_completed_work (폴링) ───▶│ SELECT ... WHERE status='DONE'           │
   │◀── [{work_id, outcome, ...}] ──│                                          │
   │ (상대 시스템에 전달)            │                                          │
   │ ack_work(work_id) ────────────▶│ UPDATE status=ACKED WHERE status='DONE'  │
```

| 상태 | 의미 | 진입 |
| --- | --- | --- |
| `OPEN` | 부서원 작업 중 ("차례") | Host의 `request_*` 도구 |
| `DONE` | 부서원이 끝냄, Host 회수 대기 | 부서원의 발행/이관/확정 |
| `ACKED` | Host가 상대 부서에 전달 완료 | Host의 `ack_work` |
| `CANCELLED` | 사이클 중단으로 취소 (QA는 해당 검증 실행도 CANCELLED) | Host의 `cancel_work`, 또는 다른 사이클의 `request_*` 호출 시 남은 OPEN 정리 |

`outcome` JSON의 형태:

```json
// Dev work_orders.outcome (release())
{"cycle_id": 4, "version": "1.1.0", "build_url": "http://127.0.0.1:8301/builds/4/1.1.0",
 "fixed_issue_keys": ["ISSUE-1", "ISSUE-2", "ISSUE-3"]}

// QA work_orders.outcome (_decide())
{"decision": "transfer", "cycle_id": 4, "version": "1.0.0", "run_id": 10,
 "passed": 9, "failed": 3, "decided_by": "박검증"}
```

`request_release`와 `request_verification`은 먼저 **다른 사이클**의 OPEN 요청을 CANCELLED로 정리하고(Host가 `cancel_work`를 부르지 못하고 종료된 경우의 안전장치), 같은 사이클에 OPEN 요청이 있으면 새로 만들지 않고 기존 `work_id`를 `note`와 함께 돌려줍니다. `ack_work`는 `WHERE status = 'DONE'` 조건부 갱신이라 몇 번을 호출해도 결과가 같습니다(**멱등**).

---

## 7. 인터페이스

### 7.1 Dev MCP 서버 도구 카탈로그 (`dev_mcp/server.py`, 서버명 `dev-department`)

| # | 도구 (Claude 이름) | 파라미터 | 반환 | 데모 | 실습(조정) | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `publish_initial_release` (`dev__publish_initial_release`) | `cycle_id: int` | `{cycle_id, version:"1.0.0", build_url, features[]}` | 사용 | - | 이미 릴리즈가 있으면 오류 |
| 2 | `register_tickets` (`dev__register_tickets`) | `cycle_id: int`, `issues: TicketInput[]` (key, test_case_id, title, description="", severity, version_found) | `{registered: [key...], kept: [key...]}` | 사용 | 사용 | 반영 끝난 기존 키는 재접수(RECEIVED), 미반영 티켓은 상태·담당자 유지(`kept`) |
| 3 | `fix_ticket` (`dev__fix_ticket`) | `cycle_id`, `issue_key: str`, `fix_note: str` | `{key, status:"FIXED"[, note]}` | 사용 | - | 티켓 없으면 오류, 이미 FIXED면 멱등 |
| 4 | `publish_release` (`dev__publish_release`) | `cycle_id`, `release_note: str` | `{cycle_id, version, build_url, fixed_issue_keys[]}` | 사용 | - | 최초 릴리즈 없음/반영 티켓 없음이면 오류 |
| 5 | `list_tickets` (`dev__list_tickets`) | `cycle_id` | `{tickets: [{key, test_case_id, title, severity, status, assignee, fixed_in_version}]}` | 노출 | 노출 | 조회용 |
| 6 | `request_release` (`dev__request_release`) | `cycle_id` | `{work_id, kind:"initial"/"fix"}` 또는 `{work_id, kind, note:"이미 진행 중..."}` | - | 사용 | 작업 지시 생성 |
| 7 | `list_completed_work` (`dev__list_completed_work`) | `cycle_id` | `{completed: [{work_id, kind, outcome, done_at}]}` | - | 코드가 폴링 | 조정자 Claude에는 비노출 |
| 8 | `ack_work` (`dev__ack_work`) | `work_id: int` | `{work_id, acked: true}` | - | 사용 | 멱등 |
| 9 | `cancel_work` (`dev__cancel_work`) | `cycle_id` | `{cycle_id, cancelled: [work_id...]}` | - | Host 코드 | 사이클 중단 시 OPEN 발행 요청 취소. 어떤 LLM에도 비노출 |

**Dev resources**

| URI 템플릿 | MIME | 내용 |
| --- | --- | --- |
| `dev://cycles/{cycle_id}/releases` | application/json | 릴리즈 목록 (build 제외) |
| `dev://cycles/{cycle_id}/tickets` | application/json | 티켓 목록 + `history` |
| `dev://cycles/{cycle_id}/work` | application/json | 작업 지시 목록 |

### 7.2 QA MCP 서버 도구 카탈로그 (`qa_mcp/server.py`, 서버명 `qa-department`)

| # | 도구 (Claude 이름) | 파라미터 | 반환 | 데모 | 실습(조정) | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `run_tests` (`qa__run_tests`) | `cycle_id`, `version: str`, `build_url: str` | `{run_id, version, total, passed, failed, failures:[{test_case_id, feature, name, log}]}` | 사용 | - | async. 빌드 다운로드 실패(502)/버전 불일치 시 오류. Host가 5회 제한 |
| 2 | `verify_fixed_issues` (`qa__verify_fixed_issues`) | `cycle_id`, `version`, `run_id: int`, `fixed_issue_keys: str[]` | `{verified[], reopened[]}` | 사용 | - | FIXED 기록 후 결과로 VERIFIED/REOPENED |
| 3 | `create_issue` (`qa__create_issue`) | `cycle_id`, `version`, `test_case_id`, `title`, `description`, `severity: "Critical"/"Major"/"Minor"` (Literal → enum) | `{key, created, status[, note]}` | 사용 | - | 활성 이슈 있으면 기존 키 |
| 4 | `transfer_issues` (`qa__transfer_issues`) | `cycle_id` | `{issues:[{key, test_case_id, title, description, severity, version_found}]}` | 사용 | 사용 | OPEN/REOPENED → TRANSFERRED 후, 직전 발행에 반영되지 않은 이월 이슈를 포함한 TRANSFERRED 전체를 반환. 결과가 곧 `register_tickets` 입력. 재전달 때도 같은 목록 |
| 5 | `approve_release` (`qa__approve_release`) | `cycle_id`, `version`, `run_id`, `comment: str` | `{approved:true, version, run_id}` | 사용 | 사용 | 실패가 있는 run이나 불일치 시 오류 |
| 6 | `list_issues` (`qa__list_issues`) | `cycle_id` | `{issues:[{key, test_case_id, title, severity, status, version_found, fixed_in_version}]}` | 노출 | 노출 | 조회용 |
| 7 | `request_verification` (`qa__request_verification`) | `cycle_id`, `version`, `build_url`, `fixed_issue_keys: str[]` | `{work_id, run_id}` 또는 `{work_id, note}` | - | 사용 | 수정 이슈 FIXED 처리 + 검증 항목 12건 PENDING 생성 |
| 8 | `list_completed_work` (`qa__list_completed_work`) | `cycle_id` | `{completed:[{work_id, run_id, outcome, done_at}]}` | - | 코드가 폴링 | `outcome.decision` = transfer/approve |
| 9 | `ack_work` (`qa__ack_work`) | `work_id` | `{work_id, acked:true}` | - | 사용 | 멱등 |
| 10 | `cancel_work` (`qa__cancel_work`) | `cycle_id` | `{cycle_id, cancelled: [work_id...]}` | - | Host 코드 | 사이클 중단 시 OPEN 검증 요청과 그 검증 실행 취소. 어떤 LLM에도 비노출 |

**QA resources**

| URI | 내용 |
| --- | --- |
| `qa://test-cases` | 테스트케이스 12건 |
| `qa://cycles/{cycle_id}/test-runs` | 사이클의 테스트 실행 목록 |
| `qa://test-runs/{run_id}` | 실행 상세 + 케이스별 `results` |
| `qa://cycles/{cycle_id}/issues` | 이슈 목록 + `history` |
| `qa://status` | 현재 검증 요청의 run 요약과 `readiness`(pending, unverified, open, unresolved, can_transfer, can_approve) |

**도구 노출 범위 정리**

| 집합 | 개수 | 내용 |
| --- | --- | --- |
| MCP 서버가 제공 (`tools/list`) | 19 | Dev 9 + QA 10 |
| 데모 Claude에 노출 (`anthropic_tools()` − `HOST_ONLY_TOOLS`) | 17 | `cancel_work` 2개를 뺀 전부. 조정용 도구도 보이지만 `SYSTEM_PROMPT`가 데모 절차만 지시 |
| 실습 조정자 Claude에 노출 (`MANUAL_TOOLS`) | 9 | `dev__request_release`, `dev__register_tickets`, `dev__ack_work`, `dev__list_tickets`, `qa__request_verification`, `qa__transfer_issues`, `qa__approve_release`, `qa__ack_work`, `qa__list_issues` |
| Host 코드만 호출 | 4 | `dev__list_completed_work`, `qa__list_completed_work`(폴링), `dev__cancel_work`, `qa__cancel_work`(중단). 이 밖에 보험용 `*__ack_work`도 코드가 호출 |

### 7.3 도구 정의가 Claude에 전달되는 모습

`tools/list`에서 받은 MCP 도구(예: QA `request_verification`)는 다음과 같은 Claude 도구 정의가 됩니다. 입력 스키마는 SDK가 함수 시그니처로부터 생성한 것을 그대로 사용합니다(아래는 구조를 보여 주는 형태).

```json
{
  "name": "qa__request_verification",
  "description": "(실습) 검증부서에 검증을 요청한다. 검증부서원이 QA 웹에서 판정하고 이관 또는 확정을 결정하면 완료 작업으로 남는다.",
  "input_schema": {
    "type": "object",
    "properties": {
      "cycle_id": {"type": "integer"},
      "version": {"type": "string"},
      "build_url": {"type": "string"},
      "fixed_issue_keys": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["cycle_id", "version", "build_url", "fixed_issue_keys"]
  }
}
```

`register_tickets`의 `issues`는 Pydantic 모델 `TicketInput`이므로 스키마에 `$defs`로 정의되고 `Field(description=...)`의 설명("QA 이슈 키 (예: ISSUE-1)", "Critical | Major | Minor")이 포함됩니다. `create_issue`의 `severity`는 `Literal` 타입이라 `enum`으로 표현됩니다. Hub의 MCP Tools 화면은 이 변환 결과(앞 2개)를 그대로 보여 주고, `cancel_work`에는 "Host 코드 전용 (Claude 비노출)" 표시를 붙입니다.

### 7.4 MCP 메시지 형태 예시 (코드에서 유도)

**도구 목록 조회**

```json
// → POST http://127.0.0.1:8302/mcp
{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

// ← 결과 (요약)
{"jsonrpc": "2.0", "id": 2, "result": {"tools": [
  {"name": "run_tests", "description": "(데모) build_url에서 빌드를 내려받아 테스트케이스 12건을 실행한다. ...",
   "inputSchema": {"type": "object", "properties": {"cycle_id": {"type": "integer"}, "version": {"type": "string"},
                   "build_url": {"type": "string"}}, "required": ["cycle_id", "version", "build_url"]}},
  {"name": "request_verification", "...": "..."}
]}}
```

**도구 호출 — 성공**

```json
// → 조정자 Claude의 tool_use(qa__request_verification)를 Host가 변환
{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
 "params": {"name": "request_verification",
            "arguments": {"cycle_id": 4, "version": "1.0.0",
                          "build_url": "http://127.0.0.1:8301/builds/4/1.0.0", "fixed_issue_keys": []}}}

// ← 결과: dict 반환 → 텍스트(JSON)와 structuredContent를 함께 담음
{"jsonrpc": "2.0", "id": 7, "result": {
  "content": [{"type": "text", "text": "{\"work_id\": 7, \"run_id\": 10}"}],
  "structuredContent": {"work_id": 7, "run_id": 10},
  "isError": false}}
```

**도구 호출 — 업무 오류 (`WorkError` → `ToolError`)**

```json
// → 데모에서 이미 최초 릴리즈가 있는데 다시 호출
{"jsonrpc": "2.0", "id": 9, "method": "tools/call",
 "params": {"name": "publish_initial_release", "arguments": {"cycle_id": 3}}}

// ← JSON-RPC error가 아니라 정상 result + isError
{"jsonrpc": "2.0", "id": 9, "result": {
  "content": [{"type": "text", "text": "cycle 3에는 이미 최초 릴리즈가 있습니다. publish_release를 사용하세요."}],
  "isError": true}}
```

**Claude 쪽에서 보이는 형태** (`agent.py` `_agent_loop`)

```json
// assistant 응답의 content 블록
{"type": "tool_use", "id": "toolu_01...", "name": "qa__transfer_issues", "input": {"cycle_id": 4}}

// Host가 다음 user 메시지로 돌려주는 블록
{"type": "tool_result", "tool_use_id": "toolu_01...",
 "content": "{\"issues\": [{\"key\": \"ISSUE-1\", \"test_case_id\": \"TC-02\", \"title\": \"...\", ...}]}",
 "is_error": false}
```

**리소스 읽기**

```json
{"jsonrpc": "2.0", "id": 12, "method": "resources/read", "params": {"uri": "qa://status"}}
// ← contents[0].text
{"run": {"id": 10, "cycle_id": 4, "version": "1.0.0", "passed": 5, "failed": 2, "total": 12},
 "readiness": {"pending": 5, "unverified": 0, "open": 2, "unresolved": 2, "can_transfer": false, "can_approve": false}}
```

### 7.5 Hub 웹 라우트 (`host/app.py`, :8300)

| 메서드 | 경로 | 설명 | 데이터 출처 |
| --- | --- | --- | --- |
| GET | `/` | Overview (실습 배너, 상태 카드, 파이프라인, 에이전트 요약, 버전별 결과, 이슈 현황, 최근 이벤트, 사이클 이력) | resources + host.db + 병합 이벤트 |
| GET | `/releases`, `/releases/{version}` | 릴리즈 목록/상세 | `dev://.../releases` + `qa://.../test-runs` |
| GET | `/test-runs`, `/test-runs/{run_id}` | 검증 실행 목록/상세 | `qa://...` |
| GET | `/issues`, `/issues/{key}` | 이슈 목록/상세 (QA 이력 + Dev 티켓 이력 병합) | `qa://.../issues` + `dev://.../tickets` |
| GET | `/timeline` | Host + Dev + QA 병합 타임라인 | host.db + `/api/events` |
| GET | `/trace` | Agent Trace | `agent_steps` |
| GET | `/tools` | MCP Tools (서버 정보, 도구, 스키마, Claude 변환 결과) | `McpConnection.tools` |
| POST | `/api/cycles` | 데모 실행 (진행 중이면 409) | - |
| POST | `/api/work/start` | 실습 시작 (진행 중이면 409) | - |
| POST | `/api/work/abort` | 사이클 중단. 실습이면 Dev/QA `cancel_work` 호출 후 응답 (진행 중이 아니면 409) | MCP tools/call |
| POST | `/api/reset` | 전체 초기화: Dev/QA `/admin/reset` 후 host.db 비움 (실행 중 409, 하위 실패 503) | - |
| GET | `/api/state` | running, 최근 cycle, 차례, agent, MCP 연결 상태 | - |
| GET | `/api/cycles/{id}/summary` | 사이클 결과 요약(점검용) | resources |
| GET | `/api/events/stream` | 병합 SSE | `EventRelay` |

모든 화면 경로는 `?cycle=N`으로 과거 사이클을 지정해 볼 수 있습니다.

### 7.6 Dev 웹/REST (:8301)

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/` | 개발 작업대 (발행 요청 카드, 접수된 티켓, Host 요청 이력, 발행 이력) |
| GET | `/tickets`, `/tickets/{key}` | 티켓 목록/상세(이력) |
| GET | `/timeline` | Dev 이벤트 |
| GET | `/health` | `{"service":"dev-mcp","status":"ok","mcp":"/mcp"}` |
| GET | `/builds/{cycle_id}/{version}` | 빌드 매니페스트 (MCP 외 일반 HTTP) |
| GET | `/api/events`, `/api/events/stream` | 이벤트 조회 / SSE |
| POST | `/admin/reset` | DB 초기화 + 명부 재시드 |
| POST | `/api/work/release` | 발행 `{actor, release_note}` |
| POST | `/api/work/issues/{key}/start` | 착수 `{actor}` |
| POST | `/api/work/issues/{key}/fix` | 수정 완료 `{actor, fix_note}` |
| POST | `/api/work/draft/fix/{key}`, `/api/work/draft/release` | AI 초안 |
| * | `/mcp` | MCP Streamable HTTP (마지막에 `/`로 마운트) |

### 7.7 QA 웹/REST (:8302)

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/` | 검증 작업대 (요약 카드, 검증 항목, 수정 확인, 이 버전 이슈, 이관/확정 액션바) |
| GET | `/runs`, `/runs/{run_id}` | 검증 이력/상세 |
| GET | `/issues`, `/issues/{key}` | 이슈 목록/상세 |
| GET | `/timeline`, `/health`, `/api/events`, `/api/events/stream` | 공통 |
| POST | `/admin/reset` | DB 초기화(`test_cases` 유지) + 재시드 |
| POST | `/api/work/results/{id}/execute` | 실행(빌드로 시뮬레이션, 참고 결과) |
| POST | `/api/work/results/{id}/judge` | 판정 `{actor, result: PASS/FAIL}` |
| POST | `/api/work/issues` | 이슈 등록 + FAIL 처리 `{actor, result_id, title, severity, description}` |
| POST | `/api/work/issues/{key}/verify` | 수정 확인/재현 `{actor, ok}` |
| POST | `/api/work/transfer`, `/api/work/approve` | 이관/확정 결정 `{actor}` |
| POST | `/api/work/draft/issue/{result_id}` | AI 초안 |
| * | `/mcp` | MCP Streamable HTTP |

### 7.8 SSE

| 스트림 | 생산자 | 내용 |
| --- | --- | --- |
| Dev/QA `/api/events/stream` | `EventBus.stream()` | 자기 시스템 이벤트. 첫 줄 `retry: 3000`, 15초마다 `: keep-alive` |
| Hub `/api/events/stream` | `EventRelay.stream()` | Host 이벤트 + Dev/QA 이벤트(`source` 표기) + `agent.step`, `cycle.status` 일시 이벤트 |

데모 중에는 QA `run_tests`가 테스트 1건마다 `test.progress` 일시 이벤트를 발행하고, 관찰 채널을 거쳐 Hub 상단 진행 막대에 표시됩니다(사이클당 36건 = 3회 × 12건). 브라우저(`app.js`)는 이벤트를 받으면 타임라인에 행을 추가하고, 주요 이벤트는 토스트로 띄우며, `data-live` 영역을 부분 갱신합니다. 대화상자가 열려 있거나 입력 중이면 갱신을 미룹니다(입력 보호). `system.reset`을 받으면 페이지를 다시 엽니다.

---

## 8. 동작 흐름

### 8.1 데모 — Claude의 도구 호출 순서

시작: Hub **데모 실행** → `POST /api/cycles` → `AgentRunner.start("demo")` → `_run()` → `_agent_loop(SYSTEM_PROMPT, "릴리즈 사이클을 진행하세요. cycle_id=N", 전체 17개 도구, 80턴)`.

논리적 호출 순서는 다음과 같습니다(mock `MockPlanner`는 정확히 이 순서, Claude는 독립 호출을 한 턴에 묶을 수 있음).

```text
[v1.0.0]
 1  dev__publish_initial_release {cycle_id}
      → {version:"1.0.0", build_url:".../builds/N/1.0.0", features:[...]}
 2  qa__run_tests {cycle_id, version:"1.0.0", build_url}
      → {run_id, passed:9, failed:3, failures:[TC-02, TC-05, TC-08 + 로그]}
 3  qa__create_issue x3 (TC-02, TC-05, TC-08; 제목·설명·심각도는 Claude가 로그 분석으로 작성.
                         mock은 기능별 고정값 login Major / search Minor / payment Critical)
      → ISSUE-1, ISSUE-2, ISSUE-3
 4  qa__transfer_issues {cycle_id}          → issues[3]  (OPEN → TRANSFERRED)
 5  dev__register_tickets {cycle_id, issues} → registered[3]
 6  dev__fix_ticket x3 (fix_note: 로그에서 추정한 원인과 수정)
 7  dev__publish_release {cycle_id, release_note}
      → {version:"1.1.0", fixed_issue_keys:[ISSUE-1..3]}   (D-103 수정 → 회귀 D-201 발생)

[v1.1.0]
 8  qa__run_tests {version:"1.1.0", build_url}  → passed 11, failed 1 (TC-11)
 9  qa__verify_fixed_issues {run_id, fixed_issue_keys:[ISSUE-1..3]} → verified[3]
10  qa__create_issue (TC-11 회귀)              → ISSUE-4
11  qa__transfer_issues                         → issues[1]
12  dev__register_tickets                       → registered[ISSUE-4]
13  dev__fix_ticket ISSUE-4
14  dev__publish_release                        → {version:"1.2.0", fixed_issue_keys:[ISSUE-4]}

[v1.2.0]
15  qa__run_tests {version:"1.2.0"}             → passed 12, failed 0
16  qa__verify_fixed_issues [ISSUE-4]           → verified[ISSUE-4]
17  qa__approve_release {version:"1.2.0", run_id, comment}  → 사이클 APPROVED
    Claude: 결과 요약 2~3문장 (end_turn)
```

호출 수: v1.0.0 11회(1+1+3+1+1+3+1) + v1.1.0 7회 + v1.2.0 3회 = **21회**. Claude는 같은 21회를 병렬 묶음으로 **15턴**(마지막 요약 턴 포함)에 수행했습니다.

데모 중 각 시스템 화면에는 "데모 사이클 — 관찰용" 배너가 표시되고, 작업자는 "Host AI"로 기록됩니다.

```text
 Claude          Host(AgentRunner)            Dev MCP                QA MCP
   │ tool_use publish_initial_release            │                      │
   │────────────▶│ tools/call ──────────────────▶│ releases INSERT       │
   │             │◀── {version, build_url} ──────│                      │
   │◀ tool_result│                               │                      │
   │ tool_use run_tests(version, build_url)      │                      │
   │────────────▶│ tools/call ─────────────────────────────────────────▶│
   │             │                               │◀── GET /builds/N/1.0.0│
   │             │                               │── manifest ─────────▶│ 12건 판정
   │             │◀──────────────── {passed 9, failed 3, failures} ─────│
   │◀ tool_result│                               │                      │
   │ tool_use create_issue x3 (한 턴에 병렬)      │                      │
   │   ...                                       │                      │
```

### 8.2 실습 — 사용자 관점

| 단계 | 담당 / 웹 | 사용자 행동 | 화면 반응 |
| --- | --- | --- | --- |
| 1 | 매니저 / Hub | **실습 시작** | 토스트 "실습 시작", Hub 배너 "개발부서 차례", Dev 웹 사이드바 "차례", 발행 요청 #N 카드 |
| 2 | 최개발 / Dev | 작업자 선택 → **v1.0.0 발행** (노트 비우면 기본 문구) | 토스트 "검증부서 차례입니다". 2초 내 QA 웹에 검증 항목 12건 '대기' |
| 3 | 김검증·이검증·박검증 / QA | 담당 항목 **실행**(참고 결과) → **PASS** 또는 **FAIL**(이슈 대화상자, **AI 초안**) | Hub 배너 "판정 n/12", 타임라인에 판정 실시간 |
| 4 | (누구든) QA | 판정이 모두 끝나면 **Dev 이관** (확인 대화상자) | QA 배너 "개발부서 차례", 2초 내 Dev 웹에 티켓 3건 접수 |
| 5 | 최개발·정개발 / Dev | 티켓 **착수** → **수정 완료**(수정 내역 AI 초안) → 릴리즈 노트 **AI 초안** → **v1.1.0 발행** | QA 웹 '수정 확인'에 ISSUE-1~3, 항목 12건 대기 |
| 6 | QA | ISSUE-1~3 **수정 확인**, TC-11 FAIL → ISSUE-4 등록 → **Dev 이관** | 같은 방식으로 전달 |
| 7 | Dev | ISSUE-4 수정 → **v1.2.0 발행** | QA에 검증 요청 |
| 8 | QA | ISSUE-4 수정 확인, 12건 PASS → **버전 확정** | Hub 사이클 "승인", 파이프라인 완료 |
| 9 | 매니저 / Hub | **Agent Trace** | 실행 열: "사이클 시작", "Dev 발행 완료 전달 (v1.0.0)", "QA 이관 결정 전달 (v1.0.0)" ... |

QA 작업대의 이관/확정 버튼은 `readiness`로 활성화가 결정됩니다.

| 조건 | 이관 | 확정 |
| --- | --- | --- |
| 판정 대기(PENDING) 0건 + 이번 버전 수정 확인 대기 0건 | 필요 | 필요 |
| 이관 대상 > 0: OPEN/REOPENED 이슈 + 이전 버전에 반영되지 않은 TRANSFERRED(이월) 이슈 | 필요 | - |
| 미해결(OPEN/TRANSFERRED/REOPENED/FIXED) 0건 | - | 필요 |

조건 미충족 시 서버도 사유("판정하지 않은 항목이 n건 있습니다." 등)로 거부합니다(`_blocker`).

### 8.3 실습 — 시스템 관점

```text
 Hub 조정자(코드)         조정자 Claude / Mock          Dev 시스템               QA 시스템            사람
      │ start("manual")        │                          │                        │                  │
      │ _handoff("사이클 시작") ▶│ dev__request_release ──▶│ work#1 OPEN(initial)   │                  │
      │                        │◀─ {work_id:1, kind:initial}                        │                  │
      │                        │ "요청했습니다" (end_turn)│                        │                  │
      │ ┌─ 2초 폴링 루프 ──────────────────────────────────────────────────────────────────────┐   │
      │ │ dev__list_completed_work ─────────────────────▶│ []                      │                  │
      │ │ qa__list_completed_work ───────────────────────────────────────────────▶│ []               │
      │ │ ... (polls 증가)                                │◀──────── 최개발: v1.0.0 발행 ──────────────│
      │ │                                                 │ work#1 DONE, outcome{version,build_url}   │
      │ │ dev__list_completed_work ─────────────────────▶│ [work#1]               │                  │
      │ │ emit "완료 작업 발견: Dev 발행 완료 전달 (v1.0.0)"                         │                  │
      │ │ _handoff ─────────────▶│ qa__request_verification(outcome 그대로) ───────▶│ run OPEN, 12 PENDING
      │ │                        │ dev__ack_work(1) ──────▶│ work#1 ACKED           │                  │
      │ │                        │ "전달했습니다" (end_turn)│                        │                  │
      │ │ dev__ack_work(1) (보험, 멱등) ─────────────────▶│ 변화 없음               │                  │
      │ │ ...                                                                      │◀─ 판정·이슈·이관 ─│
      │ │ qa__list_completed_work ───────────────────────────────────────────────▶│ [work{decision:transfer}]
      │ │ _handoff ─────────────▶│ qa__transfer_issues ───────────────────────────▶│ OPEN → TRANSFERRED
      │ │                        │ dev__register_tickets(issues 그대로) ─▶│ 티켓 RECEIVED       │
      │ │                        │ dev__request_release ──▶│ work#2 OPEN(fix)       │                  │
      │ │                        │ qa__ack_work ──────────────────────────────────▶│ ACKED            │
      │ └──────────────────────────────────────────────────────────────── status가 APPROVED가 될 때까지
```

포인트는 다음과 같습니다.

1. **폴링(발견)은 코드, 판단(전달 방법)은 LLM**. 조정자 Claude는 상황 데이터(JSON)와 조정 규칙(프롬프트)을 받아, 어느 도구를 어떤 순서·인자로 부를지 결정합니다.
2. **값은 그대로 전달**: 프롬프트 규칙 "주어진 값을 바꾸지 않는다". `outcome.version`, `build_url`, `fixed_issue_keys`, `transfer_issues` 결과의 `issues`가 그대로 다음 도구의 인자가 됩니다.
3. **검증된 ack**: 필수 도구가 모두 성공한 경우에만 코드가 전달 직후 `ack_work`를 한 번 더 호출합니다(LLM이 ack를 빠뜨린 경우 대비). 전달이 일부만 수행되면 LLM의 ack도 가드가 막고, 완료 작업은 다음 폴링에서 다시 전달됩니다(5.5절).
4. Hub Overview 배너에 "Host는 2초마다 완료 작업을 확인합니다 (폴링 n회, 전달 m회)"가 표시됩니다. **사람이 오래 일할수록 폴링 수만 늘어나고 전달 수는 그대로**라는 점이 학습 관찰 포인트입니다.

2026-10-15 Claude 조정자 실습의 트레이스에서도 "사이클 시작" 실행에서 `dev__request_release` → 결과 `{"work_id": 7, "kind": "initial"}` → 한 문장 보고, "Dev 발행 완료 전달 (v1.0.0)" 실행에서 `qa__request_verification` → `dev__ack_work` → 보고, "QA 이관 결정 전달 (v1.0.0)" 실행에서 `qa__transfer_issues` → `dev__register_tickets` → (`dev__request_release` + `qa__ack_work`를 한 턴에 병렬) 순서가 확인됩니다.

### 8.4 세 가지 전달(handoff) 패턴

전체 실습 한 사이클의 전달은 시작 1회 + 아래 패턴 6회 = **7회**입니다.

| # | 패턴 | 트리거 (`event`) | 도구 호출 순서 | 사이클 상태 |
| --- | --- | --- | --- | --- |
| 0 | 사이클 시작 | `cycle_started` | `dev__request_release` | RELEASING |
| A | **발행 완료** (Dev → QA) | `dev_work_completed`, outcome에 version/build_url/fixed_issue_keys | `qa__request_verification(version, build_url, fixed_issue_keys)` → `dev__ack_work(work_id)` | → VERIFYING (iteration +1) |
| B | **이관** (QA → Dev) | `qa_work_completed`, `outcome.decision = "transfer"` | `qa__transfer_issues` → `dev__register_tickets(issues)` → `dev__request_release` → `qa__ack_work(work_id)` | → TRANSFERRING → FIXING |
| C | **확정** (QA → 기록) | `qa_work_completed`, `outcome.decision = "approve"` | `qa__approve_release(version, run_id, comment)` → `qa__ack_work(work_id)` | → APPROVED (루프 종료) |

순서: 0 → A(v1.0.0) → B(v1.0.0) → A(v1.1.0) → B(v1.1.0) → A(v1.2.0) → C(v1.2.0).
도구 호출(트레이스 대상): 1 + 2 + 4 + 2 + 4 + 2 + 2 = **17회**.

패턴 C의 `comment` 예 (MockCoordinator): `"검증부서 박검증 확정 (12 PASS)"`. Claude는 프롬프트에 따라 "결정한 부서원과 결과를 한 문장"으로 작성합니다.

---

## 9. 화면 구성

### 9.1 Hub 웹 (8300)

![Hub overview](../03_mcp/docs/images/hub_overview.png)

| 화면 | 구성 |
| --- | --- |
| **Overview** | (실습 중) 차례 배너 + "QA 웹/Dev 웹 열기" 버튼, 판정 진행 수, 폴링·전달 횟수 / 상태 카드 4개(사이클 상태·검증 횟수·도구 호출, 현재 버전, 최근 통과율, 미해결 이슈) / 파이프라인(발행 → 검증 → 이관 → 수정 → 승인) / 에이전트 요약(최근 보고) / 버전별 검증 결과 막대 / 이슈 현황(QA 상태 + Dev 티켓 상태 결합) / 최근 이벤트 12건(실시간) / 사이클 이력(모드, 상태, LLM 턴, 도구 호출, 에이전트) |
| **Releases / Test Runs / Issues / Timeline** | Stage 1·2와 같은 관제 화면. 데이터는 MCP resources 조합. 이슈 상세는 QA 이력과 Dev 티켓 이력을 시간순 병합 |
| **Agent Trace** | 상단 카드(에이전트, LLM 턴, 도구 호출, 입력/출력 토큰) + 단계 표(#, 턴, **실행 라벨**, 주체, 종류 tool_use/tool_result/text/error, 도구, 인자·결과 펼치기, 소요 ms) |
| **MCP Tools** | 서버별 카드(서버명 `dev-department`/`qa-department`, URL, 연결 상태, 오류) + 도구 표(MCP 이름, Claude 이름 `dev__...`, 설명, 입력 스키마 펼치기) + "Claude에 전달되는 tools (일부)" JSON |

상단 액션: **초기화**(확인 대화상자), **실습 시작**, **데모 실행**, 진행 중에는 **사이클 중단**.

![Agent Trace](../03_mcp/docs/images/trace.png)

위 Agent Trace 화면은 mock 조정자로 실습을 진행하던 중의 기록입니다. 실행 라벨별로 주체(`Planner`)의 `tool_use`, `DEV MCP`/`QA MCP`의 `tool_result`(수 ms), 한 문장 보고(text)가 이어집니다. Claude 조정자에서는 주체가 `Claude`로 바뀌고, 보고 생성에 전달 1회당 약 2~2.5초가 걸렸습니다(2026-10-15 측정).

본 장의 화면 캡처는 2026-10-16 데이터 폴더를 분리한 사본(기본 포트 8300~8302, mock)에서 1440x900으로 촬영했습니다. Dev 웹 캡처는 부분 수정 상황(ISSUE-1 수정 완료, ISSUE-2 착수, ISSUE-3 접수)이라 발행 요청 카드에 "미완료 2건은 이번 버전에 반영되지 않고 상태가 유지됩니다" 안내가 보입니다.

### 9.2 Dev 웹 (8301)

![Dev web](../03_mcp/docs/images/dev_web.png)

| 영역 | 내용 |
| --- | --- |
| 개발 작업대 — 발행 요청 카드 | "발행 요청 #N vX.Y.Z", Host 요청 배지, 반영될 티켓 / 미완료 티켓 안내, 릴리즈 노트 입력(`data-preserve`로 부분 갱신 시 유지), **AI 초안**, **vX.Y.Z 발행**(확인 대화상자) |
| 접수된 티켓 | 키·TC·발견 버전, 제목·설명(수정 후에는 수정 내역), 심각도, 상태, 담당, **착수 / 내가 맡기 / 수정 완료**(수정 내역 대화상자 + AI 초안) |
| Host 요청 이력 | OPEN(작업 중) / DONE(Host 회수 대기) / ACKED(QA에 전달 완료) / CANCELLED(사이클 중단으로 취소) |
| 발행 이력 | 버전, 반영 이슈, 발행자, `build.json` 링크(`/builds/{cycle}/{version}`) |
| 티켓 / 타임라인 | 티켓 목록·상세(이력), Dev 이벤트("Host" 행은 AI가 도구로 요청한 작업) |

### 9.3 QA 웹 (8302)

![QA web](../03_mcp/docs/images/qa_web.png)

| 영역 | 내용 |
| --- | --- |
| 상태 배너 | 이관 결정 후 "개발부서 차례", 확정 후 "버전 확정", 중단 시 "중단"(검증 요청 취소 안내) |
| 요약 카드 | 검증 대상 버전(사이클·Run), 판정 진행 n/12, PASS/FAIL·내 담당 건수, 수정 확인 대기 |
| 검증 항목 | 내 항목/전체 탭(작업자 기준 필터, 담당 행 강조), 실행 결과(참고), 판정 배지·판정자, "실행 결과와 다름" 표시, **실행 / PASS / FAIL** |
| 이슈 등록 대화상자 | 제목, 심각도, 설명, 기존 미해결 이슈 연결 안내, **AI 초안**(실패 로그 기반), "등록 후 FAIL 처리" |
| 수정 확인 | Host가 이번 버전에 반영됐다고 전달한 이슈, **재현 / 수정 확인** |
| 이 버전에서 등록한 이슈 | 키, 제목, 등록자, 심각도, 상태 |
| 액션바 | 판정 대기·수정 확인 대기·이관 대상(이전 버전 미반영 건수 포함)·미해결 건수, **Dev 이관 / 버전 확정**(조건부 활성, 확인 대화상자) |

### 9.4 공통 웹 계층

Stage 2와 같은 구조입니다. `base.html`(사이드바, 상단 사이클 상태, 작업자 선택, 공통 대화상자), `app.js`(SSE·부분 갱신·토스트·테마), `workbench.js`(작업자 선택과 `?as=이름` 바로가기, 부서 불일치 경고, 작업대 버튼, 대화상자, AI 초안, 실습 시작/중단), `common/work.py`(명부·오류). 작업자 선택은 `localStorage`에 기억됩니다.

---

## 10. LLM 활용

### 10.1 LLM이 쓰이는 곳

| 용도 | 위치 | 호출 방식 | 실패 시 |
| --- | --- | --- | --- |
| 데모 에이전트 | `agent.py` `_agent_loop(SYSTEM_PROMPT)` | Messages API 도구 루프, 전체 17 도구, 최대 80턴 | 예외 → 사이클 FAILED (mock 모드는 MockPlanner) |
| 실습 조정자 | `agent.py` `_handoff` → `_agent_loop(COORDINATOR_PROMPT)` | 전달 1회당 짧은 루프, 조정용 9 도구, 최대 12턴 | 예외 → 사이클 FAILED (mock 모드는 MockCoordinator) |
| AI 초안 (이슈) | `qa_mcp/service.py` `draft_issue` | `LLMClient.json` 구조화 출력(`ISSUE_SCHEMA`) | 템플릿 fallback (`source: "mock"`) |
| AI 초안 (수정 내역, 릴리즈 노트) | `dev_mcp/service.py` `write_fix_note`, `write_release_note` | `LLMClient.json` (`FIX_SCHEMA`, `NOTE_SCHEMA`) | 템플릿 fallback |

공통 설정: `claude-opus-5-5`(환경변수 `CLAUDE_MODEL`), `output_config.effort = "low"`, 베타 `server-side-fallback-2026-07-01` + `fallbacks="default"`. 에이전트 루프는 `cache_control={"type": "ephemeral"}`로 프롬프트 캐싱을 켜서, 턴마다 반복되는 시스템 프롬프트·도구 정의·이전 대화의 입력 비용을 줄입니다.

`LLM_MODE`: `auto`(키가 있으면 llm, 없으면 mock), `llm`, `mock`. `run.py --mock`은 `LLM_MODE=mock`을 세 프로세스 모두에 설정합니다.

### 10.2 데모 시스템 프롬프트 (`SYSTEM_PROMPT`) 요약

- 역할: "커머스 웹 서비스의 릴리즈 관리 AI 에이전트". dev__ / qa__ 도구로 사이클을 끝까지 진행.
- 업무 절차 8단계: 최초 발행 → `qa__run_tests`(발행 결과의 version·build_url 그대로) → 수정 이슈 확인 → 실패마다 이슈 등록(제목 40자, 설명 2~3문장, 심각도 기준: 결제/매출 Critical, 주요 기능 Major, 불편 Minor) → 실패 없으면 승인 → 있으면 이관 → 티켓 등록 → 티켓마다 수정 → 차기 발행 후 2번으로.
- 규칙: 모든 호출에 cycle_id, 검증 최대 5회, 한국어 간결, 독립 호출은 한 번에 함께 요청 가능, 마치면 2~3문장 요약.

### 10.3 조정자 시스템 프롬프트 (`COORDINATOR_PROMPT`) 발췌

```text
당신은 개발부서 시스템과 검증부서 시스템을 잇는 조정 AI 에이전트입니다.
두 시스템은 서로를 모르며, 실제 업무는 각 부서의 사람이 합니다. 당신은 한 부서가 끝낸 작업의 결과를 다른 부서에 전달합니다.

상황별 처리:
- 사이클 시작: dev__request_release(cycle_id)로 개발부서에 최초 버전 발행을 요청한다.
- 개발부서 발행 완료(outcome에 version, build_url, fixed_issue_keys): qa__request_verification에 그 값을 그대로 넣어
  검증을 요청하고, dev__ack_work(work_id)로 확인 처리한다.
- 검증부서 '이관' 결정(outcome.decision = transfer): qa__transfer_issues로 이관 데이터를 받고, 결과의 issues를 그대로
  dev__register_tickets에 전달한 뒤, dev__request_release로 다음 버전 발행을 요청하고, qa__ack_work(work_id)로 확인 처리한다.
- 검증부서 '확정' 결정(outcome.decision = approve): qa__approve_release(cycle_id, version, run_id, comment)로 확정을 기록하고
  qa__ack_work(work_id)로 확인 처리한다. comment에는 결정한 부서원과 결과를 한 문장으로 쓴다.

규칙: 주어진 값을 바꾸지 않는다. 끝나면 무엇을 어디에 전달했는지 한 문장으로 보고한다.
```

조정자에게 들어가는 user 메시지 예:

```text
QA 이관 결정 전달 (v1.0.0)

상황 데이터:
{
 "event": "qa_work_completed",
 "cycle_id": 4,
 "work": {"work_id": 7, "run_id": 10, "outcome": {"decision": "transfer", "cycle_id": 4, "version": "1.0.0",
          "run_id": 10, "passed": 9, "failed": 3, "decided_by": "박검증"}, "done_at": "..."}
}
```

### 10.4 tool_use 처리와 최대 턴

- `stop_reason`이 `tool_use`면 도구를 실행하고 계속, `end_turn`이면 종료, `pause_turn`이면 같은 메시지로 재요청, 그 밖(`max_tokens` 등)은 `Host / error` 단계로 기록하고 종료합니다.
- 최대 턴: 데모 `MAX_AGENT_TURNS`(기본 80), 전달 1회 12턴. 데모는 별도로 `qa__run_tests` 5회 제한을 Host가 강제합니다.
- 데모에서 Claude가 승인 없이 종료하면 `FAILED`("승인 없이 에이전트가 종료됨")로 기록합니다.

### 10.5 mock과의 동등성

| 관점 | LLM | mock |
| --- | --- | --- |
| 다음 도구 결정 | Claude(프롬프트 해석) | `MockPlanner` / `MockCoordinator` 코드 |
| 도구 실행 경로 | `execute_tool` → `McpHub.call_tool` → MCP | 동일 |
| 판정(PASS/FAIL) | 서버의 결정적 시뮬레이터 | 동일 |
| 문서 내용 | Claude 작성 | 템플릿 |
| 결과 | 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED | 동일 |
| 오류 대응 | 오류 결과를 읽고 재시도·분석·보고 | 즉시 실패 |

### 10.6 비용과 시간 (측정치)

| 시나리오 | 모드 | 시간 | LLM 턴 | 도구 호출 | 토큰 |
| --- | --- | --- | --- | --- | --- |
| 데모 (지연 0) | mock | 약 1초 | - | 21 | - |
| 데모 (지연 0) | Claude | 약 48초 | 15 | 21 | - |
| 실습 (부서원 작업은 API로 수행) | Claude 조정자 | 사람 작업 시간에 좌우 | 22 (전달 7회) | 17 | 입력 약 4.9만 |

실습에서 LLM 비용은 **전달 횟수에 비례**하고 사람의 작업 시간과는 무관합니다(폴링은 코드가 수행). 2026-10-15 Claude 조정자 트레이스에서 전달 1회의 보고 생성은 약 2~2.5초였습니다. 표의 값은 모두 2026-10-15 측정치입니다.

---

## 11. 예외 처리와 운영

### 11.1 도구 오류를 LLM에 돌려주기

```text
 서비스 WorkError("반영할 수정 완료 티켓이 없습니다...")
   └▶ server.py run(): raise ToolError(str(exc))
        └▶ MCP 결과 {content:[text], isError:true}
             └▶ McpHub.call_tool(): (True, text, None)
                  └▶ _agent_loop: tool_result {is_error: true, content: text}
                       └▶ Claude가 오류 문구를 읽고 순서를 바로잡거나 보고
```

- 연결 실패·시간 초과·알 수 없는 도구도 예외가 아니라 `is_error` 결과로 LLM에 전달됩니다.
- 오류 메시지는 다음 행동을 안내하도록 작성했습니다. 예) "cycle 3에는 이미 최초 릴리즈가 있습니다. publish_release를 사용하세요.", "{key} 티켓이 없습니다. register_tickets로 먼저 등록하세요."
- 점검 결과: 데모 중 Dev MCP를 중지하자 Claude가 재시도 후 상황을 분석해 보고하고 종료했습니다(사이클 FAILED). Agent Trace에는 `error` 배지(빨강)로 표시되고, `tool.error` 이벤트가 타임라인에 남습니다.
- mock은 오류 시 `RuntimeError`로 사이클을 실패 처리합니다.

### 11.2 사람 작업 쪽 오류

| 상황 | 응답 | 위치 |
| --- | --- | --- |
| Host 요청 없이 Dev 작업 | 409 "지금은 개발부서 차례가 아닙니다. (Host의 발행 요청이 없습니다)" | `DevSystem._order` |
| Host 요청 없이 QA 작업 | 409 "지금은 검증부서 차례가 아닙니다. (Host의 검증 요청이 없습니다)" | `QASystem._run` |
| 다른 부서원으로 작업 | 403 "상단에서 ... 작업자를 선택하세요. (이 시스템의 부서원만 작업할 수 있습니다)" | `Members.require` |
| 판정 남은 상태로 이관/확정 | 400 사유 메시지, 버튼 비활성 | `QASystem._blocker` |
| 수정 내역/릴리즈 노트 비움 | 400 "...('AI 초안'으로 작성할 수 있습니다)" | `DevSystem.fix`, `release` |
| 빌드 다운로드 실패 | 502 "빌드 다운로드 실패: ..." | `QASystem._download` |

웹 계층은 `WorkError`를 `{"error": ...}` JSON과 해당 상태 코드로 돌려주고, `workbench.js`가 "처리할 수 없음" 토스트로 보여 줍니다.

### 11.3 멱등 ack와 중복 방지

- `ack_work`: `UPDATE ... WHERE id = ? AND status = 'DONE'` — 여러 번 호출해도 안전. 조정자는 필수 도구가 모두 성공했을 때만, LLM의 ack와 별개로 코드에서 한 번 더 호출합니다.
- `request_release` / `request_verification`: 같은 사이클의 OPEN 요청이 있으면 기존 `work_id` 반환(재전달은 검증 횟수에 넣지 않음).
- `transfer_issues`: OPEN/REOPENED를 이관한 뒤 TRANSFERRED 전체를 반환하므로, 반복 호출해도 같은 목록.
- `create_issue` / `staff_issue`: 같은 TC의 활성 이슈가 있으면 기존 키 반환.
- `fix_ticket`: 이미 FIXED면 그대로 반환.
- `register_tickets`: 같은 키가 있으면 재접수로 처리(중복 행 없음, UNIQUE 제약). 이미 RECEIVED면 변화 없음.
- `cancel_work`: OPEN 요청만 취소하므로 반복 호출해도 안전.

### 11.4 기동 정리와 재개

- `AgentRunner.__init__`: 이전 프로세스에서 끝나지 못한 **데모** 사이클을 `CANCELLED`("서버 재시작으로 중단")로 정리합니다. 데모는 Claude 대화 문맥이 메모리에만 있어 이어 갈 수 없기 때문입니다.
- `lifespan` → `runner.resume()`: 활성 **실습** 사이클이 있으면 `_coordinate(resumed=True)`로 폴링을 재개합니다. 실습 상태는 모두 Dev/QA DB의 작업 지시에 있으므로, 시작 전달을 건너뛰고 폴링만 다시 시작하면 됩니다. Host가 꺼져 있는 동안 부서원이 끝낸 작업도 DONE으로 남아 있다가 재기동 후 전달됩니다.
- Host 종료(`stop()`)는 작업 지시를 취소하지 않습니다. 취소는 사용자 중단(`abort()`)에서만 합니다.
- `McpConnection`은 연결 실패 시 2초 간격으로 계속 재연결하고, 데모는 `wait_ready(timeout=10)`, 실습은 `timeout=30` 동안 연결을 기다립니다. 초과 시 사이클은 FAILED("MCP 서버에 연결할 수 없습니다." 등)가 됩니다.
- 재개는 2026-10-16 격리 사본에서 실제로 시험했습니다(12장 #16).

### 11.5 중단과 초기화

| 동작 | 처리 |
| --- | --- |
| 사이클 중단 (`POST /api/work/abort`) | 태스크 취소 → `CANCELLED`, "사이클 #N 중단" 이벤트. 실습이면 이어서 Dev/QA `cancel_work`로 OPEN 작업 지시(QA는 검증 실행 포함)를 취소하고 응답하므로, 응답 직후 두 작업대에서 "차례"와 작업 버튼이 사라짐. 실행 중이 아니면 409 |
| 초기화 (`POST /api/reset`) | 실행 중이면 409. Dev/QA `/admin/reset` 호출(하나라도 실패 시 503) → host.db 전체 삭제 → `system.reset` 이벤트로 모든 화면 새로고침. Dev는 명부 재시드, QA는 `test_cases` 유지 + 담당자 재시드 |
| 동시 실행 방지 | `start()`가 실행 중 태스크 또는 활성 사이클이 있으면 `CycleConflict` → 409 "이미 진행 중인 사이클이 있습니다." |

### 11.6 운영 설정

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `LLM_MODE` | `auto` | auto / llm / mock |
| `ANTHROPIC_API_KEY` | - | 있으면 auto에서 llm |
| `CLAUDE_MODEL` | `claude-opus-5-5` | 에이전트·AI 초안 모델 |
| `MAX_AGENT_TURNS` | `80` | 데모 최대 턴 |
| `STEP_DELAY` | `0.4` | 데모 도구 호출 사이 관찰용 지연(초) |
| `TEST_DELAY` | `0.1` | 데모 테스트 1건당 지연(초) |
| `HOST_PORT` / `DEV_PORT` / `QA_PORT` | 8300 / 8301 / 8302 | 포트. `run.py` 기동 메시지, 사이드바 배지, 안내 문구도 이 값을 표시 |
| `DEV_URL` / `QA_URL` | `http://127.0.0.1:{포트}` | Host가 접속할 주소 (MCP는 `{URL}/mcp`) |

`.env`는 단계 폴더, 프로젝트 루트 순으로 읽습니다(`override=False`).

---

## 12. 점검 결과

### 12.1 최초 점검 (2026-10-15)

| # | 항목 | 방법 | 결과 |
| --- | --- | --- | --- |
| 1 | 도구 발견 | MCP Tools 화면에서 도구·스키마·Claude 변환 결과 확인 | 통과 (당시 Dev 8개, QA 9개. `cancel_work` 추가 전) |
| 2 | mock 데모 | `--mock` 데모 실행 (지연 0) | 9/3 → 11/1 → 12/0 → APPROVED, 도구 호출 21회, 약 1초 |
| 3 | Claude 데모 | `claude-opus-5-5` 데모 실행 (지연 0) | 같은 결과, 15턴, 약 48초, 도구 호출 21회 |
| 4 | mock 실습 | 부서원 작업을 REST API로 수행 | v1.0.0 → v1.1.0(회귀) → v1.2.0 확정, 전달 7회 |
| 5 | Claude 조정자 실습 | 4번과 같은 절차를 Claude 조정자로 수행 | 완주, 전달 7회, 22턴, 입력 약 4.9만 토큰. 상황마다 올바른 순서로 전달하고 한 문장으로 보고 |
| 6 | 차례·권한 | Host 요청 없이 작업, 타 부서원으로 작업 | 409 / 403 |
| 7 | 도구 오류 복구 | 데모 중 Dev MCP 중지 | Claude가 재시도 후 상황을 분석·보고, 사이클 FAILED (`is_error` 경로 확인) |
| 8 | 전 화면 렌더링 | Hub·Dev·QA 모든 페이지 | 정상 |
| 9 | 4단계 동시 회귀 | 01~04 단계를 동시에 기동해 각 데모 결과 비교 | 4단계 모두 같은 결과 |

### 12.2 결함 수정과 재점검 (2026-10-16)

모든 재점검은 사용자 데이터(`03_mcp/data`)와 분리된 **격리 사본**에서 했습니다. 사본은 포트 8310~8312(`HOST_PORT`/`DEV_PORT`/`QA_PORT`, 화면 캡처만 8300~8302), `LLM_MODE=mock`, 자체 `data/` 폴더로 기동했습니다. 따라서 이번 재점검의 조정자는 mock이며, Claude 조정자 경로는 다시 시험하지 않았습니다.

| # | 항목 | 방법 | 결과 |
| --- | --- | --- | --- |
| 10 | 결함: Hub "실습 시작" 무반응 | 브라우저에서 클릭 | 원인: `base.html`이 `{% if members %}`일 때만 `workbench.js`를 로드. Hub는 명부가 없어(`members: None`) 스크립트가 빠졌고, `data-action="work-start"` 핸들러가 `workbench.js`에 있어 클릭이 무시됨. 수정: `workbench.js`를 항상 로드(작업자 선택 요소가 없으면 해당 기능을 건너뛰므로 Hub에서도 안전) |
| 11 | 도구 발견 | Host 기동 로그 | Dev 9개, QA 10개 도구 연결 (`connected dev (9 tools)`, `connected qa (10 tools)`). 기동 메시지가 8311/8312/8310을 표시 |
| 12 | mock 데모 | `/api/cycles` 실행 후 `/api/cycles/{id}/summary` | APPROVED, 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, 검증 3회, 도구 호출 21회. `STEP_DELAY` 0.4초 기본값으로 13.6초 |
| 13 | 데모 진행 막대 | 데모 중 Hub SSE 수신 | `test.progress` 36건(3회 × 12건) 수신 |
| 14 | **실제 브라우저 전 과정 클릭** | Playwright + Edge(headless). Hub 실습 시작 → Dev 발행 → QA 12건 실행·판정(FAIL은 이슈 대화상자 + AI 초안) → Dev 이관 → Dev 착수·수정 완료(AI 초안) → 반복 → 버전 확정 | Hub 사이클 APPROVED, 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, 전달 7회, 도구 호출 17회, Agent Trace 41행, 페이지 오류 0건 |
| 15 | **중단 직후 작업대** | (1) QA 차례 중단 (2) Dev 차례 중단 (3) 새 사이클 진행 후 중단. 중단 API 응답 직후 두 작업대 HTML 확인 | 세 경우 모두 Dev 작업대에 발행 요청 카드·"차례" 배지 없음, QA 작업대에 작업 버튼·"차례" 배지 없음, QA 배너 "중단". 중단 뒤 Dev 발행 시도는 409. 새 사이클의 검증 요청은 새 사이클로 정상 전달 |
| 16 | **Host 재시작 재개** | (1) 실습 시작 → Dev v1.0.0 발행 → QA 검증 요청 후 세 프로세스 모두 종료·재기동 (2) Dev 수정 중 Host만 종료하고 그동안 Dev v1.1.0 발행 → Host 재기동 | (1) 재기동 후 사이클 #7 VERIFYING 유지, 폴링 재개, QA 이관이 Dev 티켓으로 전달됨 (2) Host 재기동 후 DONE 상태로 남은 v1.1.0 발행을 찾아 QA에 전달. 이후 v1.2.0 확정까지 완주: APPROVED, 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, 검증 3회, 전달 7회, 도구 호출 17회, 작업 지시 Dev 3건·QA 3건 모두 ACKED |
| 17 | 전달 검증 후 ack | 임시 DB에서 실제 `DevSystem`·`QASystem`·`AgentRunner.execute_tool`을 쓰고, `dev__register_tickets` 첫 시도만 실패시키는 가짜 Hub로 실행 | 1차: `register_tickets` 실패 후 `qa__ack_work`가 가드에 막혀 QA 서버까지 가지 않음. 2차(재전달): `transfer_issues`가 ISSUE-1을 다시 반환, 등록·발행 요청·ack 성공. 같은 이관 데이터를 한 번 더 등록해도 티켓 이력 1행 유지 |
| 18 | 결함: 부분 수정 교착 | `partial3.py`: QA가 ISSUE-1~3 이관 → Dev가 ISSUE-1만 수정(ISSUE-2는 착수만) 후 v1.1.0 발행 → QA가 ISSUE-1 확인, 12건 판정(10/2, TC-05·TC-08 FAIL은 TRANSFERRED인 ISSUE-2·3에 연결) | 수정 전: 이관 400("이관할 이슈(OPEN/REOPENED)가 없습니다"), 확정도 거부되어 **교착 재현**. 수정 후: ISSUE-2·3 재이관 → Dev 티켓 ISSUE-2 IN_PROGRESS(정개발)·ISSUE-3 RECEIVED 유지 → v1.2.0 11/1(회귀 ISSUE-4) → v1.3.0 12/0 → **APPROVED**, ISSUE-1~4 VERIFIED, 전달 9회 |
| 19 | 수정 후 회귀 점검 | #18 수정 반영 사본에서 #12·#14·#15·#17 재실행 | mock 데모 9/3 → 11/1 → 12/0 APPROVED(도구 호출 21회), 브라우저 클릭 실습 APPROVED(Agent Trace 41행, 페이지 오류 0건), 중단 3종 통과, ack 가드·재전달 통과 |
| 20 | 화면 캡처 | 데이터 폴더를 분리한 사본을 **기본 포트 8300~8302**로 기동(당시 사용 중인 프로세스 없음 확인), Playwright, 1440x900 | `hub_overview.png`, `qa_web.png`, `dev_web.png`, `trace.png` 재촬영, 페이지 오류 0건 |

참고:

- #14의 Agent Trace 41행은 mock 조정자 기준 계산값과 같습니다. 트레이스 대상 도구 호출 17회 × (tool_use + tool_result) 2행 = 34행에 전달 7회의 요약 text 7행을 더한 값입니다(폴링과 보험용 ack는 트레이스에 남지 않음).
- #17은 `_coordinate` 루프 전체가 아니라 가드와 재전달 멱등성을 단위 수준에서 확인한 것입니다. 실제 MCP 장애 중 재전달(LLM 조정자)은 시험하지 않았습니다.

---

## 13. 한계와 알려진 이슈

### 13.1 구조적 한계 (MCP 방식 자체)

| 한계 | 설명 |
| --- | --- |
| 수직 구조 | Dev/QA는 스스로 판단하거나 서로 대화하지 않는 수동적 도구입니다. 모든 연결이 Host 한 곳을 지나므로 Host가 단일 장애점이자 병목입니다 |
| 세부 지시 부담 | 데모에서 중앙 LLM이 테스트 실행, 이슈 등록, 티켓 수정을 하나하나 직접 지시합니다. 업무 절차가 전부 Host 프롬프트에 있습니다 |
| 장시간 작업 | 도구 호출은 요청-응답 단위라 사람의 작업 완료를 알 표준 방법이 없습니다. Host가 2초마다 폴링합니다(최대 2초 지연 + 불필요한 호출) |
| 진행 상황 | "몇 건 판정했는지" 같은 진행 상황을 서버가 알려 주지 않아 Host가 `qa://status` 리소스를 따로 읽습니다 |
| 중단 통지 | Host의 중단을 부서 시스템이 알 표준 방법도 없습니다. 그래서 `cancel_work`라는 업무 도구를 따로 만들어 Host 코드가 호출합니다 |
| 도구 수 증가 | 데모 Claude에는 조정용 도구까지 17개가 노출됩니다. 부서·도구가 늘면 컨텍스트와 선택 정확도에 부담이 됩니다 |

### 13.2 코드 이슈와 조치 (2026-10-16 기준)

| # | 이슈 | 조치 | 상태 |
| --- | --- | --- | --- |
| K-1 | Host 재시작 재개 미시험 | 전체 재시작, Host 단독 중지 중 부서 작업 완료의 두 경우를 시험(12장 #16). 코드 변경 없이 정상 재개. 재개 중 재전달이 안전하도록 K-4의 멱등화를 함께 적용 | **수정 완료** (시험 완료) |
| K-2 | QA `request_verification`의 OPEN 검사가 사이클과 무관해, QA 차례에 중단한 뒤 새 실습을 시작하면 흐름이 멈춤 | Dev `request_release`·QA `request_verification`이 다른 사이클의 OPEN 요청을 CANCELLED로 정리(QA는 검증 실행도)하고, 중복 검사는 같은 사이클 안에서만 함 | **수정 완료** |
| K-3 | 중단 직후 Dev/QA의 OPEN 작업 지시가 남아 부서 웹에 "차례"로 보임 | Dev/QA에 `cancel_work(cycle_id)` MCP 도구 추가(`dev_mcp/service.py` `DevSystem.cancel_work`, `qa_mcp/service.py` `QASystem.cancel_work`). Host `AgentRunner.abort()`가 실습 사이클 중단 시 두 서버에 호출. `MANUAL_TOOLS`에 없고, 데모 Claude에도 `HOST_ONLY_TOOLS`로 제외. Host 종료(`stop()`)에서는 호출하지 않아 재개와 충돌하지 않음 | **수정 완료** (12장 #15) |
| K-4 | 조정자가 전달 성공 여부와 무관하게 보험용 `ack_work`를 호출해, 일부 실패한 전달도 재시도되지 않음 | 전달 패턴별 필수 도구(`REQUIRED_CALLS`)를 모두 성공했을 때만 ack. LLM의 `ack_work`도 `execute_tool`의 가드가 막음. 실패 시 다음 폴링에서 재전달, 같은 작업 3회 실패 시 사이클 FAILED. 재전달 대비로 `transfer_issues`(이관된 이슈 재반환)·`register_tickets`(RECEIVED 유지)·검증 횟수 집계 멱등화 | **수정 완료** (12장 #17, 단위 수준) |
| K-5 | QA 판정 시 담당자 일치 검사 없음 | Stage 1(`01_standalone/.../workbench.py` `judge`)도 부서 소속만 확인하고 담당자는 검사하지 않음을 확인. 전 단계 공통 정책(담당은 화면 필터·강조 용도, 같은 부서원은 대신 판정 가능)으로 유지 | 의도된 설계 (Stage 1과 동일) |
| K-6 | `_agent_loop`의 `stop_on_approve` 인자 미사용 | 인자 제거 | **수정 완료** |
| K-7 | 포트 변경 시 표시 문자열이 8300/8301/8302로 고정 | `run.py`가 `common.config`의 포트를 표시, Hub·Dev·QA 사이드바 배지와 하단 "시스템" 표시, Overview 안내, Dev 작업대 안내가 설정값 사용 | **수정 완료** (8310~8312 기동 메시지·화면에서 확인) |
| K-8 | Overview 배너의 "2초"가 고정 문자열 | `POLL_SECONDS` 상수 값을 템플릿에 전달 | **수정 완료** |
| K-9 | 인증 없음: MCP `/mcp`와 `/api/work/*`는 로컬 무인증, `actor`는 요청 본문 값 | 학습용 범위로 유지 | 한계로 유지 |
| K-10 | `app.js`의 `test.progress`(진행 막대) 처리가 Stage 3에서 동작하지 않음 | QA `run_tests`가 테스트 1건마다 `test.progress`를 발행하도록 복구(Stage 2와 같은 방식). 미사용 토스트 유형(`integration.error`, `webhook.received`)은 제거하고 `work.cancelled`, `work.retry` 추가 | **수정 완료** (12장 #13) |
| K-11 | 공통 테이블 `integration_logs`와 `integration_log.html` 미사용 | 스키마와 템플릿 제거 | **수정 완료** |
| K-12 | 부분 수정 교착: Dev가 일부 티켓만 수정해 발행하면, 반영되지 않은 이슈는 QA에서 TRANSFERRED로 남음. 이관은 OPEN/REOPENED가 있어야, 확정은 미해결이 없어야 가능해 둘 다 막힘 | QA `readiness`가 TRANSFERRED(이월) 이슈를 이관 대상에 포함(`carried`), `transfer_issues`가 이월 이슈를 함께 반환, Dev `register_tickets`는 미반영 티켓(RECEIVED/IN_PROGRESS/미발행 FIXED)의 상태·담당자 유지. QA 액션바·Dev 발행 카드 문구 정리. 조정자 패턴과 `REQUIRED_CALLS`는 그대로 동작(Stage 4와 같은 방식) | **수정 완료** (12장 #18, #19) |

남은 제약:

- mock 조정자는 도구 오류 시 재전달하지 않고 즉시 사이클을 실패 처리합니다(LLM처럼 복구하지 않는 mock의 원래 성격).
- 중단 시 Dev/QA MCP가 꺼져 있으면 `cancel_work`가 실패합니다(로그 경고). 이 경우 다음 실습 시작 때 K-2의 정리 로직이 남은 요청을 취소합니다.
- 전달 도중 Host가 강제 종료되면 재개 후 같은 완료 작업을 다시 전달합니다. 멱등화로 데이터는 중복되지 않지만, 트레이스에는 전달이 두 번 기록될 수 있습니다.

### 13.3 문서 정리 (2026-10-16)

| 문서 | 이전 내용 | 조치 |
| --- | --- | --- |
| PRD 3.1·3.2 | 초기 설계의 도구명(`start_release_cycle`, `receive_issues`, `fix_issue`, `verify_issues`), `cycle_id` 누락, resource `dev://releases`·`qa://issues` 등 | 현재 도구 19개(서명 포함)와 resource URI로 갱신, `cancel_work` 추가 |
| PRD 3.1·6 | `GET /builds/{version}` | `GET /builds/{cycle_id}/{version}` |
| PRD 6.1 | 조정 도구 목록 | `cancel_work` 추가, 요구 F-12(검증된 ack)·F-13(중단 즉시 취소) 추가 |
| README "MCP 구성" | 실습 도구에 `list_tickets`·`list_issues` 누락, `list_completed_work`를 조정자 도구처럼 기재 | "데모 / 조정자 Claude 노출 / Host 코드만 호출" 세 열로 재구성, 포트 환경변수와 2026-10-16 점검 결과 추가 |
| TODO, USER_SCENARIO | 초기 도구명, 중단·재전달 시나리오 없음 | 도구명 정정, 2026-10-16 정리 항목과 예외 시나리오 추가 |
| `docs/images/*.png` | 에이전트 요약 설명이 옛 문구, Claude 조정자 실행 중 화면 | 현재 화면으로 재촬영(데이터 분리 사본, 기본 포트 8300~8302, mock, 1440x900) |
| PRD, README, USER_SCENARIO | 부분 수정 시 진행 방법 없음 | `transfer_issues`·`register_tickets` 설명 갱신, 요구 F-14와 예외 시나리오 추가 |

---

## 14. 학습 포인트

### 14.1 Stage 2(API)와의 비교

| 관점 | Stage 2 — REST API + Webhook | Stage 3 — MCP |
| --- | --- | --- |
| 연결 형태 | Dev ↔ QA **직접**(서로의 주소를 설정으로 앎) | Dev, QA는 서로 모름. **Host → 각 서버**만 존재 |
| 흐름 결정 주체 | 코드(Webhook 체인, 사전 합의된 API 계약) | 데모: LLM이 업무 절차 전체 / 실습: LLM이 전달 규칙을 해석 |
| 인터페이스 발견 | 사전 합의, OpenAPI 문서(`/docs`)를 사람이 읽고 코드 작성 | `tools/list`로 **런타임 자동 발견**, 스키마가 LLM 도구 정의로 직행 |
| 새 기능 추가 | 호출 측 코드 수정 필요 | 서버에 도구 추가 → Host 재연결 시 Claude가 바로 사용 (Host 코드 수정 불필요) |
| 사람 작업 완료 통지 | Webhook으로 즉시 push | 표준 통지 없음 → Host가 **폴링** |
| Hub의 역할 | 관찰자(흐름에 끼어들지 않음) | **흐름의 중심**(관찰 + 조정) |
| 오류 처리 | HTTP 상태코드, 재시도 코드 | `isError` 결과를 LLM이 읽고 판단 |
| 판단 위치 | 각 시스템 코드 | 중앙 LLM |

### 14.2 "MCP는 에이전트 → 도구(수직) 연결이다"

```text
           Stage 3 (MCP): 수직                         Stage 4 (A2A): 수평
                                                  
              ┌────────┐                          ┌──────────┐      ┌──────────┐
              │  Host  │  ← 판단·절차·문맥 전부     │ Dev 에이전트│◀────▶│ QA 에이전트│
              │ (LLM)  │                          │ (자기 판단) │ 업무 │ (자기 판단) │
              └─┬────┬─┘                          └────┬─────┘ 위임 └────┬─────┘
       tools/call│    │tools/call                      │ MCP 등            │
                ▼    ▼                                 ▼                  ▼
          ┌──────┐  ┌──────┐                      Dev 시스템          QA 시스템
          │ Dev  │  │  QA  │  ← 수동적 도구
          │ 도구 │  │ 도구 │
          └──────┘  └──────┘
```

- MCP의 기본 관계는 **하나의 에이전트가 여러 도구를 쓰는 것**입니다. 서버는 "무엇을 할 수 있는지"만 설명하고, "언제·왜 할지"는 모두 Host가 결정합니다.
- 그래서 데모의 Claude는 업무 전체를 알아야 하고, 실습에서도 두 부서의 상호작용 규칙을 Host 프롬프트가 모두 들고 있습니다.
- MCP가 잘하는 것: 기존 시스템의 기능을 AI가 쓰기 좋게 표준화된 방식으로 노출하는 일(도구 발견, 스키마, 오류 전달). 이것은 Stage 4에서도 각 부서 에이전트가 자기 시스템을 다룰 때 계속 유효한 방식입니다.

### 14.3 Stage 4(A2A)로 가는 동기

이번 단계에서 직접 겪은 불편이 곧 A2A가 다루는 문제입니다.

| Stage 3에서 겪은 문제 | A2A가 제시하는 방향 |
| --- | --- |
| 사람의 장시간 작업을 도구 호출 하나로 표현할 수 없어 2초 폴링 | **Task**: 수명주기(submitted → working → input-required → completed 등)를 가진 작업 단위, 스트리밍·푸시 알림으로 진행 상황 전달 |
| "몇 건 판정했는지"를 Host가 따로 조회 | 작업 측 에이전트가 **스스로 상태와 중간 결과를 알려 줌** |
| 중앙 LLM이 테스트 실행·이슈 등록 하나하나까지 지시 | 각 부서가 **자기 에이전트**를 갖고, "이 버전을 검증해 달라" 같은 **업무 단위로 위임** |
| Dev/QA는 판단하지 않는 수동적 도구 | 각 에이전트가 **내부를 숨긴 채(opaque)** 자기 판단으로 업무 수행 |
| Host가 모든 연결의 중심(단일 장애점) | 에이전트끼리 **Agent Card**로 서로의 능력을 발견하고 직접 협업(수평) |

정리하면, **MCP는 "에이전트가 도구를 쓰는 방법"**, **A2A는 "에이전트가 다른 에이전트에게 일을 맡기는 방법"** 이며, 두 프로토콜은 경쟁이 아니라 보완 관계입니다. Stage 4에서는 각 부서 에이전트가 내부적으로는 자기 시스템을 도구로 쓰고, 부서 간에는 A2A로 업무를 주고받는 구성을 확인합니다.

---

## 부록 A. 실행 방법

### A.1 준비

```powershell
cd D:\users\yk\A2A_Class\A2A-Practice\03_mcp
uv sync
```

Claude를 쓰려면 루트 또는 `03_mcp/`의 `.env`에 `ANTHROPIC_API_KEY`를 둡니다. 없으면 자동으로 mock 모드입니다.

### A.2 기동

```powershell
uv run run.py                 # Dev MCP(8301) → QA MCP(8302) → Host(8300) 순서로 기동 (0.8초 간격)
uv run run.py --mock          # LLM 없이 MockPlanner / MockCoordinator
uv run run.py --only qa_mcp   # 특정 서비스만 (dev_mcp | qa_mcp | host) — 장애 실습용
```

포트를 바꿔 실행하는 예입니다. 데이터는 실행한 폴더의 `data/`를 쓰므로, 기존 데이터와 분리하려면 2026-10-16 점검처럼 폴더 사본에서 실행합니다.

```powershell
$env:HOST_PORT = "8310"; $env:DEV_PORT = "8311"; $env:QA_PORT = "8312"
uv run run.py --mock
```

`Ctrl+C`로 세 프로세스를 함께 종료합니다(5초 내 미종료 시 강제 종료).

### A.3 사용

| 목적 | 방법 |
| --- | --- |
| 도구 확인 | http://localhost:8300/tools |
| 데모 | Hub 상단 **데모 실행** → Overview, Agent Trace 관찰 |
| 실습 | Hub **실습 시작** → Dev 웹(http://localhost:8301/?as=최개발)에서 발행 → QA 웹(http://localhost:8302/?as=김검증, `?as=이검증`, `?as=박검증`)에서 판정 → 이관/확정 → 반복 |
| 상태 API | `GET http://localhost:8300/api/state`, `GET /api/cycles/{id}/summary` |
| MCP 엔드포인트 | `http://localhost:8301/mcp`, `http://localhost:8302/mcp` (다른 MCP 클라이언트로도 접속 가능) |
| 빌드 산출물 | `http://localhost:8301/builds/{cycle_id}/{version}` |
| 초기화 | Hub **초기화** (사이클 실행 중에는 불가) |

### A.4 문제 해결

| 증상 | 확인 |
| --- | --- |
| Hub 상단 "연동 장애: DEV MCP 서버 연결 안 됨" | Dev 프로세스 기동 여부, MCP Tools 화면의 오류 메시지. Host는 2초마다 재연결 |
| 부서 웹에서 409 | Host의 요청(차례)이 없는 상태. Hub에서 실습을 시작했는지 확인 |
| 부서 웹에서 403 | 상단 작업자가 그 부서 명부에 있는지 확인 |
| 중단했는데 부서 웹에 이전 요청이 남아 보임 | 중단 시 Dev/QA MCP가 꺼져 있었으면 `cancel_work`가 실패한다(Hub 로그 경고). 새 실습을 시작하면 이전 사이클의 OPEN 요청이 자동 정리된다. 완전히 비우려면 **초기화** |

---

## 부록 B. 용어 정리

| 용어 | 설명 |
| --- | --- |
| MCP (Model Context Protocol) | LLM 애플리케이션이 외부 도구·데이터를 표준 방식으로 쓰게 하는 JSON-RPC 기반 개방형 프로토콜 |
| MCP Host | LLM과 사용자가 있는 AI 애플리케이션. Client를 만들어 서버를 연결하고 도구 호출을 통제. 여기서는 Hub 웹 |
| MCP Client | Host 안에서 서버 하나당 하나씩 만드는 연결 객체. 여기서는 `McpConnection` |
| MCP Server | tools/resources/prompts를 노출하는 프로그램. 여기서는 Dev MCP, QA MCP |
| Tool | 모델이 호출을 결정하는 동작. 이름·설명·입력 JSON Schema |
| Resource | URI로 식별되는 읽기 전용 데이터. 여기서는 Hub 대시보드가 사용 |
| Prompt | 사용자가 선택하는 프롬프트 템플릿(본 단계 미사용) |
| `tools/list` / `tools/call` | 도구 목록 조회 / 도구 실행 JSON-RPC 메서드 |
| `isError` | 도구 수준 오류 표시. LLM이 읽고 복구할 수 있도록 정상 결과로 반환 |
| Streamable HTTP | 단일 HTTP 엔드포인트에 POST, 응답은 JSON 또는 SSE인 MCP 전송 방식 |
| stdio | Host가 서버를 자식 프로세스로 띄우고 표준입출력으로 통신하는 전송 방식 |
| 도구 이름 한정 | Claude에 노출할 때 `<server>__<tool>`로 이름을 붙이는 규칙 (`dev__ack_work`) |
| tool_use / tool_result | Claude Messages API에서 모델의 도구 호출 블록 / Host가 돌려주는 결과 블록 |
| 에이전트 루프 | LLM 응답의 tool_use를 실행하고 결과를 돌려주는 반복. `end_turn`까지 |
| 데모 | Host의 Claude(또는 MockPlanner)가 모든 업무를 도구로 직접 수행하는 방식 |
| 실습 | 사람이 부서 웹에서 일하고 Host가 결과를 전달(조정)하는 방식 |
| 조정자 (coordinator) | 실습에서 완료 작업을 폴링·발견하고 상대 시스템에 전달하는 Host 구성요소. Claude(COORDINATOR_PROMPT) 또는 MockCoordinator |
| 작업 지시 (work order) | Host가 부서에 넣는 요청(`request_release` / `request_verification`). 상태 OPEN → DONE → ACKED |
| 완료 작업 (completed work) | 부서원이 끝내 `DONE`이 된 작업 지시. `outcome` JSON에 결과 |
| outcome | 완료 작업의 결과 JSON. Dev: version·build_url·fixed_issue_keys / QA: decision·version·run_id 등 |
| ack | Host가 완료 작업을 상대에게 전달했음을 확인 처리. 멱등 |
| 전달 (handoff) | 완료 작업 1건을 상대 시스템에 넘기는 한 번의 조정 실행. 사이클당 7회 |
| 폴링 (polling) | Host가 2초마다 `list_completed_work`를 호출해 완료 작업을 확인하는 것 |
| run_label | Agent Trace에서 단계들을 실행 단위("사이클 시작", "Dev 발행 완료 전달 (v1.0.0)")로 묶는 라벨 |
| 관찰 채널 (relay) | Dev/QA 이벤트를 Hub로 실시간 중계하는 SSE 경로. 업무 연결과 분리 |
| 결정적 시뮬레이터 | 빌드 매니페스트의 결함 목록으로 PASS/FAIL을 정하는 규칙(`catalog.run_test`). LLM과 무관 |
| 회귀 결함 | 수정 과정에서 새로 생긴 결함. D-103 수정 → D-201(TC-11) |
| AI 초안 | 부서원 화면에서 Claude가 이슈·수정 내역·릴리즈 노트 초안을 작성하는 기능. 실패 시 템플릿 |
| MockPlanner / MockCoordinator | LLM 없이 같은 도구 호출 순서를 재현하는 규칙 기반 구현 |
| A2A (Agent2Agent) | 에이전트가 다른 에이전트에게 업무(Task)를 위임하는 프로토콜. Stage 4 주제 |
