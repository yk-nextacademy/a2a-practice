# Stage 1. Standalone 개발 완료 보고서

> 릴리즈·검증·결함 이관 업무를 하나의 웹 서비스(모놀리식) 안에 구현한 첫 번째 단계의 개발 결과를 정리한 문서입니다.
> 이 문서의 기술 설명은 모두 `01_standalone/` 아래의 실제 소스 코드를 근거로 작성했습니다.

---

## 0. 문서 정보

| 항목 | 내용 |
| --- | --- |
| 단계 | Stage 1 — Standalone (모놀리식 웹 서비스) |
| 폴더 | `01_standalone/` |
| 포트 | 8100 (`run.py`의 `--port` 기본값) |
| 실행 단위 | 단일 프로세스(uvicorn 1개), 단일 SQLite 파일(`01_standalone/data/app.db`) |
| 작성일 | 2026-10-16 (같은 날 알려진 이슈 수정과 재점검 결과 반영) |
| 상태 | 완료 |
| 관련 문서 | [README](../01_standalone/README.md) · [PRD](../01_standalone/docs/PRD.md) · [TODO](../01_standalone/docs/TODO.md) · [USER_SCENARIO](../01_standalone/docs/USER_SCENARIO.md) |
| 다음 단계 | [Stage 2. API](../02_api/README.md) |

---

## 1. 요약

### 1.1 무엇을 만들었나

개발부서(Dev)가 새 버전을 발행하면 검증부서(QA)가 12개 테스트케이스로 검증하고, 실패 항목을 이슈로 등록해 개발부서로 이관하며, 개발부서가 이슈를 수정해 차기 버전을 발행하는 과정을 QA가 승인할 때까지 반복하는 **릴리즈 사이클 관리 웹 서비스**를 만들었습니다.

Stage 1의 핵심 특징은 다음 세 가지입니다.

1. **하나의 프로세스**: FastAPI 애플리케이션 하나 안에 `DevService`, `QAService`, 데모 실행기(`CycleRunner`), 실습 진행기(`Workbench`), 화면이 모두 들어 있습니다.
2. **함수 직접 호출**: 검증부서가 개발부서에 이슈를 넘기는 "이관"은 `qa.transfer()`의 반환값을 `dev.receive_issues()`에 인자로 넘기는 **파이썬 함수 호출**입니다(`01_standalone/src/standalone/services/cycle.py`).
3. **하나의 DB**: 두 부서가 같은 `issues` 테이블을 함께 읽고 쓰며, 상태 변경은 공용 함수 `change_status()`(`01_standalone/src/standalone/services/issue_store.py`) 하나로 처리합니다.

사이클은 두 가지 방식으로 진행할 수 있습니다.

| 방식 | 진입 버튼 | 진행 주체 | 구현 위치 |
| --- | --- | --- | --- |
| 데모 | 상단 **데모 실행** | 코드(자동 루틴) | `services/cycle.py`의 `CycleRunner` |
| 실습 | 상단 **실습 시작** | 사람(부서원 5명 역할) | `services/workbench.py`의 `Workbench` |

### 1.2 결과 한눈에 보기

| 버전 | 테스트 결과 | 처리 | 이슈 상태 변화 |
| --- | --- | --- | --- |
| v1.0.0 | 9 PASS / 3 FAIL (TC-02, TC-05, TC-08) | ISSUE-1, 2, 3 등록 → Dev 이관 → 수정 | ISSUE-1~3: OPEN → TRANSFERRED → IN_PROGRESS → FIXED |
| v1.1.0 | 11 PASS / 1 FAIL (TC-11, 회귀) | ISSUE-1~3 수정 확인, ISSUE-4 등록 → 이관 → 수정 | ISSUE-1~3: VERIFIED, ISSUE-4: OPEN → … → FIXED |
| v1.2.0 | 12 PASS / 0 FAIL | ISSUE-4 수정 확인, 사이클 승인 | ISSUE-4: VERIFIED, 사이클 APPROVED |

| 측정 항목 (지연 0 설정) | mock 모드 | LLM 모드 |
| --- | --- | --- |
| 데모 1사이클 소요 | 1초 미만 (약 0.2초) | 약 46초 |
| LLM 호출 수 | 0회 | 10회 (이슈 분석 4 + 수정 내역 4 + 릴리즈 노트 2) |
| 최종 결과 | APPROVED, 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED | 동일 |
| 부서 간 상호작용 단위 | 함수 호출 | 함수 호출 |

실습 모드도 실제 브라우저에서 부서원 5명 역할로 끝까지 진행해 같은 결과(9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, APPROVED)를 확인했습니다(12장).

### 1.3 핵심 시사점

- 업무 도메인(발행 → 검증 → 이관 → 수정 → 재발행 → 승인)을 가장 단순한 구조로 구현해, 이후 단계(API, MCP, A2A)에서 **무엇이 바뀌고 무엇이 그대로인지** 비교할 기준선을 만들었습니다.
- PASS/FAIL·버전·이슈 수는 결정적 시뮬레이터(`domain/simulator.py`)가 정하고, LLM은 문서 "내용"만 작성하므로 mock과 LLM 모드의 결과 숫자가 항상 같습니다.
- 구조가 단순한 대신, Dev와 QA가 프로세스·DB·코드를 공유하므로 **부서별 독립 운영·배포·장애 격리가 불가능**합니다. 이것이 Stage 2(API)로 넘어가는 동기입니다.

---

## 2. 기술 배경

### 2.1 모놀리식(Monolithic) / 스탠드얼론(Standalone) 아키텍처란

**모놀리식 아키텍처**는 애플리케이션의 모든 기능(화면, 업무 로직, 데이터 접근)을 **하나의 실행 단위**로 묶어 개발·배포·실행하는 방식입니다. "모놀리스(monolith)"는 하나의 거대한 돌기둥이라는 뜻으로, 내부에 여러 모듈이 있더라도 밖에서 보면 하나의 프로그램입니다.

**스탠드얼론(standalone)** 은 다른 시스템에 의존하지 않고 그것 하나만으로 완결되어 동작하는 프로그램을 가리킵니다. 이 프로젝트에서는 "외부 서비스와 통신하지 않고 단독으로 실행되는 모놀리식 웹 서비스"라는 의미로 씁니다(LLM 호출은 선택 사항이며, 없어도 mock으로 완결됩니다).

모놀리식 구조의 특징은 다음과 같습니다.

| 관점 | 모놀리식에서의 모습 |
| --- | --- |
| 실행 | 프로세스 1개. 서버를 켜면 모든 기능이 함께 켜지고, 끄면 함께 꺼진다 |
| 모듈 간 통신 | 같은 메모리 공간 안에서의 **함수(메서드) 호출**. 직렬화·네트워크가 없다 |
| 데이터 | 보통 **DB 하나**를 모든 모듈이 공유한다. 조인·트랜잭션이 쉽다 |
| 배포 | 코드 한 줄을 고쳐도 전체를 다시 빌드·배포한다 |
| 확장 | 전체를 통째로 복제(수평 확장)하는 방식만 가능하다 |
| 장애 | 한 모듈의 치명적 오류가 프로세스 전체에 영향을 준다 |

### 2.2 언제 쓰는가

- 서비스 초기, 도메인을 아직 탐색하는 단계 (경계를 어디서 나눌지 모를 때)
- 팀이 작고, 배포 주기를 하나로 맞춰도 문제없을 때
- 기능 간 데이터 일관성이 매우 중요하고 트랜잭션으로 묶고 싶을 때
- 학습·프로토타입처럼 "동작하는 전체 흐름"을 빨리 보여 줘야 할 때 (이 프로젝트의 Stage 1이 여기에 해당)

### 2.3 장점과 단점

| 장점 | 단점 |
| --- | --- |
| 구조가 단순해 이해·디버깅이 쉽다 (호출 스택 하나로 흐름 추적) | 모듈 간 결합도가 높아지기 쉽다 (다른 모듈의 내부 함수·테이블에 직접 접근) |
| 함수 호출이라 지연이 거의 없고 실패 모드가 적다 | 한 부분만 따로 배포·확장할 수 없다 |
| DB 하나라 조회·일관성 유지가 쉽다 | 조직이 커지면 여러 팀이 같은 코드베이스·DB를 동시에 고쳐 충돌한다 |
| 실행·운영 환경이 하나라 설치가 간단하다 | 장애 격리가 안 된다 (한 부서 기능의 버그가 전체 서비스를 멈출 수 있다) |
| 로컬 개발·테스트가 쉽다 | 기술 스택을 모듈별로 다르게 가져가기 어렵다 |

### 2.4 이 프로젝트에 대응시키면

현실에서 개발부서와 검증부서는 서로 다른 시스템(예: 개발은 저장소·CI, 검증은 테스트 관리 도구·이슈 트래커)을 쓰는 별개의 조직입니다. Stage 1은 이 두 조직을 **하나의 프로그램 안의 두 클래스**로 표현합니다.

```text
현실 세계                              Stage 1 구현
-----------------------------------    ---------------------------------------------
개발부서 시스템                    →   DevService 클래스 (services/dev_service.py)
검증부서 시스템                    →   QAService 클래스  (services/qa_service.py)
부서 간 이슈 이관 (메일, 티켓 등)  →   qa.transfer() 반환값을 dev.receive_issues()에 전달
각 부서의 이슈 관리 DB             →   하나의 issues 테이블 (공유)
"누구 차례인가" 조율               →   cycles.status 컬럼 하나 (공유)
업무 절차서                        →   CycleRunner._run() 의 for 루프 (하드코딩)
```

즉 Stage 1은 "부서 경계"가 **클래스 경계**로만 존재하고, 실행·데이터·배포 경계는 없습니다.

---

## 3. 목표와 범위

### 3.1 학습 목표

1. 릴리즈·검증·결함 이관 업무 도메인 자체를 이해한다 (테스트케이스, 숨은 결함, 회귀 결함, 이슈 상태 머신).
2. 가장 단순한 구조(모놀리식, 함수 호출, 공유 DB)로 전체 흐름을 끝까지 동작시켜, 이후 단계의 비교 기준을 만든다.
3. LLM을 "판단"이 아닌 "문서 작성" 역할로 붙이는 방법(구조화 출력, mock 대체)을 익힌다.
4. 업무 흐름이 코드에 고정(하드코딩)되어 있을 때의 한계를 직접 체감한다.

### 3.2 구현 범위

| 구분 | 내용 | 근거 코드 |
| --- | --- | --- |
| 도메인 | 기능 4개, 테스트케이스 12개, 결함 4개, 회귀 규칙 1개, 부서원 5명 | `domain/scenario.py` |
| 시뮬레이터 | 버전 증가, 결함 수정 적용, 테스트 실행 (순수 함수) | `domain/simulator.py` |
| 저장소 | SQLite 스키마 9개 테이블, 기동 시 자동 생성·마이그레이션 | `infra/db.py` |
| 이벤트 | DB 기록 + SSE 실시간 전달 | `infra/events.py` |
| LLM | Claude 구조화 출력 호출, 실패 시 mock 대체 | `infra/llm.py` |
| Dev 업무 | 최초 발행, 이슈 수신, 수정 내역 작성, 차기 버전 발행 | `services/dev_service.py` |
| QA 업무 | 검증 실행, 수정 이슈 확인/재오픈, 실패 이슈 등록, 이관, 승인 | `services/qa_service.py` |
| 데모 | 자동 사이클 실행기, 상태 머신, 중복 실행 방지, 최대 반복 5회 | `services/cycle.py` |
| 실습 | 사람이 진행하는 단계 규칙, 권한·차례 검사, AI 초안 | `services/workbench.py` |
| 웹 | 화면 10종(관제 8 + 작업대 2), JSON API, SSE | `web/app.py`, `web/templates/` |
| UI | 모노톤 디자인 시스템, 다크 모드, 실시간 부분 갱신, 입력 보호 | `web/static/css/app.css`, `web/static/js/*.js` |

### 3.3 범위 밖

- 사용자 인증·로그인 (작업자는 화면 상단에서 스스로 선택하며, 서버는 요청 본문의 이름만 확인)
- 실제 빌드·테스트 실행 (모두 시뮬레이터가 결정)
- 부서별 독립 프로세스·독립 DB·네트워크 통신 (Stage 2 이후의 주제)
- 다중 서버 인스턴스, 외부 메시지 브로커
- 자동화된 테스트 코드(pytest 등) — 점검은 API 호출과 화면 확인으로 수행

---

## 4. 시스템 구성

### 4.1 전체 구조도

```text
 +--------------------------- 브라우저 (여러 창 / 여러 작업자) ---------------------------+
 |  Overview, Releases, Test Runs, Issues, Timeline, 검증 작업대(/qa), 개발 작업대(/dev)   |
 |  app.js (SSE 구독, 부분 갱신, 토스트)   workbench.js (작업자 선택, 작업대 버튼 동작)     |
 +-------------------------------------------+---------------------------------------------+
                         HTTP (GET 화면, POST /api/*)  |  SSE (GET /api/events/stream)
                                             v
 +==================== 단일 프로세스: uvicorn + FastAPI (127.0.0.1:8100) ====================+
 |                                                                                           |
 |  web/app.py  ── 라우트(화면/JSON API/SSE), WorkError → JSON 오류 응답                     |
 |     |  queries.py (조회 쿼리)   ui.py (템플릿 필터)   templates/ (Jinja2)                 |
 |     |                                                                                     |
 |     +──> CycleRunner (데모)  ──함수 호출──> DevService ──┐                                |
 |     |         asyncio.Task        ──함수 호출──> QAService ───┤                           |
 |     |                                                         │ change_status()           |
 |     +──> Workbench (실습)    ──함수 호출──> DevService/QAService (내부 메서드 재사용)     |
 |                                                         │                                 |
 |     EventBus ──(INSERT events)──┐                       │      LLMClient ──> Claude API   |
 |        └── asyncio.Queue × 구독자 수 ──> SSE            │      (mock이면 호출 안 함)      |
 |                                 v                       v                                 |
 |                       +-------------------------------------------+                       |
 |                       |  SQLite  data/app.db  (WAL, 단일 연결)    |                       |
 |                       |  cycles releases test_cases test_runs     |                       |
 |                       |  test_results issues issue_history        |                       |
 |                       |  members events                           |                       |
 |                       +-------------------------------------------+                       |
 +===========================================================================================+
```

