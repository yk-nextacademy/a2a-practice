# A2A 이해를 위한 Toy Project 계획

> 주제: **개발부서 ↔ 검증부서의 SW 릴리즈·검증·결함 이관 사이클**을
> `Standalone → API → MCP → A2A` 순서로 4번 다시 구현하면서,
> "같은 업무"가 기술에 따라 **어떻게 연결 방식만 바뀌는지** 체감하는 것이 목표.
> 각 단계는 **실 서비스를 가정**하여 SQLite 저장소 + 웹 대시보드를 갖춘다.

---

> 구현 결과와 최종 구성은 [README.md](README.md), 진행 현황은 [TODO.md](TODO.md)를 기준으로 한다.
> 아래는 초기 기획이며, 구현 과정에서 바뀐 점: 모델 기본값 `claude-opus-5-5`, 포트 Stage N → 8N00대, uv 단계별 프로젝트,
> 디렉터리는 `docs/`(PRD·TODO·USER_SCENARIO) / `src/`(서비스별 패키지) / `data/`(SQLite) 구조.

## 0. 확정된 결정 사항

| 항목 | 결정 |
| --- | --- |
| LLM | Claude API 사용. 키는 루트 `.env`로 관리 (`.env.example` 제공, 키 없으면 mock 모드로 자동 전환) |
| 저장소 | SQLite (단계/서비스별 독립 DB 파일) |
| UI | 실 서비스 가정 → 단계별 웹 대시보드 |
| 회귀 버그 시뮬레이션 | 포함 |
| A2A 토폴로지 | **Dev ↔ Hub(Orchestrator) ↔ QA** 허브-스포크 구조 (7장 참조) |

---

## 1. 핵심 접근 원칙

1. **도메인은 고정, 통신 방식만 교체**
   - 4개 단계 모두 동일한 업무 시나리오·데이터 모델·시뮬레이션 결과를 사용한다.
   - 단계 간 비교 시 "통신/협업 계층"만 바뀐 것이 보이도록 한다.
2. **각 단계는 완전히 독립 실행**
   - 단계별 폴더가 자기 코드·`requirements.txt`·`README.md`·실행 스크립트·DB를 가진다.
   - 단계 간 공용 모듈 import 없음 (도메인 코드는 의도적으로 복사).
   - 예외: API 키는 루트 `.env` 하나를 공유 (`python-dotenv`의 상위 폴더 탐색으로 로드).
3. **결정적(deterministic) 시뮬레이션**
   - 랜덤 시드 고정 → 어느 단계에서 실행해도 같은 결과. 출력 비교로 이식 정확성 검증.
   - LLM은 "판단·문서화"(심각도 분류, 결함 리포트·릴리즈 노트 작성, 승인 판단)에만 사용하고,
     테스트 PASS/FAIL 자체는 시뮬레이터가 결정 → LLM을 써도 사이클 결과는 동일.
4. **LLM 없이도 동작**: 키가 없거나 `--mock` 옵션이면 규칙 기반 로직으로 대체.
5. **점진적 복잡도**: 각 단계는 직전 단계의 한계(pain point)를 해결하는 형태로 설명.

---

## 2. 업무 도메인 정의 (전 단계 공통)

### 2.1 등장 주체

| 주체 | 역할 |
| --- | --- |
| 개발부서 (Dev) | 버전 발행, 이관받은 결함 수정, 차기 버전에 반영 |
| 검증부서 (QA) | 발행된 버전 검증(테스트 실행), 결과 관리, 결함 등록 및 Dev로 이관 |
| Hub (Stage 4) | 전체 흐름 관제, 업무 라우팅, 승인 처리, 감사 로그, 대시보드 |

### 2.2 데이터 모델 (SQLite 테이블)

