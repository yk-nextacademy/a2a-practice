# Stage 2. API — 개발 완료 보고서

> 개발부서(Dev)와 검증부서(QA)가 각자 운영하는 독립 웹 서비스가 사전에 합의한 REST API와 Webhook으로 업무를 주고받고, Hub가 이 흐름을 관제하는 단계의 개발 완료 보고서입니다.
> 이 문서는 `02_api/` 아래 소스 코드를 근거로 작성했습니다. 2026-10-16에 알려진 이슈를 수정하고 README·PRD·TODO·USER_SCENARIO를 코드에 맞춰 정비했으며, 수정 내역과 남은 차이는 12장과 13장에 정리했습니다.

---

## 0. 문서 정보

| 항목 | 내용 |
| --- | --- |
| 단계 | Stage 2. API (부서별 시스템 REST 연동 + Hub 관제) |
| 폴더 | `02_api/` |
| 포트 | Hub 웹 `8200`, Dev Server `8201`, QA Server `8202` (세 서비스 모두 웹 화면 + REST API) |
| 데이터 | `02_api/data/dev.db`(Dev 소유), `02_api/data/qa.db`(QA 소유), Hub는 DB 없음 |
| 기술 스택 | Python 3.12, uv, FastAPI, Uvicorn, Jinja2, httpx, SQLite(WAL), anthropic SDK 1.14.0 |
| LLM | `claude-opus-5-5`, effort `low`, 구조화 출력(JSON Schema), `LLM_MODE=auto|llm|mock` |
| 작성일 | 2026-10-16 (알려진 이슈 수정·재점검 반영) |
| 상태 | 완료 |
| 관련 문서 | `02_api/README.md`, `02_api/docs/PRD.md`, `02_api/docs/TODO.md`, `02_api/docs/USER_SCENARIO.md` |
| 앞/뒤 단계 | 앞: `01_standalone/` (Stage 1, 단일 프로세스·단일 DB) · 뒤: `03_mcp/` (Stage 3, MCP) |

---

## 1. 요약

Stage 2는 Stage 1에서 한 프로세스 안에 함께 있던 개발부서와 검증부서의 업무를 **서로 다른 프로세스, 서로 다른 DB, 서로 다른 웹 화면**으로 분리했습니다. 두 시스템은 상대의 DB나 함수에 직접 접근할 수 없고, **사전에 합의한 HTTP 엔드포인트**로만 업무를 넘깁니다. 업무 흐름을 잇는 엔드포인트 4개와, 사이클 중단을 알리는 통지 1개입니다.

| 호출 방향 | 엔드포인트 | 의미 |
| --- | --- | --- |
| Dev → QA | `POST /api/webhooks/release-published` | 새 버전을 발행했으니 검증해 달라 (Webhook) |
| QA → Dev | `GET /builds/{cycle}/{version}` | 검증할 빌드 산출물을 내려받는다 (REST 조회) |
| QA → Dev | `POST /api/tickets` | 발견한 결함을 개발부서에 이관한다 (REST 생성) |
| QA → Dev | `POST /api/webhooks/verification-completed` | 이 버전을 확정했다 (Webhook) |
| Dev → QA | `POST /api/webhooks/cycle-cancelled` | 사이클을 중단했으니 진행 중인 검증을 멈춰 달라 (Webhook, 2026-10-16 추가) |

세 번째 서비스인 **Hub**는 업무 흐름에 끼어들지 않습니다. 화면을 그릴 때마다 Dev와 QA의 조회 API를 호출해 결과를 합치고, 두 시스템의 SSE 이벤트 스트림을 하나로 묶어 브라우저에 중계합니다. "데모 실행 / 실습 시작 / 사이클 중단 / 초기화" 버튼만 편의상 Dev·QA API를 대신 호출합니다.

주요 결과는 다음과 같습니다.

- 데모(자동) 모드에서 Webhook 체인만으로 `v1.0.0 9/3 → v1.1.0 11/1 → v1.2.0 12/0 → 확정`까지 사람 개입 없이 끝까지 진행되며, ISSUE-1~4가 모두 `VERIFIED`가 됩니다. mock 모드와 LLM 모드의 판정 결과는 같습니다 (판정은 결정론적 시뮬레이터가 하고 LLM은 문장만 씁니다).
- 지연을 0으로 두고 측정했을 때 mock 모드는 약 1초, LLM 모드는 약 34초가 걸렸고, 서비스 간 호출(outbound)은 **9회**였습니다.
- 실습(수동) 모드에서 개발부서원은 Dev 웹, 검증부서원은 QA 웹에서만 일합니다. 부서원 명부는 시스템마다 따로 있고, 명부에 없는 작업자(다른 부서원)는 403, 자기 부서 차례가 아니면 409를 받습니다. 권한은 Stage 1과 같이 부서 단위로만 검사합니다.
- 모든 서비스 간 호출은 호출한 쪽(`out`)과 받은 쪽(`in`) 양쪽에서 `integration_logs`에 기록되고, `X-Cycle-Id` 헤더로 사이클 단위 추적이 됩니다. 상대 서비스가 장애일 때는 최대 3회까지 시도합니다.
- 2026-10-16에 Hub의 "실습 시작"·"사이클 중단" 버튼 무반응 버그와 알려진 이슈 7건(중단 미전파, 확정 통지의 상태 미검사, 데모 QA 결정 미기록, 초기화 불일치, QA 시작 시 정리 부재, 미사용 대기값, 실습 부분 수정 시 교착)을 고쳤습니다. 이후 Edge 실제 브라우저로 실습 전 과정을 클릭해 진행했고, LLM 모드의 AI 초안 3종도 확인했습니다(12장).

이 단계로 시스템 분리와 장애 격리를 얻었습니다. 대신 **서로의 URL과 스키마를 알아야 하는 강결합**, **코드에 고정된 업무 경로**, **어느 시스템에도 전체 그림이 없다는 점**이 한계로 드러났고, 이 한계가 다음 단계(MCP)로 넘어가는 이유가 됩니다.

---

## 2. 기술 배경

이 장은 REST API, Webhook, 서비스 분리를 처음 접하는 사람을 위해 일반 개념을 먼저 설명하고, 이 프로젝트에서 각 개념이 어디에 쓰였는지 연결합니다.

### 2.1 REST API

**무엇인가.** REST(Representational State Transfer)는 HTTP를 이용해 시스템 사이에서 데이터를 주고받는 설계 방식입니다. 다루는 대상을 "자원(resource)"으로 보고 URL로 이름을 붙이며(`/api/tickets`, `/api/releases/1.1.0`), 그 자원에 할 일은 HTTP 메서드로 나타냅니다.

| 메서드 | 의미 | 예시 |
| --- | --- | --- |
| `GET` | 조회 (상태를 바꾸지 않음) | `GET /api/tickets?cycle_id=1` |
| `POST` | 생성 또는 동작 요청 | `POST /api/tickets` (티켓 생성) |
| `PUT` / `PATCH` | 전체/부분 수정 | (이 프로젝트에서는 사용하지 않음) |
| `DELETE` | 삭제 | (사용하지 않음) |

응답은 상태 코드로 결과를 알립니다. `200 OK`, `201 Created`, `202 Accepted`(접수했고 처리는 나중에), `400 Bad Request`(요청 내용 오류), `403 Forbidden`(권한 없음), `404 Not Found`, `409 Conflict`(현재 상태와 충돌), `502 Bad Gateway`(뒤쪽 시스템 호출 실패) 같은 식입니다. 본문은 보통 JSON입니다.

**왜 쓰는가.** HTTP와 JSON은 거의 모든 언어와 플랫폼에서 지원하므로, 서로 다른 팀이 만든 시스템도 "URL + 메서드 + JSON 스키마"만 합의하면 연동할 수 있습니다. 각 요청이 독립적이라(무상태) 서버를 늘리기도 쉽습니다.

**장점과 단점.**

| 장점 | 단점 |
| --- | --- |
| 단순하고 표준적이며, 도구(curl, Swagger, 브라우저)가 풍부하다 | 호출하는 쪽이 상대의 URL과 스키마를 미리 알아야 한다 (강결합) |
| 언어·플랫폼과 무관하다 | 스키마가 바뀌면 양쪽을 함께 고쳐야 한다 (버전 관리 부담) |
| 무상태라 확장과 캐시가 쉽다 | 네트워크 장애, 지연, 부분 실패를 직접 처리해야 한다 |
| 조회와 변경의 의미가 메서드로 드러난다 | "언제 어떤 순서로 부를지"는 표준이 정해 주지 않는다 |

**흔한 구현.** 서버는 FastAPI, Spring, Express 같은 프레임워크로 라우트를 정의하고 요청 본문을 스키마(Pydantic, DTO)로 검증합니다. 클라이언트는 httpx, requests, fetch 같은 HTTP 라이브러리로 호출하며, 실무에서는 타임아웃, 재시도, 호출 로그, 추적 ID(Correlation ID)를 함께 붙입니다. 스펙은 OpenAPI(Swagger) 문서로 공유합니다.

### 2.2 Webhook

**무엇인가.** Webhook은 "무슨 일이 생기면 미리 알려 준 URL로 HTTP POST를 보내 알려 주는" 방식입니다. 기술적으로는 그냥 REST 호출이지만, **방향이 반대**라는 점이 다릅니다. 받는 쪽이 계속 "새 버전 나왔어?" 하고 물어보는(polling) 대신, 일이 생긴 쪽이 먼저 알려 줍니다(push).

```text
 Polling (조회 반복)                      Webhook (이벤트 통지)
 QA ── GET 새 버전 있나? ──▶ Dev          Dev ── (발행 순간) POST /release-published ──▶ QA
 QA ── GET 새 버전 있나? ──▶ Dev
 QA ── GET 새 버전 있나? ──▶ Dev (있음)
```

**왜 쓰는가.** 이벤트가 생긴 즉시 상대가 알 수 있고, 쓸데없는 조회 요청이 없습니다. GitHub의 push 알림이나 결제사의 결제 완료 통지가 대표적인 예입니다.

**장점과 단점.**

| 장점 | 단점 |
| --- | --- |
| 즉시성: 이벤트 직후 상대가 반응한다 | 받는 쪽이 꺼져 있으면 통지가 유실될 수 있다 (재시도·재전송 설계 필요) |
| 조회 반복이 없어 효율적이다 | 보내는 쪽이 받는 쪽 URL을 알아야 한다 |
| 이벤트 체인으로 자동화 흐름을 만들기 쉽다 | 같은 통지가 두 번 오는 경우 등 멱등성 처리가 필요하다 |
| | 흐름이 여러 시스템에 흩어져 전체를 추적하기 어렵다 |

**흔한 구현.** 받는 쪽은 짧게 검증하고 바로 `202 Accepted`를 돌려준 뒤, 실제 처리는 백그라운드 작업으로 넘깁니다. 보내는 쪽은 실패하면 지수 백오프로 재시도하고, 서명(HMAC)으로 위조를 막으며, 이벤트 ID로 중복을 거릅니다.

### 2.3 서비스 분리 (Service Separation)

**무엇인가.** 하나의 애플리케이션(모놀리스)에 들어 있던 업무 영역을 독립적으로 배포·실행되는 여러 서비스로 나누는 것입니다. 각 서비스는 **자기 데이터를 자기만 소유**하고(Database per Service), 다른 서비스의 데이터가 필요하면 API로 요청합니다.

```text
 모놀리스 (Stage 1)                        서비스 분리 (Stage 2)
 ┌──────────────────────────┐              ┌──────────┐  HTTP  ┌──────────┐
 │  Dev 로직 ── 함수 호출 ── QA 로직 │       │ Dev 서비스 │◀──────▶│ QA 서비스 │
 │        └── 공유 DB ──┘     │              │  dev.db   │        │  qa.db    │
 └──────────────────────────┘              └──────────┘        └──────────┘
```

**왜 하는가.** 조직 구조와 시스템 구조를 맞추고(콘웨이의 법칙), 팀이 각자 일정에 맞춰 배포하며, 한 서비스의 장애가 다른 서비스로 번지지 않게 하기 위해서입니다.

**장점과 단점.**

| 장점 | 단점 |
| --- | --- |
| 독립 배포·독립 확장 | 네트워크 호출이 생겨 지연과 부분 실패가 발생한다 |
| 장애 격리: 한쪽이 죽어도 다른 쪽은 산다 | 데이터가 나뉘어 일관성(트랜잭션) 유지가 어렵다 |
| 팀별 소유권이 명확하다 | 전체 현황을 보려면 여러 서비스를 모아 조회해야 한다 |
| 기술 선택의 자유 | 운영 대상(프로세스, 로그, 모니터링)이 늘어난다 |

**흔한 구현.** 서비스마다 별도 프로세스(또는 컨테이너)와 DB를 두고, 서비스 간 통신은 REST/gRPC(동기) 또는 메시지 큐(비동기)로 합니다. 화면용으로는 여러 서비스를 모아 주는 BFF(Backend For Frontend)나 API 게이트웨이를 둡니다. 추적을 위해 Correlation ID를 모든 요청에 전파하고 로그를 한곳에 모읍니다.

### 2.4 이 프로젝트에서의 대응

| 일반 개념 | Stage 2 구현 | 코드 위치 |
| --- | --- | --- |
| 서비스 분리 | Dev Server, QA Server, Hub를 별도 프로세스로 실행 | `02_api/run.py`, 각 `__main__.py` |
| Database per Service | `dev.db`, `qa.db`를 따로 두고, Hub는 DB를 두지 않음 | `02_api/src/common/config.py` `db_path()` |
| REST 조회 | QA가 빌드 산출물을 `GET /builds/{cycle}/{version}`로 내려받음 | `dev_server/app.py` `build()` |
| REST 생성 | QA가 결함을 `POST /api/tickets`로 Dev에 티켓으로 생성 | `dev_server/app.py` `receive_tickets()` |
| Webhook | `release-published`·`cycle-cancelled`(Dev→QA), `verification-completed`(QA→Dev) | `qa_server/app.py`, `dev_server/app.py` |
| 202 Accepted 후 비동기 처리 | 데모에서 Webhook을 받으면 `asyncio.create_task`로 검증/수정 진행 | `qa_server/service.py` `on_release_published()`, `dev_server/service.py` `_spawn()` |
| 재시도 + 백오프 | 5xx·네트워크 오류는 최대 3회 시도, 시도 사이 대기 0.5초 → 1.0초 | `common/http.py` `IntegrationClient` |
| Correlation ID | `X-Cycle-Id` 헤더에 사이클 번호 전파 | `common/http.py` |
| 호출 로그 | 호출한 쪽 `out`, 받은 쪽 `in`을 각자 `integration_logs`에 기록 | `common/http.py` `InboundLogMiddleware` |
| BFF / 관제 화면 | Hub가 두 시스템 API를 조회해 화면 조합, SSE 병합 중계 | `hub/app.py`, `hub/upstream.py` |
| API 문서 | FastAPI 자동 Swagger (`/docs`) | 각 `app.py` |

---

## 3. 목표와 범위

### 3.1 목표

| ID | 목표 (PRD 기능 요구사항) | 달성 |
| --- | --- | --- |
| F-01 | 3개 웹 서비스가 독립 프로세스로 동작, Dev/QA는 독립 DB | 달성 |
| F-02 | 데모: Webhook 체인으로 사람 개입 없이 끝까지 진행 | 달성 |
| F-03 | 실습: 개발부서원은 Dev 웹, 검증부서원은 QA 웹에서 진행 (부서별 명부·권한) | 달성 |
| F-04 | QA는 검증 후 Dev 이관 또는 버전 확정을 선택 | 달성 |
| F-05 | 모든 시스템 간 HTTP 호출을 integration log로 기록, `cycle_id` 전파 | 달성 (Hub의 대리 호출은 기록 대상이 아님, 13장) |
| F-06 | 상대 시스템 장애 시 재시도 3회, 데모는 실패 처리, 실습은 작업 취소·재시도 안내 | 달성 |
| F-07 | Hub는 관제 전용 (차례, 집계, 병합 이력, 호출 로그) | 달성 |
| F-08 | LLM(이슈 분석, 수정 내역, 릴리즈 노트) / mock, 실습은 AI 초안 | 달성 (실습 AI 초안은 mock·LLM 모두 점검) |
| F-09 | 실시간 갱신 중 입력 폼 보호, 이모지 없는 모노톤 UI | 달성 |

### 3.2 범위

| 포함 | 제외 |
| --- | --- |
| Dev/QA 업무 로직 분리, REST/Webhook 연동 계약 4종 | 인증·인가 토큰, Webhook 서명(HMAC) |
| 데모(자동)·실습(수동) 두 가지 진행 방식 | 메시지 큐, 이벤트 재전송 보관함(outbox) |
| 시스템별 부서원 명부와 부서 단위 권한 검사 | 실제 빌드·배포·테스트 실행 (시뮬레이터로 대체) |
| 재시도, 호출 로그, 사이클 중단, 초기화 | 서비스 디스커버리 (주소는 환경변수로 고정) |
| Hub 관제(조회 조합, SSE 병합, 서비스 상태) | 분산 트랜잭션, 멱등키 |
| LLM 콘텐츠 생성과 mock 대체 | 다중 사용자 동시 편집 충돌 처리 |

### 3.3 업무 도메인 (전 단계 공통)

- 제품 `Commerce Web`, 기능 4개(login/search/payment/report) × 테스트케이스 3개 = 12건 (`qa_server/catalog.py` `TEST_CASES`)
- 초기 결함: `D-101`→TC-02, `D-102`→TC-05, `D-103`→TC-08. `D-103`을 고치면 회귀 결함 `D-201`→TC-11이 생깁니다 (`dev_server/catalog.py` `DEFECTS`, `REGRESSIONS`).
- 기대 결과: v1.0.0 9 PASS/3 FAIL → v1.1.0 11/1 → v1.2.0 12/0 → 확정. ISSUE-1~4 모두 VERIFIED.
- 가상 인물: QA 김검증(login·search, TC-01~06), 이검증(payment, TC-07~09), 박검증(report, TC-10~12) / Dev 최개발(백엔드 개발), 정개발(프론트엔드 개발)

