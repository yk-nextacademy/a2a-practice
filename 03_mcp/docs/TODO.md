# TODO — Stage 3. MCP

> `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[-]` 보류

## 1. 구현

- [x] uv 프로젝트 구성, `run.py`(3개 프로세스 기동/종료)
- [x] Dev MCP Server: tools(최초 발행·티켓 등록·수정·발행·조회), resources, `/builds/{cycle_id}/{version}`
- [x] QA MCP Server: tools(테스트 실행·수정 확인·이슈 등록·이관·승인·조회), resources
- [x] Host: MCP 클라이언트 세션 관리, 도구 발견 및 Claude tools 변환
- [x] Host: Agent 루프(Claude, 수동 루프 + 트레이스 기록), mock 플래너
- [x] Host: 대시보드(resources 기반) + Agent Trace + Tools 화면

## 2. 점검

- [x] `tools/list` 결과가 Tools 화면에 표시
- [x] mock 모드 시나리오 결과 일치
- [x] LLM 모드에서 Claude가 사이클 완주, Agent Trace 기록 확인
- [x] 도구 오류 시 is_error 반환 및 복구 확인

## 4. 3개 웹 서비스 + 실습 모드 (추가 요청, 2026-10-15)

- [x] Hub 웹 = MCP Host: 관제(차례·폴링/전달 횟수, 병합 타임라인) + 데모 에이전트 + 실습 조정자
- [x] Dev 웹 + Dev MCP: 작업 지시(request_release / list_completed_work / ack_work), 개발 작업대
- [x] QA 웹 + QA MCP: 작업 지시(request_verification / …), 검증 작업대(판정·이슈·수정 확인·이관/확정 결정)
- [x] 조정자: 2초 폴링 → 전달 실행(Claude 조정 프롬프트 + 조정용 도구만 노출 / mock 조정자), 재시작 시 재개
- [x] 관찰 채널: Dev/QA 이벤트 SSE 중계, Agent Trace 실행 라벨
- [x] 점검: mock 데모·실습, Claude 조정자 실습 완주(전달 7회), 차례·권한, 전 화면
- [x] 문서: README, PRD, USER_SCENARIO

## 5. 알려진 이슈 정리 (2026-10-16)

- [x] Hub "실습 시작" 무반응 수정 (`base.html`이 `workbench.js`를 항상 로드)
- [x] 다른 사이클의 OPEN 작업 지시 정리 + 사이클 단위 중복 검사 (Dev `request_release`, QA `request_verification`)
- [x] 중단 즉시 정리: Host 전용 `cancel_work(cycle_id)` 도구 (Dev/QA), Host `abort()`가 호출. LLM에는 비노출
- [x] 전달 검증 후 ack: 필수 도구가 모두 성공해야 ack, 실패 시 재전달(3회), `transfer_issues`·`register_tickets` 재시도 멱등화
- [x] 포트·폴링 주기 표시를 설정값/상수로 (`run.py`, 사이드바, Overview, Dev 작업대)
- [x] 정리: `stop_on_approve` 인자, `integration_logs` 테이블, `integration_log.html`, 미사용 토스트 유형 제거, 데모 진행 막대(`test.progress`) 복구
- [x] 점검: mock 데모, 실제 브라우저 클릭 실습, 중단 직후 작업대, Host 재시작 재개, 화면 캡처 재촬영
- [x] 부분 수정 교착 해소: QA가 미반영(이월) TRANSFERRED 이슈를 다시 이관(`transfer_issues`·readiness), Dev `register_tickets`는 미반영 티켓 상태·담당자 유지, 작업대 문구 정리
- [x] 화면 캡처를 기본 포트(8300~8302) 사본에서 재촬영

## 3. 문서화

- [x] `README.md`
- [x] `docs/USER_SCENARIO.md`
- [x] 루트 `TODO.md` 갱신
