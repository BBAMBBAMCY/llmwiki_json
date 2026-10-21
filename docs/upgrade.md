# 기존 사용자 업그레이드 — AI 에이전트용 절차서

이 문서는 **AI 에이전트(Claude Code·Codex 등)가 읽고 그대로 실행하는 절차서**다. 사용자는 에이전트에게 이렇게만
말하면 된다.

> https://github.com/BrilliantTiger/llmwiki_json/blob/main/docs/upgrade.md 를 읽고 내 llmwiki 를 업그레이드해 줘

대상은 v1.1.0 이전에 llmwiki_json 을 쓰기 시작한 사람이다 — 코드(`scripts/`·`viewer/`)와 위키(`wiki/`·`raw/`)가 한
저장소에 있다. 공개 저장소를 clone 해 그 안에 위키를 쌓았든, 파일을 복사해 자기 private 저장소를 만들었든 같다.
v1.1.0 부터는 **코드 repo**(이 저장소의 clone, 갱신만 받는다)와 **데이터 repo**(사용자의 위키)를 git 으로 나눈다.
**위키 폴더는 그대로다.** 사용자가 쓰던 폴더(`W`)가 계속 그 위키의 자리다. 코드는 `W` 옆에 저장소 이름
그대로 `llmwiki_json/` 으로 clone 한다(`git clone` 이 붙이는 기본 이름). 그 밖의 이름을 지어 붙이지 않는다.
이 절차는 `W` 를 데이터 repo 로 바꾸고, 옆에 코드를 clone 하고, 정본 스키마를 새 판으로 옮긴다.

업그레이드 뒤 모습 (예: `W` = `~/llmwiki`):

```
~/llmwiki/        ← 쓰던 위키 폴더 그대로 (경로·이름·remote 를 바꾸지 않는다)
  wiki/ raw/ config/ LOCAL.md llmwiki.lock CLAUDE.md AGENTS.md   ← 데이터 repo
  .llmwiki/       ← 파생물·백업 (git 제외)
~/llmwiki_json/   ← 코드 repo (공개 저장소 clone — `llmwiki update` 가 여기서 받는다)
```

## 에이전트가 지킬 것

- 사용자에게는 사용자가 쓰는 언어로 말한다.
- **정본 `wiki/**/*.json` 을 손으로 고치지 않는다.** 판 옮기기는 `migrate`(아래 `update` 가 부른다)만 한다.
  `raw/` 는 읽지도 고치지도 않는다.
- 아래 **멈춤** 조건을 만나면 그 자리에서 멈추고 사용자에게 상황과 선택지를 알린다. 추측으로 넘어가지 않는다.
- `--apply`(adopt)와 `git push` 는 사용자가 계획을 보고 승낙한 뒤에 한다. 사용자가 처음부터 "묻지 말고 끝까지"
  라고 했으면 묻지 않는다.
- **코드 저장소(BrilliantTiger/llmwiki_json)로는 절대 push 하지 않는다.** 위키는 사용자의 개인 저장소로만 간다.
- 보안정보(토큰·비밀번호·접속 주소)는 보고에 옮기지 않고 `(접속 정보 생략)` 으로 쓴다.
- 명령은 아래 표기를 쓴다. `W` 는 사용자가 쓰던 위키 폴더(옛 위키 저장소), `C` 는 언제나 `W` 와 같은 부모
  폴더의 `llmwiki_json`(예: `W` = `~/llmwiki` 이면 `C` = `~/llmwiki_json`), `PY` 는 Python 3.9+ 인터프리터다.

| | macOS·Linux | Windows (PowerShell) |
|---|---|---|
| `PY` | `python3` | `py -3` (없으면 `python`) |
| `B` | `git -C W branch --show-current` (보통 `main`) | 같다 |
| 설치기 | `C/scripts/install.sh` | `C\scripts\install.ps1` (`pwsh -File` 로) |
| `LW` | `PY C/scripts/llmwiki.py --root W` | `py -3 C\scripts\llmwiki.py --root W` |

**경로와 이름은 사용자의 것이다.** `W` 는 옮기지도 이름을 바꾸지도 않는다 — 업그레이드 뒤에도 같은 폴더에서
같은 방식으로 쓴다. remote 이름도 바꾸지 않는다. 새로 생기는 폴더는 코드 repo `llmwiki_json` 하나뿐이다.

`LW` 는 설치기가 놓는 `llmwiki` 명령과 같다. 설치기가 hook 에 쓰는 인터프리터는 따로 고르므로 `PY` 와 달라도
된다(3.9 이상이면 된다). 새 shell 이 아니면 PATH 에 `llmwiki` 가 아직 없을 수 있으니 이
절차에서는 `LW` 를 쓴다. `W`·`C`·`LW` 는 자리표시자다 — 명령마다 실제 경로로 풀어 쓴다(zsh 는 `$LW` 처럼 변수에
담은 명령을 낱말로 나누지 않으니 변수에 담지 않는다).