---

## 4. 시스템 구성

### 4.1 구성도

```text
                         브라우저 (운영자 / 릴리즈 매니저)
                                     │ HTTP, SSE
                                     ▼
              ┌────────────── Hub 웹  :8200  (관제, DB 없음) ──────────────┐
              │ 화면 조합: GET /api/state, /api/cycles, /api/releases,      │
              │            /api/tickets, /api/test-runs, /api/issues,       │
              │            /api/events, /api/integration-logs, /health      │
              │ SSE 병합 : 두 시스템의 /api/events/stream 구독 → 하나로 중계  │
              │ 대리 호출: 데모 실행·실습 시작·중단·초기화                   │
              └──────────┬─────────────────────────────────┬───────────────┘
                         │ (조회·대리 호출)                  │ (조회·대리 호출)
                         ▼                                  ▼
 개발부서원 ──▶ Dev 웹 / Dev Server :8201            QA Server / QA 웹 :8202 ◀── 검증부서원
 (최개발,       ┌────────────────────┐              ┌────────────────────┐   (김검증,
  정개발)       │ DevService          │              │ QAService           │    이검증,
               │ IntegrationClient   │              │ IntegrationClient   │    박검증)
               │ InboundLogMiddleware│              │ InboundLogMiddleware│
               └─────────┬──────────┘              └─────────┬──────────┘
                         │ data/dev.db                        │ data/qa.db
                         │                                    │
                         │ ① POST /api/webhooks/release-published ──────────────▶│
                         │◀────────────────────────────── ② GET /builds/{c}/{v} ─│
                         │◀────────────────────────────── ③ POST /api/tickets ───│ (Dev 이관)
                         │◀──────────── ④ POST /api/webhooks/verification-completed│ (버전 확정)
                         │ ⑤ POST /api/webhooks/cycle-cancelled ────────────────▶│ (사이클 중단)
```

### 4.2 프로세스와 포트

`02_api/run.py`가 세 서비스를 각각 `python -m <모듈>` 하위 프로세스로 띄웁니다. 기동 순서는 Dev → QA → Hub이고, 0.5초 간격을 둡니다. `PYTHONPATH`를 `src`로 지정하고, `--mock`이면 `LLM_MODE=mock`을 환경변수로 넘깁니다. Ctrl+C를 누르면 모든 하위 프로세스에 terminate를 보내고, 5초 안에 끝나지 않으면 kill합니다.

| 서비스 | 모듈 | 포트(환경변수) | 바인딩 | DB | 자기 웹 화면 |
| --- | --- | --- | --- | --- | --- |
| Dev Server | `dev_server` | `DEV_PORT`=8201 | 127.0.0.1 | `data/dev.db` | 개발 작업대, 릴리즈, 티켓, 타임라인, 연동 로그 |
| QA Server | `qa_server` | `QA_PORT`=8202 | 127.0.0.1 | `data/qa.db` | 검증 작업대, 검증 이력, 이슈, 타임라인, 연동 로그 |
| Hub | `hub` | `HUB_PORT`=8200 | 127.0.0.1 | 없음 | Overview, Releases, Test Runs, Issues, Timeline, Integration |

`--only qa_server`처럼 한 서비스만 띄울 수 있어 장애 실습에 씁니다. Uvicorn 로그 수준은 `warning`입니다(`*/__main__.py`).

### 4.3 누가 누구를 아는가 (결합 관계)

서비스 분리 후에도 "상대에 대한 지식"은 남습니다. Stage 2의 결합 관계를 정리하면 다음과 같습니다.

| 아는 쪽 | 알아야 하는 것 | 근거 코드 |
| --- | --- | --- |
| Dev | QA의 주소 `QA_URL`, `release-published` 경로와 본문 스키마(`cycle_id, version, build_url, fixed_issue_keys, mode`) | `dev_server/service.py` `_notify_qa()` |
| Dev | `cycle-cancelled` 경로와 스키마(`cycle_id, reason`) | `dev_server/service.py` `abort()` |
| Dev | 자기 빌드 URL 규칙 `{DEV_URL}/builds/{cycle}/{version}` (QA가 그대로 호출) | `dev_server/service.py` `_build_url()` |
| QA | Dev의 주소 `DEV_URL`, `POST /api/tickets` 스키마(`cycle_id, version, issues[]`) | `qa_server/service.py` `_transfer()` |
| QA | `verification-completed` 경로와 스키마(`cycle_id, version, run_id, passed, total`) | `qa_server/service.py` `_notify_approved()` |
| QA | 빌드 산출물 형식 (`behaviors[].breaks`, `behaviors[].log`) | `qa_server/catalog.py` `run_test()` |
| Hub | 두 시스템의 주소와 조회 API 전부, 응답 필드 이름, 사이클 상태값의 의미 | `hub/app.py` 전체 |
| 공통 | `cycle_id`가 Dev가 발급한 사이클 번호라는 약속 (QA는 자기 사이클 테이블이 없음) | QA 테이블에 `cycle_id` 컬럼만 존재 |

주소는 `common/config.py`에서 환경변수 `DEV_URL`(기본 `http://127.0.0.1:8201`), `QA_URL`(기본 `http://127.0.0.1:8202`)로 읽습니다. 서비스 디스커버리는 없고, 설정으로 서로의 위치를 알아야 합니다.

### 4.4 데이터 소유

| 데이터 | 소유 시스템 | 다른 시스템이 얻는 방법 |
| --- | --- | --- |
| 사이클(차례, 상태, 반복 횟수) | Dev | Hub가 `GET /api/state`, `/api/cycles` 조회 |
| 릴리즈, 빌드 산출물 | Dev | QA는 `build_url`로 다운로드, Hub는 `/api/releases` 조회 |
| 티켓(이관받은 문제의 사본), 티켓 이력 | Dev | Hub가 `/api/tickets` 조회 |
| 테스트케이스, 담당자 | QA | Hub가 `/api/test-runs/{id}` 결과에서 확인 |
| 검증 실행, 판정, 결정(이관/확정) | QA | Hub가 `/api/test-runs` 조회 |
| 이슈(원본), 이슈 이력 | QA | Dev는 이관 시 사본을 받음, Hub는 `/api/issues` 조회 |
| 부서원 명부 | 각자 (`members` 테이블) | 공유하지 않음 |
| 이벤트, 연동 로그 | 각자 | Hub가 `/api/events`, `/api/integration-logs`를 조회해 병합 |

이슈는 QA가 원본을, Dev가 사본(티켓)을 가집니다. 같은 이슈 키(`ISSUE-n`)로 연결되지만 상태는 따로 관리됩니다(QA `TRANSFERRED`일 때 Dev는 `IN_PROGRESS`일 수 있음). 두 상태를 나란히 보려면 Hub가 키로 결합해야 합니다(`hub/app.py` `issues_with_tickets()`).

---

## 5. 소스 구조

### 5.1 전체 트리

```text
02_api/
├─ run.py                         # 3개 서비스 하위 프로세스 기동/종료 (--mock, --only)
├─ pyproject.toml                 # fastapi, uvicorn, jinja2, httpx, anthropic, python-dotenv (Python >= 3.12)
├─ uv.lock                        # 의존성 고정
├─ README.md                      # 실행 방법, 연동 계약, 데이터 소유, 점검 결과
├─ data/                          # dev.db, qa.db (실행 시 생성)
├─ docs/
│  ├─ PRD.md                      # 요구사항
│  ├─ TODO.md                     # 작업 목록 (2026-10-15 리팩토링, 2026-10-16 이슈 수정 포함)
│  ├─ USER_SCENARIO.md            # 실습·데모·장애 시나리오
│  └─ images/                     # hub_overview, hub_integration, dev_web, qa_web (.png)
└─ src/
   ├─ common/                     # 세 서비스 공통 기반
   │  ├─ __init__.py
   │  ├─ config.py                # .env 로드, 주소·포트·모델·지연값, llm_mode(), db_path()
   │  ├─ db.py                    # Database: SQLite(WAL), 공통 스키마(events, integration_logs, members), migrate(), reset()
   │  ├─ events.py                # EventBus: 이벤트 DB 기록 + SSE 구독자 전달
   │  ├─ http.py                  # IntegrationClient(재시도·호출 로그), InboundLogMiddleware(수신 로그)
   │  ├─ llm.py                   # LLMClient: Claude 구조화 출력 호출, 실패 시 mock 대체
   │  ├─ service_api.py           # 공통 운영 API: /health, /api/events, /api/events/stream, /api/integration-logs
   │  ├─ work.py                  # WorkError(업무 오류 + HTTP 상태), Members(부서원 명부·권한 검사)
   │  └─ web/
   │     ├─ setup.py              # setup_web(): /static 마운트, 서비스+공통 템플릿 ChoiceLoader, 필터
   │     ├─ ui.py                 # 상태별 배지 톤, 상태 한글 라벨, 시간 포맷 필터, pct()
   │     ├─ static/css/app.css    # 모노톤 UI, 다크 테마
   │     ├─ static/js/app.js      # SSE 연결, 부분 갱신(입력 보호, data-preserve), 토스트, 시작/중단/초기화
   │     ├─ static/js/workbench.js# 작업자 선택(?as=), 작업대 버튼 → /api/work/*, 대화상자, AI 초안, 실습 시작/중단
   │     └─ templates/
   │        ├─ base.html          # 공통 레이아웃 (사이드바, 상단 바, 작업자 선택, 확인/중단 대화상자)
   │        ├─ _macros.html       # badge, empty, result_bar, pipeline, timeline 매크로
   │        ├─ timeline.html      # Dev/QA 공통 타임라인 화면
   │        └─ integration_log.html # Dev/QA 공통 연동 로그 화면
   ├─ dev_server/                 # 개발부서 시스템 (:8201)
   │  ├─ __init__.py
   │  ├─ __main__.py              # uvicorn 기동
   │  ├─ catalog.py               # 제품, 결함 정의, 회귀 규칙, next_version(), apply_fixes(), build_manifest()
   │  ├─ service.py               # DevService: 사이클, 발행, 티켓 수신·수정, 확정 수신, 실습 작업, LLM 문장
   │  ├─ app.py                   # 웹 화면 6개 + 운영/연동 API + 실습 API
   │  └─ templates/               # layout, desk, releases, tickets, ticket_detail
   ├─ qa_server/                  # 검증부서 시스템 (:8202)
   │  ├─ __init__.py
   │  ├─ __main__.py
   │  ├─ catalog.py               # 테스트케이스 12건, 기능별 심각도, run_test()
   │  ├─ service.py               # QAService: 발행 통지 수신, 자동 검증, 실습 판정·이슈·수정 확인·이관·확정
   │  ├─ app.py                   # 웹 화면 7개 + 연동 API + 실습 API
   │  └─ templates/               # layout, desk, runs, test_run_detail, issues, issue_detail
   └─ hub/                        # 관제 (:8200, DB 없음)
      ├─ __init__.py
      ├─ __main__.py
      ├─ upstream.py              # Upstream(요청 단위 조회·오류 수집·대리 POST·health), EventRelay(SSE 병합)
      ├─ app.py                   # 화면 9개 조합, 편의 조작(대리 호출), /api/state, 사이클 요약
      └─ templates/               # layout, overview, releases, release_detail, test_runs, test_run_detail,
                                  # issues, issue_detail, timeline, integration
```

### 5.2 공통 계층 (`02_api/src/common`)

**`config.py`** — 단계 폴더(`02_api/.env`)와 프로젝트 루트(`.env`)를 차례로 읽되, 이미 설정된 환경변수는 덮어쓰지 않습니다(`override=False`). `llm_mode()`는 `LLM_MODE=mock`이거나, `auto`인데 `ANTHROPIC_API_KEY`가 없으면 `mock`을 돌려줍니다. `PORTAL_PORT = HUB_PORT`는 이전 이름(Portal)과의 호환용 별칭입니다.

**`db.py` `Database`** — 서비스마다 하나씩 만들고, `isolation_level=None`(autocommit)과 WAL 모드를 씁니다. 생성 시 공통 스키마(`events`, `integration_logs`, `members`)에 서비스 스키마를 더해 `executescript`합니다.
- `migrate(columns)`: `PRAGMA table_info`로 없는 컬럼만 `ALTER TABLE ADD COLUMN`합니다. 실습 모드를 나중에 추가하면서 기존 DB 파일을 지우지 않고도 쓸 수 있게 했습니다.
- `JSON_COLUMNS`(`features`, `defects`, `fixed_issue_keys`, `payload`, `build`)는 읽을 때 자동으로 `json.loads`하고, `execute()`는 list/dict 인자를 자동으로 `json.dumps`합니다.
- `reset(keep=())`: `keep`에 든 테이블을 뺀 모든 테이블을 비우고 `sqlite_sequence`도 지워 번호를 1부터 다시 매깁니다.

**`events.py` `EventBus`** — `emit()`은 이벤트를 `events` 테이블에 저장한 뒤 구독자에게 보내고, `publish()`는 DB에 남기지 않는 일시 이벤트(테스트 진행률 `test.progress`, 사이클 상태 `cycle.status`, `system.reset`)를 보냅니다. `stream()`은 구독자마다 `asyncio.Queue`를 만들고, 15초 동안 이벤트가 없으면 `: keep-alive` 주석을 보냅니다. 처음에 `retry: 3000`을 보내 브라우저가 끊기면 3초 후 다시 연결하게 합니다.

**`http.py` `IntegrationClient`** — Stage 2의 핵심 연동 부품입니다.

```text
request(method, url, json_body, cycle_id)
  for attempt in 1..3:
      시작 시각 측정
      httpx 요청 (헤더: X-Caller=<서비스명>, X-Cycle-Id=<사이클>)
        ├ 응답 수신: status, response_text 저장
        │   └ status >= 500 이면 error = "HTTP 5xx"   (재시도 대상)
        └ httpx.HTTPError (연결 실패, 타임아웃 등): error = "<예외명>: <메시지>"  (재시도 대상)
      integration_logs에 direction='out' 한 행 기록 (attempt 번호, 소요 ms, 요청/응답 본문 4000자 제한)
      error 없음:
          status >= 400 → IntegrationError 즉시 발생 (4xx는 재시도하지 않음)
          그 외         → 응답 JSON 반환
      error 있음: attempt < 3 이면 BACKOFF[attempt-1] 초 대기 (0.5 → 1.0)
  3회 모두 실패 → IntegrationError("... 호출 실패 (3회 시도): <마지막 오류>")
```

- `httpx.AsyncClient(timeout=30)`이고 기본 헤더 `X-Caller`에 `dev-server` 또는 `qa-server`가 들어갑니다.
- `BACKOFF = (0.5, 1.0)`입니다(2026-10-16에 쓰이지 않던 세 번째 값 2.0을 제거). 연결이 거부되면 실패까지 약 1.5초에 각 시도 시간이 더해집니다.
- 4xx(예: 상대가 409를 돌려줌)는 "상대가 의도적으로 거절한 것"으로 보고 재시도하지 않습니다.

**`http.py` `InboundLogMiddleware`** — Starlette `BaseHTTPMiddleware`입니다. 요청에 `X-Caller` 헤더가 있을 때만(즉 다른 서비스가 `IntegrationClient`로 보낸 요청만) `direction='in'`으로 기록합니다. `url` 컬럼에는 `"qa-server -> /api/tickets"`처럼 "호출자 -> 경로"를 저장하고, `X-Cycle-Id`를 정수로 바꿔 `cycle_id`에 넣습니다. 요청 본문은 남기지만 응답 본문은 `NULL`로 둡니다. 브라우저 요청과 Hub의 조회·대리 호출에는 `X-Caller`가 없으므로 기록되지 않습니다.

**`llm.py` `LLMClient`** — 10장에서 자세히 설명합니다.

**`service_api.py` `add_common_routes()`** — Dev와 QA에 같은 운영 API(`/health`, `/api/events`, `/api/events/stream`, `/api/integration-logs`)를 붙입니다. Hub는 이 API들로 두 시스템을 같은 방식으로 조회합니다.

**`work.py`** — `WorkError(message, status=400)`는 업무 규칙 위반을 HTTP 상태와 함께 나타내는 예외이고, Dev/QA `app.py`의 `exception_handler`가 `{"error": "..."}` JSON으로 바꿉니다. `Members`는 시스템별 부서원 명부입니다. `seed()`는 `INSERT OR IGNORE`로 넣고, `require(name)`은 명부에 없는 이름이면 `WorkError(..., 403)`을 냅니다. `actor()`는 이벤트 주체 문자열(`"Dev 최개발"`, `"QA 김검증"`)을 만듭니다.

**`web/`** — `setup_web(app, service_templates)`가 `/static`을 마운트하고, Jinja2 로더를 "서비스 템플릿 우선, 없으면 공통 템플릿" 순서의 `ChoiceLoader`로 구성합니다. 그래서 Dev와 QA는 `timeline.html`, `integration_log.html`을 공유하고 Hub는 자기 `timeline.html`을 따로 씁니다.

### 5.3 `DevService` (`02_api/src/dev_server/service.py`)