### 4.2 프로세스·포트·DB

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 프로세스 | uvicorn 1개, 워커 1개 | `run.py`: `uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")` |
| 바인딩 | `127.0.0.1:8100` (로컬 전용) | `run.py` |
| DB 파일 | `01_standalone/data/app.db` (폴더 자동 생성) | `config.py`의 `load_settings()` |
| DB 연결 | `sqlite3.connect(check_same_thread=False, isolation_level=None)` 단일 연결, autocommit, `PRAGMA journal_mode=WAL` | `infra/db.py`의 `Database.__init__` |
| 백그라운드 작업 | 데모 사이클 1개를 `asyncio.create_task`로 실행 | `services/cycle.py`의 `CycleRunner.start()` |
| 실시간 채널 | SSE, 구독자마다 `asyncio.Queue` 1개 | `infra/events.py` |
| 외부 통신 | LLM 모드에서만 Anthropic API 호출 | `infra/llm.py` |

### 4.3 계층 구조

```text
 web       (표현 계층)   app.py, queries.py, ui.py, templates/, static/
   │                     - HTTP 라우팅, 템플릿 렌더링, JSON 응답, SSE 스트림
   v
 services  (업무 계층)   cycle.py, workbench.py, dev_service.py, qa_service.py, issue_store.py
   │                     - 부서별 업무 규칙, 사이클 상태 머신, 권한·차례 검사
   v
 infra     (기반 계층)   db.py, events.py, llm.py
   │                     - SQLite 접근, 이벤트 기록·전파, Claude 호출
   v
 domain    (도메인)      scenario.py, simulator.py
                         - 고정 시나리오 데이터와 순수 함수. 다른 계층에 의존하지 않음
```

의존 방향은 위에서 아래로만 흐릅니다. `domain`은 어떤 계층도 import하지 않으며(`simulator.py`는 같은 패키지의 `scenario`만 사용), 이 덕분에 같은 시나리오·시뮬레이터를 Stage 2~4에서도 그대로 재사용할 수 있습니다.

### 4.4 객체 조립 (Composition Root)

모든 객체는 `web/app.py`의 `create_app()`에서 한 번에 만들어지고 서로 주입됩니다.

```text
create_app()
  settings = load_settings()                  # .env 로딩, LLM 모드 결정
  db       = Database(settings.db_path)       # 스키마 생성 + 마이그레이션
  bus      = EventBus(db)
  llm      = LLMClient(settings.llm_mode, settings.model)
  dev      = DevService(db, bus, llm, step_delay)
  qa       = QAService(db, bus, llm, step_delay, test_delay)
  qa.seed_test_cases()                        # test_cases 12건 INSERT OR IGNORE
  runner   = CycleRunner(db, bus, dev, qa, llm.label, step_delay)   # 미완료 데모 사이클 정리
  wb       = Workbench(db, bus, dev, qa)
  wb.seed()                                   # members 5명, test_cases.assignee 지정
  queries  = Queries(db)
```

같은 `db`, `bus`, `llm` 인스턴스를 모든 서비스가 공유한다는 점이 모놀리식 구조를 그대로 보여 줍니다.

---

## 5. 소스 구조

### 5.1 전체 트리

```text
01_standalone/
├─ run.py                         진입점. --port, --mock 인자 처리 후 uvicorn 실행
├─ pyproject.toml                 uv 프로젝트 정의 (fastapi, uvicorn, jinja2, anthropic, python-dotenv)
├─ uv.lock                        의존성 고정 (anthropic 1.14.0, fastapi 0.143.1, uvicorn 0.54.0 등)
├─ README.md                      실행 방법, 실습 순서, 구조, API, 점검 결과
├─ data/app.db                    SQLite 파일 (실행 시 자동 생성)
├─ docs/
│  ├─ PRD.md                      요구사항 (F-01~F-16), 데이터 모델, 단계 규칙
│  ├─ TODO.md                     구현·점검 체크리스트
│  ├─ USER_SCENARIO.md            시나리오 0(실습), 1(데모), 2(결과 확인), 3(예외)
│  └─ images/                     overview.png, qa_desk.png, dev_desk.png
└─ src/standalone/
   ├─ __init__.py                 패키지 표식
   ├─ config.py                   .env 탐색(단계 폴더 → 루트), Settings 생성, LLM 모드 결정
   ├─ domain/
   │  ├─ scenario.py              제품명, 기능·테스트케이스·결함·회귀 규칙·심각도·부서원 상수
   │  └─ simulator.py             next_version / initial_defects / apply_fixes / run_test
   ├─ infra/
   │  ├─ db.py                    SCHEMA, MIGRATIONS, Database(all/one/scalar/execute/reset)
   │  ├─ events.py                EventBus(emit/publish/stream)
   │  └─ llm.py                   LLMClient.json(): 구조화 출력 + mock 대체 + 호출 통계
   ├─ services/
   │  ├─ issue_store.py           change_status(): 이슈 상태 변경 + issue_history 기록
   │  ├─ dev_service.py           DevService: 발행·수신·수정·릴리즈 노트 (LLM 2종)
   │  ├─ qa_service.py            QAService: 검증·확인·등록·이관·승인 (LLM 1종)
   │  ├─ cycle.py                 CycleRunner: 데모 업무 흐름 (하드코딩 루프)
   │  └─ workbench.py             Workbench: 실습 모드 단계 규칙, 권한·차례 검사, AI 초안
   └─ web/
      ├─ app.py                   create_app(): 객체 조립, 화면 라우트 10개, JSON API 18개 + SSE 1개
      ├─ queries.py               Queries: 화면용 조회 SQL
      ├─ ui.py                    템플릿 필터(tone, hhmmss, ymdhm, status_label, pct)
      ├─ templates/
      │  ├─ base.html             공통 레이아웃: 사이드바, 상단바(작업자·버튼), 공용 대화상자
      │  ├─ _macros.html          badge, empty, result_bar, pipeline, timeline 매크로
      │  ├─ overview.html         관제 대시보드
      │  ├─ releases.html         버전 목록
      │  ├─ release_detail.html   버전 상세 (릴리즈 노트, 반영 이슈, 검증, 발견 이슈)
      │  ├─ test_runs.html        검증 실행 목록
      │  ├─ test_run_detail.html  테스트 매트릭스와 로그
      │  ├─ issues.html           이슈 목록
      │  ├─ issue_detail.html     이슈 상세와 상태 이력
      │  ├─ timeline.html         이벤트 로그
      │  ├─ qa.html               검증 작업대 (+ 이슈 등록 대화상자)
      │  └─ dev.html              개발 작업대 (+ 수정 완료 대화상자)
      └─ static/
         ├─ css/app.css           모노톤(shadcn 스타일) 디자인 토큰, 다크 모드
         ├─ js/app.js             테마, 토스트, SSE 구독, 부분 갱신, 입력 보호, 공용 버튼
         └─ js/workbench.js       작업자 선택(?as=), 내 항목 필터, 작업대 버튼·대화상자
```

### 5.2 주요 모듈 상세

#### 5.2.1 `config.py` — 설정

- 모듈 import 시점에 `01_standalone/.env`와 루트 `.env`를 차례로 읽습니다(`override=False`이므로 먼저 읽은 값과 OS 환경변수가 우선).
- `load_settings()`가 LLM 모드를 결정합니다.

| `LLM_MODE` | `ANTHROPIC_API_KEY` | 결과 모드 |
| --- | --- | --- |
| `mock` | 무관 | mock |
| `auto` (기본) | 없음 | mock |
| `auto` | 있음 | llm |
| `llm` | 무관 | llm (키가 없으면 호출 실패 후 mock 대체) |

- 그 밖의 설정: `CLAUDE_MODEL`(기본 `claude-opus-5-5`), `STEP_DELAY`(기본 0.4초), `TEST_DELAY`(기본 0.1초).

#### 5.2.2 `domain/scenario.py` — 시나리오 상수

| 상수 | 내용 |
| --- | --- |
| `PRODUCT` | `"Commerce Web"` |
| `INITIAL_VERSION` | `"1.0.0"` |
| `MAX_ITERATIONS` | 5 (데모 최대 검증 횟수) |
| `FEATURES` | login, search, payment, report |
| `TEST_CASES` | TC-01~TC-12 (기능별 3건, 이름 포함) |
| `DEFECTS` | D-101(TC-02), D-102(TC-05), D-103(TC-08), D-201(TC-11) 과 각 실패 로그 |
| `INITIAL_DEFECTS` | `["D-101", "D-102", "D-103"]` |
| `REGRESSIONS` | `{"D-103": "D-201"}` — D-103을 고치면 D-201이 생긴다 |
| `FEATURE_SEVERITY` | mock 심각도: payment=Critical, login/report=Major, search=Minor |
| `SEVERITIES` | Critical, Major, Minor |
| `TEAM` | 김검증(login·search), 이검증(payment), 박검증(report), 최개발(백엔드), 정개발(프론트엔드) |

#### 5.2.3 `domain/simulator.py` — 결정적 시뮬레이터

| 함수 | 입력 → 출력 | 설명 |
| --- | --- | --- |
| `next_version(version)` | `"1.0.0"` → `"1.1.0"` | minor 증가, patch는 0 |
| `initial_defects()` | → `["D-101","D-102","D-103"]` | 최초 빌드의 숨은 결함 |
| `apply_fixes(defects, fixed_tc_ids)` | → `(다음 결함 목록, 새 회귀 결함 목록)` | 수정한 테스트케이스에 영향을 주는 결함 제거, `REGRESSIONS`에 따라 새 결함 추가 |
| `run_test(defects, tc_id)` | → `(passed, log)` | 해당 테스트케이스를 깨뜨리는 결함이 있으면 `(False, 결함 로그)`, 없으면 `(True, "OK")` |

모두 부수효과가 없는 순수 함수라, 같은 입력에는 항상 같은 결과를 냅니다.

#### 5.2.4 `infra/db.py` — `Database`

- `SCHEMA`: 9개 테이블 `CREATE TABLE IF NOT EXISTS`.
- `MIGRATIONS`: 실습 모드에서 추가된 컬럼 목록. `_migrate()`가 `PRAGMA table_info`로 없는 컬럼만 `ALTER TABLE ADD COLUMN` 하고, 예전 데모 전용 DB의 `test_results.result`가 NULL이면 `passed` 값으로 PASS/FAIL을 채웁니다.
- `JSON_COLUMNS = {features, defects, fixed_issue_keys, payload}`: `execute()`는 list/dict 인자를 JSON 문자열로 저장하고, `_decode()`는 조회 시 다시 파이썬 객체로 되돌립니다.
- `reset()`: `sqlite_` 접두사가 아닌 모든 테이블의 행 삭제 + `sqlite_sequence` 초기화.

#### 5.2.5 `infra/events.py` — `EventBus`

| 메서드 | 동작 |
| --- | --- |
| `emit(actor, type, message, payload, cycle_id)` | `events` 테이블에 INSERT 후 `publish()` — 영구 기록되는 업무 이벤트 |
| `publish(event)` | 모든 구독자 큐에 `put_nowait` — DB에 남기지 않는 일시 이벤트(진행률, 상태 변경 알림, 초기화)에 사용 |
| `stream()` | 구독 큐 생성, `retry: 3000` 전송, 15초 동안 이벤트가 없으면 `: keep-alive` 주석 전송, 연결 종료 시 큐 제거 |

#### 5.2.6 `infra/llm.py` — `LLMClient`

- `mode == "llm"`일 때만 `anthropic.AsyncAnthropic()` 클라이언트를 만듭니다.
- `json(system, prompt, schema, fallback)`이 유일한 호출 창구이며 `(결과 dict, "llm" | "mock")`을 반환합니다. 자세한 내용은 10장.
- 통계: `calls`, `failures`, `last_error` — `/api/state`로 노출.

#### 5.2.7 `services/issue_store.py` — `change_status()`

```python
def change_status(db, issue, to_status, actor, note="", **fields) -> None
```

이슈의 `status`, `updated_at`과 추가 필드(`fixed_in_version`, `fix_note`, `assignee` 등)를 UPDATE하고, `issue_history`에 (from, to, actor, note, ts)를 INSERT한 뒤 메모리의 `issue` dict도 갱신합니다. Dev와 QA가 **같은 함수로 같은 테이블**을 고친다는 점이 Stage 1의 공유 구조를 상징합니다.

#### 5.2.8 `services/dev_service.py` — `DevService`

| 메서드 | 역할 | LLM |
| --- | --- | --- |
| `release_initial(cycle_id)` | v1.0.0 발행. 결함 `initial_defects()`, 고정 문구 릴리즈 노트 | 없음 |
| `receive_issues(cycle_id, issues)` | 이관받은 이슈를 모두 `IN_PROGRESS`로 변경, `issues.received` 이벤트 | 없음 |
| `fix_and_release(cycle_id, issues)` | 차기 버전 계산 → 이슈별 수정 내역 작성·`FIXED` → `apply_fixes` → 회귀 시 `Simulator` 이벤트 → 릴리즈 노트 작성 → 릴리즈 저장 | 수정 내역, 릴리즈 노트 |
| `_save_release(...)` | `releases` INSERT | - |
| `_write_fix_note(issue)` | `FIX_SCHEMA`로 수정 내역 1~2문장 | 사용 |
| `_write_release_note(version, issues)` | `NOTE_SCHEMA`로 릴리즈 노트(5줄 이내) | 사용 |

#### 5.2.9 `services/qa_service.py` — `QAService`

| 메서드 | 역할 | LLM |
| --- | --- | --- |
| `seed_test_cases()` | `test_cases` 12건 `INSERT OR IGNORE` | - |
| `verify(cycle_id, version)` | `test_runs` 생성 → 12건 `run_test` → `test_results` 기록·진행률 발행 → 완료 → 수정 이슈 확인 → 실패 이슈 등록 | 실패 분석 |
| `_verify_fixed_issues(...)` | 이번 버전에 반영된 `FIXED` 이슈의 테스트가 통과하면 `VERIFIED`, 아니면 `REOPENED` | - |
| `_register_failures(...)` | 실패 테스트마다, 같은 테스트케이스에 미해결 이슈(`ACTIVE_STATUSES`)가 없으면 새 이슈 등록 | 사용 |
| `_analyze(failure, version)` | `ISSUE_SCHEMA`로 제목·설명·심각도 생성 | 사용 |
| `transfer(cycle_id)` | `OPEN`/`REOPENED` 이슈를 `TRANSFERRED`로 바꾸고 목록 반환 | - |
| `approve(cycle_id, version, run_id)` | `release.approved` 이벤트 발행 | - |