## 0. 지금 상태 확인

1. `W` 를 정한다. 사용자가 말한 경로, 없으면 지금 작업 디렉터리, 그것도 아니면 전역 지침
   (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) 의 `## llmwiki_json 개인 지식 위키` 절에 적힌 정본 경로
   (`…/wiki/**/*.json` 의 앞부분)다. `W/wiki/` 가 있어야 한다.
2. 이미 끝난 경우를 거른다:
   - `W/llmwiki.lock` 이 있다 → 이미 데이터 repo 다. `llmwiki update` (없으면 그 lock 의 코드 repo 에서
     `install.sh update`) 만 돌리고 6단계로 간다.
   - `W/scripts/llmwiki.py` 가 없고 `W/llmwiki.lock` 도 없다 → llmwiki 저장소가 아니다. **멈춤.**
3. 아래를 모아 둔다(보고에 쓴다).
   - `git -C W status --porcelain` — 비어 있지 않으면 **멈춤.** 무엇이 바뀌었는지 보여 주고 커밋할지 치울지
     사용자에게 묻는다. (주기 루틴이 돌던 중이면 끝나기를 기다린다.)
   - `git -C W remote -v` — 어느 remote 가 공개 코드 저장소이고 어느 것이 개인 저장소인지.
   - `git -C W log --oneline -1`(이 commit 이 되돌릴 자리다), `cat W/VERSION 2>/dev/null` (옛 clone 에는 없을 수 있다).
   - `git -C W status -sb` 가 공개 저장소 기준 "behind N" 을 보여도 **정상이다.** 공개 저장소를 pull 하지 않는다 —
     이 절차가 코드를 새로 clone 하고, 4단계 adopt 가 branch 가 공개 저장소를 따르지 않게 바꾼다.
   - 주기 ingest 루틴이 걸려 있는지: `~/.llmwiki/installed-routine` (Windows `%USERPROFILE%\.llmwiki\installed-routine`)
     이 있으면 그 JSON 의 `agent`·`interval`·`push`·`remote` 를 적어 둔다. 없으면 루틴이 없다.
4. **사용자가 코드를 직접 고쳤는지** 본다. 옛 저장소의 코드 파일은 adopt 가 지우고 새 코드로 대신한다. 사용자가
   고친 코드가 있으면 그 수정은 새 구조로 넘어가지 않는다.
   - `W` 가 공개 저장소 이력을 가지면(remote 중 하나가 `BrilliantTiger/llmwiki_json`):
     `git -C W fetch <그 remote>` 뒤 `git -C W log --oneline <그 remote>/main..HEAD -- scripts viewer tools tests .skills INIT.md`
     에 사용자가 만든 commit 이 있는지 보고, 있으면 `git -C W show --stat <commit>` 으로 무엇을 건드렸는지 본다.
     **코드 수정이 아닌 것**(멈추지 않는다): `tools/config/*.json`(ingest 가 프로젝트·tag 를 등록한다 — adopt 가
     `config/` 로 옮긴다), `INIT.md`·`CLAUDE.md`·`AGENTS.md`(사용자 규칙 — adopt 가 `LOCAL.md` 로 옮긴다).
     **코드 수정**: 그 밖의 `scripts/`·`viewer/`·`tools/`·`tests/`·`.skills/` 파일.
   - 이력이 따로면(복사해 만든 private 저장소): 이 자리에서는 판정하지 않는다. 4단계 `LW adopt W --dry-run` 의
     "지운다 (코드)" 목록을 사용자에게 보이며 그중 직접 고친 파일이 있는지 묻는다(`diff -q W/<파일> C/<파일>` 은
     코드가 그동안 바뀌어 대부분 다르니 판정 근거가 되지 못한다). 사용자가 "묻지 말라"고 했으면 고친 것이 없다고
     보고 진행하고, 보고에 그렇게 적는다.
   - 사용자가 고친 코드가 있으면 **멈춤.** 목록을 보여 주고, 그 수정은 코드 repo 쪽에 제안(PR)하거나 버려야 한다고
     알린다. 진행하라고 하면 계속한다(1단계의 백업에 남는다).

## 1. 백업

```bash
mkdir -p W/.llmwiki && git -C W bundle create "W/.llmwiki/backup-$(date +%Y%m%d).bundle" --all
```

Windows: `New-Item -ItemType Directory -Force W\.llmwiki; git -C W bundle create "W\.llmwiki\backup-$(Get-Date -Format yyyyMMdd).bundle" --all`.
백업도 `W` 안(`.llmwiki/`, git 이 보지 않는 자리)에 둔다.
만든 파일의 경로를 보고에 적는다. 되돌릴 때는 `git clone <bundle> <새 경로>` 다.