| 구분 | 메서드 | 역할 |
| --- | --- | --- |
| 초기화 | `__init__` | `migrate(MIGRATIONS)`, 명부 seed, **이전 프로세스에서 끝나지 못한 데모 사이클을 `CANCELLED`로 정리** (실습 사이클은 유지) |
| 사이클 | `active_cycle()` | 상태가 `RELEASING/VERIFYING/TRANSFERRING/FIXING`인 가장 최근 사이클 |
| | `start_cycle(mode)` | 진행 중 사이클이 있으면 `CycleConflict`. 데모는 `iteration=1`로 만들고 `_initial_release` 백그라운드 작업 시작, 실습은 `iteration=0`으로 만들고 사람의 발행을 기다림 |
| | `_set_cycle()` | 상태 변경 + `cycle.status` 일시 이벤트 |
| | `_fail()` / `abort()` | `FAILED`(오류 사유 저장) / 모든 백그라운드 작업 취소 → `CANCELLED` → QA에 `cycle-cancelled` 통지. 통지가 실패해도 중단은 유지하고 `integration.error` 이벤트를 남김 |
| 발행 | `_notify_qa()` | `release-published` Webhook 본문 구성 → `IntegrationClient.post` → `webhook.sent` 이벤트 |
| 데모 | `_initial_release()` | v1.0.0 저장(결함 D-101~103) → `_publish` |
| | `_publish()` | `VERIFYING`으로 바꾸고 QA 통지, 실패하면 사이클 `FAILED` |
| | `_fix_and_release()` | 최대 반복(5) 검사 → `FIXING` → 티켓마다 `IN_PROGRESS → FIXED`(LLM 수정 내역) → 결함 적용(회귀 발생 시 이벤트) → 릴리즈 노트 → 저장 → `iteration+1` → `_publish` |
| 수신 | `receive_tickets()` | 사이클이 `VERIFYING`이 아니면 `CycleConflict`(409 "검증 단계의 사이클이 아닙니다."). 새 이슈는 `RECEIVED`로 생성. 이미 있는 키 중 수정 중(`RECEIVED`/`IN_PROGRESS`)인 이월 티켓은 상태·담당자를 그대로 두고 이력만 남기며, `FIXED` 티켓(재현되어 재이관)은 `RECEIVED`로 되돌리고 `fixed_in_version` 초기화. `TRANSFERRING`으로 바꾼 뒤, 데모면 `_fix_and_release` 시작, 실습이면 곧바로 `FIXING` |
| | `verification_completed()` | 사이클이 `VERIFYING`이고 통지 버전이 `current_version`일 때만 처리(아니면 409). 해당 릴리즈에 `approved_at` 기록, 사이클 `APPROVED`, `cycle.completed` 이벤트 |
| 실습 | `_manual(*statuses)` | 진행 중인 실습 사이클이 없거나 단계가 맞지 않으면 `WorkError(409)` |
| | `release(actor, note)` | 명부 확인(403) → 단계 확인(`RELEASING`/`FIXING`) → 최초면 기본 결함 + 기본 노트, 이후면 반영할 티켓(`FIXED`이면서 `fixed_in_version IS NULL`)과 노트 필수(400) → 릴리즈 저장 → QA 통지. **통지 실패 시 저장한 릴리즈를 삭제하고 `WorkError(502)`** → 성공 시 티켓에 `fixed_in_version` 기록, 회귀 이벤트, `VERIFYING`, `iteration+1` |
| | `start_fix()` / `fix()` | `FIXING` 단계에서만. 착수는 담당자 지정 + `IN_PROGRESS`, 수정 완료는 수정 내역 필수 + `FIXED`(`RECEIVED`에서 바로 완료도 허용) |
| | `draft_fix()` / `draft_release()` | AI 초안(LLM 또는 mock) |
| 보조 | `shippable()`, `next_version()` | 반영 대기 티켓, 다음 버전 번호(`1.0.0 → 1.1.0 → 1.2.0`) |
| | `_apply_fixes()` | `catalog.apply_fixes()`로 남은 결함 + 새 회귀 계산. 실습 발행에서는 통지 전 `emit=False`로 계산하고 성공 후 `emit=True`로 회귀 이벤트를 냄 |

빌드 산출물 계산은 `dev_server/catalog.py`에 있습니다. `apply_fixes(defects, fixed_test_case_ids)`는 고친 테스트케이스를 깨뜨리던 결함을 지우고, 지운 결함 중 `REGRESSIONS`에 있는 것(`D-103 → D-201`)을 새로 넣습니다. `build_manifest()`는 남은 결함을 `behaviors` 배열로 만듭니다.

### 5.4 `QAService` (`02_api/src/qa_server/service.py`)

| 구분 | 메서드 | 역할 |
| --- | --- | --- |
| 초기화 | `__init__`, `seed()` | 마이그레이션, 과거 데이터의 `result` 컬럼 채우기, **끝나지 못한 데모 실행(`RUNNING`)을 `CANCELLED`로 정리**, 테스트케이스·명부 seed, 기능별 담당자 지정 |
| 수신 | `on_release_published()` | `webhook.received` 이벤트 → `fixed_issue_keys`의 이슈를 `FIXED`(수정 버전 기록) → 실습이면 `_open_manual_run`, 데모면 `_verify` 백그라운드 작업(작업 이름 `verify-{cycle}`) |
| | `on_cycle_cancelled()` | 해당 사이클의 `_verify` 작업 취소, 그 사이클의 `RUNNING` 실행을 모두 `CANCELLED`로 닫고 `webhook.received` 이벤트 |
| 데모 | `_verify()` | 빌드 다운로드(`GET build_url`) → `_run_tests` → `_verify_fixed` → `_register_failures` → 실패가 있으면 `_transfer`, 없으면 `_notify_approved` → 실행에 `decision`(`TRANSFERRED`/`APPROVED`), `decided_by='Demo'` 기록. 연동 실패는 `integration.error` 이벤트로 남김 |
| | `_run_tests()` | 12건을 `catalog.run_test(build, tc)`로 판정, 건마다 `test.progress` 일시 이벤트, 끝나면 `DONE` |
| | `_verify_fixed()` | 이번 버전에 수정됐다는 이슈를 테스트 결과로 `VERIFIED` 또는 `REOPENED` |
| | `_register_failures()` | 실패 항목 중 활성 이슈(`OPEN/TRANSFERRED/REOPENED`)가 없는 것만 LLM 분석 후 새 이슈 등록 |
| 공통 | `_transfer()` | 이관 대상(`OPEN/REOPENED` + 직전 발행에 반영되지 않아 `TRANSFERRED`로 남은 이월 이슈)을 모아 `POST /api/tickets`. **호출이 성공한 뒤에** 이슈를 `TRANSFERRED`로 바꿈(이월 이슈는 "다시 이관" 이력) |
| | `_notify_approved()` | `POST /api/webhooks/verification-completed` 후 `release.approved` 이벤트 |
| 실습 | `_open_manual_run()` | 같은 사이클의 `RUNNING` 실행을 `CANCELLED`로 닫고, 새 실행과 12개 `PENDING` 결과(담당자 포함) 생성 |
| | `execute()` | 명부 확인 → 첫 실행이면 Dev에서 빌드를 내려받아 `test_runs.build`에 보관(실패 시 502) → 시뮬레이터 결과를 `sim_log`에 저장 (참고용, 판정은 아님) |
| | `judge()` | 명부 확인 → `PASS`/`FAIL` 기록, 판정자·시각, 집계 갱신. 담당자 일치는 검사하지 않음(Stage 1과 동일, 11.5) |
| | `create_issue()` | 제목·심각도 필수 → 해당 항목을 `FAIL`로 판정 → 같은 테스트케이스에 활성 이슈가 있으면 거기에 연결(`created: false`), 없으면 새 이슈 |
| | `verify_issue()` | 이번 버전에 수정됐다는 `FIXED` 이슈만 `VERIFIED`/`REOPENED` |
| | `readiness()` | 판정 대기, 수정 확인 대기, 이관 대상(`OPEN/REOPENED/TRANSFERRED`), 미해결(`OPEN/TRANSFERRED/REOPENED/FIXED`) 개수와 `can_transfer`, `can_approve` |
| | `transfer()` / `approve()` | 준비 조건 확인(400, 사유 문구는 `_blocker`) → 연동 호출(실패 시 502) → 실행을 `DONE`으로 닫고 `decision`, `decided_by` 기록 |
| LLM | `analyze()`, `draft_issue()` | 실패 로그로 결함 리포트(제목·설명·심각도) 작성 |

이관 조건은 "판정 대기 0, 수정 확인 대기 0, 이관 대상 1건 이상", 확정 조건은 "판정 대기 0, 수정 확인 대기 0, 미해결 0"입니다. 실습 중 QA 차례에 `TRANSFERRED`로 남은 이슈는 정의상 직전 발행에 반영되지 않은 이월 이슈(반영된 이슈는 발행 통지로 `FIXED`가 됨)이므로 이관 대상에 포함합니다. 이 규칙이 없으면 Dev가 일부 티켓만 고쳐 발행했을 때 이관도 확정도 할 수 없는 교착이 생깁니다(12.1 (3)).

### 5.5 Hub 업스트림 계층 (`02_api/src/hub/upstream.py`, `app.py`)

**`Upstream`** — HTTP 요청 하나마다 새로 만듭니다.
- `get(base, path, default, **params)`: 404면 `default`를 돌려주고, 연결 실패나 5xx면 `"Dev Server 응답 없음 (ConnectError)"` 같은 문구를 `errors`에 모은 뒤 `default`를 돌려줍니다. 화면은 이 `errors`를 공통 `base.html`의 "연동 장애" 배너로 보여 줍니다. 한 서비스가 죽어도 화면 전체가 깨지지 않고, 나머지 데이터는 그대로 보입니다.
- `post(base, path)`: 대리 호출용입니다. 상대 응답 상태와 본문을 그대로 돌려주고, 연결에 실패하면 `503`을 돌려줍니다.
- `health(base)`: 3초 타임아웃으로 `/health`를 호출하고 지연 시간(ms)을 잽니다. 실패하면 `DOWN`입니다.

**`EventRelay`** — Hub가 시작될 때 Dev와 QA의 `/api/events/stream`을 각각 구독하는 작업 두 개를 띄웁니다. 받은 이벤트에 `source`(`Dev`/`QA`)를 붙여 Hub 구독자에게 다시 보냅니다. 연결되면 `service.up`, 끊기면 `service.down`을 보내고 2초 후 다시 연결합니다. 브라우저의 `app.js`는 `service.*` 이벤트는 무시하고, 나머지 이벤트가 오면 부분 갱신을 예약합니다.

**`hub/app.py` 조합 헬퍼**
- `base_ctx()`: Dev `/api/state`와 QA `/api/state`를 동시에 조회합니다. `?cycle=N`이 있으면 해당 사이클을 따로 조회합니다. 실습 사이클이 진행 중이면 상태가 `VERIFYING/TRANSFERRING`일 때 `turn="QA"`, 그 밖에는 `turn="Dev"`로 정합니다.
- `releases_with_runs()`: Dev 릴리즈와 QA 검증 실행을 **버전 문자열**로 결합합니다.
- `issues_with_tickets()`: QA 이슈와 Dev 티켓을 **이슈 키**로 결합합니다 (`dev_status`, `fix_note`, `assignee` 추가).
- `merged_events()`: 두 시스템 이벤트에 `source`를 붙이고 `(ts, source=="QA", id)` 기준 역순으로 정렬합니다.
- 이슈 상세는 QA `issue_history`와 Dev `ticket_history`를 시각 순으로 합칩니다.

Hub는 상태를 저장하지 않으므로 Hub를 다시 시작해도 잃는 데이터가 없습니다. 대신 화면을 열 때마다 여러 번 조회합니다(Overview 한 번에 Dev/QA 조회 약 9~10회).

---

## 6. 데이터 모델

### 6.1 공통 테이블 (Dev, QA 각각 보유) — `common/db.py`

| 테이블 | 컬럼 | 설명 |
| --- | --- | --- |
| `events` | `id, ts, cycle_id, actor, type, message, payload(JSON)` | 업무 이벤트 (타임라인 원천) |
| `integration_logs` | `id, ts(ms), cycle_id, direction(out/in), method, url, status, duration_ms, attempt, request_body, response_body, error` | 서비스 간 호출 기록 |
| `members` | `name(PK), dept(Dev/QA), role, features(JSON)` | 이 시스템의 부서원 명부 |

### 6.2 Dev Server (`data/dev.db`) — `dev_server/service.py` `SCHEMA` + `MIGRATIONS`

| 테이블 | 컬럼 | 비고 |
| --- | --- | --- |
| `cycles` | `id, status, iteration, current_version, llm_mode, error, started_at, finished_at`, `mode`(마이그레이션, 기본 `'demo'`) | 사이클과 차례의 원본 |
| `releases` | `id, cycle_id, version, features(JSON), build(JSON), fixed_issue_keys(JSON), release_note, released_at, approved_at`, `released_by`(마이그레이션) | `UNIQUE(cycle_id, version)`, `build`가 빌드 산출물 |
| `tickets` | `id, cycle_id, key, test_case_id, title, description, severity, version_found, status, fix_note, fixed_in_version, received_at, updated_at`, `assignee`(마이그레이션) | `UNIQUE(cycle_id, key)` |
| `ticket_history` | `id, ticket_id, from_status, to_status, note, ts`, `actor`(마이그레이션) | 티켓 상태 이력 |

**사이클 상태 전이 (Dev 소유)**

```text
                start_cycle()
                     │
                     ▼
               ┌──────────┐  발행 + QA 통지 성공   ┌──────────┐
               │RELEASING │ ─────────────────────▶ │VERIFYING │ ◀──────────────────┐
               └──────────┘                        └────┬─────┘                    │
                     │                                  │                          │
                     │                    POST /api/tickets 수신       verification-completed 수신
                     │                                  ▼                          │       │
                     │                           ┌─────────────┐                   │       ▼
                     │                           │TRANSFERRING │                   │  ┌──────────┐
                     │                           └──────┬──────┘                   │  │ APPROVED │
                     │                 데모: STEP_DELAY 후 / 실습: 즉시              │  └──────────┘
                     │                                  ▼                          │
                     │                             ┌─────────┐  수정 후 발행 성공    │
                     │                             │ FIXING  │ ────────────────────┘
                     │                             └─────────┘
   (어느 진행 단계에서든)  abort() → CANCELLED (+ QA에 중단 통지),  연동 실패/내부 오류/반복 초과(데모) → FAILED
   서버 재시작 시 진행 중이던 데모 사이클 → CANCELLED ("서버 재시작으로 중단")
```

- `iteration`: 데모는 1에서 시작해 수정·발행 때마다 +1, 실습은 0에서 시작해 발행 때마다 +1. 결과적으로 둘 다 "검증 횟수"(완주 시 3)를 뜻합니다.
- 데모는 `iteration >= MAX_ITERATIONS(5)`이면 `FAILED`가 됩니다. 실습에는 이 제한이 없습니다.

**티켓 상태 전이 (Dev 소유)**

```text
  POST /api/tickets(신규) ──▶ RECEIVED ──착수──▶ IN_PROGRESS ──수정 완료──▶ FIXED ──(발행 시 fixed_in_version 기록)
                                 │                                  ▲          │
                                 └────────── 수정 완료(착수 생략) ───┘          │
                       POST /api/tickets(같은 키 재이관) ◀───────────────────────┘ → RECEIVED (fixed_in_version = NULL)
  POST /api/tickets(이월 재이관, 티켓이 RECEIVED/IN_PROGRESS) → 상태·담당자 유지, 이력만 추가
```

### 6.3 QA Server (`data/qa.db`) — `qa_server/service.py` `SCHEMA` + `MIGRATIONS`

| 테이블 | 컬럼 | 비고 |
| --- | --- | --- |
| `test_cases` | `id(PK), feature, name`, `assignee`(마이그레이션) | 초기화해도 유지 (`reset(keep=("test_cases",))`) |
| `test_runs` | `id, cycle_id, version, build_url, total, passed, failed, status, started_at, finished_at`, `mode, build(JSON), decision, decided_by`(마이그레이션) | 버전 하나의 검증 실행 |
| `test_results` | `id, run_id, test_case_id, passed, log`, `result, assignee, sim_log, checked_by, checked_at`(마이그레이션) | 항목별 결과 |
| `issues` | `id, cycle_id, key, test_case_id, version_found, title, description, severity, status, fixed_in_version, created_at, updated_at`, `reporter`(마이그레이션) | `UNIQUE(cycle_id, key)`, 이슈 원본 |
| `issue_history` | `id, issue_id, from_status, to_status, note, ts`, `actor`(마이그레이션) | 이슈 상태 이력 |

**상태값**

| 대상 | 값 | 의미 |
| --- | --- | --- |
| `test_runs.status` | `RUNNING` → `DONE` / `CANCELLED` | 진행 중 / 완료 / 취소(실습의 새 발행으로 대체, 사이클 중단 통지, 서버 재시작 시 데모 실행 정리) |
| `test_runs.decision` | `TRANSFERRED` / `APPROVED` / `NULL` | 이관 / 확정 / 미결정. 데모는 `decided_by='Demo'`, 실습은 결정한 검증부서원 |
| `test_results.result` | `PENDING` → `PASS` / `FAIL` | 판정 대기 / 통과 / 실패 |
| `test_results.sim_log` | `"PASS\nOK"` / `"FAIL\n<로그>"` | 실습 '실행' 결과(참고). 판정과 다르면 화면에 "실행 결과와 다름" |
| `issues.severity` | `Critical` / `Major` / `Minor` | mock 기본값: payment Critical, login·report Major, search Minor |

**이슈 상태 전이 (QA 소유)**

```text
  등록 ──▶ OPEN ──(Dev 이관 성공)──▶ TRANSFERRED ──(release-published에 키 포함)──▶ FIXED
                                        ▲                                            │
                                        │                               이번 버전 재검증
                                        │                         ┌──────────────────┴──────────────┐
                                        │                         ▼                                 ▼
                                        └──(재이관)──────── REOPENED                            VERIFIED (종료)

  TRANSFERRED ──(다음 발행에 키가 없음 = 이월)──▶ QA 차례에 다시 이관 → TRANSFERRED (이력 "다시 이관")
```

