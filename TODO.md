# 전체 진행 현황

> 상태 표기: `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[-]` 보류(선택 과제)
> 단계별 상세 작업은 각 폴더의 `docs/TODO.md`에서 관리한다.

## M0. 공통 기반

- [x] 기획 문서 작성 (`plan.md`)
- [x] 루트 구조: `README.md`, `TODO.md`, `.env.example`, `.gitignore`
- [x] `.env` 구성 (임시 API 키, 완료 후 사용자가 수동 삭제)
- [x] 단계별 폴더 및 `docs/PRD.md` 작성 (4개)
- [x] 공통 설계 확정: 시나리오 규칙, DB 스키마, UI 디자인 시스템(모노톤)

## M1. Stage 1 — Standalone

- [x] 구현 (`01_standalone/docs/TODO.md`)
- [x] 점검: 시나리오 결과 일치 (mock / LLM)
- [x] 문서화: `README.md`, `docs/USER_SCENARIO.md`

## M2. Stage 2 — API

- [x] 구현 (`02_api/docs/TODO.md`)
- [x] 점검: 3개 프로세스 분리 실행, 시나리오 결과 일치
- [x] 문서화

## M3. Stage 3 — MCP

- [x] 구현 (`03_mcp/docs/TODO.md`)
- [x] 점검: mock / LLM 모드 모두 사이클 완주
- [x] 문서화

## M4. Stage 4 — A2A

- [x] 구현 (`04_a2a/docs/TODO.md`)
- [x] 점검: Agent Card 수집, Task/Artifact/Streaming/input-required 동작
- [x] 문서화

## M5. 마무리

- [x] 루트 README 비교표 최종 정리 (실측 포함)
- [x] 전 단계 일괄 실행 점검 (4개 단계 동시 기동, 결과 동일)
- [-] (선택) A2A Peer 모드, A2A + MCP 결합
- [x] 루트 `docs/` 문서화: 단계별 개발 완료 보고서 4종, 프로젝트 개요, 기술 비교 종합, A2A 발표 자료

## 이슈 / 결정 로그

| 날짜 | 내용 |
| --- | --- |
| 2026-10-15 | LLM 모델 기본값 `claude-opus-5-5`, 단순 판단 작업은 effort `low` 사용 |
| 2026-10-15 | 패키지 관리: 단계별 uv 프로젝트 (`uv sync`, `uv run run.py`) |
| 2026-10-15 | 포트 대역: Stage N → 8N00(대시보드), 8N01(Dev), 8N02(QA) |
| 2026-10-15 | 기동 시 이전 프로세스에서 끝나지 못한 사이클은 CANCELLED 처리 (Stage 1~3) |
| 2026-10-15 | Stage 4 Hub 자체 Agent Card, Peer 모드, A2A + MCP 결합은 선택 과제로 보류 |
| 2026-10-15 | Stage 1 실습 모드 추가 (데모 실행 유지, 검증/개발 작업대, 부서원 권한, AI 초안) |
| 2026-10-15 | Stage 2를 Hub(관제) / Dev(발행·수정) / QA(검증·이관·확정) 3개 웹 서비스로 리팩토링, 실습 모드 분산 적용 |
| 2026-10-15 | Stage 3·4도 Hub / Dev / QA 3개 웹 서비스 + 실습 모드로 개선 (MCP: Host 폴링 + AI 전달, A2A: Task 대기 + 진행 스트리밍 + CancelTask) |
| 2026-10-15 | 빌드 산출물은 Dev가 `build_url`로 제공, QA가 내려받아 검증 (전 단계 공통 개념) |
| 2026-10-16 | Stage 2~4 Hub 실습 시작/사이클 중단 버튼 미동작 수정 (`workbench.js`를 모든 화면에서 로드), 실제 브라우저 클릭 점검 도입 |
| 2026-10-16 | Stage 3 중단 후 새 실습 시 이전 사이클 작업 지시를 CANCELLED로 정리 |
| 2026-10-16 | 루트 `docs/` 개발 완료 보고서·기술 비교·A2A 발표 자료 작성 |
| 2026-10-16 | 보고서의 알려진 이슈 전면 수정 (중단 전파, 진행 중 초기화 거절, 전달 성공 후 확인, Hub 재시작 복구, 부분 수정 교착 해소 등), 보고서 퇴고, 발표 자료 웹 슬라이드 제작 |
