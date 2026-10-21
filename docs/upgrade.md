# 기존 사용자 업그레이드 — AI 에이전트용 절차서

이 문서는 **AI 에이전트(Claude Code·Codex 등)가 읽고 그대로 실행하는 절차서**다. 사용자는 에이전트에게 이렇게만
말하면 된다.

> https://github.com/BrilliantTiger/llmwiki_json/blob/main/docs/upgrade.md 를 읽고 내 llmwiki 를 업그레이드해 줘

대상은 llmwiki_json 을 설치해 쓰던 사람이다. 공개 저장소를 clone 해 그 안에 위키를 쌓았든, 파일을 복사해 자기
private 저장소를 만들었든 같다.

**설치한 폴더 그대로 업그레이드한다.** `~/opmd/llmwiki` 에 설치했으면 `~/opmd/llmwiki` 안에서, `~/llmwiki_json`
에 설치했으면 `~/llmwiki_json` 안에서 끝난다. 코드(`scripts/`·`viewer/`·`tools/`…)와 위키(`wiki/`·`raw/`)는 지금처럼
그 폴더에 같이 있고, 새 코드는 그 폴더의 코드 파일에 합쳐진다. 새 폴더를 만들지 않고, 폴더·remote 이름을 바꾸지
않는다. 사용자가 고친 코드와 `INIT.md` 에 더한 규칙은 남는다.

## 에이전트가 지킬 것

- 사용자에게는 사용자가 쓰는 언어로 말한다.
- **정본 `wiki/**/*.json` 을 손으로 고치지 않는다.** 판 옮기기는 `update` 가 부르는 `migrate` 만 한다.
  `raw/` 는 고치지 않는다.
- 아래 **멈춤** 조건을 만나면 그 자리에서 멈추고 사용자에게 상황과 선택지를 알린다. 추측으로 넘어가지 않는다.
- `update` 의 실제 적용과 `git push` 는 사용자가 계획을 보고 승낙한 뒤에 한다. 사용자가 처음부터 "묻지 말고
  끝까지"라고 했으면 묻지 않는다.
- **공개 저장소(BrilliantTiger/llmwiki_json)로는 절대 push 하지 않는다.** 위키는 사용자의 개인 저장소로만 간다.
- 보안정보(토큰·비밀번호·접속 주소)는 보고에 옮기지 않고 `(접속 정보 생략)` 으로 쓴다.
- 명령의 `W` 는 사용자가 설치한 폴더, `PY` 는 Python 3.9+ 인터프리터(macOS·Linux `python3`, Windows `py -3`),
  `B` 는 `git -C W branch --show-current` 다. 자리표시자는 명령마다 실제 경로로 풀어 쓴다(변수에 담지 않는다).

## 0. 지금 상태 확인

1. `W` 를 정한다. 사용자가 말한 경로, 없으면 지금 작업 디렉터리, 그것도 아니면 전역 지침(`~/.claude/CLAUDE.md`,
   `~/.codex/AGENTS.md`)의 `## llmwiki_json 개인 지식 위키` 절에 적힌 정본 경로(`…/wiki/**/*.json` 의 앞부분)다.
   `W/wiki/` 와 `W/scripts/llmwiki.py` 가 있어야 한다. 없으면 **멈춤.**
2. `W/llmwiki.lock` 이 있으면 이 폴더는 코드를 다른 폴더에 둔 "데이터 repo" 다(예전 절차로 나눈 것). **멈춤** —
   사용자에게 알리고, 한 폴더로 되돌릴지 묻는다.