`ACTIVE_STATUSES = ("OPEN", "TRANSFERRED", "IN_PROGRESS", "REOPENED")` — 중복 이슈 판단 기준입니다.

#### 5.2.10 `services/cycle.py` — `CycleRunner` (데모)

- `STAGES = [RELEASING, VERIFYING, TRANSFERRING, FIXING, APPROVED]`: 대시보드 파이프라인 표시용.
- `ACTIVE = (RELEASING, VERIFYING, TRANSFERRING, FIXING)`: 진행 중 상태.
- 생성자: 이전 프로세스에서 끝나지 못한 **데모** 사이클을 `CANCELLED`로 정리.
- `start()`: 진행 중인 사이클(데모·실습 모두)이 있으면 `CycleAlreadyRunning`, 없으면 사이클 행을 만들고 `_run()`을 백그라운드 태스크로 시작.
- `stop()`: 앱 종료(lifespan) 시 태스크 취소.
- `_set()`: 사이클 상태 UPDATE + `cycle.status` 일시 이벤트 publish.
- `_run()`: 업무 흐름 자체. 8.1절에서 상세히 설명합니다.

#### 5.2.11 `services/workbench.py` — `Workbench` (실습)

| 구분 | 메서드 | 부서 | 허용 사이클 상태 |
| --- | --- | --- | --- |
| 기준 데이터 | `seed()`, `members()` | - | - |
| 사이클 | `start(llm_label, demo_running)` | 누구나 | 진행 중 사이클 없음 |
| 사이클 | `abort()` | 누구나 | 진행 중인 실습 사이클 |
| Dev | `release(actor, note)` | Dev | RELEASING, FIXING |
| QA | `execute(result_id, actor)` | QA | VERIFYING |
| QA | `judge(result_id, actor, result)` | QA | VERIFYING |
| QA | `create_issue(actor, data)` | QA | VERIFYING |
| QA | `verify_issue(key, actor, ok)` | QA | VERIFYING |
| QA | `transfer(actor)` | QA | VERIFYING (+ `can_transfer`) |
| QA | `approve(actor)` | QA | VERIFYING (+ `can_approve`) |
| Dev | `start_fix(key, actor)` | Dev | FIXING |
| Dev | `fix(key, actor, fix_note)` | Dev | FIXING |
| AI 초안 | `draft_issue(result_id, actor)` | QA | VERIFYING |
| AI 초안 | `draft_fix(key, actor)` | Dev | FIXING |
| AI 초안 | `draft_release(actor)` | Dev | RELEASING, FIXING |

실습 시작·중단은 데모 실행·초기화와 같은 릴리즈 매니저 조작으로 보고 작업자를 검사하지 않습니다. 반면 AI 초안은 부서 업무의 일부이고 LLM 호출 비용이 드므로, 다른 작업과 마찬가지로 해당 부서 작업자만 요청할 수 있습니다.

보조 메서드: `_member()`(부서 검사, 403), `_cycle()`(차례 검사, 409), `_result()`(현재 검증 버전의 항목인지, 404), `shippable()`(수정 완료·미발행 이슈), `next_version()`, `current_run()`, `readiness()`(검증 완료 조건), `_blocker()`(차단 사유 문구), `_dev_issue()`(수정 가능 상태 검사).

AI 초안은 `self.qa._analyze`, `self.dev._write_fix_note`, `self.dev._write_release_note` 등 **다른 서비스의 내부(밑줄) 메서드를 직접 호출**합니다. 같은 프로세스라 가능한 일이며, 결합도가 높다는 증거이기도 합니다.

#### 5.2.12 `web/app.py`, `queries.py`, `ui.py`

- `render()`: 모든 화면에 공통 컨텍스트(단계 정보, 메뉴, LLM 라벨, 데모 실행 여부, 진행 중 실습 사이클, 차례 `TURN`, 부서원 목록, `?cycle=` 유지 쿼리)를 넣습니다.
- `TURN = {RELEASING: Dev, FIXING: Dev, VERIFYING: QA, TRANSFERRING: QA}` — 사이드바 "차례" 배지 표시용.
- `work_cycle()`: 작업대는 진행 중 실습 사이클, 없으면 가장 최근 실습 사이클을 보여 줍니다.
- `@app.exception_handler(WorkError)`: `{"error": 메시지}`와 `WorkError.status`(400/403/404/409)로 응답.
- `queries.py`: 사이클 목록(릴리즈·이슈 수 서브쿼리), 릴리즈와 테스트 실행 LEFT JOIN, 통계(`stats`) 등 조회 전용 SQL.
- `ui.py`: 상태값 → 배지 톤(`TONES`), 상태값 → 한글 라벨(`STATUS_LABELS`), 시각 포맷, 통과율 계산.

#### 5.2.13 `web/static/js/app.js`, `workbench.js`

| 파일 | 주요 기능 |
| --- | --- |
| `app.js` | 테마 전환(localStorage), 토스트, SSE 연결(`EventSource`), 이벤트 유형별 처리(진행률 표시·타임라인 추가·토스트·부분 갱신), `isEditing()` 입력 보호, `data-live` 영역 교체, `data-preserve` 값 보존, 데모 실행·초기화 버튼, 대화상자 열기/닫기, `apiPost()` |
| `workbench.js` | 작업자 선택(`?as=` 처리, localStorage 기억), 부서 불일치 경고, 내 항목/전체 필터, 검증 작업대 버튼(실행·PASS·FAIL·AI 초안·등록·수정 확인·재현·이관·확정), 개발 작업대 버튼(착수·수정 완료·AI 초안·발행), 확인 대화상자, 버튼 busy 처리 |

---

## 6. 데이터 모델

### 6.1 테이블 정의

`01_standalone/src/standalone/infra/db.py`의 `SCHEMA`와 `MIGRATIONS`를 합친 최종 구조입니다. (M) 표시는 실습 모드에서 추가된 마이그레이션 컬럼입니다.

#### cycles — 릴리즈 사이클

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | INTEGER PK | 사이클 번호 |
| status | TEXT | RELEASING / VERIFYING / TRANSFERRING / FIXING / APPROVED / FAILED / CANCELLED |
| iteration | INTEGER | 검증 회차 (데모: 1부터, 실습: 0에서 시작해 발행마다 +1) |
| current_version | TEXT | 현재 검증 대상 버전 |
| llm_mode | TEXT | 실행 당시 LLM 라벨 (`claude-opus-5-5` 또는 `mock`) |
| started_at / finished_at | TEXT | ISO 시각 (초 단위) |
| mode (M) | TEXT | `demo` / `manual` (기본 `demo`) |

#### releases — 발행된 버전

| 컬럼 | 설명 |
| --- | --- |
| id, cycle_id, version | `UNIQUE (cycle_id, version)` |
| features | JSON. 기능 목록 |
| defects | JSON. **숨은 결함 목록** (개발자는 모르는 시뮬레이터 정보) |
| fixed_issue_keys | JSON. 이 버전에 반영된 이슈 키 |
| release_note | 릴리즈 노트 본문 |
| released_at | 발행 시각 |
| released_by (M) | 발행자 이름 (실습 모드만 기록) |

#### test_cases — 테스트케이스 (기준 데이터)

| 컬럼 | 설명 |
| --- | --- |
| id | TC-01 ~ TC-12 |
| feature, name | 기능, 이름 |
| assignee (M) | 담당 검증부서원 (`Workbench.seed()`가 기능별로 지정) |

#### test_runs — 버전별 검증 실행

| 컬럼 | 설명 |
| --- | --- |
| id, cycle_id, version | |
| total, passed, failed | 집계 (데모는 테스트마다, 실습은 판정마다 갱신) |
| status | RUNNING → DONE |
| started_at / finished_at | |

#### test_results — 테스트 항목 결과

| 컬럼 | 설명 |
| --- | --- |
| id, run_id, test_case_id | |
| passed | 0/1 |
| log | 실패 로그 또는 `OK` |
| result (M) | PENDING / PASS / FAIL (실습에서 사람의 판정) |
| assignee (M) | 담당자 (실습) |
| sim_log (M) | `실행` 버튼의 참고 결과 (`"PASS\nOK"` 또는 `"FAIL\n로그"`) |
| checked_by (M) | 판정자 (데모는 `Demo`) |
| checked_at (M) | 판정 시각 |

#### issues — 결함 이슈 (Dev·QA 공유)

| 컬럼 | 설명 |
| --- | --- |
| id, cycle_id, key | `ISSUE-n`, `UNIQUE (cycle_id, key)` |
| test_case_id | 연결 테스트케이스 |
| version_found | 발견 버전 |
| title, description, severity | LLM 또는 사람이 작성 |
| status | OPEN / TRANSFERRED / IN_PROGRESS / FIXED / VERIFIED / REOPENED |
| fixed_in_version | 수정이 반영된(될) 버전 |
| fix_note | 수정 내역 |
| created_at / updated_at | |
| reporter (M) | 등록자 (실습) |
| assignee (M) | 수정 담당 개발자 (실습) |

#### issue_history — 이슈 상태 이력

| 컬럼 | 설명 |
| --- | --- |
| id, issue_id | `issues.id` 참조 |
| from_status, to_status | 최초 등록은 from이 NULL |
| actor | 데모: `QA`/`Dev`, 실습: 부서원 이름 |
| note, ts | 메모와 시각 |

#### members — 부서원 (실습)

| 컬럼 | 설명 |
| --- | --- |
| name (PK), dept(`QA`/`Dev`), role, features(JSON, 담당 기능 목록) | `scenario.TEAM`에서 시드 |

#### events — 업무 이벤트 로그

| 컬럼 | 설명 |
| --- | --- |
| id, ts, cycle_id | |
| actor | Cycle / Dev / QA / Simulator / Workbench / `QA 김검증` 형태 |
| type | 이벤트 유형 (7.3절) |
| message | 사람이 읽는 문장 |
| payload | JSON |

### 6.2 엔터티 관계

```text
cycles 1 ──< releases        (cycle_id, version 단위)
cycles 1 ──< test_runs 1 ──< test_results >── 1 test_cases >── members (assignee 이름)
cycles 1 ──< issues    1 ──< issue_history
cycles 1 ──< events
releases.version  ≈ test_runs.version ≈ issues.version_found / fixed_in_version (문자열로 연결)
```

외래키 제약은 선언하지 않았고, 코드가 `cycle_id`와 버전 문자열로 관계를 맞춥니다.

### 6.3 사이클 상태 전이

```text
데모 (CycleRunner)
                                  failed == 0
  [RELEASING] ──> [VERIFYING] ─────────────────────────────> [APPROVED]
                     ^   │ failed > 0
                     │   v
                     │ [TRANSFERRING] ── iteration == 5 ──> [FAILED]
                     │   │
                     │   v
                     └─[FIXING]

  어느 단계든: 예외 발생 → FAILED (cycle.error) / 태스크 취소·재기동 정리 → CANCELLED

실습 (Workbench)
  [RELEASING] ──release()──> [VERIFYING] ──approve()──> [APPROVED]
   (iteration 0)              ^   │ transfer()
                              │   v
                              │ [TRANSFERRING]  (같은 함수 안에서 곧바로 FIXING으로 바뀌는 일시 상태)
                              │   │
                              │   v
                              └─[FIXING] ──release()──┘

  진행 중 어느 상태든: abort() → [CANCELLED]
```

### 6.4 테스트 실행·결과 상태

| 대상 | 데모 | 실습 |
| --- | --- | --- |
| test_runs.status | `verify()` 시작 시 RUNNING, 12건 완료 즉시 DONE | 발행 시 RUNNING, `transfer()`/`approve()` 시 DONE |
| test_results.result | 생성 즉시 PASS/FAIL | 발행 시 PENDING → 판정 PASS/FAIL (재판정 가능), 이슈 등록 시 FAIL |

### 6.5 이슈 상태 전이

```text
                 (등록)
                   │
                   v
                [OPEN] ──transfer──> [TRANSFERRED] ──receive/start_fix──> [IN_PROGRESS]
                   ^                       │                                  │
                   │                       └──────── fix (실습만 직접 가능) ───┤
                   │                                                          v
            [REOPENED] <──재현(테스트 실패)── [FIXED] <──────── fix ──────────┘
                   │                          │
                   └──transfer──> [TRANSFERRED]│ 수정 확인(테스트 통과)
                                              v
                                         [VERIFIED]
```

| 전이 | 데모에서 일어나는 곳 | 실습에서 일어나는 곳 | 이력 actor |
| --- | --- | --- | --- |
| (없음) → OPEN | `QAService._register_failures` | `Workbench.create_issue` | QA / 등록자 이름 |
| OPEN, REOPENED → TRANSFERRED | `QAService.transfer` | `Workbench.transfer` | QA / 이관자 |
| TRANSFERRED → IN_PROGRESS | `DevService.receive_issues` | `Workbench.start_fix` (IN_PROGRESS에서 다시 누르면 담당자 변경 "내가 맡기") | Dev / 개발자 |
| TRANSFERRED, IN_PROGRESS → FIXED | `DevService.fix_and_release` (`fixed_in_version` 함께 지정) | `Workbench.fix` (`fixed_in_version`은 NULL, 발행 시 지정) | Dev / 개발자 |
| FIXED → VERIFIED | `QAService._verify_fixed_issues` (자동) | `Workbench.verify_issue(ok=True)` | QA / 확인자 |
| FIXED → REOPENED | `QAService._verify_fixed_issues` (자동) | `Workbench.verify_issue(ok=False)` | QA / 확인자 |

실습 모드에서 발행 시 `fixed_in_version`을 채우는 UPDATE는 `change_status()`를 거치지 않으므로 이력 행이 추가되지 않습니다(상태가 바뀌지 않기 때문).

---

## 7. 인터페이스

### 7.1 화면 라우트 (GET)