### 6.4 두 시스템의 상태가 함께 움직이는 모습 (ISSUE-3 예시, 데모)

| 시점 | QA `issues.status` | Dev `tickets.status` | Dev `cycles.status` |
| --- | --- | --- | --- |
| v1.0.0 검증 중 TC-08 실패 | `OPEN` | (없음) | `VERIFYING` |
| QA가 이관 | `TRANSFERRED` | `RECEIVED` | `TRANSFERRING` |
| Dev 수정 | `TRANSFERRED` | `IN_PROGRESS` → `FIXED` | `FIXING` |
| v1.1.0 발행 통지 | `FIXED` (fixed_in 1.1.0) | `FIXED` (fixed_in 1.1.0) | `VERIFYING` |
| v1.1.0에서 TC-08 통과 | `VERIFIED` | `FIXED` | `VERIFYING` |

같은 결함을 두 시스템이 각자의 언어로 기록하고, "둘을 합친 모습"은 Hub에서만 보입니다.

---

## 7. 인터페이스

### 7.1 Dev Server (:8201) REST API

| Method | Path | 태그 | 용도 | 주요 응답 |
| --- | --- | --- | --- | --- |
| GET | `/health` | ops | 상태 확인 | `{"service":"dev-server","status":"ok","llm":"mock"}` |
| GET | `/api/events?cycle_id=&limit=500` | ops | 이벤트 목록 (최신순) | 배열 |
| GET | `/api/events/stream` | ops | SSE | `text/event-stream` |
| GET | `/api/integration-logs?cycle_id=&limit=300` | ops | 연동 로그 | 배열 |
| POST | `/api/admin/reset?dry_run=` | ops | 데이터 초기화 (데모 진행 중이면 409). `dry_run=true`면 가능 여부만 확인 | `{"ok":true}` |
| GET | `/api/state` | cycle | 실행 여부, 최근 사이클, LLM 통계 | 아래 예시 |
| POST | `/api/cycles?mode=demo\|manual` | cycle | 사이클 시작 | 201 `{"cycle_id":1}` / 400 / 409 |
| GET | `/api/cycles` | cycle | 사이클 목록 (릴리즈 수, 티켓 수 포함) | 배열 |
| GET | `/api/cycles/{id}` | cycle | 사이클 1건 | 객체 / 404 |
| POST | `/api/cycles/{id}/abort` | cycle | 사이클 중단 (QA에 중단 통지) | `{"ok":true}` / 409 |
| GET | `/api/releases?cycle_id=` | release | 릴리즈 목록 (빌드 제외) | 배열 |
| GET | `/api/releases/{version}?cycle_id=` | release | 릴리즈 1건 | 객체 / 404 |
| GET | `/builds/{cycle_id}/{version}` | release | **빌드 산출물 다운로드 (QA가 호출)** | 매니페스트 / 404 |
| POST | `/api/tickets` | ticket | **이관 수신 (QA가 호출)**, `VERIFYING` 사이클만 | 202 `{"accepted":n}` / 409 |
| GET | `/api/tickets?cycle_id=` | ticket | 티켓 목록 | 배열 |
| GET | `/api/tickets/{key}?cycle_id=` | ticket | 티켓 + 이력 | 객체 / 404 |
| POST | `/api/webhooks/verification-completed` | webhook | **확정 통지 수신 (QA가 호출)**, `VERIFYING` 사이클의 현재 버전만 | `{"ok":true}` / 409 |
| POST | `/api/work/start` | work | 실습 사이클 시작 | `{"cycle_id":n}` / 409 |
| POST | `/api/work/abort` | work | 진행 중 사이클 중단 (QA에 중단 통지) | `{"ok":true}` / 409 |
| POST | `/api/work/release` | work | 버전 발행 `{actor, release_note}` | `{"version":"1.1.0"}` / 400·403·409·502 |
| POST | `/api/work/issues/{key}/start` | work | 티켓 착수 `{actor}` | `{"ok":true}` / 400·403·409 |
| POST | `/api/work/issues/{key}/fix` | work | 수정 완료 `{actor, fix_note}` | `{"ok":true}` / 400·403·409 |
| POST | `/api/work/draft/fix/{key}` | work | 수정 내역 AI 초안 | `{"fix_note":"..."}` |
| POST | `/api/work/draft/release` | work | 릴리즈 노트 AI 초안 | `{"release_note":"..."}` |

화면 경로(스키마 비공개): `/`(개발 작업대), `/releases`, `/tickets`, `/tickets/{key}`, `/timeline`, `/integration`.

`GET /api/state` 응답 예:

```json
{
  "running": true,
  "cycle": {"id": 3, "status": "FIXING", "iteration": 1, "current_version": "1.0.0", "llm_mode": "mock",
            "error": null, "started_at": "2026-10-15T15:09:37", "finished_at": null, "mode": "manual"},
  "llm": {"mode": "mock", "model": "claude-opus-5-5", "calls": 0, "failures": 0, "last_error": null}
}
```

### 7.2 QA Server (:8202) REST API

| Method | Path | 태그 | 용도 | 주요 응답 |
| --- | --- | --- | --- | --- |
| GET | `/health`, `/api/events`, `/api/events/stream`, `/api/integration-logs` | ops | Dev와 동일한 공통 API | |
| POST | `/api/admin/reset?dry_run=` | ops | 초기화 (자동 검증 작업 진행 중이면 409), 테스트케이스는 유지. `dry_run=true`면 확인만 | `{"ok":true}` |
| POST | `/api/webhooks/release-published` | webhook | **발행 통지 수신 (Dev가 호출)** | 202 `{"accepted":true}` |
| POST | `/api/webhooks/cycle-cancelled` | webhook | **사이클 중단 통지 수신 (Dev가 호출)** | `{"cancelled_runs":n}` |
| GET | `/api/state` | test | 실습 검증 중 여부, 현재 실행 요약, 준비 상태 | 아래 예시 |
| GET | `/api/test-cases` | test | 테스트케이스와 담당자 | 배열 |
| GET | `/api/test-runs?cycle_id=` | test | 검증 실행 목록 | 배열 |
| GET | `/api/test-runs/{run_id}` | test | 실행 + 결과 12건 | 객체 / 404 |
| GET | `/api/issues?cycle_id=` | issue | 이슈 목록 | 배열 |
| GET | `/api/issues/{key}?cycle_id=` | issue | 이슈 + 이력 | 객체 / 404 |
| POST | `/api/work/results/{id}/execute` | work | 실행(참고 결과) `{actor}` | `{"passed":false,"log":"..."}` / 403·404·409·502 |
| POST | `/api/work/results/{id}/judge` | work | 판정 `{actor, result: PASS\|FAIL}` | `{"ok":true}` |
| POST | `/api/work/issues` | work | 이슈 등록 + FAIL 판정 `{actor, result_id, title, severity, description}` | `{"key":"ISSUE-1","created":true}` |
| POST | `/api/work/issues/{key}/verify` | work | 수정 확인/재현 `{actor, ok}` | `{"ok":true}` |
| POST | `/api/work/transfer` | work | Dev 이관 `{actor}` | `{"transferred":["ISSUE-1",...]}` / 400·502 |
| POST | `/api/work/approve` | work | 버전 확정 `{actor}` | `{"ok":true}` / 400·502 |
| POST | `/api/work/draft/issue/{id}` | work | 이슈 AI 초안 | `{"title","description","severity","source"}` |

화면 경로: `/`(검증 작업대), `/runs`, `/runs/{id}`, `/issues`, `/issues/{key}`, `/timeline`, `/integration`.

`GET /api/state` 응답 예 (화면 캡처 `qa_web.png` 시점, 김검증이 담당 6건을 판정한 직후):

```json
{
  "verifying": true,
  "run": {"id": 1, "cycle_id": 1, "version": "1.0.0", "passed": 4, "failed": 2, "total": 12},
  "readiness": {"pending": 6, "unverified": 0, "open": 2, "unresolved": 2, "can_transfer": false, "can_approve": false}
}
```

### 7.3 Hub (:8200) API

| Method | Path | 용도 | 실제 동작 |
| --- | --- | --- | --- |
| GET | `/`, `/releases`, `/releases/{v}`, `/test-runs`, `/test-runs/{id}`, `/issues`, `/issues/{key}`, `/timeline`, `/integration` | 관제 화면 9개 (`?cycle=N`으로 과거 사이클 보기) | Dev/QA 조회 조합 |
| POST | `/api/cycles` | 데모 실행 | Dev `POST /api/cycles?mode=demo` 대리 호출 |
| POST | `/api/work/start` | 실습 시작 | Dev `POST /api/cycles?mode=manual` 대리 호출 |
| POST | `/api/work/abort` | 사이클 중단 | Dev `/api/state`로 사이클 확인 후 `POST /api/cycles/{id}/abort` (QA 통지는 Dev가 함) |
| POST | `/api/reset` | 초기화 | 1단계: Dev·QA에 `?dry_run=true`로 가능 여부 확인, 하나라도 거절하면 그 응답을 반환하고 아무것도 지우지 않음. 2단계: Dev → QA 순서로 실제 초기화 |
| GET | `/api/state` | 통합 상태 | Dev 상태 + `"qa": QA 상태` |
| GET | `/api/cycles/{id}/summary` | 사이클 요약 (회귀 점검용) | 릴리즈별 결과·결정, 이슈별 QA/Dev 상태 |
| GET | `/api/events/stream` | 병합 SSE | `EventRelay` |

### 7.4 연동 계약 상세와 예시 JSON

아래 예시는 데모 사이클 #1을 mock 모드로 실행했을 때 코드가 만드는 본문입니다(키 순서는 코드의 dict 정의 순서).

**① Dev → QA `POST /api/webhooks/release-published`** (`dev_server/service.py` `_notify_qa()`, 수신 스키마 `qa_server/app.py` `ReleasePublished`)

요청 헤더: `X-Caller: dev-server`, `X-Cycle-Id: 1`, `Content-Type: application/json`

v1.0.0 (최초 발행):

```json
{
  "cycle_id": 1,
  "version": "1.0.0",
  "fixed_issue_keys": [],
  "build_url": "http://127.0.0.1:8201/builds/1/1.0.0",
  "mode": "demo"
}
```

v1.1.0 (수정 반영 발행):

```json
{
  "cycle_id": 1,
  "version": "1.1.0",
  "fixed_issue_keys": ["ISSUE-1", "ISSUE-2", "ISSUE-3"],
  "build_url": "http://127.0.0.1:8201/builds/1/1.1.0",
  "mode": "demo"
}
```

응답: `202 Accepted`

```json
{"accepted": true}
```

수신 스키마에서 `fixed_issue_keys`는 기본값 `[]`, `mode`는 기본값 `"demo"`입니다. `mode`가 `"manual"`이면 QA는 자동 검증 대신 판정 대기 항목을 만듭니다. 이 한 필드로 데모·실습이 시스템 경계를 넘어 전달됩니다.

**② QA → Dev `GET /builds/{cycle_id}/{version}`** (`dev_server/app.py` `build()`)

`GET http://127.0.0.1:8201/builds/1/1.1.0` 응답 `200` (D-103을 고치면서 D-201이 생긴 빌드):

```json
{
  "product": "Commerce Web",
  "version": "1.1.0",
  "features": ["login", "search", "payment", "report"],
  "behaviors": [
    {
      "id": "D-201",
      "breaks": "TC-11",
      "log": "AssertionError: report total 1,250,000 != payment ledger 1,249,990\n  at report/summary.py:73 refund rounding mismatch"
    }
  ]
}
```

v1.2.0의 매니페스트는 `"behaviors": []`이고, 그래서 12건 모두 통과합니다. 없는 빌드는 `404 {"detail":"build not found"}`입니다.

**③ QA → Dev `POST /api/tickets`** (`qa_server/service.py` `_transfer()`, 수신 스키마 `dev_server/app.py` `TicketsPayload`/`IssuePayload`)

v1.0.0 검증 후 이관 (mock 분석 결과):

```json
{
  "cycle_id": 1,
  "version": "1.0.0",
  "issues": [
    {
      "key": "ISSUE-1",
      "test_case_id": "TC-02",
      "title": "[login] 세션 유지 실패",
      "description": "TC-02 실행 시 실패. AssertionError: session expired after 5m (expected >= 30m)",
      "severity": "Major",
      "version_found": "1.0.0"
    },
    {
      "key": "ISSUE-2",
      "test_case_id": "TC-05",
      "title": "[search] 페이지네이션 실패",
      "description": "TC-05 실행 시 실패. AssertionError: page 2 returned 0 items (expected 20)",
      "severity": "Minor",
      "version_found": "1.0.0"
    },
    {
      "key": "ISSUE-3",
      "test_case_id": "TC-08",
      "title": "[payment] 환불 금액 계산 실패",
      "description": "TC-08 실행 시 실패. AssertionError: refund amount 9999 != 10000",
      "severity": "Critical",
      "version_found": "1.0.0"
    }
  ]
}
```

응답: `202 Accepted` `{"accepted": 3}`. 사이클이 `VERIFYING`이 아니면 `409 {"error": "검증 단계의 사이클이 아닙니다."}`이며, 4xx이므로 QA의 `IntegrationClient`는 재시도하지 않고 바로 실패 처리합니다.

**④ QA → Dev `POST /api/webhooks/verification-completed`** (`qa_server/service.py` `_notify_approved()`, 수신 스키마 `VerificationPayload`)

```json
{"cycle_id": 1, "version": "1.2.0", "run_id": 3, "passed": 12, "total": 12}
```

응답: `200` `{"ok": true}`. 사이클이 `VERIFYING`이 아니거나 통지 버전이 현재 버전과 다르면 `409 {"error": "검증 중인 사이클·버전이 아닙니다."}`이고, 사이클 상태는 바뀌지 않습니다.

**⑤ Dev → QA `POST /api/webhooks/cycle-cancelled`** (`dev_server/service.py` `abort()`, 수신 스키마 `qa_server/app.py` `CycleCancelled`)

```json
{"cycle_id": 2, "reason": "사이클 중단"}
```

응답: `200` `{"cancelled_runs": 1}` (닫은 `RUNNING` 실행 수). QA는 해당 사이클의 자동 검증 작업도 취소합니다. Dev는 이 통지가 실패해도 중단을 되돌리지 않고 `integration.error` 이벤트만 남깁니다.

**실습 API 오류 응답 형식** (`WorkError` → `exception_handler`)

```json
{"error": "상단에서 검증부서 작업자를 선택하세요. (이 시스템의 부서원만 작업할 수 있습니다)"}
```

`HTTPException(404)`은 FastAPI 기본 형식 `{"detail": "ticket not found"}`이고, `app.js`의 `post()`는 `error`와 `detail`을 모두 읽어 토스트로 보여 줍니다.

**integration_logs 행 예시** (Hub `/integration`에서 펼친 모습과 같은 데이터)

QA 쪽 `out`:

```json
{
  "id": 3, "ts": "2026-10-15T15:10:06.454", "cycle_id": 3, "direction": "out", "method": "POST",
  "url": "http://127.0.0.1:8201/api/tickets", "status": 202, "duration_ms": 10, "attempt": 1,
  "request_body": "{\"cycle_id\": 3, \"version\": \"1.0.0\", \"issues\": [ ... ]}",
  "response_body": "{\"accepted\":3}", "error": null
}
```

Dev 쪽 `in`:

```json
{
  "id": 4, "ts": "2026-10-15T15:10:06.450", "cycle_id": 3, "direction": "in", "method": "POST",
  "url": "qa-server -> /api/tickets", "status": 202, "duration_ms": 8, "attempt": 1,
  "request_body": "{\"cycle_id\": 3, \"version\": \"1.0.0\", \"issues\": [ ... ]}",
  "response_body": null, "error": null
}
```

QA 서버가 꺼진 상태에서 Dev가 발행 통지를 보내면 `out` 행이 attempt 1, 2, 3으로 세 줄 남고, 각각 `status: null`, `error: "ConnectError: ..."`가 기록됩니다.

### 7.5 SSE 이벤트

Dev와 QA는 각자 `/api/events/stream`을, Hub는 병합 스트림 `/api/events/stream`을 제공합니다. 모든 화면의 `<body data-stream="/api/events/stream">`을 `app.js`가 구독합니다.

```text
retry: 3000

data: {"ts": "2026-10-15T15:09:37", "cycle_id": 3, "actor": "QA", "type": "webhook.received", "message": "v1.0.0 발행 통지 수신", "payload": {"version": "1.0.0", "build_url": "http://127.0.0.1:8201/builds/3/1.0.0"}, "id": 12, "source": "QA"}

data: {"type": "test.progress", "actor": "QA", "cycle_id": 1, "payload": {"run_id": 1, "done": 5, "total": 12, "test_case_id": "TC-05", "passed": false}, "source": "QA"}

: keep-alive
```

`source`는 Hub가 중계할 때만 붙습니다. 주요 이벤트 타입은 다음과 같습니다.

| 발생 시스템 | 저장 이벤트(`emit`) | 일시 이벤트(`publish`) |
| --- | --- | --- |
| Dev | `cycle.started`, `release.published`, `webhook.sent`, `tickets.received`, `ticket.started`, `ticket.fixed`, `regression.introduced`(actor `Simulator`), `cycle.completed`, `cycle.failed`, `cycle.cancelled`, `integration.error`(중단 통지 실패) | `cycle.status`, `system.reset` |
| QA | `webhook.received`, `test_run.started`, `test_run.opened`, `test_run.finished`, `test.judged`, `issue.created`, `issue.verified`, `issue.reopened`, `issues.transferred`, `release.approved`, `integration.error`, `verification.error` | `test.progress`, `system.reset` |
| Hub | (없음) | `service.up`, `service.down`, `system.reset` |