```text
releases     : version, features(json), fixed_issue_ids(json), release_note, released_at
test_cases   : id, name, target_feature
test_runs    : run_id, version, passed, failed, started_at, finished_at
test_results : run_id, test_case_id, passed, log
issues       : issue_id, version_found, test_case_id, title, description, severity,
               status(OPEN → TRANSFERRED → IN_PROGRESS → FIXED → VERIFIED / REOPENED),
               fixed_in_version, created_at, updated_at
events       : id, ts, actor, type, payload(json)        # 감사/타임라인 로그
```

### 2.3 시뮬레이션 규칙 (결정적)

- 각 버전에는 내부적으로 "숨은 버그 목록"이 있다. 해당 기능의 테스트케이스는 버그가 있으면 FAIL.
- Dev가 이관받은 Issue를 수정하면 다음 버전에서 그 버그가 사라진다.
- 시드 고정 확률로 수정 중 **회귀 버그(regression)** 1건 발생 → 반복 사이클 발생.
- 종료 조건: 모든 테스트 PASS → 릴리즈 승인, 또는 최대 반복 횟수(5회) 도달.

### 2.4 기대 시나리오 (모든 단계 동일 결과 목표)

```text
[Dev] v1.0.0 발행 (기능: login, search, payment, report)
[QA ] v1.0.0 검증: 12건 중 9 PASS / 3 FAIL → ISSUE-1,2,3 등록 → Dev 이관
[Dev] ISSUE-1,2,3 수정 → v1.1.0 발행
[QA ] v1.1.0 검증: 11 PASS / 1 FAIL (회귀) → ISSUE-4 등록 → Dev 이관, ISSUE-1~3 VERIFIED
[Dev] ISSUE-4 수정 → v1.2.0 발행
[QA ] v1.2.0 검증: 12 PASS → 릴리즈 승인
```

---

## 3. 공통 대시보드 사양

모든 단계에서 같은 화면 구성을 유지해 "UI는 같은데 뒤의 연결 방식이 다르다"를 보여준다.

| 화면 | 내용 |
| --- | --- |
| Overview | 현재 사이클 상태, 최신 버전, 오픈 결함 수, 버전별 PASS율 추이 차트 |
| Releases | 버전 목록, 릴리즈 노트, 반영된 결함 |
| Test Runs | 실행 이력, 케이스별 결과/로그 |
| Issues | 결함 목록·상태·심각도, 상태 이력 |
| Timeline | `events` 기반 실시간 이벤트 로그 (SSE) |
| (단계별 추가) | Stage 3: Tool 호출 트레이스 / Stage 4: 에이전트 레지스트리, A2A 메시지 트레이스, 승인함 |

- 기술: FastAPI + Jinja2 템플릿 + HTMX + SSE, 차트는 Chart.js (Node 미설치 환경 → 빌드 도구 없는 구성)
- 조작: "사이클 시작" 버튼, (Stage 4) 승인/반려 버튼

---

## 4. 단계별 구현 계획

### Stage 1. Standalone — "모놀리식 웹 서비스"

**목적**: 도메인 로직과 업무 흐름 자체를 이해한다. (부서 간 통신 없음)

- 구성: 단일 FastAPI 앱 = 대시보드 + `DevService` + `QAService` + 단일 SQLite(`app.db`)
- 흐름: 사이클 시작 시 `CycleRunner`가 `dev.release() → qa.verify() → dev.fix()`를 **직접 함수 호출**
- 실행: `python main.py` → `http://localhost:8000`
- 학습 포인트: 흐름이 코드에 하드코딩, 두 부서가 하나의 DB를 공유
- **한계**: 두 부서가 별도 시스템·별도 DB로 운영된다면?

### Stage 2. API — "부서별 시스템 REST 연동"

**목적**: 부서 시스템을 분리하고 사전 합의된 API 스펙으로 통신한다.