| Path | 템플릿 | 목적 | 비고 |
| --- | --- | --- | --- |
| `/` | overview.html | 관제 대시보드 | `?cycle=N`으로 과거 사이클 조회 |
| `/releases` | releases.html | 버전 목록과 검증 결과 | `?cycle=` 지원 |
| `/releases/{version}` | release_detail.html | 릴리즈 노트, 반영 이슈, 검증 실행, 발견 이슈 | 없으면 404 |
| `/test-runs` | test_runs.html | 검증 실행 목록 | `?cycle=` 지원 |
| `/test-runs/{run_id}` | test_run_detail.html | 테스트 매트릭스, 항목별 결과·판정자·로그 | 없으면 404 |
| `/issues` | issues.html | 이슈 목록 | `?cycle=` 지원 |
| `/issues/{key}` | issue_detail.html | 설명, 수정 내역, 상태 이력, 메타 정보 | 없으면 404 |
| `/timeline` | timeline.html | 사이클 이벤트 전체(최대 500건, 최신순) | `?cycle=` 지원 |
| `/qa` | qa.html | 검증 작업대 | `?as=이름` (JS가 작업자 지정) |
| `/dev` | dev.html | 개발 작업대 | `?as=이름` |
| `/static/*` | - | CSS/JS 정적 파일 | StaticFiles |

### 7.2 JSON API

#### 공통·데모

| Method | Path | 목적 | 응답 / 오류 |
| --- | --- | --- | --- |
| POST | `/api/cycles` | 데모 실행 | `{"cycle_id"}` / 진행 중 사이클(데모·실습)이 있으면 409 |
| POST | `/api/reset` | 전체 데이터 초기화 후 기준 데이터 재시드, `system.reset` 발행 | `{"ok": true}` / 진행 중인 사이클(데모·실습)이 있으면 409 |
| GET | `/api/state` | 데모 실행 여부, 최근 사이클, 진행 중 실습, LLM 통계 | `{running, cycle, manual, llm:{mode, model, calls, failures, last_error}}` |
| GET | `/api/cycles/{id}/summary` | 사이클 결과 요약(점검용): 버전별 PASS/FAIL, 이슈 상태 | 없으면 404 |
| GET | `/api/events/stream` | SSE 스트림 | `text/event-stream` |

#### 실습 (요청 본문 `actor`에 작업자 이름)

| Method | Path | 부서 | 본문 | 응답 |
| --- | --- | --- | --- | --- |
| POST | `/api/work/start` | - | 없음 | `{"cycle_id"}` |
| POST | `/api/work/abort` | - | 없음 | `{"ok": true}` |
| POST | `/api/work/release` | Dev | `{actor, release_note}` | `{"version"}` |
| POST | `/api/work/results/{id}/execute` | QA | `{actor}` | `{"passed", "log"}` |
| POST | `/api/work/results/{id}/judge` | QA | `{actor, result: "PASS"/"FAIL"}` | `{"ok": true}` |
| POST | `/api/work/issues` | QA | `{actor, result_id, title, severity, description}` | `{"key", "created"}` |
| POST | `/api/work/issues/{key}/verify` | QA | `{actor, ok}` | `{"ok": true}` |
| POST | `/api/work/transfer` | QA | `{actor}` | `{"transferred": [keys]}` |
| POST | `/api/work/approve` | QA | `{actor}` | `{"ok": true}` |
| POST | `/api/work/issues/{key}/start` | Dev | `{actor}` | `{"ok": true}` |
| POST | `/api/work/issues/{key}/fix` | Dev | `{actor, fix_note}` | `{"ok": true}` |
| POST | `/api/work/draft/issue/{result_id}` | QA | `{actor}` | `{"title", "description", "severity", "source"}` |
| POST | `/api/work/draft/fix/{key}` | Dev | `{actor}` | `{"fix_note"}` |
| POST | `/api/work/draft/release` | Dev | `{actor}` | `{"release_note"}` (반영할 이슈가 없으면 빈 문자열) |

`/api/work/start`·`/api/work/abort`는 릴리즈 매니저 조작이라 작업자를 받지 않습니다(5.2.11절).

#### 오류 응답 규칙 (`WorkError`)

| 상태 코드 | 의미 | 예시 메시지 |
| --- | --- | --- |
| 400 | 입력·조건 불충족 | "판정하지 않은 항목이 n건 있습니다.", "반영할 수정 완료 이슈가 없습니다…", "이슈 제목을 입력하세요." |
| 403 | 작업자 미선택 또는 부서 불일치 | "상단에서 작업자를 선택하세요.", "검증부서 작업자만 할 수 있는 작업입니다. (현재: Dev 최개발)" |
| 404 | 현재 검증 버전의 항목이 아님 | "현재 검증 중인 버전의 항목이 아닙니다." |
| 409 | 진행 중 실습이 없거나 해당 부서 차례가 아님, 중복 시작 | "지금은 이 작업을 할 수 없습니다. (현재 단계: FIXING)" |

### 7.3 SSE 이벤트

SSE 메시지는 `data: {JSON}` 형식이며, JSON에는 `ts, cycle_id, actor, type, message, payload`와 (DB 기록 이벤트인 경우) `id`가 들어 있습니다.

#### DB에 기록되는 업무 이벤트 (`emit`)

| type | 발행 주체 | 모드 | 의미 | 토스트 |
| --- | --- | --- | --- | --- |
| cycle.started | Cycle / Workbench | 데모 / 실습 | 사이클 시작 | - |
| release.published | Dev / `Dev 이름` | 공통 | 버전 발행 | 릴리즈 발행 |
| test_run.started | QA | 데모 | 검증 시작 | - |
| test_run.finished | QA / `QA 이름` | 공통 | 검증 완료 (n PASS / m FAIL) | - |
| test.judged | `QA 이름` | 실습 | 항목 판정, 또는 기존 이슈에 연결 | - |
| issue.created | QA / `QA 이름` | 공통 | 이슈 등록 | 이슈 등록 |
| issue.verified | QA / `QA 이름` | 공통 | 수정 확인 | - |
| issue.reopened | QA / `QA 이름` | 공통 | 재현, 재오픈 | - |
| issues.transferred | QA / `QA 이름` | 공통 | Dev 이관 | 이슈 이관 |
| issues.received | Dev | 데모 | 이슈 수신·착수 | - |
| issue.started | `Dev 이름` | 실습 | 이슈 착수 | - |
| issue.fixed | Dev / `Dev 이름` | 공통 | 수정 완료 | 수정 완료 |
| regression.introduced | Simulator | 공통 | 숨은 회귀 결함 발생 (학습용) | - |
| release.approved | QA / `QA 이름` | 공통 | 승인 / 버전 확정 | 릴리즈 승인 |
| cycle.completed | Cycle / Workbench | 공통 | 사이클 완료 | 사이클 완료 |
| cycle.failed | Cycle | 데모 | 최대 반복 초과 | 사이클 실패 |
| cycle.error | Cycle | 데모 | 예상하지 못한 오류 | 오류 |
| cycle.cancelled | Workbench | 실습 | 실습 중단 | 사이클 중단 |

#### DB에 남기지 않는 일시 이벤트 (`publish`)

| type | 발행 주체 | 브라우저 처리 |
| --- | --- | --- |
| test.progress | QA (데모 검증 중 테스트마다) | 상단 진행 표시 "테스트 실행 5/12 · TC-05 FAIL" |
| cycle.status | Cycle / Workbench (`_set`) | 부분 갱신 |
| system.reset | System (`/api/reset`) | 현재 경로로 새로고침 |

브라우저(`app.js`)는 `test.progress`면 진행 표시만, `system.reset`이면 새로고침, `id`가 있으면 타임라인 맨 위에 추가, 토스트 대상이면 토스트를 띄운 뒤 350ms 후 부분 갱신을 예약합니다.

---

## 8. 동작 흐름

### 8.1 데모 모드

#### 8.1.1 시작

```text
브라우저 [데모 실행] ─POST /api/cycles─> app.start_cycle()
  └─ CycleRunner.start()
       ├─ running 이거나 status IN ACTIVE 인 사이클 존재 → CycleAlreadyRunning → 409
       ├─ INSERT cycles (status='RELEASING', iteration=1, llm_mode, mode='demo')
       └─ asyncio.create_task(_run(cycle_id))  → 즉시 {"cycle_id"} 응답
```

이후 모든 진행은 서버의 백그라운드 태스크 안에서 일어나며, 브라우저는 SSE로 결과만 받습니다.

#### 8.1.2 전체 시퀀스

```text
CycleRunner._run(cycle_id)
│
├─ emit cycle.started
├─ dev.release_initial()                                  ── v1.0.0
│
├─ [iteration 1] _set(VERIFYING, iteration=1, current_version=1.0.0)
│   ├─ qa.verify(1.0.0)  → 9 PASS / 3 FAIL
│   │    ├─ _verify_fixed_issues : 대상 없음
│   │    └─ _register_failures   : ISSUE-1(TC-02), ISSUE-2(TC-05), ISSUE-3(TC-08)  [LLM 3회]
│   ├─ _set(TRANSFERRING) → qa.transfer() → dev.receive_issues()
│   └─ _set(FIXING) → dev.fix_and_release()               ── v1.1.0         [LLM 3+1회]
│        └─ apply_fixes: D-101, D-102, D-103 제거 / D-103 → D-201 회귀 추가
│
├─ [iteration 2] _set(VERIFYING, iteration=2, current_version=1.1.0)
│   ├─ qa.verify(1.1.0)  → 11 PASS / 1 FAIL
│   │    ├─ _verify_fixed_issues : ISSUE-1, 2, 3 → VERIFIED
│   │    └─ _register_failures   : ISSUE-4(TC-11)                               [LLM 1회]
│   ├─ _set(TRANSFERRING) → qa.transfer() → dev.receive_issues()
│   └─ _set(FIXING) → dev.fix_and_release()               ── v1.2.0         [LLM 1+1회]
│        └─ apply_fixes: D-201 제거 / 회귀 없음 → 결함 0
│
└─ [iteration 3] _set(VERIFYING, iteration=3, current_version=1.2.0)
    ├─ qa.verify(1.2.0)  → 12 PASS / 0 FAIL
    │    └─ _verify_fixed_issues : ISSUE-4 → VERIFIED
    ├─ qa.approve()  → emit release.approved
    ├─ _set(APPROVED, finished_at)
    └─ emit cycle.completed  ("v1.2.0 승인 (반복 3회)")
```

각 단계 사이에는 `STEP_DELAY`(기본 0.4초), 테스트 1건마다 `TEST_DELAY`(기본 0.1초)의 `asyncio.sleep`이 들어가 화면에서 흐름을 눈으로 볼 수 있습니다. 측정 시에는 두 값을 0으로 두었습니다.

#### 8.1.3 v1.0.0 단계 상세

| 순서 | 호출 | DB 변경 | 이벤트 |
| --- | --- | --- | --- |
| 1 | `_run` 진입 | - | `cycle.started` "릴리즈 사이클 #N 시작 (LLM: …)" |
| 2 | `dev.release_initial` | releases INSERT: v1.0.0, defects `[D-101, D-102, D-103]`, fixed `[]`, 고정 문구 노트 | `release.published` |
| 3 | `_set(VERIFYING)` | cycles.status, iteration=1, current_version=1.0.0 | `cycle.status` (일시) |
| 4 | `qa.verify` 시작 | test_runs INSERT (RUNNING, total 12) | `test_run.started` |
| 5 | 12회 반복 `simulator.run_test` | test_results INSERT ×12 (checked_by=`Demo`), test_runs 집계 UPDATE ×12 | `test.progress` ×12 (일시) |
| 6 | 검증 종료 | test_runs status=DONE | `test_run.finished` "9 PASS / 3 FAIL" |
| 7 | `_verify_fixed_issues` | 없음 (FIXED 이슈 없음) | - |
| 8 | `_register_failures` → `_analyze` ×3 | issues INSERT ×3 (OPEN), issue_history ×3 | `issue.created` ×3 |
| 9 | `_set(TRANSFERRING)` | cycles.status | `cycle.status` |
| 10 | `qa.transfer` | issues 3건 → TRANSFERRED, 이력 ×3 | `issues.transferred` |
| 11 | `dev.receive_issues` | issues 3건 → IN_PROGRESS, 이력 ×3 | `issues.received` |
| 12 | `_set(FIXING)` | cycles.status | `cycle.status` |
| 13 | `dev.fix_and_release` → `_write_fix_note` ×3 | issues 3건 → FIXED, fixed_in_version=1.1.0, fix_note, 이력 ×3 | `issue.fixed` ×3 |
| 14 | `simulator.apply_fixes` | (계산만) 결과 defects `[D-201]`, introduced `[D-201]` | `regression.introduced` (actor `Simulator`) |
| 15 | `_write_release_note` → `_save_release` | releases INSERT: v1.1.0, defects `[D-201]`, fixed `[ISSUE-1, 2, 3]` | `release.published` |

#### 8.1.4 v1.1.0 단계 상세

| 순서 | 호출 | DB 변경 | 이벤트 |
| --- | --- | --- | --- |
| 1 | `_set(VERIFYING)` | iteration=2, current_version=1.1.0 | `cycle.status` |
| 2 | `qa.verify` | test_runs #2, test_results ×12 (TC-11만 FAIL) | `test_run.started`, `test.progress` ×12, `test_run.finished` "11 PASS / 1 FAIL" |
| 3 | `_verify_fixed_issues` | ISSUE-1~3: 각 테스트(TC-02, 05, 08) PASS → VERIFIED | `issue.verified` ×3 |
| 4 | `_register_failures` | TC-11: 미해결 이슈 없음 → ISSUE-4 OPEN (LLM 분석) | `issue.created` |
| 5 | 이관·수신 | ISSUE-4 → TRANSFERRED → IN_PROGRESS | `issues.transferred`, `issues.received` |
| 6 | `fix_and_release` | ISSUE-4 → FIXED (fixed_in_version=1.2.0), releases v1.2.0 defects `[]` | `issue.fixed`, `release.published` |

#### 8.1.5 v1.2.0 단계 상세

| 순서 | 호출 | DB 변경 | 이벤트 |
| --- | --- | --- | --- |
| 1 | `_set(VERIFYING)` | iteration=3, current_version=1.2.0 | `cycle.status` |
| 2 | `qa.verify` | test_runs #3, 12건 모두 PASS | `test_run.finished` "12 PASS / 0 FAIL" |
| 3 | `_verify_fixed_issues` | ISSUE-4 → VERIFIED | `issue.verified` |
| 4 | `_register_failures` | 실패 없음 | - |
| 5 | `qa.approve` | - | `release.approved` |
| 6 | `_set(APPROVED)` | status=APPROVED, finished_at | `cycle.status` |
| 7 | 종료 | - | `cycle.completed` |