## 2. 코드 repo 준비

코드는 `W` 옆에 둔다: `C` = `W` 의 부모 폴더 + `/llmwiki_json`.

- **`W` 자신이 그 자리(`…/llmwiki_json`)면 멈춤.** 공개 저장소를 기본 이름으로 clone 해 그 안에 위키를 쌓은
  경우다. 코드와 위키가 같은 이름을 쓸 수 없으니 사용자에게 어떻게 할지 묻는다. 지어낸 이름으로 진행하지 않는다.
- `C` 가 없으면: `git clone https://github.com/BrilliantTiger/llmwiki_json C`
- `C` 가 있으면 그 repo 가 이 저장소의 clone 인지(`git -C C remote -v`) 보고 `git -C C pull --ff-only`.
  로컬 변경이 있어 실패하면 **멈춤.** 이 저장소의 clone 이 아닌 다른 것이면 **멈춤.**
- `cat C/VERSION` 이 `1.1.0` 이상인지 본다.

## 3. 설치기를 새 코드 repo 에서 돌린다

hook·MCP·전역 지침·`llmwiki` 명령을 새 코드와 데이터 경로로 바꾼다. **adopt 보다 먼저** 한다 — adopt 가 옛
`W/scripts/` 를 지우면 그것을 가리키던 hook 이 조용히 꺼진다.

```bash
C/scripts/install.sh --data W --yes --ingest-routine <루틴>
```

- `<루틴>` 은 0단계에서 본 `agent`(claude·codex), 루틴이 없었으면 `none`. 주기가 기본(3600)이 아니었으면
  `--routine-interval <interval>` 을 더한다. 루틴이 개인 저장소로 push 했으면(`push: true`) `--remote-name <remote>`
  를 더한다.
- Windows: `pwsh -File C\scripts\install.ps1 -Data W -Yes -IngestRoutine <루틴>`.
- 끝에 `== 확인` 목록이 나온다. 한 클라이언트만 쓰면(`--claude`·`--codex` 로 좁혔거나 한쪽만 설치됐으면) 다른
  쪽의 `codex.*`·`claude.*` FAIL 은 무시한다. `FAIL codex.trust` 는 사용자가 Codex 를 한 번 띄워 hook 을 신뢰해야 해서 나는 것이고
  (6단계에서 안내), 그 밖의 FAIL 은 **멈춤.**
- adopt 전이라 `ok   layout: 단일 repo …` 줄이 나오는 것은 정상이다.
- `~/.local/bin` (Windows `%LOCALAPPDATA%\llmwiki`) 이 PATH 에 없다는 안내가 나오면 보고에 옮긴다. PATH 는 고치지 않는다.

## 4. adopt — 옛 저장소를 데이터 repo 로

```bash
LW adopt W --dry-run
```

계획을 읽고 사용자에게 요약한다:

- **지운다 (코드)** — 개수만. 0단계 4번에서 찾은 사용자 수정 파일이 여기 있는지.
- **남긴다** — 코드 디렉터리 안이지만 코드 repo 에 없는 사용자 파일. 지우지 않는다. 옮길 곳은 사용자가 정한다.
- **옮긴다** — `tools/config/*.json` → `config/`.
- **git 추적에서 뺀다** — 커밋돼 있던 파생물(`index/`, `viewer/public/data/`). 이제 `.llmwiki/` 아래에 만들어진다.
- **remote … 는 코드 저장소다** — 공개 저장소를 가리키는 remote 다. 이름·주소는 그대로 두고, branch 가 그것을
  따르고 있었으면 코드 저장소가 아닌 remote 가 하나뿐일 때 그쪽으로 잇는다(여럿이면 끊어 둔다 — 6단계에서 고른다).
  이 remote 로는 push 하지 않는다.
- **LOCAL.md 로** — 옛 `INIT.md` 에 사용자가 더한 규칙. "검토 필요" 수.
- **충돌** — 하나라도 있으면 **멈춤.**

승낙을 받으면:

```bash
LW adopt W --apply
```

- adopt 는 바꾼 뒤 build·validate·lint 를 돌려 결과를 보이고, **그 결과와 상관없이 커밋까지 한다.** 정본이 옛
  판이면 `build: exit 2`, `validate: exit 1`, 명령 자체의 종료 코드도 0 이 아닐 수 있다 — 아래 확인을 하고 5단계로
  간다. 출력을 파이프로 자르지 말고 끝까지 본다.
- 출력의 `wiki/ sha256 불변` 줄을 확인한다. 없거나 다르다고 하면 **멈춤.**
- 정본이 옛 스키마 판이면 validate·lint 가 `schema_version must be …` error 를 page 수만큼 낸다. **정상이다** —
  다음 단계가 옮긴다. 그 밖의 error 가 있으면 적어 둔다(5단계 뒤 다시 본다).
