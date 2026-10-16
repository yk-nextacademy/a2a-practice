# A2A Practice — 릴리즈·검증 협업 시스템 4단계 구현

개발부서(Dev)와 검증부서(QA)의 **릴리즈 → 검증 → 결함 이관 → 수정 → 재발행** 사이클을
`Standalone → API → MCP → A2A` 4가지 기술로 각각 구현하여, 같은 업무가 연결 방식에 따라
어떻게 달라지는지 비교 학습하는 프로젝트입니다. 모든 단계는 SQLite 저장소와 웹 대시보드를 갖춘 "실 서비스" 형태입니다.

## 단계 구성

| 단계 | 폴더 | 구조 | 대시보드 |
| --- | --- | --- | --- |
| 1. Standalone | [`01_standalone`](01_standalone/README.md) | 단일 웹 서비스(모놀리식), 함수 직접 호출 | http://localhost:8100 |
| 2. API | [`02_api`](02_api/README.md) | Hub(관제) / Dev Server(발행·수정) / QA Server(검증·이관·확정), REST + Webhook | Hub http://localhost:8200, Dev :8201, QA :8202 |
| 3. MCP | [`03_mcp`](03_mcp/README.md) | Hub 웹 = MCP Host(관제 + Claude) / Dev 웹 + Dev MCP / QA 웹 + QA MCP | Hub http://localhost:8300, Dev :8301, QA :8302 |
| 4. A2A | [`04_a2a`](04_a2a/README.md) | Hub 웹(Orchestrator) / Dev 웹 + Dev Agent / QA 웹 + QA Agent | Hub http://localhost:8400, Dev :8401, QA :8402 |

각 단계는 **완전히 독립 실행**됩니다 (단계 간 코드 공유 없음, 단계별 uv 프로젝트·DB). 포트가 겹치지 않아 네 단계를 동시에 띄울 수도 있습니다.

## 공통 사전 준비

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- 루트 `.env` (없으면 `.env.example` 복사). `ANTHROPIC_API_KEY`가 없으면 자동으로 mock(규칙 기반) 모드로 동작합니다.

```powershell
Copy-Item .env.example .env   # 키 입력
cd 01_standalone              # 02_api, 03_mcp, 04_a2a 도 같은 방식
uv sync
uv run run.py                 # LLM 없이: uv run run.py --mock
```

## 문서 구조

```text
A2A-Practice/
├─ README.md            # 이 문서
├─ TODO.md              # 전체 진행 현황 (마일스톤, 결정 로그)
├─ plan.md              # 초기 기획/설계
├─ docs/                # 개발 완료 보고서(단계별), 기술 비교 종합, A2A 발표 자료 — docs/README.md에서 시작
├─ .env.example
└─ 0N_<stage>/
   ├─ README.md         # 실행 방법, 구조, 점검 결과, 학습 포인트
   ├─ pyproject.toml    # uv 프로젝트
   ├─ run.py            # 단계 전체 기동 진입점
   ├─ docs/
   │  ├─ PRD.md         # 요구사항 정의
   │  ├─ TODO.md        # 단계별 작업 목록 (구현 / 점검 / 문서화)
   │  ├─ USER_SCENARIO.md
   │  └─ images/        # 화면 캡처
   ├─ src/              # 소스 (서비스별 패키지)
   └─ data/             # 런타임 SQLite DB (git 제외)
```

## 공통 업무 시나리오 (전 단계 동일 결과)

| 버전 | 테스트 결과 | 처리 |
| --- | --- | --- |
| v1.0.0 | 12건 중 9 PASS / 3 FAIL | ISSUE-1,2,3 등록 → Dev 이관 |
| v1.1.0 | 11 PASS / 1 FAIL (ISSUE-3 수정 중 생긴 회귀) | ISSUE-1~3 VERIFIED, ISSUE-4 등록 → Dev 이관 |
| v1.2.0 | 12 PASS | 릴리즈 승인 (Stage 4는 사람의 승인 후) |