#### 8.1.6 데모 결과 데이터 요약

| 이슈 | 테스트 | 발견 | 수정 버전 | 최종 상태 | mock 심각도 | 이력 행 수 |
| --- | --- | --- | --- | --- | --- | --- |
| ISSUE-1 | TC-02 세션 유지 | v1.0.0 | v1.1.0 | VERIFIED | Major | 5 |
| ISSUE-2 | TC-05 페이지네이션 | v1.0.0 | v1.1.0 | VERIFIED | Minor | 5 |
| ISSUE-3 | TC-08 환불 금액 계산 | v1.0.0 | v1.1.0 | VERIFIED | Critical | 5 |
| ISSUE-4 | TC-11 결제 합계 리포트 | v1.1.0 | v1.2.0 | VERIFIED | Major | 5 |

LLM 모드에서는 심각도를 LLM이 정하므로 위 mock 심각도와 다를 수 있지만, 이슈 개수·테스트·버전·상태는 동일합니다.

### 8.2 실습 모드

#### 8.2.1 사용자 관점

준비: 브라우저 창 여러 개를 열어 각각 `/qa?as=김검증`, `/qa?as=이검증`, `/qa?as=박검증`, `/dev?as=최개발`, `/dev?as=정개발`로 접속하면 실제 협업처럼 진행할 수 있습니다. 작업자는 브라우저(localStorage)에 기억됩니다.

| 단계 | 담당 | 사용자 행동 | 화면 반응 |
| --- | --- | --- | --- |
| 1 | 누구나 | 상단 **실습 시작** | 토스트 후 개발 작업대로 이동, 사이드바 "개발 작업대"에 "차례" 배지, 데모 실행 버튼 비활성화 |
| 2 | 최개발 | 개발 작업대 **v1.0.0 발행** → 확인 (노트는 비워도 됨) | 모든 창에서 "검증 작업대"로 차례 이동, 12개 항목이 "대기" |
| 3 | 김검증 | **내 항목** 탭 → TC-01 **실행** → 토스트 "실행 결과: PASS" → **PASS** | 판정 진행 1/12, 다른 창에도 실시간 반영 |
| 4 | 김검증 | TC-02 **실행** → FAIL 로그 확인 → **FAIL** | 이슈 등록 대화상자: 제목 `[login] 세션 유지 실패`, 설명에 실행 로그가 미리 채워짐 |
| 5 | 김검증 | **AI 초안** → 제목·설명·심각도 채워짐 → **등록 후 FAIL 처리** | ISSUE-1 등록, TC-02 판정 FAIL과 이슈 링크 표시 |
| 6 | 이검증·박검증 | 각자 담당 항목 판정, TC-08은 FAIL + 이슈 등록 | 하단 "검증 완료" 바의 판정 대기 수 감소 |
| 7 | 검증부서원 | 판정 대기 0건이 되면 **Dev 이관** 활성화 → 확인 | 이슈 3건 TRANSFERRED, 차례가 개발로 이동 |
| 8 | 최개발·정개발 | 이슈마다 **착수** → **수정 완료** (수정 내역 직접 입력 또는 AI 초안) | 상태 FIXED, "발행 대기" 표시, 발행 카드에 "수정 완료된 이슈 n건이 반영됩니다" |
| 9 | 정개발 | 릴리즈 노트 **AI 초안** → **v1.1.0 발행** | 검증 차례, "수정 확인" 카드에 ISSUE-1~3 |
| 10 | 검증부서원 | 12개 항목 재판정, ISSUE-1~3 **수정 확인**, TC-11(회귀) FAIL + 이슈 등록 → **Dev 이관** | ISSUE-4 등록·이관 |
| 11 | 개발부서원 | ISSUE-4 수정 → **v1.2.0 발행** | 검증 차례 |
| 12 | 검증부서원 | 전 항목 PASS, ISSUE-4 **수정 확인** → **버전 확정** | 사이클 "승인", v1.2.0 확정, 토스트 "릴리즈 승인" "사이클 완료" |

예외 상황에서 사용자가 보는 반응:

| 상황 | 화면 반응 |
| --- | --- |
| 작업자 미선택 / 다른 부서원으로 작업대 진입 | 작업대 상단 "작업자 확인" 배너 (JS), 버튼을 눌러도 서버가 403, 토스트 "처리할 수 없음" |
| 판정이 남았는데 이관·확정 | 버튼 비활성화, 하단 바에 대기 수 표시 (서버도 400) |
| 미해결 이슈가 있는데 확정 | 확정 버튼 비활성화 (서버 400 "미해결 이슈가 n건 있어 확정할 수 없습니다. 개발부서로 이관하세요.") |
| 실행 결과는 FAIL인데 PASS 판정 | 판정 칸에 "실행 결과와 다름". 판정은 존중되고, 결함은 다음 버전에도 남음 |
| 일부 이슈만 수정하고 발행 | 발행 카드에 "미완료 n건은 다음 버전으로 넘어갑니다". 다음 검증에서 해당 항목의 FAIL은 기존 이슈에 연결되고, 하단 바에 "개발부서 미완료 n건"이 표시되며 **Dev 이관**으로 다시 개발부서 차례가 됨 (상태·담당자 유지) |
| 미해결 이슈가 있는 항목을 FAIL 처리 | 이슈 등록 대화상자에 "연결" 안내, 등록하면 기존 이슈에 연결 (토스트 "ISSUE-n에 연결") |
| 수정 확인 대기(FIXED) 이슈가 있는 항목을 FAIL 처리 | 대화상자에 "수정 확인 대기" 안내: 수정이 안 되었으면 '수정 확인' 카드의 **재현**을 쓰도록 유도 (여기서 등록하면 새 이슈 생성) |
| 실습 중 데모 실행·초기화 | 두 버튼 모두 비활성화 (API 409) |
| 실습 중단 | 상단 **실습 중단** → 확인 → 사이클 CANCELLED, 기록 유지 |

#### 8.2.2 시스템 관점

```text
[실습 시작]  POST /api/work/start
  Workbench.start(llm_label, demo_running)
    ├─ 데모 실행 중 또는 진행 중 사이클 존재 → 409
    ├─ INSERT cycles (RELEASING, iteration=0, mode='manual')
    └─ emit cycle.started (actor Workbench)

[v1.0.0 발행]  POST /api/work/release {actor:최개발, release_note:""}
  Workbench.release()
    ├─ _member(최개발, Dev)                  부서 검사 (403)
    ├─ _cycle(RELEASING, FIXING)             차례 검사 (409)
    ├─ 이전 릴리즈 없음 → defects = initial_defects(), 노트 비면 기본 문구
    ├─ INSERT releases (released_by=최개발)
    ├─ INSERT test_runs (RUNNING) + test_results ×12 (result=PENDING, assignee=담당자)
    ├─ _set(VERIFYING, iteration=1, current_version=1.0.0)
    └─ emit release.published (actor "Dev 최개발")

[실행]  POST /api/work/results/{id}/execute {actor:김검증}
  Workbench.execute()
    ├─ _member(QA) / _cycle(VERIFYING) / _result(현재 run의 항목인지)
    ├─ release.defects 로 simulator.run_test()
    └─ UPDATE test_results SET sim_log, log        (판정은 바꾸지 않음, 이벤트 없음)

[PASS 판정]  POST /api/work/results/{id}/judge {actor, result:"PASS"}
  Workbench.judge()
    ├─ UPDATE test_results SET result, passed, checked_by, checked_at
    ├─ _update_counts(run_id)                       PASS/FAIL 개수 재집계
    └─ emit test.judged

[FAIL + 이슈 등록]  POST /api/work/issues {actor, result_id, title, severity, description}
  Workbench.create_issue()
    ├─ 제목 필수, 심각도 ∈ SEVERITIES (400)
    ├─ result_id 항목을 FAIL로 판정 + 재집계
    ├─ 같은 테스트케이스에 미해결(ACTIVE_STATUSES: OPEN/TRANSFERRED/IN_PROGRESS/REOPENED) 이슈가 있으면
    │     새로 만들지 않고 연결 → emit test.judged "(미해결 ISSUE-n에 연결)" → {created:false}
    │     (FIXED는 연결 대상이 아님. 화면의 "연결" 안내도 같은 기준으로 계산)
    └─ 없으면 ISSUE-(cycle 내 이슈 수+1) INSERT (OPEN, reporter) + 이력 → emit issue.created

[AI 초안 - 이슈]  POST /api/work/draft/issue/{result_id} {actor}
  Workbench.draft_issue() → _member(QA) / _cycle(VERIFYING)
    └─ qa._analyze(로그가 없으면 "실행을 먼저…" 안내 문구) → {title, description, severity, source}

[Dev 이관]  POST /api/work/transfer {actor}
  Workbench.transfer()
    ├─ readiness(): pending==0 AND unverified==0 AND (open + carried)>0 이어야 함 (아니면 400 + 사유)
    ├─ test_runs DONE + emit test_run.finished
    ├─ _set(TRANSFERRING)
    ├─ OPEN/REOPENED 이슈 → TRANSFERRED (이력 actor=이관자)
    ├─ 개발부서 미완료(TRANSFERRED/IN_PROGRESS) 이슈는 상태·담당자 유지
    ├─ emit issues.transferred ("이슈 n건 이관 / 개발부서 미완료 m건 계속 수정")
    └─ _set(FIXING)

[착수]  POST /api/work/issues/{key}/start {actor:최개발}
  Workbench.start_fix() → _member(Dev), _cycle(FIXING), _dev_issue(TRANSFERRED|IN_PROGRESS)
    └─ IN_PROGRESS, assignee=최개발 + emit issue.started

[수정 완료]  POST /api/work/issues/{key}/fix {actor, fix_note}
  Workbench.fix() → 수정 내역 필수(400)
    └─ FIXED, fix_note, fixed_in_version=NULL, assignee(없으면 본인) + emit issue.fixed

[v1.1.0 발행]  POST /api/work/release {actor:정개발, release_note}
  Workbench.release()
    ├─ shippable() = FIXED AND fixed_in_version IS NULL  (없으면 400)
    ├─ apply_fixes(이전 defects, shippable의 test_case_id) → 회귀 시 emit regression.introduced
    ├─ 릴리즈 노트 필수 (최초 발행만 생략 가능, 400)
    ├─ UPDATE issues SET fixed_in_version = 새 버전  (shippable 대상만)
    ├─ INSERT releases + test_runs + test_results ×12 (PENDING)
    └─ _set(VERIFYING, iteration+1) + emit release.published

[수정 확인 / 재현]  POST /api/work/issues/{key}/verify {actor, ok}
  Workbench.verify_issue() → 이슈가 FIXED 이고 fixed_in_version == current_version 이어야 함 (400)
    └─ VERIFIED 또는 REOPENED + emit issue.verified / issue.reopened

[버전 확정]  POST /api/work/approve {actor}
  Workbench.approve()
    ├─ readiness(): pending==0 AND unverified==0 AND unresolved==0 (아니면 400)
    ├─ test_runs DONE, _set(APPROVED, finished_at)
    └─ emit release.approved, cycle.completed
```

`readiness()`의 계산 항목은 다음과 같습니다.

| 항목 | 정의 |
| --- | --- |
| pending | 현재 run에서 `result = 'PENDING'`인 항목 수 |
| unverified | `status = 'FIXED'`이고 `fixed_in_version = current_version`인 이슈 수 (이번 버전에서 확인해야 할 수정) |
| open | `OPEN` 또는 `REOPENED` 이슈 수 (새로 이관할 대상) |
| carried | `TRANSFERRED` 또는 `IN_PROGRESS` 이슈 수 (일부 수정 발행으로 개발부서에 남은 미완료 이슈) |
| unresolved | `OPEN, TRANSFERRED, IN_PROGRESS, REOPENED, FIXED` 이슈 수 |
| can_transfer | `pending == 0 and unverified == 0 and (open + carried) > 0` |
| can_approve | `pending == 0 and unverified == 0 and unresolved == 0` |

검증 작업대 화면(`qa.html`)은 같은 `readiness` 결과로 버튼 활성화를 결정하므로, 화면 규칙과 서버 규칙이 한 곳에서 나옵니다.

#### 8.2.3 데모와 실습의 차이

| 항목 | 데모 (`CycleRunner`) | 실습 (`Workbench`) |
| --- | --- | --- |
| 단계 전환 | 코드의 for 루프 | 사람이 버튼으로 |
| 테스트 판정 | 시뮬레이터 결과 = 판정 | 시뮬레이터 결과는 참고(`sim_log`), 판정은 사람 |
| 이슈 작성 | LLM 자동 분석 | 사람이 작성, AI 초안은 선택 |
| 수정 이슈 확인 | 테스트 결과로 자동 VERIFIED/REOPENED | 사람이 "수정 확인"/"재현" |
| 수정 범위 | 이관된 이슈 전부를 한 번에 수정 | 일부만 수정하고 발행 가능 (나머지는 이월) |
| 이력 actor | `QA`, `Dev` | 부서원 이름 |
| 발행자 기록 | 없음 | `releases.released_by` |
| 종료 조건 | 전체 PASS → APPROVED, 5회 초과 → FAILED | 사람이 확정 → APPROVED, 중단 → CANCELLED (반복 제한 없음) |
| 공통 | 같은 시나리오·시뮬레이터·`issues` 테이블·`change_status()`·이벤트 버스 | |

### 8.3 회귀 버그 시뮬레이션 동작 원리

1. 각 릴리즈 행은 `defects` 컬럼에 **숨은 결함 목록**을 가집니다. 이는 시뮬레이터만 아는 "빌드의 실제 상태"이며, 개발자·검증자 화면에는 직접 표시되지 않습니다.
2. 테스트 실행 `run_test(defects, tc)`는 목록 중 해당 테스트케이스를 깨뜨리는 결함이 있으면 그 결함의 로그와 함께 FAIL을 돌려줍니다.
3. 개발부서가 이슈를 수정하고 발행하면 `apply_fixes(이전 defects, 수정한 이슈들의 test_case_id)`가 호출됩니다.
   - 수정 대상 테스트케이스에 영향을 주는 결함을 제거합니다.
   - 제거된 결함 중 `REGRESSIONS`에 있는 것이 있으면 새 결함을 추가합니다.