- adopt 는 바뀐 것을 한 커밋으로 남긴다. `커밋 …` 줄이 없으면 **멈춤.**
- `remote … 는 코드 저장소다` 줄이 있으면 `git -C W remote -v` 와 `git -C W rev-parse --abbrev-ref @{u}` 로 결과를 본다.

## 5. 스키마를 옮기고 맞춘다

```bash
LW update --yes
```

코드는 방금 받은 것이라 pull 은 "이미 같다"로 지나가고, 정본 판이 다르면 `migrate --dry-run` 요약 → `--apply`
(사본에서 검증한 뒤 교체) → 지침·skills 생성 → build·validate·lint → `llmwiki.lock` 갱신 → 커밋까지 한다.

- `validate 0 errors` 가 아니면 **멈춤.** 남은 error 를 보여 준다.
- 그다음 `LW repair --dry-run` 을 돌려 고칠 block 이 있는지 본다. **적용하지 않는다** — 목록을 보고에 넣고, 사용자가
  원하면 `LW repair` 로 적용한다. (repair 는 판이 다르면 거부되니 반드시 이 단계 뒤에 돌린다.)
- `W/LOCAL.md` 에 `## 검토 필요` 절이 있으면 각 절의 upstream 줄과 사용자 줄을 비교해 보여 준다. 사용자 줄이
  upstream 줄에 이 위키만의 규칙을 더한 것이면, 더한 부분만 규칙으로 남기고 upstream 인용과 표지를 지우자고
  제안한다. 승낙을 받으면 고치고 `LW guides` 로 `CLAUDE.md`·`AGENTS.md` 를 다시 만든 뒤 커밋한다.
  (LOCAL.md 는 정본이 아니라서 직접 고쳐도 된다.)

## 6. push 와 마무리

1. push 대상을 정한다: `git -C W remote -v` 에서 `BrilliantTiger/llmwiki_json` 을 가리키는 것을 **뺀** 사용자
   저장소. 이름(`private`·`origin` 등)이 아니라 주소로 고른다. adopt 가 branch 를 그 remote 로
   이어 두었으면 `git -C W rev-parse --abbrev-ref @{u}` 가 그것이다. 후보가 여럿이고 upstream 이 없으면 사용자에게
   묻는다. 고른 뒤 첫 push 는 `git -C W push -u <remote> B`.
   - 그런 remote 가 없으면 push 하지 않는다. 사용자에게 개인 저장소를 붙이는 법을 알린다:
     `C/scripts/install.sh --data W --private-remote <URL>` 또는 `--gh-create <이름>`.
2. `git -C W fetch <remote>` 뒤 `git -C W log --oneline HEAD..<remote>/B` 이 비어 있는지 본다. 다른 기계가 그동안
   push 했으면 `git -C W pull --rebase <remote> B` 을 하고 `LW update --yes` 를 다시 돌린다.
3. 승낙을 받고 `git -C W push <remote> B`.
4. `C/scripts/install.sh verify` (Windows `install.ps1 verify`) 를 돌린다. 쓰지 않는 클라이언트의 FAIL 과
   `codex.trust` 말고 FAIL 이 있으면 보고에 넣는다.
5. 사용자에게 보고한다:
   - 한 일과 커밋(adopt·update·LOCAL.md), 백업 bundle 경로
   - 정본 판(전→후), validate error 수, `repair --dry-run` 결과
   - 남긴 사용자 파일, LOCAL.md 정리 결과
   - 사용자가 할 일: Codex 를 쓰면 한 번 띄워 'Hooks need review' 에서 hook 을 신뢰(`t`), 열려 있던 Claude
     Code·Codex 세션은 새로 열기(전역 지침과 hook 이 바뀌었다), PATH 안내가 있었으면 그것
   - 같은 위키를 쓰는 **다른 기계**가 있으면 그 기계에서도 이 절차를 한 번 돌려야 한다. 그 기계가 먼저 pull 만
     하면 `scripts/` 가 사라져 hook 이 조용히 꺼진다 — 그쪽은 0단계에서 `W/llmwiki.lock` 이 이미 있으니
     2·3단계(그 기계의 `W` 옆에 코드 clone, `install.sh --data W`)만 하면 된다.
   - 앞으로 갱신은 `llmwiki update` 하나다.

## 문제가 생기면

- adopt 이후를 되돌린다: `git -C W reset --hard <adopt 앞 commit>` (0단계에서 적은 HEAD). push 전이면 그것으로 끝이다.
- 설치를 되돌린다: `C/scripts/install.sh uninstall` — 이 설치기가 넣은 것만 뺀다.
- 위키 전체를 되돌린다: 1단계 bundle 에서 `git clone`.
