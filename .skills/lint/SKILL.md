---
name: lint
description: JSON 위키의 모순, 낡은 주장, 고아 페이지, 누락 링크, stale map을 점검한다. "린트 해줘", "위키 점검해줘" 요청 시 사용.
---

# Lint

규칙의 정본은 저장소 루트 `INIT.md`다 (데이터 repo 에서는 그것과 `LOCAL.md` 로 만든 `CLAUDE.md`·`AGENTS.md`).
아래 `llmwiki` 는 위키 CLI 다 — 데이터 repo 에서는 설치기가 놓은 `llmwiki` 명령, 코드와 데이터가 한 저장소면
`llmwiki` 와 같다.

1. `llmwiki validate`와 `llmwiki lint --json`을 실행한다.
2. 중복 page/block ID, 끊어진 link/source ref, source_text와 어긋난 block(fingerprint·data·refs·id·links), 미판정 conflict, 고아 page, 빈 summary, stale index/map을 확인한다. 통합 품질 경고(표 덤프 summary, 개념에 닿지 않은 source, compile debt, 200KB 초과 page, supersedes 없는 대체 문장)는 다음 ingest 에서 갚을 목록이다.
3. index/map만 믿지 않고 관련 canonical object를 `get --pointer`로 확인한다.
4. 의미상 모순, 최신 소스가 뒤집은 주장, 빠진 개념 페이지, 누락된 cross-reference와 데이터 공백을 추가로 점검한다.
5. 발견 사항을 심각도 순으로 보고한다. 수정은 사용자 확인 후 진행하고, JSON을 직접 고치지 않는다 — block은 `edit`, page 머리는 `render --format md` → `ingest --update`, 어긋난 block은 `repair --dry-run`으로 판정을 본 뒤 `repair`로 고친다.
6. 수정 후 build/validate/lint를 재실행하고 `wiki/log.jsonl`에 lint 기록을 append한다.