4. 결과적으로 다음과 같이 진행됩니다.

```text
v1.0.0 defects = [D-101, D-102, D-103]
        TC-02 FAIL (D-101)   TC-05 FAIL (D-102)   TC-08 FAIL (D-103)     → 9 / 3

  수정: TC-02, TC-05, TC-08
  apply_fixes: removed = [D-101, D-102, D-103]
               introduced = [REGRESSIONS[D-103]] = [D-201]
v1.1.0 defects = [D-201]
        TC-11 FAIL (D-201: "report total 1,250,000 != payment ledger 1,249,990")  → 11 / 1

  수정: TC-11
  apply_fixes: removed = [D-201], introduced = []
v1.2.0 defects = []                                                      → 12 / 0
```

5. 업무적 의미: 환불 금액 계산(TC-08)의 절사 오류를 고친 결과, 결제 합계 리포트(TC-11)의 반올림과 불일치가 생긴 상황입니다. 개발자는 이를 알지 못하므로, 회귀 발생은 actor `Simulator`의 `regression.introduced` 이벤트로만 기록되어 Timeline에서 학습용으로 볼 수 있습니다(`dev_service.py`의 주석 "개발자는 모르는 정보").
6. 실습 모드에서도 같은 `apply_fixes`가 쓰이므로, 사람이 진행해도 TC-08을 고치면 다음 버전에서 TC-11이 깨집니다. 반대로 사람이 FAIL 항목을 PASS로 잘못 판정하면 이슈가 생기지 않아 결함이 다음 버전에도 그대로 남습니다.

---

## 9. 화면 구성

모든 화면은 `base.html` 레이아웃을 공유합니다.

| 영역 | 구성 |
| --- | --- |
| 사이드바 | 브랜드(Release Desk, Stage 1 · Standalone), Workspace 메뉴 5개, 실습 메뉴 2개(현재 차례인 부서에 "차례" 배지), 하단 정보(구조, LLM 라벨, 실시간 연결 상태, 테마 전환) |
| 상단바 | 경로(crumb), 현재 사이클 번호·모드(실습/데모)·상태 배지, 데모 진행률 표시, **작업자** 선택(검증부서/개발부서 그룹), 초기화·실습 시작(또는 실습 중단)·데모 실행 버튼 |
| 공용 대화상자 | 초기화 확인, 실습 중단 확인, 일반 확인(이관·확정·발행) |
| 실시간 영역 | `#cycle-status`, `#actions`, `#page` 세 곳이 `data-live`로 지정되어 이벤트 수신 시 교체 |

UI는 모노톤(흑백·회색 계열) shadcn 스타일로, 상태는 배지 톤(solid/outline/muted/live/danger)으로만 구분하며 이모지는 쓰지 않습니다.

### 9.1 Overview (`/`)

![Overview](../01_standalone/docs/images/overview.png)

- 목적: 현재(또는 `?cycle=N`으로 지정한) 사이클을 한 화면에서 관제.
- 주요 요소
  - 실습 진행 중이면 상단 배너: "실습 진행 중 · 검증부서/개발부서 차례"와 해당 작업대로 이동 버튼
  - 통계 카드 4개: 사이클 상태(번호·반복 횟수·시작 시각), 현재 버전(발행 수, 최초 발행 전에는 `-`), 최근 검증 통과율, 미해결 이슈(누적 등록 수)
  - 사이클 진행 파이프라인: 발행(Dev) → 검증(QA) → 이관(QA → Dev) → 수정(Dev) → 승인(QA), 현재 단계 강조
  - 버전별 검증 결과 막대(PASS 검정, FAIL 빗금), 행 클릭 시 릴리즈 상세
  - 이슈 현황 표(이슈, 제목, 심각도, 상태, 발견/수정 버전)
  - 최근 이벤트 12건(실시간 추가)
  - 사이클 이력(최근 8개: 모드, 상태, 최종 버전, 반복, 릴리즈 수, 이슈 수, LLM, 시작·종료)
- 위 캡처(2026-10-16 재촬영)는 데모 사이클 #1 완료 후 실습 사이클 #2의 v1.1.0을 검증하는 중인 화면입니다. 실습 진행 중이므로 상단 초기화·데모 실행 버튼이 비활성화되어 있습니다.

### 9.2 Releases (`/releases`, `/releases/{version}`)

- 목록: 버전, 반영 이슈, 검증 결과 막대, 통과율, 발행 시각.
- 상세: 릴리즈 노트(LLM 또는 사람이 작성), 반영된 이슈(수정 내역·상태), 검증 실행(Run 링크·결과 막대), 이 버전에서 발견된 이슈.

### 9.3 Test Runs (`/test-runs`, `/test-runs/{id}`)

- 목록: Run 번호, 버전, 상태(RUNNING/DONE), 결과 막대, PASS·FAIL 수, 시작·종료.
- 상세: 12칸 테스트 매트릭스(실패 칸이 채워짐, 실습의 대기 항목은 빈 칸), 항목별 결과·판정자(데모는 `Demo`)·로그.

### 9.4 Issues (`/issues`, `/issues/{key}`)

- 목록: 이슈, 제목, 심각도, 상태, 테스트, 발견·수정 버전, 갱신 시각.
- 상세: 설명, 수정 내역, **상태 이력**(시각, 담당, from → to, 메모), 메타 정보(테스트케이스, 발견·수정 버전 링크, 등록·갱신 시각). 데모에서는 담당이 `QA`/`Dev`로 번갈아 나타나지만 실제로는 같은 프로세스가 같은 테이블을 고친 기록입니다.

### 9.5 Timeline (`/timeline`)

- 사이클 이벤트 전체를 최신순으로 표시하고, SSE로 새 이벤트를 맨 위에 추가합니다. `Simulator` 행은 숨은 회귀 결함 발생을 보여 줍니다.

### 9.6 검증 작업대 (`/qa`)

![QA desk](../01_standalone/docs/images/qa_desk.png)

- 목적: 검증부서원이 발행된 버전을 담당 항목별로 검증하고 이슈를 등록한 뒤, 이관 또는 확정.
- 주요 요소
  - 작업자 확인 배너(작업자가 검증부서가 아니면 표시)
  - 차례가 아닐 때 안내 배너("개발부서 차례" 등)
  - 카드 4개: 검증 대상 버전·회차, 판정 진행(n/12 진행 막대), PASS/FAIL과 내 담당 수, 수정 확인 대기 수
  - 검증 항목 표: 항목, 기능, 이름, 담당, 실행 결과(미실행/PASS/FAIL+로그 첫 줄), 판정(대기/PASS/FAIL, 이슈 링크, 판정자, "실행 결과와 다름"), 작업(실행·PASS·FAIL). **내 항목/전체** 탭, 내 담당 행 강조
  - 수정 확인 카드: 이번 버전에 반영된 이슈별 **재현**/**수정 확인**
  - 이 버전에서 등록한 이슈 카드
  - 하단 "검증 완료" 바: 판정 대기·수정 확인 대기·이관 대상·미해결 수, **Dev 이관**/**버전 확정**
  - 이슈 등록 대화상자: 안내 배너(미해결 이슈가 있으면 "연결", 수정 확인 대기 이슈가 있으면 "재현" 유도), 제목, 심각도(설명 문구 포함), 설명, **AI 초안**, **등록 후 FAIL 처리**
- 위 캡처는 실습 사이클 #2의 v1.0.0 검증에서 김검증이 담당 6건을 판정(TC-02, TC-05 FAIL → ISSUE-1, 2)한 직후 화면입니다.

### 9.7 개발 작업대 (`/dev`)

![Dev desk](../01_standalone/docs/images/dev_desk.png)

- 목적: 개발부서원이 이관된 이슈를 수정하고 차기 버전을 발행.
- 주요 요소
  - 작업자 확인 배너, 차례가 아닐 때 안내 배너("검증부서 차례")
  - 버전 발행 카드(`RELEASING`/`FIXING`일 때): 다음 버전 번호, 반영될 이슈 목록, 미완료 이월 안내, 릴리즈 노트 입력란(`data-preserve`), **AI 초안**, **vX.Y.0 발행**(수정 완료 이슈가 없으면 비활성화, 최초 발행은 항상 가능)
  - 이관된 이슈 표: 이슈·테스트·발견 버전, 내용(설명 또는 수정 내역), 심각도, 상태, 담당, 작업(**착수**/"내가 맡기", **수정 완료**, "발행 대기")
  - 발행 이력 표: 버전, 반영 이슈, 발행자, 검증 결과(검증 중 표시), 발행 시각
  - 수정 완료 대화상자: 이슈 설명, 수정 내역 입력, **AI 초안**, **수정 완료**
- 위 캡처는 v1.0.0 이관 후 ISSUE-1·2는 수정 완료, ISSUE-3은 최개발이 착수한 상태입니다. 발행 카드가 "미완료 1건은 다음 버전으로 넘어갑니다"를 안내합니다.

---

## 10. LLM 활용

### 10.1 원칙

- LLM은 **판단을 하지 않고 문서를 작성**합니다. PASS/FAIL, 버전, 이슈 개수, 상태 전이는 모두 시뮬레이터와 서비스 코드가 결정합니다.
- 따라서 mock 모드와 LLM 모드의 결과 숫자가 같고, LLM이 실패해도 업무 흐름은 멈추지 않습니다.
- 모든 호출은 `LLMClient.json()` 하나를 통과합니다(`01_standalone/src/standalone/infra/llm.py`).

### 10.2 호출 지점

| # | 주체 | 메서드 | 산출물 | 스키마 | 데모 | 실습 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | QA | `QAService._analyze` | 이슈 제목·설명·심각도 | `ISSUE_SCHEMA` | 실패 테스트마다 자동 (4회) | 이슈 대화상자 **AI 초안** (`draft_issue`) |
| 2 | Dev | `DevService._write_fix_note` | 수정 내역 1~2문장 | `FIX_SCHEMA` | 이슈마다 자동 (4회) | 수정 대화상자 **AI 초안** (`draft_fix`) |
| 3 | Dev | `DevService._write_release_note` | 릴리즈 노트(목록, 5줄 이내) | `NOTE_SCHEMA` | 차기 버전마다 자동 (2회) | 발행 카드 **AI 초안** (`draft_release`) |

최초 버전 v1.0.0의 릴리즈 노트는 LLM을 쓰지 않고 고정 문구(`"Commerce Web 1.0.0 최초 릴리즈\n- 기능: …"`)를 사용합니다.

### 10.3 프롬프트 요약

| 호출 | system | user prompt 구성 |
| --- | --- | --- |
| 이슈 분석 | "커머스 웹 서비스 검증팀의 QA 엔지니어입니다. 테스트 실패 로그를 분석해 결함 리포트를 작성합니다. 한국어로 작성합니다." | 제품·버전, 기능, 테스트케이스 ID·이름, 실패 로그 + "심각도는 매출/결제 영향은 Critical, 주요 기능 장애는 Major, 불편 수준은 Minor." |
| 수정 내역 | "커머스 웹 서비스 개발팀의 시니어 개발자입니다. 간결하고 사실적인 한국어로 작성합니다." | "다음 결함을 수정했습니다. 수정 내역을 작성하세요." + 이슈 키·제목·설명 |
| 릴리즈 노트 | (위와 동일) | "Commerce Web vX.Y.0 릴리즈 노트를 작성하세요. 반영된 수정 사항:" + 이슈별 `- 키 [심각도] 제목: 수정 내역` |

### 10.4 구조화 출력 스키마

| 스키마 | 속성 | 제약 |
| --- | --- | --- |
| `ISSUE_SCHEMA` | `title`(40자 이내 설명), `description`(재현 조건·기대 결과·실제 결과 2~3문장), `severity` | `severity` enum = Critical/Major/Minor, 모두 required, `additionalProperties: false` |
| `FIX_SCHEMA` | `fix_note`(원인과 수정 내용 1~2문장) | required, `additionalProperties: false` |
| `NOTE_SCHEMA` | `release_note`(목록 형태, 5줄 이내) | required, `additionalProperties: false` |

스키마는 `output_config.format`에 `{"type": "json_schema", "schema": ...}`로 전달되어, 응답 텍스트가 항상 스키마에 맞는 JSON으로 옵니다. 코드는 `json.loads()` 결과를 그대로 사용합니다.

### 10.5 호출 설정

```python
response = await self._client.beta.messages.create(
    model=self.model,                       # 기본 claude-opus-5-5 (CLAUDE_MODEL)
    max_tokens=8000,
    system=system,
    messages=[{"role": "user", "content": prompt}],
    output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
)
```

| 설정 | 값 | 의도 |
| --- | --- | --- |
| SDK | `anthropic` 1.14.0 (`uv.lock`), `AsyncAnthropic` | 비동기 이벤트 루프를 막지 않음 |
| 모델 | `claude-opus-5-5` | 환경변수로 변경 가능 |
| effort | `low` | 짧은 문서 작성이므로 응답 속도 우선 |
| 서버 측 대체 | beta `server-side-fallback-2026-07-01`, `fallbacks="default"` | 서버 측에서 모델 가용성 문제를 흡수 |
| max_tokens | 8000 | 충분한 여유 |

### 10.6 mock 대체와 오류 처리

| 상황 | 동작 | 반환 source |
| --- | --- | --- |
| mock 모드 (`_client is None`) | 즉시 `fallback()` 결과 반환, 호출 수 증가 없음 | `"mock"` |
| `stop_reason != "end_turn"` | `RuntimeError` 발생 → 대체 | `"mock"` |
| 텍스트 블록 없음 | `StopIteration` → 대체 | `"mock"` |
| JSON 파싱 실패 | `json.JSONDecodeError` → 대체 | `"mock"` |
| API 오류(인증, 네트워크, 한도 등) | `anthropic.APIError` → 대체 | `"mock"` |
| 그 밖의 예외 (예: `LLM_MODE=llm`인데 키가 없을 때의 `TypeError`) | `except Exception`으로 모두 대체 (작업 취소 `CancelledError`는 전파) | `"mock"` |

대체 시 `failures += 1`, `last_error`에 오류 기록, 경고 로그 "LLM 호출 실패, mock으로 대체"를 남깁니다. 사이클은 그대로 진행합니다.

mock 결과는 다음과 같습니다.