브라우저 처리 (`common/web/static/js/app.js`): `test.progress`는 상단 진행 막대, `system.reset`은 페이지 다시 열기, `service.*`는 무시, `id`가 있는 이벤트는 타임라인 맨 위에 추가, 지정된 타입은 토스트, 그리고 0.35초 뒤 부분 갱신을 예약합니다.

---

## 8. 동작 흐름

### 8.1 데모 — 세 서비스 사이의 호출 순서

`STEP_DELAY=0.4`, `TEST_DELAY=0.1`이 기본이며, 측정 시에는 둘 다 0으로 두었습니다. `[out n]`은 서비스 간 호출의 누적 번호입니다.

**사이클 시작**

```text
브라우저      Hub(8200)                    Dev(8201)                                 QA(8202)
   │ POST /api/cycles ─▶│
   │                     │ POST /api/cycles?mode=demo ─▶│ cycles INSERT (RELEASING, iteration=1, mode=demo)
   │                     │◀──────── 201 {"cycle_id":1} ─│ cycle.started 이벤트, _initial_release 작업 시작
   │◀── 201 ─────────────│   (Hub 호출에는 X-Caller가 없어 integration_logs에 남지 않음)
```

**v1.0.0 — 9 PASS / 3 FAIL → 이관**

```text
Dev(8201)                                                         QA(8202)
 │ releases INSERT v1.0.0 (behaviors: D-101, D-102, D-103, by Demo)
 │ cycles → VERIFYING, current_version=1.0.0, release.published
 │ [out 1] POST /api/webhooks/release-published {fixed_issue_keys: [], mode: demo} ─▶│
 │                                                                   │ webhook.received
 │                                                                   │ _verify 작업 시작
 │◀──────────────────────────────────────────── 202 {"accepted":true}│
 │ webhook.sent                                                      │
 │◀──────────────────────────────────── [out 2] GET /builds/1/1.0.0 ─│
 │ 200 매니페스트 ──────────────────────────────────────────────────▶│
 │                                                                   │ test_runs INSERT (RUNNING, demo)
 │                                                                   │ TC-01..12 판정 (test.progress ×12)
 │                                                                   │ TC-02, TC-05, TC-08 FAIL → DONE 9/3
 │                                                                   │ analyze ×3 → ISSUE-1, 2, 3 (OPEN)
 │◀────────────────────────────── [out 3] POST /api/tickets {issues: 3건} ─│
 │ tickets INSERT ×3 (RECEIVED), cycles → TRANSFERRING                │
 │ tickets.received, _fix_and_release 작업 시작                        │
 │ 202 {"accepted":3} ──────────────────────────────────────────────▶│
 │                                                                   │ ISSUE-1..3 → TRANSFERRED
 │                                                                   │ issues.transferred
```

**v1.1.0 — 11 PASS / 1 FAIL (회귀) → 이관**

```text
Dev(8201)                                                         QA(8202)
 │ cycles → FIXING
 │ ISSUE-1..3: RECEIVED → IN_PROGRESS → FIXED (LLM 수정 내역, fixed_in 1.1.0), ticket.fixed ×3
 │ apply_fixes: D-101, D-102, D-103 제거, D-201 추가 → regression.introduced (Simulator)
 │ LLM 릴리즈 노트 → releases INSERT v1.1.0, iteration=2
 │ cycles → VERIFYING
 │ [out 4] POST release-published {fixed_issue_keys: [ISSUE-1..3]} ─▶│
 │                                                                   │ ISSUE-1..3 → FIXED (fixed_in 1.1.0)
 │◀──────────────────────────────────────────── 202 ─────────────────│ _verify 작업 시작
 │◀──────────────────────────────────── [out 5] GET /builds/1/1.1.0 ─│
 │ 200 (behaviors: D-201) ──────────────────────────────────────────▶│
 │                                                                   │ 11/1 (TC-11 FAIL)
 │                                                                   │ _verify_fixed: ISSUE-1..3 → VERIFIED
 │                                                                   │ analyze → ISSUE-4 (TC-11, report, Major)
 │◀────────────────────────────── [out 6] POST /api/tickets {ISSUE-4} ─│
 │ ISSUE-4 RECEIVED, TRANSFERRING, 202 ─────────────────────────────▶│ ISSUE-4 → TRANSFERRED
```

**v1.2.0 — 12 PASS / 0 FAIL → 확정**

```text
Dev(8201)                                                         QA(8202)
 │ FIXING, ISSUE-4 수정 → D-201 제거, behaviors 없음
 │ releases INSERT v1.2.0, iteration=3, VERIFYING
 │ [out 7] POST release-published {fixed_issue_keys: [ISSUE-4]} ─────▶│ ISSUE-4 → FIXED
 │◀──────────────────────────────────── [out 8] GET /builds/1/1.2.0 ─│
 │ 200 (behaviors: []) ─────────────────────────────────────────────▶│ 12/0, ISSUE-4 → VERIFIED
 │                                                                   │ 실패 없음 → 확정 통지
 │◀────────────── [out 9] POST /api/webhooks/verification-completed ─│ {passed:12, total:12}
 │ releases v1.2.0 approved_at, cycles → APPROVED                    │
 │ cycle.completed ("검증 3회")                                       │
 │ 200 {"ok":true} ─────────────────────────────────────────────────▶│ release.approved
```

| 버전 | 결과 | 새 이슈 | 수정 확인 | QA 결정 | 서비스 간 호출 |
| --- | --- | --- | --- | --- | --- |
| v1.0.0 | 9/3 | ISSUE-1(TC-02), ISSUE-2(TC-05), ISSUE-3(TC-08) | - | 이관 | release-published, GET build, tickets |
| v1.1.0 | 11/1 | ISSUE-4(TC-11, 회귀) | ISSUE-1~3 VERIFIED | 이관 | release-published, GET build, tickets |
| v1.2.0 | 12/0 | - | ISSUE-4 VERIFIED | 확정 | release-published, GET build, verification-completed |

호출은 모두 9회(outbound 기준)이고, 받는 쪽 `in` 기록도 9행 생깁니다. Hub Integration 화면의 "서비스 간 호출" 수는 `out` 행만 셉니다(`hub/app.py` `integration()`). 데모 한 사이클의 LLM 호출은 이슈 분석 4회(Dev가 아니라 QA), 수정 내역 4회, 릴리즈 노트 2회로 총 10회이며, Dev와 QA가 각자 자기 `LLMClient`로 호출합니다.

**참고: 동기 호출 속의 비동기 처리.** Webhook을 받는 쪽은 실제 작업을 `asyncio.create_task`로 넘기고 바로 202를 돌려줍니다. 그래서 Dev의 `release-published` 호출은 QA의 12건 검증이 끝날 때까지 기다리지 않습니다. 반대로 QA의 `POST /api/tickets`도 Dev가 수정을 끝낼 때까지 기다리지 않습니다. 결과적으로 "호출 → 202 → 상대가 나중에 다시 호출"하는 핑퐁 구조가 됩니다.

### 8.2 실습 — 사용자 관점과 시스템 관점

#### 8.2.1 사용자 관점 (역할별 화면 이동)

| 단계 | 사람 / 웹 | 행동 | 화면에 보이는 결과 |
| --- | --- | --- | --- |
| 1 | 운영자 / Hub | **실습 시작** (확인 대화상자 없이 바로 시작) | 상단 "사이클 #n 실습 발행 중", "개발부서 차례" 배너, 사이드바 Dev 웹에 "차례" |
| 2 | 최개발 / Dev | 작업자 선택(또는 `?as=최개발`) → **v1.0.0 발행** → 확인 | Dev "검증부서 차례" 배너, QA 웹에 검증 항목 12건 '대기' |
| 3 | 김검증 / QA | **내 항목** 탭 → TC-01 **실행** → **PASS** | 실행 결과 PASS, 판정 PASS(김검증), Hub 배너 "판정 1/12" |
| 4 | 김검증 / QA | TC-02 **실행**(FAIL 로그) → **FAIL** → 이슈 대화상자 **AI 초안** → **등록 후 FAIL 처리** | ISSUE-1 등록(등록자 김검증), 항목 FAIL |
| 5 | 이검증·박검증 / QA | 각자 담당 항목 판정, TC-08 FAIL 이슈 등록 | 하단 "검증 완료" 바의 판정 대기 수 감소 |
| 6 | 아무 검증부서원 / QA | **Dev 이관** → 확인 | QA "개발부서 차례" 배너, Dev 웹에 티켓 3건 접수 |
| 7 | 최개발·정개발 / Dev | 티켓 **착수** → **수정 완료**(AI 초안 또는 직접 입력) | 티켓 FIXED, 발행 카드에 "수정 완료된 티켓 n건이 반영됩니다" |
| 8 | 정개발 / Dev | 릴리즈 노트 **AI 초안** → **v1.1.0 발행** | QA '수정 확인' 카드에 ISSUE-1~3 |
| 9 | 검증부서원 / QA | 재검증, ISSUE-1~3 **수정 확인**, 회귀 TC-11 FAIL 이슈 등록 → **Dev 이관** | ISSUE-4가 Dev 티켓으로 |
| 10 | 개발부서원 / Dev | ISSUE-4 수정 → **v1.2.0 발행** | QA 차례 |
| 11 | 검증부서원 / QA | 전 항목 PASS, ISSUE-4 수정 확인 → **버전 확정** | Dev 사이클 "승인", QA "버전 확정" 배너 |
| 12 | 운영자 / Hub | Overview, Issues, Integration 확인 | 버전별 결과·QA 결정, 이슈+티켓 병합 이력, 호출 기록 |

#### 8.2.2 시스템 관점 (버튼마다 일어나는 REST 호출)

| 화면 / 버튼 | 브라우저 → 자기 서비스 | 서비스 간 호출 | 서버 처리와 검사 |
| --- | --- | --- | --- |
| Hub **실습 시작** | `POST hub/api/work/start` | Hub → Dev `POST /api/cycles?mode=manual` (대리, 로그 없음) | 진행 중 사이클 있으면 409 |
| Dev **실습 시작** | `POST dev/api/work/start` | 없음 | 위와 동일 |
| Hub / Dev **데모 실행** | `POST hub/api/cycles` 또는 `POST dev/api/cycles` | Hub 경유 시 Dev 대리 호출 | `mode=demo` |
| Dev **vX 발행** | `POST dev/api/work/release {actor, release_note}` | Dev → QA `POST /api/webhooks/release-published` (`mode: manual`) | 403(명부), 409(단계), 400(반영 티켓·노트 없음), 502(통지 실패 → 발행 삭제) |
| QA (발행 통지 수신) | - | - | 이전 `RUNNING` 실행 `CANCELLED`, 새 실행 + 12건 `PENDING`(담당자 복사), `fixed_issue_keys` → `FIXED` |
| QA **실행** | `POST qa/api/work/results/{id}/execute {actor}` | 그 실행에서 처음 누를 때만 QA → Dev `GET /builds/{c}/{v}` | 빌드를 `test_runs.build`에 보관, 이후 다시 내려받지 않음. 502(다운로드 실패) |
| QA **PASS** | `POST qa/api/work/results/{id}/judge {actor, result:"PASS"}` | 없음 | 403, 409(검증 중 버전 없음), 404(다른 실행의 항목) |
| QA **FAIL** | (대화상자만 열림) | - | 제목 기본값 `[feature] name 실패`, 설명에 실행 로그 |
| QA 대화상자 **AI 초안** | `POST qa/api/work/draft/issue/{id}` | 없음 (LLM은 QA가 직접 호출) | 실행 로그가 없으면 "(실행 결과 없음...)"을 근거로 작성 |
| QA **등록 후 FAIL 처리** | `POST qa/api/work/issues {actor, result_id, title, severity, description}` | 없음 | 400(제목·심각도), 같은 TC에 활성 이슈가 있으면 연결만 |
| QA **수정 확인 / 재현** | `POST qa/api/work/issues/{key}/verify {actor, ok}` | 없음 | 이번 버전에 수정 통지된 `FIXED` 이슈만 |
| QA **Dev 이관** | `POST qa/api/work/transfer {actor}` | QA → Dev `POST /api/tickets` | 400(판정 대기, 수정 확인 대기, 이관 대상 없음), 502(Dev 실패, 아무것도 바뀌지 않음). 이월 이슈도 함께 보냄 |
| Dev (이관 수신) | - | - | 티켓 생성/재접수(수정 중 티켓은 상태·담당자 유지), `TRANSFERRING` → 즉시 `FIXING` |
| Dev **착수 / 내가 맡기** | `POST dev/api/work/issues/{key}/start {actor}` | 없음 | `FIXING`에서만, 담당자를 작업자로 |
| Dev **수정 완료** 대화상자 **AI 초안** | `POST dev/api/work/draft/fix/{key}` | 없음 | |
| Dev **수정 완료** | `POST dev/api/work/issues/{key}/fix {actor, fix_note}` | 없음 | 400(수정 내역 없음) |
| Dev 릴리즈 노트 **AI 초안** | `POST dev/api/work/draft/release` | 없음 | 반영할 티켓이 없으면 빈 문자열 |
| QA **버전 확정** | `POST qa/api/work/approve {actor}` | QA → Dev `POST /api/webhooks/verification-completed` | 400(미해결 이슈 있음 등), 502 |
| Hub / Dev **사이클 중단** | `POST hub/api/work/abort` 또는 `POST dev/api/work/abort` | Hub 경유 시 Dev `/api/state` 조회 + `POST /api/cycles/{id}/abort`. 이어서 Dev → QA `POST /api/webhooks/cycle-cancelled` | Dev 백그라운드 작업 취소, `CANCELLED`. QA는 진행 중 실행 `CANCELLED`, 작업대에 "중단" 배너 |
| Hub **초기화** | `POST hub/api/reset` | Hub → Dev, QA `POST /api/admin/reset?dry_run=true` 후 실제 초기화 | Dev는 데모 진행 중 409, QA는 자동 검증 작업 중 409. 한쪽이라도 거절하면 양쪽 모두 그대로 |

실습 사이클 하나를 끝까지 진행해도 서비스 간 호출은 데모와 같은 9회입니다(발행 통지 3, 빌드 다운로드 3, 이관 2, 확정 1). 2026-10-16 브라우저 점검에서도 out 9행, in 9행이 기록되었습니다. 단, 빌드 다운로드는 각 실행에서 누군가 처음 '실행'을 누를 때 일어나므로, '실행'을 누르지 않고 판정만 하면 다운로드가 생기지 않습니다.

#### 8.2.3 실습의 "차례"는 누가 판단하는가

```text
 Dev cycles.status         누구 차례         Dev 작업대 버튼              QA 작업대 버튼
 ─────────────────         ───────────       ─────────────────────        ──────────────────────
 RELEASING                 Dev               발행 (최초)                   (검증 항목 없음)
 VERIFYING                 QA                없음 ("검증부서 차례" 배너)     실행·판정·이슈·수정확인·이관/확정
 TRANSFERRING → FIXING     Dev               착수·수정 완료·발행            없음 (run DONE, "개발부서 차례")
 APPROVED / CANCELLED      -                 실습 시작 / 데모 실행           없음
```

Dev는 자기 사이클 상태로, QA는 "실습 모드이면서 `RUNNING`인 실행이 있는가"로 각자 차례를 판단합니다. 둘이 같은 차례를 가리키는 것은 Webhook과 이관 호출이 상태를 함께 넘기기 때문이며, 이를 보장하는 공용 저장소는 없습니다. Hub의 차례 표시는 Dev 사이클 상태만 보고 정합니다(`hub/app.py` `base_ctx()`).

### 8.3 실패와 롤백 흐름

#### 8.3.1 데모 중 QA 서버 장애 (발행 통지 실패)

```text
Dev                                                               QA (중지)
 │ releases INSERT v1.0.0, VERIFYING
 │ [attempt 1] POST release-published ──X ConnectError   (out 로그, error 기록)
 │ 0.5초 대기
 │ [attempt 2] POST release-published ──X ConnectError
 │ 1.0초 대기
 │ [attempt 3] POST release-published ──X ConnectError
 │ IntegrationError → _fail(): cycles → FAILED, error="QA 서버 연동 실패 - POST ... 호출 실패 (3회 시도): ConnectError: ..."
 │ cycle.failed 이벤트 → 모든 화면에 "사이클 #n 오류" 배너, "사이클 실패" 토스트
```

데모에서는 이미 저장한 릴리즈를 지우지 않습니다. 사이클 자체를 실패로 끝내기 때문입니다.

#### 8.3.2 실습 중 QA 서버 장애 (발행 롤백)

```text
최개발 ── v1.1.0 발행 ──▶ Dev.release()
                           ├ releases INSERT v1.1.0  (아직 티켓에 fixed_in_version 기록 안 함, 회귀 이벤트 안 냄)
                           ├ _notify_qa() 3회 실패 → IntegrationError
                           ├ DELETE FROM releases WHERE cycle_id=? AND version='1.1.0'   ← 롤백
                           └ WorkError(502): "QA 서버에 발행을 통지하지 못해 발행을 취소했습니다. 잠시 후 다시 시도하세요. (...)"
브라우저 ◀── 502 {"error": "..."} → 토스트 "처리할 수 없음", 버튼 다시 활성화
결과: 사이클은 FIXING 그대로, 티켓은 FIXED(반영 대기) 그대로 → QA 재기동 후 같은 버튼으로 다시 발행 가능
```

코드는 "되돌리기 쉬운 순서"로 짜여 있습니다. 통지가 성공하기 전에는 릴리즈 행 하나만 만들고, 티켓의 `fixed_in_version`, 회귀 이벤트, 사이클 상태 변경, `iteration` 증가는 모두 통지 성공 뒤에 합니다.

#### 8.3.3 실습 중 Dev 서버 장애 (이관·확정·빌드 다운로드)

