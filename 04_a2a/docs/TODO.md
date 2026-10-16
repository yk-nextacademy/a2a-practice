# TODO — Stage 4. A2A

> `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[-]` 보류

## 1. 구현

- [x] uv 프로젝트 구성, `run.py`(3개 프로세스 기동/종료)
- [x] Dev Agent: Agent Card, Executor(`release-build`, `fix-and-release`), Artifact, `/builds/{cycle_id}/{version}`
- [x] QA Agent: Agent Card, Executor(`verify-release`), Streaming 진행률, `input-required` 승인 흐름
- [x] Hub: Agent Registry(Agent Card 수집, health)
- [x] Hub: A2A 클라이언트 + 감사 로그(원문 JSON-RPC/SSE 기록)
- [x] Hub: Workflow Engine(상태 머신, DB 저장), 판단(LLM 구조화 출력 / mock 규칙)
- [x] Hub: Approvals(승인/반려 → 같은 Task로 회신)
- [x] Hub: 대시보드 + Agents + Message Trace + Approvals 화면, SSE 중계
- [-] (선택) Hub 자체 Agent Card / Peer 모드 / A2A + MCP 결합

## 2. 점검

- [x] Agent Card 조회(`/.well-known/agent-card.json`) 및 Agents 화면 표시
- [x] mock 모드 시나리오 결과 일치, 승인 후 APPROVED
- [x] LLM 모드 사이클 완주
- [x] Streaming 진행률 실시간 표시, Message Trace 기록 확인
- [x] 반려 시 REJECTED 처리 확인

## 4. 3개 웹 서비스 + 실습 모드 (추가 요청, 2026-10-15)

- [x] 공통 웹 계층 적용(Stage 2 기반 + 6단계 파이프라인·승인함 스타일 유지), Hub layout(승인 대기·차례 배너)
- [x] Dev Agent: DevWork(Task 대기 연결, 진행 메시지 스트리밍), Dev 웹(개발 작업대·티켓·타임라인)
- [x] QA Agent: QAWork(판정 진행률 스트리밍, 이관 → 완료 / 확정 → input-required), QA 웹(검증 작업대·이력·이슈·타임라인)
- [x] Hub: 실습 시작(mode 전달), 사이클 중단 시 CancelTask, 스트림 읽기 타임아웃 해제, 차례 표시
- [x] 점검: mock 데모·실습, LLM 실습(Hub 판단 Claude), 중단(CancelTask), 차례·권한, 전 화면
- [x] 문서: README, PRD, USER_SCENARIO

## 5. 결함 수정 (2026-10-16)

- [x] Hub "실습 시작"/"사이클 중단" 버튼 무반응 (`base.html`이 `workbench.js`를 항상 로드)
- [x] 실습 부분 수정 발행 후 QA 이관·확정 불가(교착) → 미수정 TRANSFERRED 이슈도 이관 대상 (`qa_agent/agent.py`)
- [x] 승인 대기 중 중단 시 승인 행 PENDING 잔존 → CANCELLED로 닫음, 승인함 처리 이력 표시
- [x] 중단과 Task 종료 경합 시 FAILED 기록 가능성 → 중단은 항상 CANCELLED
- [x] Hub 재시작 시 사이클 중단 → GetTask/SubscribeToTask로 이어 가기, 에이전트 재시작은 사유와 함께 FAILED
- [x] 문서와 코드 차이 정리 (PRD/README/USER_SCENARIO)
- [x] 점검: 부분 수정·중단·재시작 시나리오, mock 데모, 실제 브라우저(Edge) 실습 클릭 전 과정, 스크린샷 재촬영

## 3. 문서화

- [x] `README.md`
- [x] `docs/USER_SCENARIO.md`
- [x] 루트 `TODO.md` 갱신