| 호출 | mock 결과 |
| --- | --- |
| 이슈 분석 | 제목 `[기능] 테스트이름 실패`, 설명 `TC-xx 실행 시 실패. 로그 첫 줄`, 심각도 `FEATURE_SEVERITY[기능]` |
| 수정 내역 | `이슈제목 원인 코드 수정 및 단위 테스트 추가` |
| 릴리즈 노트 | `Commerce Web X.Y.0 유지보수 릴리즈` + 이슈별 `- 키 제목 수정` |

데모 모드에서 이슈 등록 시 `issue_history.note`에 "(분석: llm|mock)", `issue.created` 이벤트 payload에 `analyzed_by`가 남아 어느 쪽이 작성했는지 추적할 수 있습니다. 실습의 이슈 AI 초안은 응답의 `source`로 토스트 문구를 바꿉니다("Claude가 실패 로그를 분석해 작성했습니다." / "LLM 미사용(mock) 템플릿으로 작성했습니다.").

### 10.7 데모 1사이클의 LLM 호출 수

| 버전 단계 | 이슈 분석 | 수정 내역 | 릴리즈 노트 | 소계 |
| --- | --- | --- | --- | --- |
| v1.0.0 검증 → v1.1.0 발행 | 3 | 3 | 1 | 7 |
| v1.1.0 검증 → v1.2.0 발행 | 1 | 1 | 1 | 3 |
| v1.2.0 검증 → 승인 | 0 | 0 | 0 | 0 |
| 합계 | 4 | 4 | 2 | **10** |

호출은 모두 순차(await)로 이루어지므로, LLM 모드 소요 시간 약 46초는 대부분 이 10회 호출의 누적 응답 시간입니다.

---

## 11. 예외 처리와 운영

### 11.1 기동 시 미완료 데모 사이클 정리

`CycleRunner.__init__`은 기동 시 다음 SQL을 실행합니다.

```sql
UPDATE cycles SET status = 'CANCELLED', finished_at = ?
 WHERE mode = 'demo' AND status IN ('RELEASING','VERIFYING','TRANSFERRING','FIXING')
```

데모 사이클은 메모리의 asyncio 태스크로만 진행되므로 프로세스가 죽으면 이어서 진행할 방법이 없습니다. 이를 그대로 두면 "진행 중 사이클이 있다"는 판정 때문에 새 사이클을 시작할 수 없으므로 취소 처리합니다. 반면 **실습 사이클은 상태가 모두 DB에 있고 사람이 진행**하므로 재기동 후에도 그대로 이어서 진행할 수 있도록 유지합니다.

### 11.2 중단과 초기화

| 기능 | 경로 | 동작 | 제한 |
| --- | --- | --- | --- |
| 앱 종료 시 데모 중단 | lifespan 종료 → `runner.stop()` | 태스크 취소 → `_run`의 `CancelledError` 처리에서 CANCELLED 기록 | - |
| 실습 중단 | `POST /api/work/abort` | 진행 중 실습 사이클을 CANCELLED, `cycle.cancelled` 이벤트 | 진행 중 실습이 없으면 409 |
| 초기화 | `POST /api/reset` | 모든 테이블 삭제 → 테스트케이스·부서원 재시드 → `system.reset` 발행 → 모든 브라우저 새로고침 | 데모 실행 중이거나 진행 중인 사이클(데모·실습)이 있으면 409, UI 버튼도 비활성화 |

### 11.3 데모 실행 중 오류

`_run()`은 `CancelledError`(취소 → CANCELLED)와 그 밖의 모든 `Exception`(→ FAILED + `cycle.error` 이벤트 + 스택 로그)을 구분해 처리합니다. 예상하지 못한 오류라도 사이클이 "진행 중"으로 남지 않도록 하기 위함입니다.

### 11.4 권한 검사

| 검사 | 위치 | 응답 |
| --- | --- | --- |
| 작업자 존재 여부 | `Workbench._member` | 403 "상단에서 작업자를 선택하세요." |
| 부서 일치 | `Workbench._member` (AI 초안 포함 모든 부서 작업) | 403 "검증부서/개발부서 작업자만 할 수 있는 작업입니다. (현재: 부서 이름)" |
| 관리자 조작 | 실습 시작·중단, 데모 실행, 초기화 | 작업자 검사 없음 (릴리즈 매니저 조작, 인증 없는 학습용 범위) |
| 차례(사이클 상태) | `Workbench._cycle(*statuses)` | 409 "지금은 이 작업을 할 수 없습니다. (현재 단계: …)" |
| 진행 중 실습 존재 | `Workbench._cycle` | 409 "진행 중인 실습 사이클이 없습니다…" |
| 현재 버전 항목 | `Workbench._result` | 404 |
| 이슈 상태 | `_dev_issue`, `verify_issue` | 400 |
| 완료 조건 | `readiness` + `_blocker` | 400 + 구체적 사유 |

화면 쪽에서도 같은 규칙을 반영해 버튼을 비활성화하고(작업대 템플릿), 작업자 부서 불일치를 배너로 안내합니다(`workbench.js`의 `applyView`). 다만 최종 판단은 항상 서버가 합니다.

### 11.5 동시 진행 방지

| 상황 | 방지 방법 |
| --- | --- |
| 데모 중복 시작 | `runner.running`(태스크 살아 있음) 또는 진행 중 사이클 존재 시 409 |
| 실습 중 데모 시작 | 진행 중 사이클 검사가 모드와 무관하므로 409, UI 버튼도 비활성화 |
| 데모 중 실습 시작 | `Workbench.start(demo_running=runner.running)`에서 409, UI 버튼 비활성화 |
| 진행 중 초기화 | `runner.running` 또는 진행 중 사이클(모드 무관) 존재 시 409, UI 버튼 비활성화 |
| 여러 사람이 동시에 버튼 클릭 | 단일 프로세스·단일 이벤트 루프에서 `Workbench`의 상태 변경 메서드는 `await` 없이 검사와 쓰기를 한 번에 수행하므로(발행 `release`도 내부에 `await`가 없음), 요청이 사실상 하나씩 직렬 처리됩니다. 예를 들어 두 사람이 동시에 이관을 누르면 두 번째 요청은 이미 FIXING 상태라 409를 받습니다 |

### 11.6 실시간 갱신과 입력 보호

- 브라우저는 SSE 이벤트를 받을 때마다 350ms 디바운스 후 현재 URL을 다시 받아 `data-live` 영역(`#cycle-status`, `#actions`, `#page`)만 교체합니다.
- `isEditing()`이 참이면(열린 `dialog`가 있거나 `#page` 안의 INPUT/TEXTAREA/SELECT에 포커스) 갱신을 1.5초 뒤로 미루고 다시 확인합니다. 이슈 등록·수정 완료 대화상자를 작성하는 동안 다른 사람의 작업 때문에 입력이 날아가지 않습니다.
- 대화상자가 닫혀 있어도 값이 남아야 하는 입력(개발 작업대의 릴리즈 노트 `#release-note`)은 `data-preserve`로 지정되어 교체 전 값을 저장했다가 교체 후 복원합니다.
- 교체 후 `live:refreshed` 이벤트로 `workbench.js`가 작업자 강조·필터를 다시 적용합니다.
- SSE 연결이 끊기면 `retry: 3000`에 따라 3초 후 재연결하고, 사이드바 "실시간" 표시가 "재연결 중"으로 바뀝니다.

### 11.7 DB 마이그레이션

실습 기능 이전에 만들어진 `app.db`를 열어도 `Database._migrate()`가 없는 컬럼(`cycles.mode`, `releases.released_by`, `test_cases.assignee`, `test_results.result/assignee/sim_log/checked_by/checked_at`, `issues.reporter/assignee`)만 추가하고, 예전 결과의 `result`를 `passed`로부터 채웁니다. `cycles.mode`는 기본값 `demo`이므로 기존 사이클은 데모 사이클로 분류됩니다.

---

## 12. 점검 결과

### 12.1 점검 항목

최종 점검일은 2026-10-16이며, 13.3절의 수정을 반영한 코드로 다시 확인했습니다. 별도 표기가 없는 항목은 이날 mock 모드(`uv run run.py --mock`, 지연 0)에서 점검 스크립트 하나로 연속 수행했으며, 스크립트의 확인 항목 16개가 모두 통과했습니다.

| # | 항목 | 방법 | 결과 |
| --- | --- | --- | --- |
| 1 | 데모 (mock) | `POST /api/cycles` → `/api/state` 폴링 → `/api/cycles/{id}/summary` | 통과: APPROVED, 9/3 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, 약 0.2초 |
| 2 | 데모 (LLM, 2026-10-15) | 루트 `.env`의 API 키로 실행 | 통과: 결과 숫자 동일, LLM 호출 10회, 약 46초, 이슈·수정 내역·릴리즈 노트 생성 확인 |
| 3 | 실습 전 과정 (실제 브라우저) | Playwright + Edge(headless)로 작업대 버튼을 직접 클릭: 실습 시작 → 발행(확인 대화상자) → 실행·PASS·FAIL(이슈 등록 대화상자 + AI 초안) → Dev 이관 → 착수·수정 완료(대화상자 + AI 초안) → 릴리즈 노트 AI 초안 → 재발행 → 수정 확인 → 버전 확정 | 통과: v1.0.0 9/3 → v1.1.0 11/1(회귀 TC-11) → v1.2.0 12/0, ISSUE-1~4 VERIFIED, 사이클 APPROVED |
| 4 | 화면 오류 | 위 과정과 전 화면(Overview, Releases, Test Runs, Issues, Timeline, 작업대) 순회 중 페이지 오류·콘솔 오류 수집 | 통과: 0건 |
| 5 | 실습 중 초기화 차단 | 실습 진행 중 `POST /api/reset`, 상단 초기화 버튼 상태 확인 | 통과: 409, 버튼 비활성화, 실습 사이클 유지. 실습 중단 후 초기화는 200(API로 별도 확인) |
| 6 | FAIL 안내와 서버 연결 기준 일치 | v1.1.0 검증 중 FIXED 이슈(ISSUE-1)가 있는 TC-02의 FAIL 대화상자 확인 | 통과: "연결" 안내 없음, "수정 확인 대기" 안내 표시 |
| 7 | AI 초안 권한 | 작업자 없이, 또는 다른 부서 작업자로 AI 초안 요청 | 통과: 4건(릴리즈 노트 초안 무작업자·QA, 수정 내역 초안 QA, 이슈 초안 Dev) 모두 403, 같은 부서 요청은 200 |
| 8 | LLM 대체 범위 (별도 실행) | `LLMClient("llm", …)`를 API 키 없이 만들어 `json()` 호출 | 통과: `TypeError`를 mock으로 대체, `failures` 1 증가 |
| 9 | Overview 현재 버전 | 실습 시작 직후(최초 발행 전) Overview 확인 | 통과: `-` 표시 |
| 10 | 동시 진행 방지 | 실습 중 데모 실행 | 통과: 409 |
| 11 | 부분 수정 발행 (API, 별도 실행) | ISSUE-1만 수정해 v1.1.0 발행(ISSUE-3은 정개발 착수 상태로 남김) → TC-05·08 FAIL은 기존 ISSUE-2·3에 연결 → 이관 → 나머지 수정 | 통과: 수정 전에는 이관·확정 모두 400으로 교착을 재현. 수정 후 이관 시 ISSUE-2 TRANSFERRED, ISSUE-3 IN_PROGRESS·담당 정개발 유지, 사이클 FIXING. 최종 9/3 → 10/2 → 11/1 → 12/0, ISSUE-1~4 VERIFIED, APPROVED |
| 12 | 권한·차례·조건 검사 (2026-10-15) | 다른 부서 작업자, 차례가 아닐 때, 판정 전 이관·확정, 수정 전 발행 | 통과: 403 / 409 / 400과 사유 메시지 |
| 13 | DB 마이그레이션 (2026-10-15) | 실습 기능 이전에 만든 `app.db`로 기동 | 통과: 컬럼 자동 추가, 기존 결과 보존 |
| 14 | 4개 단계 동시 회귀 (2026-10-15) | Stage 1~4를 동시에 띄워 같은 사이클 실행 | 통과: 4개 단계 모두 동일 결과 |

### 12.2 결과 수치

| 지표 | 기대값 | 데모 실측 | 실습 실측 |
| --- | --- | --- | --- |
| v1.0.0 PASS / FAIL | 9 / 3 | 9 / 3 | 9 / 3 |
| v1.1.0 PASS / FAIL | 11 / 1 | 11 / 1 | 11 / 1 |
| v1.2.0 PASS / FAIL | 12 / 0 | 12 / 0 | 12 / 0 |
| 이슈 수 / VERIFIED 수 | 4 / 4 | 4 / 4 | 4 / 4 |
| 최종 사이클 상태 | APPROVED | APPROVED | APPROVED |
| 반복(검증) 횟수 | 3 | 3 | 3 |
| LLM 호출 수 (LLM 모드) | 10 | 10 | - |
| 소요 시간 (지연 0) | - | mock 약 0.2초, LLM 약 46초 | - |

### 12.3 점검의 한계

- 자동화된 단위 테스트 코드는 없습니다. 점검은 API 호출과 브라우저 자동 클릭 스크립트로 수행했습니다.
- 2026-10-16 재점검은 mock 모드로만 수행했습니다. LLM 모드 결과(2번)는 2026-10-15 측정값이며, 이번 수정은 LLM 호출 경로를 바꾸지 않았습니다(예외 대체 범위만 확대).

---

## 13. 한계와 알려진 이슈

### 13.1 구조적 한계 (Stage 1의 의도된 특성)

| 한계 | 설명 |
| --- | --- |
| 업무 흐름 하드코딩 | 데모 흐름은 `CycleRunner._run()`, 실습 단계 규칙은 `Workbench`에 코드로 고정. 승인 절차 추가·순서 변경은 코드 수정과 재배포가 필요 |
| 부서 간 강결합 | Dev·QA가 같은 `issues` 테이블과 같은 `change_status()`를 사용, `Workbench`가 `qa._analyze`, `dev._write_fix_note` 등 내부 메서드를 직접 호출 |
| 독립 배포·확장 불가 | QA 기능만 고쳐도 전체 서비스를 재시작해야 하며, 부하가 큰 부서만 따로 늘릴 수 없음 |
| 장애 격리 불가 | 한 부서 코드의 치명적 오류가 프로세스 전체(양 부서 + 화면)에 영향 |
| "차례" 관리 공유 | 두 부서의 순서를 `cycles.status` 컬럼 하나로 관리 |