| 작업 | 처리 순서 | 실패 시 결과 |
| --- | --- | --- |
| Dev 이관 | 준비 조건 확인 → `POST /api/tickets` → 성공 시 이슈 `TRANSFERRED`, 실행 `DONE`/`TRANSFERRED` | 502 "Dev 시스템에 이관하지 못했습니다...". 호출 전에 아무것도 바꾸지 않았으므로 되돌릴 것이 없음 |
| 버전 확정 | 준비 조건 확인 → `verification-completed` → 성공 시 실행 `DONE`/`APPROVED` | 502 "Dev 시스템에 확정을 통지하지 못했습니다..." |
| 실행 | 빌드가 없으면 `GET build_url` → 성공 시 보관 | 502 "Dev 시스템에서 빌드를 내려받지 못했습니다..." (판정은 계속 가능) |

#### 8.3.4 상대가 거절한 경우 (4xx)

QA가 이관·확정을 통지하는 순간 Dev 사이클이 `VERIFYING`이 아니면(예: 중단 통지가 실패해 QA 실행이 남아 있는 경우), Dev는 409를 돌려줍니다. `IntegrationClient`는 4xx를 재시도하지 않고 즉시 `IntegrationError("POST ... -> HTTP 409: ...")`를 내고, QA는 이를 502로 작업자에게 알리며 자기 상태를 바꾸지 않습니다. 데모라면 QA가 `integration.error` 이벤트를 남깁니다.

#### 8.3.5 데모 중 Dev 서버 장애

QA가 빌드 다운로드나 이관에 실패하면 `integration.error` 이벤트만 남기고 검증 작업을 끝냅니다. Dev가 꺼져 있으므로 Dev 사이클 상태는 바뀌지 않고, Dev가 다시 켜지면 `DevService.__init__`이 진행 중이던 데모 사이클을 `CANCELLED`("서버 재시작으로 중단")로 정리합니다.

#### 8.3.6 사이클 중단

```text
운영자 ── 사이클 중단 ──▶ Hub /api/work/abort ──▶ Dev /api/cycles/{id}/abort
                                                  ├ Dev 백그라운드 작업 취소, cycles → CANCELLED, cycle.cancelled
                                                  └ POST QA /api/webhooks/cycle-cancelled {cycle_id, reason}
                                                       ├ QA: verify-{cycle} 작업 취소 (데모)
                                                       └ QA: 그 사이클의 RUNNING 실행 → CANCELLED, 작업대 "중단" 배너
```

2026-10-16 이전에는 중단이 Dev 안에서만 처리되어, 데모에서는 QA 검증이 끝까지 돌다 이관 단계에서 409로 끝나고 실습에서는 QA 실행이 `RUNNING`으로 남았습니다. 지금은 Dev가 QA에 중단을 통지합니다. QA가 꺼져 있어 통지가 3회 모두 실패해도 Dev의 중단은 유지되고, QA에 남은 실행은 Dev가 이후 이관·확정 통지를 409로 거절하므로 흐름에 영향을 주지 않습니다(데모 실행은 QA 재기동 시 정리).

#### 8.3.7 Hub에서 본 장애

```text
QA 중지 → Hub 화면 요청 → Upstream.get(QA, ...) ConnectError
        → errors = ["QA Server 응답 없음 (ConnectError)"] → 모든 화면 상단 "연동 장애" 배너
        → Integration 화면: QA Server DOWN, Dev Server UP (지연 ms)
        → EventRelay: service.down 발행, 2초마다 재연결 시도 → 재기동되면 service.up
```

---

## 9. 화면 구성

모든 화면은 공통 `base.html`을 씁니다. 왼쪽 사이드바(메뉴, 다른 웹으로 가는 링크, 시스템·LLM·실시간 연결 상태, 테마 전환), 상단 바(사이클 번호·모드·상태 배지, 테스트 진행 막대, 작업자 선택, 화면별 동작 버튼), 본문으로 구성됩니다. 작업자 선택은 그 시스템의 `members`가 있을 때만 보이므로 Hub에는 없습니다.

### 9.1 Hub 웹 (8200) — 9개 화면

![Hub overview](../02_api/docs/images/hub_overview.png)

| 화면 | 경로 | 내용 | 데이터 출처 |
| --- | --- | --- | --- |
| Overview | `/` | 실습 차례 배너(판정 n/12, 해당 웹 열기 버튼), 사이클 상태·현재 버전·최근 통과율·미해결 이슈 카드, 5단계 파이프라인(발행 Dev → 검증 QA → 이관 QA→Dev → 수정 Dev → 승인 QA), 버전별 결과 막대, 이슈 현황(QA 상태 + Dev 티켓 상태), 최근 이벤트 12건, 사이클 이력 8건 | Dev state·cycles·releases·tickets·events, QA state·test-runs·issues·events |
| Releases | `/releases` | 버전별 반영 이슈, 검증 결과, QA 결정 | Dev releases + QA test-runs |
| Release 상세 | `/releases/{v}` | 릴리즈 노트, 검증 실행, 반영된 이슈, 이 버전에서 발견된 이슈 | Dev release + QA runs/issues |
| Test Runs | `/test-runs` | 검증 실행 목록 | QA |
| Test Run 상세 | `/test-runs/{id}` | 12건 결과, 판정자, 로그 | QA |
| Issues | `/issues` | QA 이슈 + Dev 티켓 상태·담당자·수정 내역 | QA issues + Dev tickets |
| Issue 상세 | `/issues/{key}` | 설명(QA), 수정 내역(Dev), **두 시스템 이력을 시간순 병합**한 표 | QA issue history + Dev ticket history |
| Timeline | `/timeline` | 두 시스템 이벤트 병합 (최대 500) | Dev/QA events |
| Integration | `/integration` | 서비스 상태(UP/DOWN, 지연), 호출 수·평균 시간·실패 수, 연동 계약 표, 호출 로그(펼치면 요청/응답/오류 본문, 재시도 배지) | Dev/QA health·integration-logs |

![Hub integration](../02_api/docs/images/hub_integration.png)

상단 버튼: 진행 중이면 `사이클 중단`(초기화 비활성), 아니면 `초기화`, `실습 시작`, `데모 실행`. `?cycle=N`을 붙이면 과거 사이클 기준으로 볼 수 있고, Overview의 사이클 이력 행을 누르면 이동합니다.