3. 아래를 모아 둔다(보고에 쓴다).
   - `git -C W status --porcelain` — 비어 있지 않으면 **멈춤.** 무엇이 바뀌었는지 보여 주고 커밋할지 치울지 묻는다.
     (주기 루틴이 돌던 중이면 끝나기를 기다린다.)
   - `git -C W log --oneline -1` — 되돌릴 자리다.
   - `git -C W remote -v` — 어느 것이 개인 저장소인지(6단계 push 대상).
   - `cat W/VERSION` (옛 설치에는 없을 수 있다).
   - 주기 ingest 루틴: `~/.llmwiki/installed-routine` (Windows `%USERPROFILE%\.llmwiki\installed-routine`) 이 있으면
     그 JSON 의 `agent`·`interval`·`push`·`remote` 를 적어 둔다. 없으면 루틴이 없다.
   - `git -C W status -sb` 가 공개 저장소 기준 "behind N" 을 보여도 **정상이다.** `git pull` 로 받지 않는다 — 위키
     commit 이 있어 합쳐지지 않는다. 아래 `update` 가 코드만 골라 얹는다.

## 1. 백업

```bash
git -C W bundle create "W/.git/llmwiki-backup-$(date +%Y%m%d).bundle" --all
```

Windows: `git -C W bundle create "W\.git\llmwiki-backup-$(Get-Date -Format yyyyMMdd).bundle" --all`.
`.git` 안이라 폴더 내용에 섞이지 않는다. 되돌릴 때는 `git clone <bundle> <새 경로>` 다.

## 2. 갱신할 코드 준비

새 `update` 는 v1.1.0 이후 코드에 있다. `W/scripts/llmwiki.py` 에 `update_in_place` 가 있으면(`grep -c update_in_place
W/scripts/llmwiki.py` 가 1 이상) 그것을 그대로 쓴다: `U` = `W`. 3단계로 간다.

없으면(옛 설치) 새 코드를 **임시로** 받아 그것으로 `W` 를 갱신한다. 설치 폴더가 아니라 끝나면 지운다.

```bash
T=$(mktemp -d) && git clone -q --depth 1 https://github.com/BrilliantTiger/llmwiki_json "$T/llmwiki_json"
```

`U` = `$T/llmwiki_json`. Windows: `$T = Join-Path $env:TEMP "llmwiki-$(Get-Random)"; git clone -q --depth 1
https://github.com/BrilliantTiger/llmwiki_json "$T\llmwiki_json"`.

## 3. 그 자리에서 갱신

```bash
PY U/scripts/llmwiki.py --root W update --dry-run
```

공개 저장소를 `W` 의 git 으로 받아(숨은 ref `refs/llmwiki/upstream` 하나) 이 폴더 코드가 어느 판 위에 있는지 찾고
(지난번 기록 → 같은 이력이면 merge-base → 이력이 따로면 가장 가까운 판), 새 판과 3-way 로 비교한 목록을 보인다:

- **새 판으로** — 이 폴더에서 손대지 않은 코드 파일. 새 판으로 바뀐다.
- **합친다** — 양쪽이 고쳤지만 겹치지 않는 파일(예: `INIT.md` 끝에 더한 개인 규칙). 둘 다 남는다.
- **지운다** — upstream 이 지운 파일.
- **남긴다** — 이 폴더에서만 고친 파일. 그대로 둔다.
- **충돌** — 같은 자리를 양쪽이 고쳤다. 하나라도 있으면 **멈춤.** 아무것도 쓰지 않았다. 파일 목록을 보여 주고,
  사용자 수정을 살릴지(손으로 합쳐 커밋) 버릴지(`git -C W checkout <기준 commit> -- <파일>` 뒤 커밋) 묻는다.

`wiki/`·`raw/`·`index/`·`viewer/public/data/`·`tools/config/`·`bench/` 는 이 폴더의 데이터라 손대지 않는다.
사용자에게 요약하고 승낙을 받으면:

```bash
PY U/scripts/llmwiki.py --root W update --yes
```

코드를 얹고, 얹은 새 코드로 정본 스키마를 옮기고(`migrate`, 사본에서 검증한 뒤 교체), build·validate·lint 를 돌린 뒤
`W` 에 커밋한다(`코드 v<old>→v<new> (<sha>→<sha>) 갱신, schema <a>→<b>`). 기준 판은 `W/tools/upstream.json` 에 남아
다음 갱신이 그것을 쓴다.

