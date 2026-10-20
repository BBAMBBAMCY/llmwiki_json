---
name: ingest
description: raw/의 새 소스를 JSON 위키에 통합한다. "ingest 해줘", "소스 처리해줘" 요청 시 사용.
---

# Ingest

규칙의 정본은 저장소 루트 `INIT.md`다.

1. `raw/`에서 대상 소스를 찾고 정독한다. 원본은 절대 수정하지 않는다.
2. 핵심 내용과 강조점을 사용자와 짧게 논의한다. 배치 요청이면 생략할 수 있다.
3. page는 CLI로만 쓴다. **`wiki/**/*.json`을 직접 쓰거나 고치지 않는다** — 손으로 쓴 JSON은 source_text·data·fingerprint·refs·links가 어긋나고, `validate`가 error로 잡는다.
   - 새 page: 원본을 그대로 넣을 때는 `python3 scripts/llmwiki.py ingest raw/… --type <type>`. 소스 요약·entity·concept·synthesis·project page는 frontmatter를 갖춘 markdown 초안 `<slug>.md`를 `wiki/` 밖(예: 임시 디렉터리)에 쓰고 같은 명령으로 ingest한다. 파일 이름이 slug가 된다.
   - 있는 block 고치기: `python3 scripts/llmwiki.py outline <page>`로 block id를 찾고, `python3 scripts/llmwiki.py edit <page> --block <id>`에 바꿀 markdown 조각을 `--text`·`--file`·stdin으로 준다. 조각이 여러 block이면 그 자리에 차례로 들어간다 — 덧붙이기와 나누기가 이것이다. 옛 block id는 `block_aliases`에 남는다.
   - page 머리(tags·projects·sources·supersedes·related·raw·summary) 바꾸기: `python3 scripts/llmwiki.py render <page> --format md > <slug>.md`로 받아 frontmatter를 고친 뒤 `ingest <slug>.md --update`.
4. 영속 ID·fingerprint·refs·links는 CLI가 본문에서 만든다. 근거는 frontmatter `sources`와 본문 `[[링크]]`로 연결한다.
   - frontmatter `raw:`에 원본의 저장소 상대경로(`raw/…`)를 **반드시** 적는다 — page의 `raw_ref`가 된다. 비어 있으면 주기 루틴이 그 소스를 아직 넣지 않은 것으로 보고 계속 다시 부른다.
   - `projects`·`tags`를 반드시 채운다 — 그래프의 프로젝트 그룹과 태그 색상이 이 두 필드에서만 나온다.
   - 본문 `[[링크]]`는 CLI가 page `links`에 같이 넣는다.
   - 뒤집힌 주장은 `supersedes`, 곁가지 관계는 `related`로 남긴다. 둘 다 그래프 선이 된다.
5. 상충은 `> ⚠️ 상충: …` 인용 block으로 적는다 — `kind: "conflict"`, `resolution.status: unresolved`가 된다. 새 주장으로 옛 주장을 지우지 않고 `history`와 `supersedes`를 남긴다.
6. 자격증명·접속 정보는 저장하지 않는다. 값이 있으면 `(접속 정보 생략)`으로 대체하거나 ingest를 중단한다.
7. `python3 scripts/llmwiki.py build`, `validate`, `lint`를 실행한다. build가 index와 JSON map, viewer data를 함께 갱신한다. validate가 block 어긋남을 내면 `repair --dry-run`으로 판정을 본 뒤 `repair`로 고친다.
8. `wiki/log.jsonl`에 ingest·edit 기록이 추가됐는지 확인하고 변경된 페이지·상충을 보고한다.