이 장의 화면 캡처 4장은 2026-10-16 브라우저 점검 중에 다시 찍었습니다(Overview·Integration은 실습 사이클 #1 확정 직후, 연동 계약 표에 `cycle-cancelled` 포함).

### 9.2 Dev 웹 (8201) — 6개 화면

![Dev web](../02_api/docs/images/dev_web.png)

| 화면 | 경로 | 내용 |
| --- | --- | --- |
| 개발 작업대 | `/` | 버전 발행 카드(다음 버전, 반영될 티켓·이월될 티켓 안내, 릴리즈 노트 입력란 `data-preserve`, AI 초안, 발행 버튼), 접수된 티켓 표(착수/내가 맡기, 수정 완료), 이번 사이클 발행 이력(빌드 `build.json` 링크, 발행자, 확정 여부) |
| 릴리즈 | `/releases` | 전체 사이클 릴리즈 100건 (모드 포함) |
| 티켓 | `/tickets` | 현재 사이클 티켓 |
| 티켓 상세 | `/tickets/{key}` | 티켓 정보와 상태 이력 |
| 타임라인 | `/timeline` | Dev가 기록한 이벤트만 (공통 템플릿) |
| 연동 로그 | `/integration` | Dev의 out/in 기록만 (공통 템플릿) |

데모 사이클일 때는 "데모 사이클 — 관찰용" 배너가 나오고 버튼이 숨겨집니다. 실습에서 `VERIFYING`이면 "검증부서 차례" 배너가 나옵니다. 사이드바의 개발 작업대 옆에는 실습 중 Dev 차례(`RELEASING`/`FIXING`)일 때 "차례" 배지가 붙습니다.

### 9.3 QA 웹 (8202) — 7개 화면

![QA web](../02_api/docs/images/qa_web.png)

| 화면 | 경로 | 내용 |
| --- | --- | --- |
| 검증 작업대 | `/` | 검증 대상·판정 진행·PASS/FAIL(내 담당 n건)·수정 확인 대기 카드, 검증 항목 표(내 항목/전체 탭, 담당자 행 강조, 실행 결과, 판정·판정자, "실행 결과와 다름" 표시, 실행/PASS/FAIL 버튼), 수정 확인 카드(재현/수정 확인), 이 버전에서 등록한 이슈, 하단 "검증 완료" 바(대기 수 요약, Dev 이관/버전 확정 버튼과 비활성 사유) |
| 검증 이력 | `/runs` | 실행 100건 |
| 검증 상세 | `/runs/{id}` | 항목별 결과 |
| 이슈 | `/issues` | 현재 사이클 이슈 |
| 이슈 상세 | `/issues/{key}` | 이슈와 상태 이력 |
| 타임라인 | `/timeline` | QA 이벤트만 |
| 연동 로그 | `/integration` | QA의 out/in 기록만 |

QA 웹 상단에는 사이클 시작 버튼이 없습니다. QA는 사이클을 소유하지 않고 Dev의 발행 통지를 기다리기 때문입니다. 실행이 끝나면 작업대 배너가 결정에 따라 "개발부서 차례", "버전 확정", "중단"(사이클 중단 또는 새 발행으로 취소됨)으로 바뀝니다.

### 9.4 공통 프런트엔드 동작

| 기능 | 구현 | 설명 |
| --- | --- | --- |
| 부분 갱신 | `app.js` `refresh()` | 같은 URL을 다시 받아 `data-live` 영역(`#cycle-status`, `#actions`, `#page`)만 교체 |
| 입력 보호 | `isEditing()` | 대화상자가 열려 있거나 `#page` 안 입력 칸에 포커스가 있으면 1.5초 뒤로 미룸 |
| 값 보존 | `data-preserve` | 교체 전 값(릴리즈 노트 등)을 저장했다가 다시 채움 |
| 작업자 | `workbench.js` | `?as=이름` → localStorage 저장 → 모든 `/api/work/*` 요청 본문에 `actor` 포함 |
| 부서 불일치 안내 | `applyView()` | 작업대의 `data-desk`(Dev/QA)와 작업자 부서가 다르면 경고 배너 (최종 차단은 서버 403) |
| 확인 대화상자 | `confirmBox()` | 발행·이관·확정 전 확인 |
| 실습 시작/중단 | `workbench.js` `work-start`, `work-abort` | 현재 서비스의 `/api/work/start`, `/api/work/abort` 호출 (Hub도 같은 경로를 대리 호출로 제공) |
| 상태 배지 | `ui.py` `TONES`, `STATUS_LABELS` | 모노톤 배지와 한글 상태명(발행 중, 검증 중, 이관 중, 수정 중, 승인, 실패, 중단) |

---

## 10. LLM 활용

### 10.1 호출 방식 (`02_api/src/common/llm.py`)

```python
response = await self._client.beta.messages.create(
    model=self.model,                       # CLAUDE_MODEL, 기본 claude-opus-5-5
    max_tokens=8000,
    system=system,
    messages=[{"role": "user", "content": prompt}],
    output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
)
```

- **구조화 출력**: JSON Schema를 지정해 항상 정해진 키의 JSON을 받습니다.
- **mock 대체**: `mode != "llm"`이면 호출하지 않고 `fallback()`을 돌려줍니다. LLM 모드라도 API 오류, `stop_reason != "end_turn"`, 텍스트 블록 없음, JSON 파싱 오류가 나면 실패 횟수를 세고 경고 로그를 남긴 뒤 mock 결과로 대신합니다. 반환값에 생성 주체(`"llm"`/`"mock"`)가 함께 옵니다.
- **통계**: `calls`, `failures`, `last_error`를 Dev `/api/state`의 `llm`으로 노출합니다(Dev 쪽 통계만).
- **모드 결정**: `LLM_MODE=auto`(기본)는 `ANTHROPIC_API_KEY`가 있으면 llm, 없으면 mock입니다. `run.py --mock`은 `LLM_MODE=mock`을 넘깁니다.

### 10.2 LLM이 쓰는 곳

| 시스템 | 용도 | 스키마 | 프롬프트 근거 | mock 결과 |
| --- | --- | --- | --- | --- |
| QA | 결함 리포트 (`analyze`) | `title`(40자 이내), `description`(재현 조건·기대·실제 2~3문장), `severity`(enum) | 제품·버전·기능·테스트케이스·실패 로그, 심각도 기준 | `[feature] name 실패`, `TC 실행 시 실패. <로그 첫 줄>`, 기능별 심각도 |
| Dev | 수정 내역 (`write_fix_note`) | `fix_note`(1~2문장) | 이슈 키·제목·설명 | `<제목> 원인 코드 수정 및 단위 테스트 추가` |
| Dev | 릴리즈 노트 (`write_release_note`) | `release_note`(목록, 5줄 이내) | 버전, 반영 티켓(키·심각도·제목·수정 내역) | `Commerce Web <ver> 유지보수 릴리즈` + `- <키> <제목> 수정` 목록 |

시스템 프롬프트는 Dev "커머스 웹 서비스 개발팀의 시니어 개발자", QA "검증팀의 QA 엔지니어 … 결함 리포트를 작성"입니다.

### 10.3 판정과 문장 생성의 분리

| 결정 | 누가 하는가 |
| --- | --- |
| 어떤 테스트가 실패하는가 | `qa_server/catalog.py` `run_test()` — 빌드의 `behaviors[].breaks`로 결정 |
| 수정하면 어떤 결함이 사라지고 무엇이 생기는가 | `dev_server/catalog.py` `apply_fixes()` |
| 이관할지 확정할지 | 데모: 실패 수(코드) / 실습: 사람 |
| 이슈 제목·설명·심각도, 수정 내역, 릴리즈 노트 | LLM 또는 mock |

LLM은 흐름의 분기에 관여하지 않으므로 mock과 LLM의 결과(9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED)가 항상 같습니다. LLM이 바꿀 수 있는 것은 문장과 심각도 표시뿐입니다. 예를 들어 LLM이 search 결함을 Major로 판단해도 흐름은 같습니다.

### 10.4 실습의 AI 초안

실습에서는 LLM이 사람을 대신하지 않고 **초안**만 씁니다. 이슈 대화상자, 수정 완료 대화상자, 릴리즈 노트의 "AI 초안" 버튼이 각각 `/api/work/draft/*`를 호출하고, 결과를 입력란에 채웁니다. 사람이 고친 뒤 등록합니다. 이슈 초안은 '실행'으로 얻은 실패 로그를 근거로 하므로, 실행 전에 누르면 "(실행 결과 없음 …)"을 근거로 쓰게 됩니다. 토스트에 생성 주체("Claude가 실패 로그를 분석해 작성했습니다" / "LLM 미사용(mock) 템플릿")가 표시됩니다.

### 10.5 성능

| 모드 | 소요 시간 (STEP_DELAY=0, TEST_DELAY=0) | LLM 호출 |
| --- | --- | --- |
| mock | 약 1초 | 0 |
| LLM (`claude-opus-5-5`, effort low) | 약 34초 | 10회 (이슈 분석 4, 수정 내역 4, 릴리즈 노트 2) |

LLM 호출은 순차로 이루어집니다(QA는 실패 항목마다, Dev는 티켓마다 차례로 호출). 소요 시간 대부분이 LLM 응답 대기입니다.

---

## 11. 예외 처리와 운영

### 11.1 재시도 정책

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 최대 시도 | 3회 | `common/http.py` `RETRIES = 3` |
| 대기 | 1회 후 0.5초, 2회 후 1.0초 | `BACKOFF = (0.5, 1.0)` |
| 재시도 대상 | 네트워크 오류(`httpx.HTTPError`), HTTP 5xx | |
| 재시도 안 함 | HTTP 4xx (즉시 `IntegrationError`) | 상대의 의도적 거절 |
| 타임아웃 | 30초 (서비스 간), Hub 조회 10초, Hub health 3초 | `IntegrationClient`, `hub/app.py`, `upstream.py` |
| 실패 후 | 데모: Dev 사이클 `FAILED` 또는 QA `integration.error` / 실습: `WorkError(502)` + 작업 취소 / 중단 통지: Dev `integration.error`, 중단은 유지 | 8.3장 |

Hub의 조회와 대리 호출에는 재시도가 없습니다. 조회 실패는 화면 배너로, 대리 호출 실패는 503으로 알립니다.

### 11.2 연동 로그 (Integration Log)

- **양쪽 기록**: 호출한 쪽은 `out`(시도마다 한 행, 응답 본문 포함), 받은 쪽은 `in`(요청 본문 포함, 응답 본문 없음)을 각자 DB에 씁니다. 그래서 한 번의 호출이 두 DB에 기록되고, 받은 쪽 기록이 없으면 "요청이 도착하지 않았다"고 판단할 수 있습니다.
- **Correlation ID**: 모든 서비스 간 요청에 `X-Cycle-Id`가 붙고, 양쪽 로그 모두 `cycle_id`로 필터됩니다. Hub는 두 시스템의 로그를 사이클 기준으로 모아 시간순으로 보여 줍니다.
- **본문 제한**: 요청·응답 본문은 4000자에서 자르고 `...(truncated)`를 붙입니다.
- **화면**: Dev/QA 각자의 `/integration`(자기 기록만), Hub `/integration`(병합, 서비스 상태, 요약).

### 11.3 시작 시 정리 (Startup Cleanup)

| 시스템 | 동작 | 코드 |
| --- | --- | --- |
| Dev | 진행 상태로 남은 **데모** 사이클을 `CANCELLED`, `error="서버 재시작으로 중단"`으로 정리. 실습 사이클은 사람이 이어서 진행할 수 있도록 유지 | `DevService.__init__` |
| Dev/QA | `migrate()`로 실습 모드용 컬럼을 기존 DB에 추가 | `Database.migrate()` |
| QA | 이전 데이터의 `test_results.result`가 비어 있으면 `passed`로 채움 | `QAService.__init__` |
| QA | `RUNNING`으로 남은 **데모** 검증 실행을 `CANCELLED`로 정리 (실습 실행은 유지, 2026-10-16 추가) | `QAService.__init__` |
| QA | 테스트케이스·명부 seed, 비어 있는 담당자 지정 | `QAService.seed()` |

데모 사이클을 정리하는 이유는, 프로세스가 끝나면 백그라운드 작업(`asyncio.Task`)이 사라져 사이클이 영원히 "진행 중"으로 남고 새 사이클을 시작할 수 없게(409) 되기 때문입니다. 종료할 때(`lifespan`)는 남은 작업을 취소하고 HTTP 클라이언트를 닫습니다.

### 11.4 중단과 초기화

| 동작 | 경로 | 조건 | 결과 |
| --- | --- | --- | --- |
| 사이클 중단 | Dev `POST /api/cycles/{id}/abort`, `/api/work/abort`, Hub `/api/work/abort` | 진행 중 사이클 (없으면 409) | Dev 백그라운드 작업 취소, `CANCELLED`, `cycle.cancelled` → QA에 `cycle-cancelled` 통지, QA 실행 `CANCELLED` |
| 초기화 (Dev) | `POST /api/admin/reset` | 데모 진행 중이면 409 | 모든 테이블 비움, 명부 재seed, `system.reset` |
| 초기화 (QA) | `POST /api/admin/reset` | 자동 검증 작업 중이면 409 | 테스트케이스 제외 비움, seed 재실행 |
| 초기화 (Hub) | `POST /api/reset` | Hub 화면은 진행 중이면 버튼 비활성 | 양쪽에 `dry_run`으로 먼저 확인한 뒤 Dev → QA 순서로 실제 초기화 |

### 11.5 권한과 업무 규칙

| 검사 | 상태 코드 | 메시지 예 | 위치 |
| --- | --- | --- | --- |
| 이 시스템 명부에 없는 작업자 (다른 부서원, 미선택) | 403 | "상단에서 개발부서 작업자를 선택하세요. (이 시스템의 부서원만 작업할 수 있습니다)" | `Members.require()` |
| 진행 중인 실습 사이클 없음 / 현재 단계에서 불가 (Dev) | 409 | "지금은 이 작업을 할 수 없습니다. (현재 단계: VERIFYING)" | `DevService._manual()` |
| 검증 중인 버전 없음 (QA) | 409 | "검증 중인 버전이 없습니다. 개발부서가 버전을 발행하면…" | `QAService._run()` |
| 진행 중 사이클이 있는데 새 사이클 | 409 | "이미 진행 중인 사이클이 있습니다." | `start_cycle` |
| 반영할 수정 티켓 없이 발행 | 400 | "반영할 수정 완료 티켓이 없습니다…" | `DevService.release()` |
| 릴리즈 노트·수정 내역·이슈 제목·심각도 누락 | 400 | "릴리즈 노트를 입력하세요. ('AI 초안'으로 작성할 수 있습니다)" | 각 서비스 |
| 판정이 남았는데 이관/확정 | 400 | "판정하지 않은 항목이 5건 있습니다." | `QAService._blocker()` |
| 미해결 이슈가 있는데 확정 | 400 | "미해결 이슈가 1건 있어 확정할 수 없습니다. 개발부서로 이관하세요." | 같은 곳 |
| 다른 실행의 항목 | 404 | "현재 검증 중인 버전의 항목이 아닙니다." | `QAService._result()` |
| 상대 시스템 호출 실패 | 502 | "QA 서버에 발행을 통지하지 못해 발행을 취소했습니다…" | release/transfer/approve/execute |
| 검증 단계가 아닌 사이클로 온 이관·확정 통지 (연동 API) | 409 | "검증 단계의 사이클이 아닙니다." / "검증 중인 사이클·버전이 아닙니다." | `DevService.receive_tickets()`, `verification_completed()` |

권한은 "어느 시스템의 명부에 있는가"라는 **부서 단위**로만 검사합니다. 같은 부서 안에서 담당자가 아닌 항목을 실행·판정하는 것은 서버가 막지 않고, 화면의 "내 항목" 필터와 행 강조로 안내합니다. 이는 Stage 1(`01_standalone/src/standalone/services/workbench.py` `_member()`)과 같은 규칙이며, 2026-10-16 검토에서 단계 간 일관성을 위해 그대로 두기로 했습니다. 따라서 403은 "다른 부서원(명부에 없는 작업자)"에게만 발생합니다.

---

## 12. 점검 결과

| 일자 | 항목 | 방법 | 결과 |
| --- | --- | --- | --- |
| 2026-10-15 | 3개 프로세스 분리 기동, Swagger `/docs` | `uv run run.py`, 각 `/docs` 열기 | 정상 |
| 2026-10-15 | 데모 (mock) | Hub 데모 실행 | v1.0.0 9/3 → v1.1.0 11/1 → v1.2.0 12/0, 확정, ISSUE-1~4 VERIFIED, 약 1초(지연 0), 서비스 간 호출 9회 |
| 2026-10-15 | 데모 (LLM) | `claude-opus-5-5`, effort low | 결과 동일, 약 34초 |
| 2026-10-15 | `cycle_id` 전파 | 양쪽 integration_logs 비교 | out/in 모두 같은 사이클 번호 |
| 2026-10-15 | 실습 전 과정 | API로 처음부터 끝까지 진행 | v1.0.0 → v1.1.0(회귀) → v1.2.0, QA 결정 이관 → 이관 → 확정, 이슈 4건 VERIFIED, 티켓 4건 FIXED |
| 2026-10-15 | 권한·조건 | API 직접 호출 | 다른 부서원 작업 403, 판정 전 확정 400, 수정 전 발행 400, 진행 중 새 사이클 409, 차례 아닌 작업 409 |
| 2026-10-15 | 장애 | QA 서버 중지 상태에서 사이클 시작 / 실습 발행 | 데모: 3회 재시도 후 FAILED, 오류 기록·표시 / 실습: 발행 취소(502), 재기동 후 재발행 가능 |
| 2026-10-15 | 화면 | Hub, Dev, QA 전 화면 열기 | 전 화면 정상 렌더링, 이모지 없음, 브라우저 콘솔 오류 없음 (당시 기록은 21개였으나 실제 화면은 22개, 2026-10-16 재점검) |
| 2026-10-15 | 4단계 동시 회귀 | 01~04 단계를 동시에 실행해 데모 결과 비교 | 네 단계 결과 동일 |
| 2026-10-16 | Hub "실습 시작"·"사이클 중단" 버튼 무반응 수정 | Playwright + Edge로 Hub 화면 클릭 | 시작 → 실습 `RELEASING`, 중단 → `CANCELLED`, JS 오류 없음 |
| 2026-10-16 | 알려진 이슈 수정 후 데모 (mock, 지연 0) | Hub `POST /api/cycles` → `/api/state` 폴링 → `/api/cycles/1/summary` | `APPROVED`, 9/3 → 11/1 → 12/0, QA 결정 이관 → 이관 → 확정(K-5 해결 확인), ISSUE-1~4 VERIFIED / 티켓 FIXED, out 9행·in 9행, 약 0.3초 |
| 2026-10-16 | 실습 전 과정 (실제 브라우저) | Playwright + Edge(headless) 클릭: Hub 실습 시작 → Dev 발행(확인 대화상자) → QA 실행·PASS·FAIL(이슈 대화상자 + AI 초안) → Dev 이관 → Dev 착수·수정 완료(대화상자 + AI 초안)·릴리즈 노트 AI 초안 → 재발행 → 수정 확인 → 버전 확정 | v1.0.0 9/3 → v1.1.0 11/1(TC-11 회귀) → v1.2.0 12/0, QA 결정 이관 → 이관 → 확정, 사이클 `APPROVED`(검증 3회), 이슈 4건 VERIFIED, 티켓 4건 FIXED, out 9행·in 9행, 페이지 JS 오류 0건 |
| 2026-10-16 | 전 화면 순회 | 위 브라우저로 Hub 9, Dev 6, QA 7개 화면 열기 | 22개 모두 HTTP 200, JS 오류 없음 (콘솔에는 브라우저가 기본으로 요청하는 `/favicon.ico` 404만 기록) |
| 2026-10-16 | 사이클 중단 전파 (K-3) | 실습 발행 직후 Hub "사이클 중단" 클릭 / 데모 v1.0.0 검증 중 Hub API로 중단 | 실습: Dev `CANCELLED`, QA 실행 `CANCELLED`, QA `/api/state` `verifying: false`, 작업대 "중단" 배너, QA `in` 로그에 `cycle-cancelled` / 데모: QA 실행 `CANCELLED`(1/12에서 멈춤), 이관·티켓 생성 없음 |
| 2026-10-16 | 상태 검사 (K-4) | 중단된 사이클로 `verification-completed`, `POST /api/tickets` 직접 호출 | 둘 다 409, 사이클은 `CANCELLED` 유지 |
| 2026-10-16 | 초기화 2단계 (K-7) | 데모 검증 중 Hub `POST /api/reset` / 종료 후 다시 호출 | 진행 중: 409, Dev·QA 데이터 모두 유지 / 종료 후: 200, 양쪽 모두 비움. `dry_run=true`는 데이터를 지우지 않음 |
| 2026-10-16 | QA 시작 시 정리 (K-8) | 데모 v1.0.0 검증 중 QA 프로세스 강제 종료 → `--only qa_server`로 재기동 | `RUNNING`이던 데모 실행이 `CANCELLED`로 정리됨 |
| 2026-10-16 | 실습 AI 초안 (LLM 모드) | `LLM_MODE=auto`(키 있음)로 기동, API로 초안 3회 호출 | 이슈 초안 `source: llm`(TC-08, Critical, 약 3.9초), 수정 내역 초안(약 3.2초), 릴리즈 노트 초안(약 3.9초) 모두 Claude가 작성, Dev LLM 실패 0회 |
| 2026-10-16 | 화면 캡처 재촬영 | 위 브라우저 점검 중 Playwright 캡처 (1440px 폭) | `hub_overview.png`, `hub_integration.png`, `dev_web.png`, `qa_web.png` 4장 교체 (부분 수정 교착 해결 후 작업대 문구 반영해 한 번 더 촬영) |
| 2026-10-16 | 실습 부분 수정 교착 재현 | API로 진행: v1.0.0 이관(ISSUE-1~3) → 정개발 ISSUE-2 착수, 최개발 ISSUE-1만 수정 → v1.1.0 발행 → ISSUE-1 수정 확인, 12건 판정(TC-05·TC-08은 기존 ISSUE-2·3에 연결) | 수정 전: 10/2, 이관 대상 0·미해결 2로 이관·확정 모두 불가(400) → **교착 확인** |
| 2026-10-16 | 교착 해결 후 같은 시나리오 | 위와 같은 순서로 끝까지 진행 | v1.1.0 이관 가능(ISSUE-2·3 다시 이관), Dev 티켓 3건 그대로(중복 없음), ISSUE-2 `IN_PROGRESS`/정개발·ISSUE-3 `RECEIVED` 유지, Dev `FIXING` → v1.2.0 11/1(TC-11 회귀, ISSUE-4) → v1.3.0 12/0 확정, 사이클 `APPROVED`, 이슈 4건 VERIFIED |
| 2026-10-16 | 교착 해결 후 회귀 점검 | 데모 API 점검 재실행, Edge 클릭 점검 재실행 | 데모 9/3 → 11/1 → 12/0 확정, out 9·in 9, 중단·초기화 점검 모두 통과 / 브라우저 실습 동일 결과, 22개 화면 200, 페이지 JS 오류 0건 |

### 12.1 2026-10-16 수정 상세

**(1) Hub "실습 시작"·"사이클 중단" 버튼 무반응**

| 항목 | 내용 |
| --- | --- |
| 증상 | Hub 상단 "실습 시작" 버튼과 "사이클 중단" 대화상자의 "중단" 버튼을 눌러도 반응이 없음 (API 직접 호출은 정상) |
| 원인 | 두 버튼의 클릭 처리기(`data-action="work-start"`, `"work-abort"`)는 `workbench.js`에 있는데, 공통 `base.html`이 이 스크립트를 `members`가 있을 때만 불러왔음. Hub는 명부가 없어(`"members": None`) 스크립트가 로드되지 않았음 |
| 수정 | `02_api/src/common/web/templates/base.html` 103행에서 `workbench.js`를 항상 불러오도록 변경 |
| 안전성 | `workbench.js`는 작업자 선택(`[data-actor]`)이나 작업대(`[data-desk]`)가 없으면 해당 기능을 건너뛰므로 Hub에서 불러와도 부작용이 없음 |

**(2) 알려진 이슈 수정 (13.2의 K-3, K-4, K-5, K-7, K-8, K-9)**

| 번호 | 수정 내용 | 위치 |
| --- | --- | --- |
| K-3 | 사이클 중단을 QA에 통지하는 Webhook `POST /api/webhooks/cycle-cancelled`를 추가. Dev `abort()`는 중단 후 QA에 통지하고(실패해도 중단 유지, `integration.error` 이벤트), QA는 해당 사이클의 자동 검증 작업을 취소하고 `RUNNING` 실행을 `CANCELLED`로 닫음. QA 작업대에 "중단" 배너 추가, Hub 연동 계약 표에 행 추가 | `dev_server/service.py` 108행 `abort()`, `dev_server/app.py` 157·249행, `qa_server/service.py` 119행 `on_cycle_cancelled()`, `qa_server/app.py` 146행, `qa_server/templates/desk.html` 24행, `hub/templates/integration.html` 40행 |
| K-4 | Dev가 확정 통지를 받을 때 사이클이 `VERIFYING`이고 버전이 현재 버전인지 검사, 아니면 409. 이관 수신도 `VERIFYING`만 허용(이전에는 진행 중 상태 전체) | `dev_server/service.py` 192·220행, `dev_server/app.py` 220행 |
| K-5 | 데모 검증 실행에도 `decision`(`TRANSFERRED`/`APPROVED`)과 `decided_by='Demo'` 기록 | `qa_server/service.py` 146행 |
| K-7 | Hub 초기화를 2단계로 변경: Dev·QA에 `POST /api/admin/reset?dry_run=true`로 먼저 확인하고, 둘 다 허용할 때만 실제 초기화 | `hub/app.py` 221행, `dev_server/app.py` 229행, `qa_server/app.py` 189행 |
| K-8 | QA 시작 시 `RUNNING`으로 남은 데모 실행을 `CANCELLED`로 정리 (Dev의 시작 시 정리와 같은 규칙) | `qa_server/service.py` 82행 |
| K-9 (일부) | 쓰이지 않던 `BACKOFF` 세 번째 값 제거: `(0.5, 1.0)` | `common/http.py` 16행 |

**(3) 실습 부분 수정 교착 (K-12)**

| 항목 | 내용 |
| --- | --- |
| 증상 | Dev가 ISSUE-1~3 중 ISSUE-1만 고쳐 v1.1.0을 발행하면(작업대 안내대로 미완료 티켓은 이월), QA에서 ISSUE-2·3은 `TRANSFERRED`로 남음. 이관 조건은 `OPEN/REOPENED`만 세고 확정 조건은 미해결 0을 요구하므로 이관도 확정도 할 수 없어 사이클이 멈춤 |
| 원인 | 이관 대상에 "이전에 이관했지만 직전 발행에 반영되지 않은" 이슈가 빠져 있었음 (Stage 1의 같은 규칙에서 이어진 문제, Stage 4는 같은 방식으로 해결) |
| 수정 (QA) | 이관 대상을 `OPEN/REOPENED/TRANSFERRED`로 확장(`TRANSFERABLE`). 실습에서 QA 차례에 `TRANSFERRED`인 이슈는 반영되지 않은 이월 이슈뿐임(반영된 이슈는 발행 통지로 `FIXED`). 이월 이슈는 "다시 이관" 이력을 남김. 비활성 사유 문구와 작업대 요약 문구 수정 (`qa_server/service.py` 24·197·331·344행, `qa_server/templates/desk.html` 149행) |
| 수정 (Dev) | 재이관 수신 시 수정 중(`RECEIVED`/`IN_PROGRESS`) 티켓은 새로 만들거나 되돌리지 않고 상태·담당자를 유지, 이력만 추가. `FIXED` 티켓(재현)은 기존처럼 `RECEIVED`로 되돌림. 발행 카드 안내 문구 보강 (`dev_server/service.py` 195행, `dev_server/templates/desk.html` 38행) |
| 영향 | 데모는 접수한 티켓을 모두 고쳐 발행하므로 `TRANSFERRED` 이월 이슈가 생기지 않아 동작이 같음(재점검으로 확인) |

**(4) 문서 정비**

README의 롤백 설명, PRD의 API 목록(Dev `/api/work/start`·`/api/work/abort`, `cycle-cancelled`, `dry_run`), TODO의 이전 명칭(Portal)과 섹션 순서, USER_SCENARIO의 예외 상황(중단, 담당자 규칙)을 코드에 맞췄습니다. 화면 수는 22개(Hub 9, Dev 6, QA 7)로 통일했습니다.

### 12.2 점검 범위의 제약

- 브라우저 점검은 headless Edge 한 대로 역할별 탭을 오가며 진행했습니다. 여러 사람이 동시에 같은 항목을 누르는 경합은 점검하지 않았습니다(범위 제외 항목).
- LLM 모드는 AI 초안 3회만 호출해 확인했습니다. LLM 모드 데모 완주(약 34초)는 2026-10-15 기록이며, 이번 수정은 LLM 호출 경로를 바꾸지 않았습니다.
- K-7의 "Dev는 허용하고 QA만 거절하는" 경우는 재현이 어려워, 각 서비스의 `dry_run` 응답과 데모 진행 중 Hub 초기화가 1단계에서 거절되는 것으로 확인했습니다. 1단계 확인과 2단계 실행 사이의 짧은 간격에 상태가 바뀌는 경합은 남아 있습니다.

---

## 13. 한계와 알려진 이슈

### 13.1 구조적 한계 (이 단계의 학습 대상)

| 한계 | 설명 |
| --- | --- |
| 강결합 | Dev는 QA의 URL과 `release-published`·`cycle-cancelled` 스키마를, QA는 Dev의 `tickets`·`verification-completed` 스키마와 빌드 매니페스트 형식을 알아야 합니다. 필드 하나를 바꾸려면 양쪽을 함께 고치고 함께 배포해야 합니다. 중단 전파를 고치는 데도 새 계약을 양쪽에 추가해야 했습니다. |
| 고정된 경로 | "발행하면 QA에 통지, 실패하면 Dev에 이관, 통과하면 확정 통지"라는 경로가 코드(`_publish`, `_verify`, `_transfer`)에 고정되어 있습니다. 보안팀 검토 단계를 끼워 넣으려면 두 시스템을 모두 고쳐야 합니다. |
| 전체 그림 부재 | 사이클 상태는 Dev에, 검증과 이슈는 QA에 있습니다. 전체를 보려면 Hub가 화면마다 양쪽을 여러 번 조회해야 합니다. |
| 분산 상태 일관성 | 이슈(QA)와 티켓(Dev)의 상태가 따로 움직이며, 맞춰 주는 장치는 호출 순서와 상태 검사(409)뿐입니다. 공용 트랜잭션이 없습니다. |
| 사람의 판단 vs 코드의 판단 | 실습에서 "차례"는 사람이 넘기지만, 넘어가는 통로(어느 API를 부를지)는 코드가 정합니다. |

### 13.2 구현상 알려진 이슈

| 번호 | 이슈 | 상태 | 비고 |
| --- | --- | --- | --- |
| K-1 | QA는 담당자를 서버에서 강제하지 않음 (같은 부서원이면 남의 항목도 실행·판정 가능) | 설계 유지 | Stage 1과 같은 부서 단위 권한. 화면에서 "내 항목" 필터와 강조로 안내. 단계 문서(README, PRD, USER_SCENARIO)에 이 규칙을 명시 |
| K-2 | AI 초안 API(`/api/work/draft/*`)는 작업자를 검사하지 않음 | 한계로 유지 | 데이터를 바꾸지 않는 조회성 호출. 실습 단계(`_manual`, `_run`) 검사는 적용됨 |
| K-3 | 사이클 중단이 QA에 전달되지 않음 | **수정 완료** | `cycle-cancelled` Webhook 추가 (12.1) |
| K-4 | 확정 통지 수신 시 사이클 상태 미검사 | **수정 완료** | `VERIFYING`·현재 버전만 허용, 이관 수신도 `VERIFYING`만 허용 |
| K-5 | 데모 검증 실행에 QA 결정 미기록 | **수정 완료** | `decided_by='Demo'` |
| K-6 | Hub의 대리 호출과 조회는 `X-Caller`가 없어 integration_logs에 남지 않음 | 설계 유지 | Integration 화면은 Dev↔QA 호출만 보여 줌. PRD F-05의 "시스템 간 호출"은 Dev↔QA 연동을 뜻함 |
| K-7 | Hub 초기화 시 Dev만 초기화되고 QA는 실패할 수 있음 | **수정 완료** | `dry_run` 2단계. 두 단계 사이 경합은 남음(12.2) |
| K-8 | QA 시작 시 정리가 없어 데모 실행이 `RUNNING`으로 남음 | **수정 완료** | 시작 시 데모 실행 `CANCELLED` |
| K-9 | `in` 로그에 응답 본문 없음, `BACKOFF` 미사용 값 | 일부 수정 | `BACKOFF` 정리 완료. `in` 로그의 응답 본문은 호출한 쪽 `out` 로그에 있으므로 유지 |
| K-10 | Webhook 서명, 인증, 멱등키 없음. 같은 `release-published`가 두 번 오면 실습 실행이 다시 만들어짐 | 범위 밖 | 3.2 제외 항목 |
| K-11 | 중단 통지가 실패하면(QA 중지) QA의 실습 실행이 `RUNNING`으로 남음 | 한계로 유지 | Dev가 이후 이관·확정 통지를 409로 거절하므로 흐름은 보호됨. 다음 발행이 오면 새 실행이 최신이 됨. 재전송 보관함(outbox)은 범위 밖 |
| K-12 | 실습에서 일부 티켓만 고쳐 발행하면 QA가 이관도 확정도 할 수 없는 교착 | **수정 완료** | 이월 `TRANSFERRED` 이슈를 이관 대상에 포함, Dev는 수정 중 티켓 유지 (12.1 (3)) |

### 13.3 문서와 코드의 차이

2026-10-16 정비로 이전에 기록한 차이(README의 QA 화면 수와 롤백 설명, PRD의 Dev 실습 API 누락, TODO의 Portal 명칭과 섹션 순서, 오래된 `hub_integration.png`)는 모두 해소했습니다. 남은 차이는 다음과 같습니다.

| 위치 | 내용 | 비고 |
| --- | --- | --- |
| 전 단계 공통 설명 | "403 wrong department/assignee" (담당자 불일치도 403) | Stage 2(및 Stage 1)는 부서 단위로만 403. 공통 문서는 이 단계의 수정 범위 밖이라 그대로 둠 |
| `common/config.py` | `PORTAL_PORT = HUB_PORT` 별칭이 남아 있고, Dev/QA 사이드바 링크가 이 이름을 씀 | 동작은 같음. 호환용 |
| 12장 2026-10-15 화면 점검 기록 | 당시 기록은 21개 | 실제 22개. 기록은 원문을 살리고 주석을 붙임 |

---

## 14. 학습 포인트

### 14.1 Stage 1과 비교

| 관점 | Stage 1 Standalone (`01_standalone`, :8100) | Stage 2 API (`02_api`, :8200~8202) |
| --- | --- | --- |
| 프로세스 | 1개 | 3개 (Hub, Dev, QA) |
| DB | 공유 DB 1개 | 시스템별 DB (`dev.db`, `qa.db`), Hub는 없음 |
| 부서 간 연결 | 함수 직접 호출 | HTTP: REST 2종 + Webhook 3종(발행, 확정, 중단 통지) |
| 이슈 데이터 | 하나의 이슈 레코드를 두 부서가 함께 수정 | QA 원본(이슈) + Dev 사본(티켓), 키로 연결 |
| 부서원 명부 | 하나 | 시스템마다 따로 (QA 명부, Dev 명부) |
| 웹 화면 | 하나의 웹 | 부서별 웹 2개 + 관제 웹 1개 |
| 실패 양상 | 예외 하나로 전체가 멈춤 | 부분 실패: 한쪽이 죽어도 다른 쪽은 동작, 재시도·롤백 필요 |
| 추적 | 같은 DB의 이벤트 | Correlation ID(`X-Cycle-Id`)와 양쪽 연동 로그 |
| 전체 현황 | DB 조회 한 번 | Hub가 양쪽 API를 조회해 조합 |
| 결과 | 9/3 → 11/1 → 12/0 | 같음 (같은 시뮬레이터 규칙) |

### 14.2 이 단계에서 확인한 것

1. **분리의 이점은 실제로 있다.** QA를 꺼도 Dev 웹은 동작하고, Hub는 "QA Server 응답 없음" 배너를 띄운 채 Dev 데이터를 보여 줍니다. 데이터 소유가 분명해서 "이 상태는 누가 바꾸는가"가 코드에서 바로 보입니다.
2. **분리의 비용도 실제로 있다.** 함수 호출 한 줄이 재시도, 타임아웃, 호출 로그, 롤백 순서, 4xx/5xx 구분을 갖춘 `IntegrationClient`가 되었습니다. "통지가 성공한 뒤에 상태를 바꾼다"는 순서 설계가 데이터 일관성을 지킵니다. Stage 1에서는 상태 한 줄로 끝나던 "사이클 중단"도, 여기서는 상대에게 알리는 새 계약(`cycle-cancelled`)과 받는 쪽의 상태 검사(409)가 있어야 비로소 두 시스템이 같은 상태가 됩니다.
3. **Webhook 체인은 자동화에는 좋지만 흐름을 흩어 놓는다.** 데모 한 사이클이 9번의 호출로 두 시스템을 오가며, 흐름 전체는 어느 코드 한 곳에도 적혀 있지 않습니다(Dev `_publish`, QA `_verify`, Dev `receive_tickets`에 나뉘어 있음).
4. **관제는 조합이다.** Hub는 버전 문자열로 릴리즈와 검증을, 이슈 키로 이슈와 티켓을, 시각으로 이벤트와 이력을 합칩니다. 시스템이 늘어나면 Hub가 알아야 할 API와 결합 규칙도 같이 늘어납니다.

### 14.3 Stage 3(MCP)로 넘어가는 이유

Stage 2의 한계를 정리하면 "**호출하는 쪽이 상대의 기능, URL, 스키마, 호출 시점을 모두 코드로 알고 있어야 한다**"는 것입니다.

| Stage 2의 문제 | Stage 3(MCP)에서 기대하는 변화 |
| --- | --- |
| 상대 기능 목록을 문서(README의 연동 계약 표)로 공유하고 코드에 하드코딩 | MCP 서버가 도구(tool) 목록과 입력 스키마를 표준 방식으로 **스스로 알려 줌** (기능 발견) |
| 시스템마다 다른 REST 경로·본문 형식 | 표준 프로토콜(JSON-RPC 기반 MCP)로 호출 방식 통일 |
| "언제 어떤 API를 부를지"가 코드에 고정 | AI 에이전트가 상황(검증 결과, 이슈 상태)을 보고 **어떤 도구를 부를지 판단** |
| 새 기능을 쓰려면 호출하는 쪽 코드를 고쳐야 함 | 서버에 도구를 추가하면 에이전트가 발견해 사용 |

다만 MCP에서도 "누가 흐름 전체를 책임지는가", "에이전트끼리 어떻게 업무를 넘기는가"는 여전히 남는 질문이며, 이것이 Stage 4(A2A)로 이어집니다.

---

## 부록 A. 실행 방법

### A.1 준비

```powershell
cd D:\users\yk\A2A_Class\A2A-Practice\02_api
uv sync
```

LLM 모드로 실행하려면 `02_api/.env` 또는 프로젝트 루트 `.env`에 `ANTHROPIC_API_KEY`를 둡니다(키가 없으면 `auto`가 mock으로 동작).

### A.2 실행

```powershell
uv run run.py                   # Hub :8200, Dev :8201, QA :8202 동시 기동
uv run run.py --mock            # LLM 없이 실행
uv run run.py --only qa_server  # 한 서비스만 실행 (장애 실습: 다른 창에서 나머지를 띄우고 QA만 껐다 켜기)
```

단독 실행: `src`를 `PYTHONPATH`에 두고 `uv run python -m dev_server` (run.py가 자동으로 설정).

### A.3 접속 주소

| 대상 | 주소 |
| --- | --- |
| Hub 웹 | http://localhost:8200 |
| Dev 웹 (작업자 지정) | http://localhost:8201/?as=최개발 , http://localhost:8201/?as=정개발 |
| QA 웹 (작업자 지정) | http://localhost:8202/?as=김검증 , `?as=이검증` , `?as=박검증` |
| API 문서 | http://localhost:8201/docs , http://localhost:8202/docs |

### A.4 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_MODE` | `auto` | `auto`(키 있으면 llm) / `llm` / `mock` |
| `CLAUDE_MODEL` | `claude-opus-5-5` | 모델 |
| `ANTHROPIC_API_KEY` | (없음) | LLM 모드에 필요 |
| `DEV_URL` / `QA_URL` | `http://127.0.0.1:8201` / `http://127.0.0.1:8202` | 상대 서비스 주소 |
| `HUB_PORT` / `DEV_PORT` / `QA_PORT` | `8200` / `8201` / `8202` | 포트 |
| `STEP_DELAY` / `TEST_DELAY` | `0.4` / `0.1` | 데모 관찰용 지연(초). 측정 시 0 |

### A.5 API로 직접 데모 돌려 보기 (PowerShell)

```powershell
# 데모 시작 (Dev에 직접)
Invoke-RestMethod -Method Post "http://127.0.0.1:8201/api/cycles?mode=demo"
# 진행 상태
Invoke-RestMethod "http://127.0.0.1:8200/api/state"
# 완료 후 요약 (cycle_id=1)
Invoke-RestMethod "http://127.0.0.1:8200/api/cycles/1/summary" | ConvertTo-Json -Depth 5
# 서비스 간 호출 기록
Invoke-RestMethod "http://127.0.0.1:8202/api/integration-logs?cycle_id=1"
# 실습: 최개발이 최초 버전 발행 (실습 사이클 시작 후)
Invoke-RestMethod -Method Post "http://127.0.0.1:8201/api/work/release" -ContentType "application/json" `
  -Body '{"actor":"최개발","release_note":""}'
```

### A.6 초기화

Hub 상단 **초기화**(진행 중이 아닐 때) 또는 `POST http://127.0.0.1:8200/api/reset`. 완전히 지우려면 서비스를 끄고 `02_api/data/*.db`를 삭제합니다.

---

## 부록 B. 용어 정리

| 용어 | 설명 |
| --- | --- |
| 사이클 (Cycle) | 최초 발행부터 확정(또는 실패·중단)까지의 한 번의 릴리즈 과정. Dev가 번호를 발급하고 상태를 소유 |
| 데모 (demo) | 사람 개입 없이 Webhook 체인으로 자동 진행하는 모드 |
| 실습 (manual) | 개발부서원·검증부서원이 각자 웹에서 직접 진행하는 모드 |
| 차례 (Turn) | 실습에서 지금 작업할 부서. Dev 사이클 상태로 판단 (`VERIFYING/TRANSFERRING` → QA, 나머지 → Dev) |
| 릴리즈 (Release) | Dev가 발행한 버전 (`1.0.0`, `1.1.0`, `1.2.0`) |
| 빌드 산출물 (Build manifest) | 릴리즈에 포함된 동작 목록. `behaviors[].breaks`가 깨뜨리는 테스트를 나타냄. `GET /builds/{cycle}/{version}` |
| `build_url` | 발행 통지에 담기는 빌드 다운로드 주소 |
| 검증 실행 (Test run) | 한 버전에 대한 12건 검증. 실습에서는 결정(이관/확정)을 기록 |
| 판정 (Judge) | 항목을 PASS/FAIL로 정하는 것. 실습에서는 사람이 함 |
| 실행 (Execute) | 실습에서 빌드로 테스트를 돌려 보는 참고 동작. 판정은 아님 |
| 이슈 (Issue) | QA가 등록한 결함 원본. `ISSUE-n` |
| 티켓 (Ticket) | Dev가 이관으로 받은 이슈의 사본. 키는 이슈와 같음 |
| 이관 (Transfer) | QA가 미해결 이슈를 `POST /api/tickets`로 Dev에 넘기는 것 |
| 확정 (Approve) | QA가 버전을 릴리즈 버전으로 확정하고 `verification-completed`로 Dev에 알리는 것 |
| 중단 통지 | Dev가 사이클을 중단한 뒤 `cycle-cancelled`로 QA에 알리는 것. QA는 진행 중인 검증 실행을 취소 |
| `dry_run` | 초기화 API를 실제로 실행하지 않고 가능 여부만 확인하는 옵션. Hub 초기화 1단계에서 사용 |
| 회귀 결함 (Regression) | 수정 과정에서 새로 생긴 결함. 여기서는 D-103 수정 시 D-201 |
| REST API | URL(자원)과 HTTP 메서드(동작), JSON 본문으로 시스템 간 데이터를 주고받는 방식 |
| Webhook | 이벤트가 생긴 쪽이 미리 약속한 상대 URL로 HTTP POST를 보내 알리는 방식 |
| 202 Accepted | "요청을 접수했고 처리는 나중에 한다"는 응답. 이 프로젝트의 Webhook·이관 수신 응답 |
| Correlation ID | 여러 시스템을 오가는 요청을 하나로 묶어 추적하는 ID. 여기서는 `X-Cycle-Id` |
| `X-Caller` | 서비스 간 호출임을 표시하는 헤더. 수신 로그 기록 기준 |
| Integration log | 서비스 간 호출 기록. 호출한 쪽 `out`, 받은 쪽 `in` |
| 재시도 / 백오프 | 실패한 호출을 일정 시간 기다린 후 다시 시도하는 것. 여기서는 최대 3회, 0.5초·1.0초 |
| 롤백 | 실패 시 앞서 한 변경을 되돌리는 것. 실습 발행에서 QA 통지 실패 시 릴리즈 삭제 |
| Hub | 흐름에 개입하지 않고 두 시스템을 조회·조합해 보여 주는 관제 서비스 |
| BFF | Backend For Frontend. 화면을 위해 여러 서비스의 데이터를 모아 주는 계층. Hub가 이 역할 |
| SSE | Server-Sent Events. 서버가 브라우저로 이벤트를 계속 밀어 보내는 HTTP 스트림 |
| EventRelay | Hub가 Dev/QA의 SSE를 구독해 하나의 스트림으로 다시 보내는 구성요소 |
| WorkError | 업무 규칙 위반을 HTTP 상태(400/403/404/409/502)와 함께 나타내는 예외 |
| 명부 (Members) | 시스템별 부서원 목록. 이 명부에 없으면 403 (권한은 부서 단위) |
| mock | LLM 대신 규칙 기반 템플릿으로 문장을 만드는 모드. 판정 결과는 LLM 모드와 같음 |
| AI 초안 | 실습에서 사람이 고쳐 쓸 수 있도록 LLM(또는 mock)이 미리 채워 주는 문장 |
| 마이그레이션 | 기존 DB 파일에 새 컬럼을 추가하는 것 (`Database.migrate()`) |