- 구성
  - `dev_server` (:8001, `dev.db`): `POST /releases`, `GET /releases`, `POST /issues`(이관 수신), `POST /issues/{id}/fix`
  - `qa_server`  (:8002, `qa.db`): `POST /test-runs`, `GET /test-runs`, `GET /issues`, `POST /webhooks/release`
  - 서버 간 알림: **Webhook 체인** (Dev 발행 → QA 웹훅, QA 결함 등록 → Dev 이관 API)
  - `portal` (:8000): 대시보드. 두 서버 API를 호출해 화면 구성 (BFF 패턴)
- 실행: `python run_all.py` (3개 프로세스 기동)
- 학습 포인트: 프로세스·데이터 분리. 하지만 상대 URL·스키마를 서로 알아야 함(강결합), 흐름은 코드가 결정
- **한계**: "판단"(심각도 분류, 승인 여부)을 AI에 맡기려면?

### Stage 3. MCP — "중앙 AI 에이전트가 부서 시스템을 도구로 사용"

**목적**: LLM 에이전트가 MCP로 시스템 기능(tool)을 발견·호출한다.

- 구성
  - `dev_mcp_server` (`dev.db`): tools — `release_version`, `list_releases`, `list_assigned_issues`, `fix_issue`
  - `qa_mcp_server`  (`qa.db`): tools — `run_tests`, `get_test_run`, `create_issue`, `transfer_issues`, `verify_issue`
  - `host_app` (:8000): 대시보드 + MCP Host(Client). Claude가 `tools/list`로 도구를 발견하고 호출 순서를 판단
  - 전송: Streamable HTTP (서버를 독립 프로세스로 운영 → 실 서비스 형태)
- 대시보드 추가: **Tool 호출 트레이스** (LLM의 사고 → tool_use → 결과)
- 학습 포인트: 도구 자동 발견, 흐름 결정 주체가 코드 → LLM으로 이동.
  단, **에이전트 1개 ↔ 도구 N개(수직)** 관계. Dev/QA는 수동적인 도구일 뿐
- **한계**: 각 부서가 자기 AI 에이전트를 갖고 내부를 숨긴 채 업무를 "위임"해야 한다면?

### Stage 4. A2A — "Dev ↔ Hub ↔ QA 에이전트 협업"

**목적**: 독립된 에이전트들이 A2A 프로토콜로 서로를 발견하고 Task 단위로 업무를 주고받는다.
허브가 전체 흐름을 관제한다. → 상세 설계는 **7장**.

- 실행: `python run_all.py` (hub :9000, dev :9001, qa :9002)

---

## 5. 단계별 비교 (최종 README에 정리)

| 관점 | Standalone | API | MCP | A2A (Hub) |
| --- | --- | --- | --- | --- |
| 프로세스 | 1개 | 부서별 서버 + 포털 | Host + 도구 서버 | Hub + 부서별 에이전트 |
| DB | 단일 | 부서별 | 부서별 | 부서별 + Hub(관제/감사) |
| 결합도 | 매우 강함 | 강함(엔드포인트·스키마) | 중간(도구 자동 발견) | 약함(Agent Card + 표준 Task) |
| 흐름 결정 | 코드 | 코드(웹훅 체인) | 중앙 LLM이 **단계별 함수 호출** | Hub는 **목표 위임**, 실행 방법은 각 에이전트가 결정 |
| 관계 | - | 시스템 ↔ 시스템 | 에이전트 → 도구 (수직) | 에이전트 ↔ 에이전트 (수평, Hub 경유) |
| 발견 | 없음 | 문서/수작업 | `tools/list` | Agent Card |
| 장시간 작업 | 동기 | 웹훅 직접 구현 | 단일 tool call 위주 | Task 상태·SSE·Push 표준 |
| 사람 개입 | 없음 | 별도 구현 | Host에서 처리 | `input-required` 표준 상태 |

---

## 6. 기술 스택