- `validate 0 errors` 가 아니면 **멈춤** — 커밋하지 않았다. 남은 error 를 보여 준다.
- 끝나면 `PY W/scripts/llmwiki.py repair --dry-run` 으로 고칠 block 이 있는지 본다. **적용하지 않는다** — 목록을
  보고에 넣고, 사용자가 원하면 `repair` 로 적용한다.

## 4. 임시 코드 지우기

2단계에서 받았으면 `rm -rf "$T"` (Windows `Remove-Item -Recurse -Force $T`).

## 5. hook 다시 설치 (같은 폴더)

hook·MCP·전역 지침·`llmwiki` 명령을 `W` 의 새 코드로 다시 건다. 경로는 그대로 `W` 다.

```bash
W/scripts/install.sh --here --yes --ingest-routine <루틴>
```

- `<루틴>` 은 0단계에서 본 `agent`(claude·codex), 루틴이 없었으면 `none`. 주기가 기본(3600)이 아니었으면
  `--routine-interval <interval>`, 루틴이 개인 저장소로 push 했으면(`push: true`) `--remote-name <remote>` 를 더한다.
- Windows: `pwsh -File W\scripts\install.ps1 -Here -Yes -IngestRoutine <루틴>`.
- 끝의 `== 확인` 목록에서 쓰지 않는 클라이언트의 `codex.*`·`claude.*` FAIL 과 `FAIL codex.trust` 는 무시한다
  (`codex.trust` 는 사용자가 Codex 를 한 번 띄워 hook 을 신뢰해야 풀린다 — 6단계에서 안내). 그 밖의 FAIL 은 **멈춤.**
- `~/.local/bin` (Windows `%LOCALAPPDATA%\llmwiki`) 이 PATH 에 없다는 안내가 나오면 보고에 옮긴다. PATH 는 고치지 않는다.

## 6. push 와 마무리

1. push 대상: `git -C W remote -v` 에서 `BrilliantTiger/llmwiki_json` 을 가리키지 않는 사용자 저장소. 이름이 아니라
   주소로 고른다. 후보가 여럿이고 `git -C W rev-parse --abbrev-ref @{u}` 가 그중 하나를 가리키지 않으면 묻는다.
   그런 remote 가 없으면 push 하지 않고, 개인 저장소를 붙이는 법을 알린다:
   `W/scripts/install.sh --here --private-remote <URL>` 또는 `--gh-create <이름>`.
2. `git -C W fetch <remote>` 뒤 `git -C W log --oneline HEAD..<remote>/B` 가 비어 있는지 본다. 다른 기계가 그동안
   push 했으면 `git -C W pull --rebase <remote> B` 를 하고 3단계 `update --yes` 를 다시 돌린다.
3. 승낙을 받고 `git -C W push <remote> B`.
4. 사용자에게 보고한다:
   - 한 일과 커밋, 백업 bundle 경로
   - 코드 판(전→후), 정본 판(전→후), validate error 수, `repair --dry-run` 결과
   - 남긴 사용자 수정 파일, 합친 파일
   - 사용자가 할 일: Codex 를 쓰면 한 번 띄워 'Hooks need review' 에서 hook 을 신뢰(`t`, UserPromptSubmit 과
     SessionStart 둘 다), 열려 있던 Claude Code·Codex 세션은 새로 열기, PATH 안내가 있었으면 그것
   - 같은 위키를 쓰는 **다른 기계**: 그 기계의 폴더에서 `git pull` 하면 새 코드도 같이 온다(코드가 이 폴더의 git 에
     있다). 그다음 그 폴더에서 `scripts/install.sh --here` 를 한 번 돌린다.
   - 앞으로 갱신은 그 폴더에서 `llmwiki update` (= `scripts/install.sh update`) 하나다.

## 문제가 생기면

- 갱신을 되돌린다: `git -C W reset --hard <0단계에서 적은 commit>`. push 전이면 그것으로 끝이다.
- 설치를 되돌린다: `W/scripts/install.sh uninstall` — 이 설치기가 넣은 것만 뺀다.
- 위키 전체를 되돌린다: 1단계 bundle 에서 `git clone`.