PASS/FAIL·버전·이슈 수는 결정적 시뮬레이터가 정하고, LLM은 이슈 분석·수정 내역·릴리즈 노트·판단 같은 "내용"만 담당합니다.
그래서 mock / LLM 어느 모드에서도 결과가 같고, 단계 간 출력 비교로 이식이 맞았는지 확인할 수 있습니다.

## 단계별 비교

| 관점 | Standalone | API | MCP | A2A (Hub) |
| --- | --- | --- | --- | --- |
| 프로세스 | 1개 | Dev / QA / Hub(관제) | Dev MCP / QA MCP / Host | Dev Agent / QA Agent / Hub |
| 사람의 참여 (실습) | 작업대 화면 2개 | 부서별 웹 (Dev 웹, QA 웹) | 부서별 웹 + AI가 부서 간 전달 | 부서별 웹(에이전트 뒤) + Hub 승인함 |
| 사람 작업 완료를 아는 방법 | 같은 DB | REST 호출(Webhook) | Host가 2초마다 폴링 | Task 상태 스트리밍 |
| DB | 단일 | 서비스별 | 서비스별 + Host 기록 | 에이전트별 + Hub(관제·감사) |
| 결합도 | 매우 강함(코드) | 강함(URL·스키마 합의) | 중간(도구 자동 발견) | 약함(Agent Card + 표준 Task) |
| 흐름 결정 | 코드 | 코드(Webhook 체인) | 중앙 LLM이 함수 단위 호출 | Hub가 업무 단위로 위임, 수행 방식은 각 에이전트가 결정 |
| 판단(LLM) 위치 | 서비스 내부 | 서비스 내부 | Host 한 곳에 집중 | 각 에이전트 + Hub에 분산 |
| 관계 | - | 시스템 ↔ 시스템 | 에이전트 → 도구 (수직) | 에이전트 ↔ 에이전트 (수평, Hub 경유) |
| 발견 | 없음 | 문서/수작업 | `tools/list` | Agent Card |
| 장시간 작업 | 동기 호출 | Webhook 직접 구현 | tool call 단위 | Task 상태·Streaming 표준 |
| 사람 개입 | 없음 | 별도 구현 필요 | Host에서 처리 | `input-required` 표준 상태 |
| 장애 시 | 프로세스 전체 | 재시도 후 실패, 연동 로그로 추적 | LLM이 오류를 보고 판단·보고 | 카드 수집 실패 → 라우팅 불가로 명확히 실패 |

### 실측 (같은 시나리오, 지연 0)

| 단계 | mock | LLM (`claude-opus-5-5`) | 상호작용 단위 |
| --- | --- | --- | --- |
| 1. Standalone | 약 1초 | 약 46초 (LLM 10회) | 함수 호출 |
| 2. API | 약 1초 | 약 34초 | REST 호출 9회 (out 기준) |
| 3. MCP | 약 1초 | 약 48초 (15턴) | MCP 도구 호출 21회 |
| 4. A2A | 약 2초 | 약 50초 (Hub 판단 3회 + 에이전트 문서 작성) | A2A Task 6개 |

Stage 3은 중앙 에이전트가 세부 함수를 21번 호출하지만, Stage 4의 Hub는 업무 목표 6건만 위임합니다.
이 차이가 "도구를 쓰는 에이전트(MCP)"와 "에이전트끼리 협업(A2A)"의 핵심 차이입니다.

## 참고

- 루트 `.env`의 API 키는 임시 키이며, 구현 완료 후 사용자가 직접 삭제합니다. `.env`, `claudeapi.txt`는 `.gitignore`에 포함되어 있습니다.
- 사용 SDK: `anthropic` 1.x, `mcp` 2.2 (`MCPServer`, `Client`), `a2a-sdk` 1.1 (protobuf 타입, `create_*_routes`).