| 항목 | 선택 |
| --- | --- |
| 언어 | Python 3.12 |
| 패키지 | 단계별 `venv` + `requirements.txt` |
| 웹 | FastAPI, uvicorn, Jinja2, HTMX, Chart.js, SSE(`sse-starlette`) |
| DB | SQLite (`sqlite3` 표준 라이브러리 또는 SQLModel) |
| HTTP | httpx |
| MCP | `mcp` 공식 Python SDK (FastMCP, Streamable HTTP) |
| A2A | `a2a-sdk` 공식 Python SDK |
| LLM | `anthropic` SDK, 모델은 `.env`의 `CLAUDE_MODEL`로 설정 |
| 설정 | `python-dotenv`, 루트 `.env` / `.env.example` / `.gitignore` |

`.env.example`:

```dotenv
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5-5
LLM_MODE=auto        # auto(키 있으면 llm) | llm | mock
```

---

## 7. Stage 4 상세 설계: Dev ↔ Hub ↔ QA

### 7.1 구조

```text
                 ┌─────────────────────────────────────────┐
  사용자 ──Web──▶│ Hub / Orchestrator (:9000)               │
                 │  - 대시보드 (관제, 승인함, 메시지 트레이스)│
                 │  - Agent Registry (Agent Card 수집)      │
                 │  - Workflow Engine (사이클 상태 머신)     │
                 │  - Audit Log (모든 A2A 송수신 기록)       │
                 │  - hub.db                                │
                 └──────┬───────────────────────┬──────────┘
                  A2A Client               A2A Client
                        ▼                       ▼
            ┌──────────────────┐     ┌──────────────────┐
            │ Dev Agent (:9001)│     │ QA Agent (:9002) │
            │ A2A Server       │     │ A2A Server       │
            │ skill: release,  │     │ skill: verify,   │
            │        fix-issues│     │        report    │
            │ dev.db + Claude  │     │ qa.db + Claude   │
            └──────────────────┘     └──────────────────┘
```

- Dev와 QA는 **서로를 모른다**. Hub의 URL만 알 필요도 없다 (Hub가 호출하는 쪽).
- Hub도 자신의 Agent Card(skill: `run-release-cycle`)를 공개하는 **A2A Server** → 외부 시스템/다른 허브가 "릴리즈 사이클 실행"을 A2A로 요청 가능.

### 7.2 Hub의 책임

| 기능 | 내용 |
| --- | --- |
| Agent Registry | 등록된 URL에서 `/.well-known/agent-card.json` 수집, 스킬·상태(health) 표시. 에이전트 교체 = URL 변경 |
| 라우팅 | 필요한 스킬을 가진 에이전트를 Agent Card의 skill/tag로 찾아 Task 전달 |
| Workflow Engine | 사이클 상태 머신(`RELEASING → VERIFYING → FIXING → … → APPROVED`)을 hub.db에 저장. 재시작해도 이어서 진행 |
| 판단 | Claude가 QA 결과를 보고 다음 행동 결정 (이관/재검증/승인 요청/중단). mock 모드는 규칙 기반 |
| Human-in-the-loop | QA가 `input-required`(예: "Critical 결함 잔존, 승인할까요?") 반환 시 대시보드 승인함에 표시 → 사용자 응답을 같은 `taskId`로 재전송 |
| 관제 | 에이전트의 Streaming(SSE) 진행 상황을 구독해 대시보드로 중계 |
| 감사 로그 | 모든 JSON-RPC 요청/응답, Task 상태 전이를 기록 → 메시지 트레이스(시퀀스 뷰) 제공 |

### 7.3 한 사이클의 메시지 흐름

```text
User → Hub   : 사이클 시작 (대시보드 버튼 or A2A message)
Hub  → Dev   : Task "v1.0.0 발행해줘"           → Artifact: Release(JSON) + 릴리즈 노트
Hub  → QA    : Task "v1.0.0 검증해줘" (stream)  → 진행률 SSE … → Artifact: TestRun + Issues
Hub          : 판단 (결함 3건 → 이관)
Hub  → Dev   : Task "ISSUE-1,2,3 수정 후 차기 버전 발행" → Artifact: Release v1.1.0
Hub  → QA    : Task "v1.1.0 검증 + ISSUE-1~3 확인"      → …
...
QA   → Hub   : (input-required) "릴리즈 승인 여부?" → 사용자 승인 → completed
```

