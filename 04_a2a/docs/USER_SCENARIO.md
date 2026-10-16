# User Scenario — Stage 4. A2A

## 등장인물

| 역할 | 사용하는 웹 | 설명 |
| --- | --- | --- |
| 릴리즈 매니저 | Hub 웹 (8400) | 사이클 시작, 관제, 최종 릴리즈 승인/반려 |
| 플랫폼 운영자 | Hub 웹 (8400) | Agent Card, A2A 메시지, Task 상태 점검 |
| 개발부서원 (최개발, 정개발) | Dev 웹 (8401) | 위임받은 Task 처리: 티켓 수정, 버전 발행 |
| 검증부서원 (김검증, 이검증, 박검증) | QA 웹 (8402) | 위임받은 Task 처리: 판정, 이슈 등록, 이관 또는 확정 결정 |

준비: `uv run run.py`. 역할별로 브라우저 창을 열면 실제 협업처럼 진행할 수 있습니다.
`http://localhost:8400`, `http://localhost:8401/?as=최개발`, `http://localhost:8402/?as=김검증` …

## 시나리오 1. 실습 — 에이전트 뒤에서 사람이 일한다

| 단계 | 담당 / 웹 | 사용자 행동 | 시스템 반응 |
| --- | --- | --- | --- |
| 1 | 매니저 / Hub | **실습 시작** | Hub가 Agent Card로 라우팅 → Dev Agent에 Task [release-build] (`mode: manual`). Hub 배너 "개발부서 차례" |
| 2 | 최개발 / Dev | 위임 업무 카드에서 **v1.0.0 발행** | Task completed + release Artifact → Hub가 QA Agent에 Task [verify-release] |
| 3 | 검증부서원 / QA | 담당 항목 **실행** → **PASS** / **FAIL**(이슈 폼, AI 초안) | 판정마다 Task 상태 메시지가 Hub로 스트리밍 → Hub 상단 진행률, 배너 "최근 진행: TC-07 PASS 판정 (이검증)" |
| 4 | 박검증 / QA | 모든 항목 판정 후 **Dev 이관** | QA Task completed + test-report·issues Artifact → Hub 판단 `transfer_to_dev` → Dev Agent에 Task [fix-and-release] |
| 5 | 개발부서원 / Dev | 티켓 **착수 → 수정 완료**(AI 초안) | "ISSUE-1 착수 (최개발)", "ISSUE-1 수정 완료 (최개발)"가 Hub로 스트리밍 |
| 6 | 정개발 / Dev | 릴리즈 노트(AI 초안) → **v1.1.0 발행** | Task completed (release Artifact에 fixes 포함) → 다시 QA로 위임 |
| 7 | 검증부서원 / QA | 수정 확인, 회귀 TC-11 이슈 등록 → **Dev 이관** | 같은 방식으로 반복 |
| 8 | 검증부서원 / QA | v1.2.0 전 항목 PASS → **버전 확정** | QA Task가 **input-required**로 멈춤 → Hub 판단 `request_approval` → 승인함 + Claude 요약 |
| 9 | 매니저 / Hub | **Approvals**에서 의견 입력 후 **승인** | 같은 Task로 회신 → approval Artifact → 사이클 "승인" |
| 10 | 운영자 / Hub | **Message Trace** | 각 Task의 SSE 응답에 부서원 진행 메시지가 status-update로 기록 |

## 시나리오 2. 데모 — 에이전트가 스스로

| 단계 | 사용자 행동 | 확인 내용 |
| --- | --- | --- |
| 1 | Hub **데모 실행** | 같은 스킬, 같은 Task 흐름. 에이전트가 LLM·규칙으로 처리 |
| 2 | Dev 웹 / QA 웹 | "데모 사이클 — 관찰용" |
| 3 | 승인함 | 전체 통과 후 승인 요청 → 승인 또는 반려 |

## 시나리오 3. 중단과 장애

| 상황 | 시스템 반응 |
| --- | --- |
| 실습 중 Hub **사이클 중단** | 진행 중인 Task에 `CancelTask` → 에이전트가 부서원 대기를 취소, Task canceled, 사이클 CANCELLED |
| 승인 대기 중 **사이클 중단** | QA의 input-required Task 취소, 승인 건은 CANCELLED로 닫혀 승인함 처리 이력으로 이동 |
| Dev가 일부 티켓만 수정해 발행 | QA 작업대의 "이관 대상"에 미수정 이슈(TRANSFERRED)가 포함되어 **Dev 이관** 가능 → Dev는 남은 티켓을 이어서 처리 |
| 위임받지 않았는데 QA 웹에서 판정 | 409 "지금은 검증부서 차례가 아닙니다 (Hub가 위임한 검증이 없습니다)" |
| QA 웹에서 개발부서원으로 작업 | 403 (QA 에이전트 명부에 없음) |
| QA Agent 중지 후 사이클 시작 | Agent Card 수집 실패 → "verify-release 스킬을 제공하는 에이전트가 없습니다" |
| Hub 재시작 | 에이전트의 Task는 살아 있으므로 Hub가 `GetTask`로 확인하고 `SubscribeToTask`로 다시 구독해 사이클을 이어 감 (승인 대기도 같은 건으로 복원). 타임라인에 "Hub 재시작: … 다시 연결합니다" |
| 에이전트 재시작 | 에이전트의 Task 저장소가 메모리(`InMemoryTaskStore`)라 진행 중 Task가 사라짐 → 사이클 FAILED, Overview에 "연결이 끊겼습니다 …" 사유 표시 (새로 시작) |

## 관찰 포인트 (학습)

- Hub는 데모와 실습을 구분하지 않는다. 사람이 일하든 LLM이 일하든 Task·진행 메시지·Artifact로만 본다.
- 사람의 장시간 작업이 working 상태의 Task 하나로 표현되고, 진행 상황은 에이전트가 먼저 알려 준다(Stage 3의 폴링과 대비).
- 최종 승인은 input-required라는 표준 상태로 요청자에게 되묻는다.