### 13.2 남아 있는 알려진 이슈

| # | 내용 | 영향 | 근거 |
| --- | --- | --- | --- |
| 1 | 인증 없음. 작업자는 요청 본문의 `actor` 이름으로만 식별 | 누구나 다른 사람 이름으로 요청 가능. 학습용 범위에서 허용 | `Workbench._member` |
| 2 | 실습 시작·중단 API는 작업자를 검사하지 않음 | 의도된 설계: 데모 실행·초기화와 같은 릴리즈 매니저 조작으로 분류. 중단은 확인 대화상자를 거치고 기록은 남음 | `web/app.py`의 `work_start`, `work_abort` |
| 3 | 여러 SQL을 하나의 트랜잭션으로 묶지 않음(autocommit). 예: 발행 시 releases·test_runs·test_results INSERT | 정상 동작 중에는 단일 이벤트 루프라 문제없으나, 도중에 프로세스가 죽으면 일부만 기록될 수 있음 | `infra/db.py` |
| 4 | 이슈 키를 `COUNT(*)+1`로 생성 | 이슈 삭제 기능이 없어 현재는 충돌 없음 | `qa_service.py`, `workbench.py` |
| 5 | SSE 구독자를 프로세스 메모리(`asyncio.Queue`)에 보관 | 다중 워커·다중 인스턴스로 확장하면 이벤트가 일부 브라우저에만 전달됨 | `infra/events.py` |
| 6 | 실습 모드에는 반복 횟수 제한이 없음 | 의도된 설계(사람이 진행). 데모만 최대 5회 | `workbench.py` |

### 13.3 2026-10-16 수정 완료 항목

초판에서 알려진 이슈로 기록했던 코드 결함과, 재점검 중 발견한 부분 수정 교착(6번)을 모두 수정했습니다.

| # | 기존 이슈 | 수정 내용 | 위치 |
| --- | --- | --- | --- |
| 1 | `/api/reset`이 데모 실행 중에만 막혀, 진행 중인 실습 사이클을 지울 수 있었음 | 진행 중인 사이클(데모·실습)이 있으면 409. 상단 초기화 버튼도 실습 중 비활성화 | `web/app.py`의 `reset()`, `base.html` |
| 2 | 이슈 등록 대화상자의 "연결" 안내가 FIXED 이슈까지 포함해, 서버 기준(`ACTIVE_STATUSES`)과 달랐음 | 화면도 `ACTIVE_STATUSES`로 계산. FIXED 이슈는 별도 "수정 확인 대기" 안내로 **재현** 사용을 유도 | `web/app.py`의 `/qa` 쿼리, `qa.html`, `workbench.js` |
| 3 | AI 초안 API가 작업자를 검사하지 않았음 | 이슈 초안은 QA, 수정 내역·릴리즈 노트 초안은 Dev 작업자만 요청 가능(403) | `workbench.py`의 `draft_*`, `web/app.py` |
| 4 | LLM 예외 중 4종만 mock으로 대체되어, 키 없이 `LLM_MODE=llm`이면 `TypeError`로 데모 FAILED·초안 500이 발생 | 모든 `Exception`을 mock으로 대체 | `infra/llm.py` |
| 5 | Overview "현재 버전" 카드가 최초 발행 전에도 `v1.0.0`으로 표시 | 발행 전에는 `-`와 "최초 버전 발행 전" 표시 | `overview.html` |
| 6 | 일부만 수정해 발행하면, 다음 검증에서 남은 이슈(TRANSFERRED/IN_PROGRESS)가 이관 대상(OPEN/REOPENED)에도 확정 조건에도 맞지 않아 이관·확정 모두 불가(교착) | 개발부서 미완료 이슈(`carried`)도 이관 대상으로 계산. 이관 시 해당 이슈는 상태·담당자를 유지한 채 차례만 FIXING으로 넘김. 하단 바·확인 대화상자·차단 사유 문구도 같은 기준으로 수정 | `workbench.py`의 `readiness()`, `transfer()`, `_blocker()`, `qa.html`, `workbench.js` |
| 7 | 파비콘 요청이 404로 콘솔 오류를 남김 | 빈 아이콘(`data:,`) 지정 | `base.html` |
| 8 | 문서 불일치: `overview.png`가 실습 모드 이전 화면, PRD의 버튼명("사이클 시작")과 데이터 모델(`issue_history.issue_key`, `cycles.llm_mode` 누락), TODO 절 번호 순서 | 화면 캡처 3종 재촬영, PRD·TODO·README·USER_SCENARIO를 코드와 일치하도록 수정 | `01_standalone/docs/` |

---

## 14. 학습 포인트

### 14.1 이 단계에서 배우는 것

1. **도메인 이해**: 숨은 결함, 테스트 실패, 이슈 등록, 이관, 수정, 재검증, 회귀 결함, 승인으로 이어지는 릴리즈 사이클의 전체 모습과 이슈 상태 머신.
2. **결정적 시뮬레이션과 LLM의 역할 분리**: 결과를 결정하는 로직(시뮬레이터)과 내용을 작성하는 LLM을 분리하면, LLM이 있든 없든 같은 결과를 재현할 수 있고 시스템을 검증하기 쉬워집니다.
3. **구조화 출력과 안전한 대체(fallback)**: JSON Schema로 LLM 출력을 고정하고, 실패 시 템플릿 결과로 대체해 업무 흐름이 끊기지 않게 하는 패턴.
4. **모놀리식의 편리함**: 함수 호출 한 줄로 부서 간 "이관"이 끝나고, 한 번의 SQL로 두 부서의 데이터를 조인해 화면을 만듭니다. 디버깅도 호출 스택 하나로 끝납니다.
5. **이벤트 기반 화면 갱신**: 업무 이벤트를 DB에 남기면서 SSE로 실시간 전달하고, 화면은 이벤트를 신호로만 쓰고 실제 내용은 서버 렌더링으로 다시 받는 단순한 실시간 패턴.
6. **사람이 개입하는 흐름(실습)과 자동 흐름(데모)의 공존**: 같은 도메인·저장소 위에서 단계 전환 주체만 바꾸는 설계, 그리고 권한(403)·차례(409)·조건(400) 검사의 구분.

### 14.2 다음 단계(API)로 넘어가는 동기

Stage 1에서 관찰되는 문제는 모두 "두 부서가 하나의 프로그램을 공유한다"는 데서 나옵니다.

| Stage 1에서 겪는 문제 | 현실에서의 의미 | Stage 2(API)에서의 해결 방향 |
| --- | --- | --- |
| QA가 Dev의 테이블(`issues`)을 직접 수정 | 다른 부서 시스템의 DB를 직접 고치는 것과 같음 | 부서별로 별도 서비스·별도 DB, 상대 데이터는 API로만 접근 |
| 이관이 함수 호출 (`dev.receive_issues(issues)`) | 상대 부서 시스템 내부 구현에 의존 | 정해진 REST 엔드포인트와 요청/응답 계약(JSON) |
| `Workbench`가 다른 서비스의 내부 메서드 호출 | 캡슐화 붕괴, 한쪽 변경이 다른 쪽을 깨뜨림 | 공개된 인터페이스만 사용 |
| 함께 배포·재시작 | 한 부서의 변경 때문에 다른 부서도 멈춤 | 부서별 독립 프로세스·포트, 독립 배포 |
| 한쪽 장애가 전체 장애 | 장애 격리 불가 | 네트워크 경계로 장애 범위 분리 (대신 타임아웃·재시도 같은 새 문제 등장) |
| 업무 흐름이 한 코드(`CycleRunner`)에 고정 | 흐름 변경 시 전체 수정 | API 단계에서도 흐름은 여전히 코드에 있음 → 이후 MCP·A2A 단계에서 "누가 흐름을 정하는가"를 다시 다룸 |

다음 단계 [Stage 2. API](../02_api/README.md)에서는 Dev와 QA를 별도 시스템으로 분리하고 HTTP(REST)로 연동합니다. 같은 시나리오·시뮬레이터를 그대로 쓰므로 결과 숫자(9/3 → 11/1 → 12/0)는 같아야 하며, 달라지는 것은 **부서 간 상호작용 단위가 "함수 호출"에서 "HTTP 요청"으로 바뀐다**는 점입니다.

---

## 부록 A. 실행 방법

### A.1 준비

- Python 3.12 이상, [uv](https://docs.astral.sh/uv/) 설치
- LLM 모드를 쓰려면 프로젝트 루트 `.env`(또는 `01_standalone/.env`)에 `ANTHROPIC_API_KEY` 설정 (`.env.example` 참고)

### A.2 명령

```powershell
cd D:\users\yk\A2A_Class\A2A-Practice\01_standalone
uv sync                         # 단계 전용 .venv 생성 및 의존성 설치
uv run run.py                   # http://localhost:8100  (키가 있으면 LLM 모드)
uv run run.py --mock            # LLM 없이 mock 모드
uv run run.py --port 8200       # 다른 포트로 실행
```

### A.3 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_MODE` | `auto` | `auto`(키 있으면 llm, 없으면 mock) / `llm` / `mock` (`--mock`은 이 값을 `mock`으로 설정) |
| `ANTHROPIC_API_KEY` | 없음 | Claude API 키 |
| `CLAUDE_MODEL` | `claude-opus-5-5` | 사용할 모델 |
| `STEP_DELAY` | `0.4` | 데모 단계 사이 지연(초) |
| `TEST_DELAY` | `0.1` | 데모 테스트 1건당 지연(초) |

지연 0으로 측정하려면 다음과 같이 실행합니다.

```powershell
$env:STEP_DELAY = "0"; $env:TEST_DELAY = "0"; uv run run.py --mock
```

### A.4 API로 데모 실행·확인

```powershell
$r = Invoke-RestMethod -Method Post http://localhost:8100/api/cycles
Invoke-RestMethod http://localhost:8100/api/state
Invoke-RestMethod "http://localhost:8100/api/cycles/$($r.cycle_id)/summary"
```

### A.5 실습 바로가기

```text
http://localhost:8100/qa?as=김검증
http://localhost:8100/qa?as=이검증
http://localhost:8100/qa?as=박검증
http://localhost:8100/dev?as=최개발
http://localhost:8100/dev?as=정개발
```

### A.6 초기화

화면 상단 **초기화** → 확인, 또는 `Invoke-RestMethod -Method Post http://localhost:8100/api/reset`. DB 파일을 완전히 새로 만들려면 서버를 끄고 `01_standalone/data/app.db`(및 `-wal`, `-shm` 파일)를 삭제합니다.

---

## 부록 B. 용어 정리

| 용어 | 설명 |
| --- | --- |
| 모놀리식(Monolithic) | 모든 기능을 하나의 실행 단위로 개발·배포하는 구조 |
| 스탠드얼론(Standalone) | 외부 시스템 없이 단독으로 완결되어 동작하는 프로그램 |
| 사이클(Cycle) | 최초 발행부터 승인(또는 실패·취소)까지의 릴리즈 반복 단위. `cycles` 테이블의 한 행 |
| 데모 모드 | `CycleRunner`가 정해진 루틴으로 사이클 전체를 자동 진행하는 방식 |
| 실습 모드 | 부서원이 작업대에서 버튼으로 단계를 진행하는 방식 (`Workbench`) |
| 작업대 | 실습 모드의 부서별 작업 화면. 검증 작업대(`/qa`), 개발 작업대(`/dev`) |
| 작업자 | 실습에서 요청을 보내는 부서원. 상단 선택 상자 또는 `?as=이름`으로 지정 |
| 차례 | 현재 사이클 상태에서 작업할 수 있는 부서 (RELEASING·FIXING = Dev, VERIFYING = QA) |
| 숨은 결함(Defect) | 빌드에 들어 있지만 개발자는 모르는 결함. `releases.defects`에 저장, 시뮬레이터만 사용 |
| 회귀 결함(Regression) | 다른 결함을 고치는 과정에서 새로 생긴 결함. 이 프로젝트에서는 D-103 수정 시 D-201 |
| 시뮬레이터 | 숨은 결함 목록으로 테스트 결과와 다음 빌드의 결함을 결정하는 순수 함수 모음 |
| 이슈(Issue) | QA가 테스트 실패를 근거로 등록한 결함 리포트. `ISSUE-n` |
| 이관(Transfer) | QA가 미해결 이슈를 Dev에 넘기는 행위. 이슈 상태 TRANSFERRED |
| 수정 확인(Verify) | 수정이 반영된 버전에서 해당 테스트 통과를 확인. 이슈 상태 VERIFIED |
| 재현 / 재오픈(Reopen) | 수정했다는 이슈가 여전히 실패. 이슈 상태 REOPENED, 다음 이관 대상 |
| 승인 / 버전 확정(Approve) | 모든 테스트 통과 및 미해결 이슈 0건일 때 QA가 버전을 확정. 사이클 APPROVED |
| 판정 | 실습에서 검증부서원이 항목에 내리는 PASS/FAIL 결정. 시뮬레이터 실행 결과는 참고용 |
| 실행(Execute) | 실습에서 발행된 빌드로 테스트를 돌려 참고 결과(`sim_log`)를 얻는 동작 |
| AI 초안 | 실습 폼에서 LLM(또는 mock)이 이슈·수정 내역·릴리즈 노트를 미리 작성해 주는 기능 |
| mock 모드 | LLM 대신 템플릿 규칙으로 문서를 작성하는 모드. API 키가 없으면 자동 선택 |
| 구조화 출력(Structured Output) | JSON Schema를 지정해 LLM 응답을 정해진 JSON 형식으로 받는 기능 |
| effort | Claude 응답의 사고·작성 노력 수준 설정. 이 프로젝트는 `low` |
| SSE(Server-Sent Events) | 서버가 브라우저로 이벤트를 한 방향으로 계속 보내는 HTTP 기반 실시간 통신 방식 |
| EventBus | 업무 이벤트를 DB에 기록하고 SSE 구독자에게 전달하는 객체 |
| 부분 갱신 | 페이지 전체를 다시 그리지 않고 `data-live` 영역만 서버 렌더링 결과로 교체하는 방식 |
| WAL | SQLite의 Write-Ahead Logging 저널 모드. 읽기와 쓰기의 동시성을 높임 |
| uv | Python 패키지·가상환경 관리 도구. 각 단계 폴더가 독립된 `.venv`를 가짐 |