### 7.4 MCP Host(Stage 3)와 A2A Hub의 차이 — 핵심 학습 포인트

둘 다 "가운데"에 있지만 성격이 다르다.

- **MCP Host**: `run_tests(case_id=3)` 처럼 **세부 함수**를 하나하나 호출. 상대는 판단하지 않는 도구.
- **A2A Hub**: "v1.1.0 검증해줘" 처럼 **목표(업무)** 를 위임. 어떤 테스트를 어떻게 돌릴지는 QA 에이전트가 결정하고,
  오래 걸리면 진행 상황을 스트리밍하고, 필요하면 되묻는다. Hub는 QA 내부(DB, LLM, 도구)를 모른다.

### 7.5 트레이드오프와 선택 확장

- 허브 구조 장점: 중앙 관제·감사·승인·흐름 가시성 (실무 요구에 부합)
- 단점: Hub가 단일 장애점, 흐름 지식이 Hub에 집중
- A2A 스펙은 토폴로지를 강제하지 않음 → (선택 4b) **Peer 모드**: Dev ↔ QA 직접 통신 + Hub는 감사 로그만 수신. 같은 에이전트 코드로 설정만 바꿔 비교
- (선택 4c) 각 에이전트 내부에서 Stage 3의 MCP 서버를 자기 도구로 사용 → "A2A(에이전트 간) + MCP(에이전트-도구)" 결합 구조

---

## 8. 디렉터리 구조 (안)

```text
A2A-Practice/
├─ plan.md
├─ README.md               # 전체 개요 + 단계 비교표 + 실행 방법
├─ .env / .env.example / .gitignore
├─ 01_standalone/
│  ├─ README.md, requirements.txt, main.py
│  ├─ app/ (domain.py, db.py, dev_service.py, qa_service.py, cycle.py, llm.py)
│  └─ web/ (templates/, static/)
├─ 02_api/
│  ├─ README.md, requirements.txt, run_all.py
│  ├─ dev_server/  qa_server/  portal/
├─ 03_mcp/
│  ├─ README.md, requirements.txt, run_all.py
│  ├─ dev_mcp_server/  qa_mcp_server/  host_app/
└─ 04_a2a/
   ├─ README.md, requirements.txt, run_all.py
   ├─ hub/        (registry, workflow, audit, a2a_server, web/)
   ├─ dev_agent/  (agent_card, executor, domain, db)
   └─ qa_agent/   (agent_card, executor, domain, db)
```

---

## 9. 진행 순서 (마일스톤)

| # | 작업 | 완료 기준 |
| --- | --- | --- |
| M0 | 공통 기반: `.env` 구조, 도메인/시뮬레이터, 대시보드 템플릿 | 시뮬레이터 단독 실행 시 2.4 시나리오 재현 |
| M1 | Stage 1 Standalone | 대시보드에서 사이클 실행 → 2.4와 동일 결과 |
| M2 | Stage 2 API | 3개 프로세스 분리 실행, 동일 결과, Swagger `/docs` 확인 |
| M3 | Stage 3 MCP | mock/LLM 모드 모두 동일 결과, Tool 트레이스 화면 |
| M4 | Stage 4 A2A 기본 | Agent Card 수집, Hub 경유 Task/Artifact로 동일 결과 |
| M4+ | A2A 심화 | Streaming 진행률, input-required 승인함, 메시지 트레이스 |
| M5 | (선택) Peer 모드, A2A+MCP 결합, 최종 README 비교 정리 | |

각 마일스톤 종료 시: 실행 확인 → 단계 README에 "배운 것 / 한계" 정리 → 다음 단계.
