"""데이터 repo 관리 — lock, 생성 지침 파일, skills 링크.

데이터 repo(`llmwiki.lock` 이 있는 root)는 코드를 갖지 않는다. 이 모듈은 코드 repo 쪽에서 데이터 repo 를
맞춘다: 어느 코드(판·커밋·경로)로 맞췄는지 lock 에 적고, 에이전트 지침(CLAUDE.md·AGENTS.md)을
`INIT.md`(코드) + `LOCAL.md`(데이터) 로 만들고, `.claude/skills`·`.codex/skills` 가 코드의 `.skills` 를
가리키게 한다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:  # package import
    from . import llmwiki_paths as P
except ImportError:  # run as a script or loaded from its file path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import llmwiki_paths as P  # type: ignore[no-redef]

GUIDE_FILES = ("CLAUDE.md", "AGENTS.md")
GUIDE_HEADER = "<!-- 생성 파일 — 직접 수정하지 말고 LOCAL.md를 수정하라. "
SKILL_LINKS = (".claude/skills", ".codex/skills")
SKILLS_DIR = ".skills"
# symlink 를 만들 수 없는 곳(권한 없는 Windows)에서 복사로 대신한 디렉터리의 표지. 이것이 있어야 우리 사본이다.
COPY_MARKER = ".llmwiki-copy"
ENV_SKILLS_COPY = "LLMWIKI_SKILLS_COPY"   # 시험·Windows: symlink 대신 복사를 강제한다
# 데이터 repo 에서 git 이 추적하지 않는 것: 파생물과 기계마다 다른 skills 링크.
DATA_GITIGNORE = (".llmwiki/", ".claude/skills", ".codex/skills")


# --------------------------------------------------------------------------- git
def git(cwd: Path, *args: str, timeout: float = 120.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def is_own_repo(path: Path) -> bool:
    """path 가 그 자신의 git 저장소 최상위인가 (다른 저장소 안의 디렉터리가 아니라)."""
    code, out = git(path, "rev-parse", "--show-toplevel", timeout=15.0)
    if code != 0 or not out:
        return False
    return Path(out.splitlines()[-1]).resolve() == Path(path).resolve()


def code_commit(code: Path | None = None) -> str:
    """코드 repo 의 HEAD. git 저장소가 아니면 빈 문자열."""
    code = (code or P.CODE_ROOT).resolve()
    if not is_own_repo(code):
        return ""
    status, out = git(code, "rev-parse", "HEAD", timeout=15.0)
    return out.splitlines()[-1].strip() if status == 0 and out else ""


# --------------------------------------------------------------------------- lock
def refresh_lock(data: Path, *, schema_version: str | None = None, code: Path | None = None) -> dict[str, Any]:
    """lock 을 지금 코드에 맞춰 다시 쓴다. schema_version 을 주지 않으면 lock 에 있던 값(없으면 코드의 판).

    내용이 같으면 쓰지 않는다 — 날짜(updated)만 바뀌는 쓰기로 데이터 repo 를 더럽히지 않는다.
    """
    current = P.read_lock(data) or {}
    schema = schema_version or str(current.get("schema_version") or P.SCHEMA_VERSION)
    payload = P.lock_payload(schema_version=schema, code_commit=code_commit(code), code=code)
    same = {k: v for k, v in payload.items() if k != "updated"} == {k: v for k, v in current.items() if k != "updated"}
    if same and current.get("updated"):
        payload["updated"] = current["updated"]
    payload["changed"] = P.write_lock(data, {k: v for k, v in payload.items() if k != "changed"})
    return payload


# --------------------------------------------------------------------------- 지침 파일
# INIT.md 는 코드와 데이터가 한 저장소일 때의 규칙이다. 데이터 repo 에서는 코드가 없으니 명령과 경로만
# 데이터 repo 의 것으로 옮겨 적는다. 개발 규칙(viewer 코드)은 코드 repo 의 일이라 싣지 않는다.
DATA_REWRITES = (
    ("python3 scripts/llmwiki.py", "llmwiki"),
    ("scripts/llmwiki.py ", "llmwiki "),
    ("`index/*.json`, `index/search.sqlite`(검색 색인), `viewer/public/data/**`",
     "`.llmwiki/index/*.json`, `.llmwiki/index/search.sqlite`(검색 색인), `.llmwiki/viewer-data/**` (git 에서 빠진다)"),
    ("`index/routes.json` → `index/catalog.json` → `index/map.json`",
     "`.llmwiki/index/routes.json` → `.llmwiki/index/catalog.json` → `.llmwiki/index/map.json`"),
    ("`index/search.sqlite`", "`.llmwiki/index/search.sqlite`"),
    ("tools/config/", "config/"),
)
DATA_PREAMBLE = (
    "이 저장소는 **데이터 repo** 다 — 정본(`wiki/`)·원본(`raw/`)·설정(`config/`)·`LOCAL.md` 만 둔다. "
    "코드는 `llmwiki.lock` 의 `code_path` 에 있고, 명령은 `llmwiki <하위 명령>` 으로 부른다 "
    "(`llmwiki` shim 이 이 디렉터리의 `llmwiki.lock` 으로 데이터 root 를 찾는다). 코드 갱신은 `llmwiki update` 다."
)


def drop_section(text: str, heading: str) -> str:
    """`## heading` 부터 다음 `## ` 앞까지를 지운다."""
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## |\Z)")
    return pattern.sub("", text)


def guide_text(data: Path, code: Path | None = None) -> str:
    """데이터 repo 의 CLAUDE.md·AGENTS.md 내용: 머리 + INIT.md(데이터 repo 용) + LOCAL.md. 기계마다 같은 글이다."""
    code = (code or P.CODE_ROOT).resolve()
    init = (code / "INIT.md").read_text(encoding="utf-8")
    body = drop_section(init, "개발 규칙")
    for old, new in DATA_REWRITES:
        body = body.replace(old, new)
    lines = body.rstrip("\n").split("\n")
    title_at = next((i for i, line in enumerate(lines) if line.startswith("# ")), -1)
    lines[title_at + 1:title_at + 1] = ["", DATA_PREAMBLE]
    try:
        local = (Path(data) / "LOCAL.md").read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        local = ""
    head = (GUIDE_HEADER + "INIT.md(코드 repo) + LOCAL.md 로 init·adopt·update·install 이 다시 만든다. -->")
    parts = [head, "", "\n".join(lines).rstrip("\n")]
    if local.strip():
        parts += ["", "<!-- 아래는 LOCAL.md -->", "", local]
    return "\n".join(parts) + "\n"


def sync_guides(data: Path, code: Path | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """CLAUDE.md·AGENTS.md 를 다시 만들고 skills 링크를 맞춘다. 같은 내용이면 쓰지 않는다."""
    data = Path(data).resolve()
    code = (code or P.CODE_ROOT).resolve()
    text = guide_text(data, code)
    report: dict[str, Any] = {"guides": {}, "skills": {}}
    excluded = exclude_nested_code(data, code, dry_run=dry_run)
    if excluded:
        report["exclude"] = excluded
    for name in GUIDE_FILES:
        path = data / name
        try:
            # 단일 repo 시절의 CLAUDE.md·AGENTS.md 는 INIT.md 를 가리키는 symlink 였다 — 그대로 쓰면 링크를
            # 따라가 INIT.md 를 고친다(지운 뒤라면 되살린다). 생성 파일은 언제나 제 파일이다.
            same = not path.is_symlink() and path.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        report["guides"][name] = "current" if same else ("would-write" if dry_run else "written")
        if not same and not dry_run:
            if path.is_symlink():
                path.unlink()
            path.write_text(text, encoding="utf-8")
    for rel in SKILL_LINKS:
        report["skills"][rel] = sync_skills(data / rel, code / SKILLS_DIR, dry_run=dry_run)
    return report


def guides_current(data: Path, code: Path | None = None) -> dict[str, bool]:
    """verify 용: 지침 파일과 skills 가 지금 코드·LOCAL.md 와 맞는가."""
    report = sync_guides(data, code, dry_run=True)
    out = {name: state == "current" for name, state in report["guides"].items()}
    out.update({rel: state in ("linked", "copied-current") for rel, state in report["skills"].items()})
    return out


def same_tree(a: Path, b: Path) -> bool:
    """두 디렉터리의 파일 목록과 내용이 같은가 (복사 표지는 빼고)."""
    def files(root: Path) -> list[str]:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_file() and p.name != COPY_MARKER and "__pycache__" not in p.parts)
    left, right = files(a), files(b)
    return left == right and all(filecmp.cmp(a / rel, b / rel, shallow=False) for rel in left)


def sync_skills(link: Path, target: Path, *, dry_run: bool = False) -> str:
    """link 가 target(코드의 .skills)을 가리키게 한다. 결과: linked · copied-current · relinked · copied ·
    kept-foreign(우리 것이 아닌 디렉터리라 두었다) · missing-target. dry-run 이면 would-* 를 낸다."""
    if not target.is_dir():
        return "missing-target"
    want_copy = os.environ.get(ENV_SKILLS_COPY, "").strip() not in ("", "0")
    if link.is_symlink():
        try:
            if Path(os.readlink(link)) == target or link.resolve() == target.resolve():
                if not want_copy:
                    return "linked"
        except OSError:
            pass
        if dry_run:
            return "would-relink"
        link.unlink()
    elif link.is_dir():
        if not (link / COPY_MARKER).is_file():
            return "kept-foreign"
        if same_tree(link, target):
            return "copied-current"
        if dry_run:
            return "would-copy"
        shutil.rmtree(link)
    elif link.exists():
        return "kept-foreign"
    elif dry_run:
        return "would-link"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not want_copy:
        try:
            link.symlink_to(target, target_is_directory=True)
            return "relinked"
        except (OSError, NotImplementedError):
            pass  # Windows 에서 개발자 모드·권한이 없으면 symlink 를 못 만든다 — 복사로 대신한다
    shutil.copytree(target, link, ignore=shutil.ignore_patterns("__pycache__"))
    (link / COPY_MARKER).write_text(f"{target}\n", encoding="utf-8")
    return "copied"


# --------------------------------------------------------------------------- init
WIKI_DIRS = ("sources", "entities", "concepts", "syntheses", "projects")
LOCAL_HEADER = """# 이 위키만의 규칙

이 파일은 이 데이터 repo 에만 해당하는 규칙을 적는 자리다. `CLAUDE.md`·`AGENTS.md` 는 코드 repo 의
`INIT.md` 뒤에 이 파일을 붙여 만드는 생성 파일이라, 규칙을 더하거나 바꾸려면 여기를 고친다
(`llmwiki guides` 또는 다음 `llmwiki update` 가 다시 만든다).
"""
LOCAL_TEMPLATE = LOCAL_HEADER + """
- (예) 회의록 소스는 `raw/meetings/` 에 둔다.
"""
# 새 위키의 groups.json — 코드 기본값에서 데모 프로젝트를 뺀 것. 프로젝트 그룹은 ingest 가 처음 만날 때 등록한다.
GENERIC_GROUPS = ("multi", "ungrouped")


def default_groups(code: Path | None = None) -> dict[str, Any]:
    code = (code or P.CODE_ROOT).resolve()
    value = json.loads((code / "tools" / "config" / "groups.json").read_text(encoding="utf-8"))
    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    return {**value, "project": {k: v for k, v in project.items() if k in GENERIC_GROUPS}}


CODE_DIR_NAME = "llmwiki_json"   # 코드 repo 폴더 이름 — 저장소 이름(git clone 기본값) 그대로다


def nested_code(data: Path, code: Path | None = None) -> str:
    """코드 repo 가 데이터 repo 안에 있으면 그 상대 경로(posix), 아니면 ""."""
    data = Path(data).resolve()
    code = (code or P.CODE_ROOT).resolve()
    if code == data or data not in code.parents:
        return ""
    return code.relative_to(data).as_posix()


def exclude_nested_code(data: Path, code: Path | None = None, *, dry_run: bool = False) -> str:
    """데이터 repo 안의 코드 repo 를 데이터 repo 의 git 이 보지 않게 `.git/info/exclude` 에 적는다.

    기존 설치 폴더를 그대로 쓰면 코드가 그 폴더 안에 산다. 코드 자리는 기계마다 다를 수 있어 커밋되는
    .gitignore 가 아니라 이 clone 에만 있는 exclude 에 적는다. 적은 줄(이미 있으면 그 줄)을 돌려준다.
    """
    rel = nested_code(data, code)
    if not rel or not is_own_repo(Path(data)):
        return ""
    line = f"/{rel}/"
    status, out = git(Path(data), "rev-parse", "--git-path", "info/exclude")
    if status != 0 or not out.strip():
        return ""
    path = Path(out.strip())
    path = path if path.is_absolute() else Path(data) / path
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if line in current.splitlines() or dry_run:
        return line
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current.rstrip("\n") + ("\n" if current.strip() else "")
                    + "# llmwiki 코드 repo (이 폴더 안에 둔 clone)\n" + line + "\n", encoding="utf-8")
    return line


def gitignore_lines(existing: str) -> str:
    """데이터 repo 의 .gitignore 에 파생물·skills 링크 줄을 더한다 (이미 있으면 그대로)."""
    lines = existing.splitlines()
    missing = [line for line in DATA_GITIGNORE if line not in lines]
    if not missing:
        return existing
    block = ["# llmwiki 파생물과 기계마다 다른 skills 링크 (llmwiki init/adopt 가 넣는다)", *missing]
    return ("\n".join(lines).rstrip("\n") + "\n\n" if existing.strip() else "") + "\n".join(block) + "\n"


def init_data_repo(target: Path, *, code: Path | None = None, use_git: bool = True,
                   message: str = "llmwiki 데이터 repo 를 시작한다") -> dict[str, Any]:
    """빈 데이터 repo 골격: wiki/ raw/ config/ LOCAL.md llmwiki.lock .gitignore, 생성 지침, 첫 커밋."""
    target = Path(target).expanduser().resolve()
    code = (code or P.CODE_ROOT).resolve()
    if target == code or code in target.parents:
        raise ValueError(f"{target} 는 코드 repo 안이다 — 데이터 repo 는 코드 repo 밖에 둔다")
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        raise ValueError(f"{target} 가 비어 있지 않다 — 기존 저장소를 데이터 repo 로 바꾸려면 `llmwiki adopt` 를 쓴다")
    created: list[str] = []
    for sub in WIKI_DIRS:
        keep = target / "wiki" / sub / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("", encoding="utf-8")
        created.append(f"wiki/{sub}/")
    (target / "raw").mkdir(parents=True, exist_ok=True)
    (target / "raw" / ".gitkeep").write_text("", encoding="utf-8")
    (target / "config").mkdir(parents=True, exist_ok=True)
    (target / "config" / "groups.json").write_text(
        json.dumps(default_groups(code), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "LOCAL.md").write_text(LOCAL_TEMPLATE, encoding="utf-8")
    (target / ".gitignore").write_text(gitignore_lines(""), encoding="utf-8")
    created += ["raw/", "config/groups.json", "LOCAL.md", ".gitignore"]
    lock = refresh_lock(target, schema_version=P.SCHEMA_VERSION, code=code)
    created.append(P.LOCK_NAME)
    guides = sync_guides(target, code)
    created += list(GUIDE_FILES)
    report: dict[str, Any] = {"root": str(target), "created": created, "lock": lock, "guides": guides}
    if use_git:
        if not (target / ".git").exists():
            status, out = git(target, "init", "-q", "-b", "main")
            if status != 0:   # 옛 git 은 -b 를 모른다
                status, out = git(target, "init", "-q")
            if status != 0:
                raise RuntimeError(f"git init 실패: {out}")
        git(target, "add", "-A")
        status, out = git(target, "commit", "-q", "-m", message)
        if status != 0:
            raise RuntimeError(f"첫 커밋 실패 (git user.name·user.email 을 확인하라): {out}")
        report["commit"] = git(target, "rev-parse", "HEAD")[1]
    return report


# --------------------------------------------------------------------------- adopt
# 단일 repo(코드+데이터)를 데이터 repo 로 바꿀 때 지우는 코드. 데이터 repo 에는 코드를 두지 않는다.
CODE_DIRS = ("scripts", "viewer", "tests", "tools/schema", "tools/parity", "docs", ".skills")
CODE_FILES = ("INIT.md", "VERSION", ".dockerignore")
# 단일 repo 시절에 커밋됐을 수 있는 파생물. 추적에서 빼고 지운다 — 분리 모드에서는 .llmwiki/ 에 다시 만든다.
DERIVED_DIRS = ("index", "viewer/public/data")
LOCAL_REVIEW = "검토 필요"


def file_digest(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wiki_digests(root: Path) -> dict[str, str]:
    """wiki/**/*.json 의 sha256 목록 — adopt 전후로 같아야 한다."""
    base = root / "wiki"
    return {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(base.rglob("*.json"))} if base.is_dir() else {}


def raw_listing(root: Path) -> dict[str, int]:
    base = root / "raw"
    if not base.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p.lstat().st_size for p in sorted(base.rglob("*"))
            if p.is_file() or p.is_symlink()}


def tracked(root: Path, rel: str) -> list[str]:
    status, out = git(root, "ls-files", "-z", "--", rel)
    return [x for x in out.split("\0") if x] if status == 0 else []


def init_history(code: Path) -> list[tuple[str, str]]:
    """(커밋, INIT.md 본문) — 지금 것이 먼저, 그다음 git 이력의 옛 판들 (중복 제거)."""
    versions = [("HEAD", (code / "INIT.md").read_text(encoding="utf-8"))]
    status, out = git(code, "log", "--format=%H", "--", "INIT.md")
    if status == 0:
        for commit in out.split():
            got, text = 0, ""
            proc = subprocess.run(["git", "show", f"{commit}:INIT.md"], cwd=str(code), capture_output=True,
                                  text=True, encoding="utf-8")
            got, text = proc.returncode, proc.stdout
            if got == 0 and text not in (body for _, body in versions):
                versions.append((commit, text))
    return versions


def local_lines(target_text: str, code: Path) -> dict[str, Any]:
    """target 의 INIT.md 에서 upstream 에 없던 줄을 고른다.

    upstream 은 코드 repo 의 INIT.md 중 target 과 가장 가까운 판이다(옛 clone 은 옛 판 위에 고쳤다 —
    지금 판과 비교하면 upstream 이 그 뒤에 바꾼 줄까지 로컬 수정으로 보인다). 그 판 대비
    - 더해진 줄은 그대로 LOCAL.md 로 옮기고,
    - 바뀐 줄(upstream 줄을 고쳐 쓴 것)과 지워진 upstream 줄은 자동으로 판정하기 어려워 `검토 필요` 로 옮긴다.
    """
    import difflib
    mine = target_text.splitlines()
    best: tuple[int, str, list[str]] | None = None
    for commit, text in init_history(code):
        base = text.splitlines()
        distance = sum(1 for line in difflib.ndiff(base, mine) if line[:1] in "+-")
        if best is None or distance < best[0]:
            best = (distance, commit, base)
    assert best is not None
    _, commit, base = best
    added: list[str] = []
    review: list[dict[str, list[str]]] = []
    matcher = difflib.SequenceMatcher(a=base, b=mine, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "insert":
            lines = list(mine[j1:j2])
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            if lines:
                added.extend(([""] if added else []) + lines)
        elif op == "replace":
            review.append({"upstream": base[i1:i2], "local": mine[j1:j2]})
        elif op == "delete":
            if any(line.strip() for line in base[i1:i2]):
                review.append({"upstream": base[i1:i2], "local": []})
    return {"base": commit, "added": added, "review": review}


def data_words(line: str) -> str:
    """단일 repo 의 명령·경로를 데이터 repo 의 것으로 (`python3 scripts/llmwiki.py` → `llmwiki`, `tools/config/` →
    `config/` …). 생성 지침이 INIT.md 에 하는 것과 같은 바꿈이다 — LOCAL.md 로 옮긴 로컬 규칙도 데이터 repo 에서 읽힌다."""
    for old, new in DATA_REWRITES:
        line = line.replace(old, new)
    return line


def local_section(found: dict[str, Any], source: str) -> str:
    """local_lines 의 결과를 LOCAL.md 에 붙일 글로."""
    parts: list[str] = []
    found = {**found, "added": [data_words(line) for line in found["added"]],
             "review": [{**item, "local": [data_words(line) for line in item["local"]]} for item in found["review"]]}
    if found["added"]:
        parts += [f"## {source} 에서 옮긴 규칙", "", *[line for line in found["added"]], ""]
    for item in found["review"]:
        parts += [f"## {LOCAL_REVIEW}: {source} 에서 upstream 줄을 바꾼 자리", "",
                  "<!-- 자동으로 판정하지 않았다. 이 위키에 필요한 규칙만 남기고 이 표지를 지운다. -->", ""]
        if item["upstream"]:
            parts += ["upstream (코드 repo INIT.md):", "", *[f"> {line}" for line in item["upstream"]], ""]
        if item["local"]:
            parts += ["이 저장소에서는:", "", *item["local"], ""]
        else:
            parts += ["이 저장소에서는 위 줄이 지워져 있었다.", ""]
    return "\n".join(parts).rstrip("\n")


# 코드 디렉터리 안에서 코드 repo 와 견주지 않고 지워도 되는 것 — 받아 온 패키지·빌드 산출물·캐시·파생물.
JUNK_PARTS = ("node_modules", "dist", "__pycache__", ".pytest_cache")


def code_removals(target: Path, code: Path) -> tuple[list[str], list[str]]:
    """지울 코드 경로와 남길 파일.

    코드 디렉터리라도 코드 repo 에 같은 경로가 없는 파일은 이 저장소만의 것(개인 스크립트·viewer 손질)이라
    지우지 않는다 — 지울 것은 코드 repo 에 있는 파일, 받아 온 패키지·캐시, 파생물뿐이다. 안이 모두 지울
    것인 디렉터리는 한 줄로 적는다(node_modules 를 파일마다 늘어놓지 않는다)."""
    derived = {target / rel for rel in DERIVED_DIRS}
    remove: list[str] = []
    keep: list[str] = []

    def removable(path: Path) -> bool:
        rel = path.relative_to(target)
        if path.name in JUNK_PARTS or path in derived:
            return True
        return (code / rel).exists() or (code / rel).is_symlink()

    def walk(folder: Path) -> bool:
        """folder 안이 모두 지울 것이면 True (그때는 부르는 쪽이 folder 를 한 줄로 적는다)."""
        verdicts: list[tuple[Path, bool]] = []
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.is_symlink() and not (child.name in JUNK_PARTS or child in derived):
                verdicts.append((child, walk(child)))
            else:
                verdicts.append((child, removable(child)))
        if all(ok for _child, ok in verdicts):
            return True
        for child, ok in verdicts:
            rel = child.relative_to(target).as_posix()
            if ok:
                remove.append(rel)
            elif child.is_file() or child.is_symlink():
                keep.append(rel)
        return False

    for rel in CODE_FILES:
        if (target / rel).is_file() or (target / rel).is_symlink():
            remove.append(rel)
    for rel in CODE_DIRS:
        base = target / rel
        if base.is_symlink() or base.is_file():
            remove.append(rel)
        elif base.is_dir() and walk(base):
            remove.append(rel)
    return remove, sorted(keep)


def remote_key(url: str) -> str:
    """git remote 주소를 비교용으로 맞춘다 — ssh·https·.git·대소문자 차이를 지운다."""
    url = url.strip().rstrip("/")
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^[a-z+]+://([^@/]+@)?", "", url)
    url = re.sub(r"^[^@/]+@([^:/]+):", r"\1/", url)
    return url.lower()


def remotes(root: Path) -> dict[str, str]:
    status, out = git(root, "remote", "-v")
    found: dict[str, str] = {}
    for line in out.splitlines() if status == 0 else []:
        parts = line.split()
        if len(parts) >= 2:
            found.setdefault(parts[0], parts[1])
    return found


def code_remotes(target: Path, code: Path) -> list[dict[str, str]]:
    """데이터 repo 가 될 target 의 remote 중 코드 repo 와 같은 저장소를 가리키는 것.

    공개 저장소를 clone 해 그 안에 위키를 쌓은 옛 단일 repo 는 origin 이 코드 저장소다. remote 이름은 사용자의
    것이라 바꾸지 않는다 — branch 가 그것을 따르지 않게만 한다.
    """
    keys = {remote_key(url) for url in remotes(code).values()}
    return [{"name": name, "url": url} for name, url in sorted(remotes(target).items())
            if remote_key(url) in keys]


def detach_code_remotes(target: Path, found: list[dict[str, str]]) -> list[dict[str, str]]:
    """지금 branch 가 코드 저장소 remote 를 따르면, 코드 저장소가 아닌 remote 가 하나뿐일 때 그쪽으로 잇고
    아니면 끊는다. remote 이름·주소는 그대로 둔다."""
    status, branch = git(target, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() if status == 0 else ""
    status, upstream_remote = git(target, "config", "--get", f"branch.{branch}.remote")
    upstream_remote = upstream_remote.strip() if status == 0 else ""
    names = {item["name"] for item in found}
    own = sorted(name for name in remotes(target) if name not in names)
    done: list[dict[str, str]] = []
    for item in found:
        entry = dict(item)
        if branch and item["name"] == upstream_remote:
            if len(own) == 1 and git(target, "fetch", "-q", own[0], branch, timeout=120.0)[0] == 0 \
                    and git(target, "branch", "-u", f"{own[0]}/{branch}")[0] == 0:
                entry["branch_upstream"] = f"{own[0]}/{branch}"
            else:
                git(target, "branch", "--unset-upstream")
                entry["branch_upstream"] = ""
        done.append(entry)
    return done


def adopt_plan(target: Path, code: Path | None = None) -> dict[str, Any]:
    """adopt 가 할 일을 계산만 한다."""
    target = Path(target).expanduser().resolve()
    code = (code or P.CODE_ROOT).resolve()
    if target == code:
        raise ValueError("코드 repo 자신은 adopt 할 수 없다 — 바꿀 단일 repo 의 경로를 준다")
    if not (target / "wiki").is_dir():
        raise ValueError(f"{target}/wiki 가 없다 — llmwiki 단일 repo 가 아니다 (새로 시작하려면 `llmwiki init`)")
    if P.is_data_repo(target):
        raise ValueError(f"{target} 는 이미 데이터 repo 다 ({P.LOCK_NAME} 있음) — 갱신은 `llmwiki update`")
    is_git = is_own_repo(target)
    if is_git:
        exclude_nested_code(target, code)          # 이 폴더 안에 clone 한 코드 repo 는 변경이 아니다
        status, out = git(target, "status", "--porcelain")
        if status != 0 or out.strip():
            raise ValueError(f"{target} 에 커밋되지 않은 변경이 있다 — 먼저 커밋하거나 치운다:\n{out[:800]}")
    remove, keep = code_removals(target, code)
    moves: list[tuple[str, str]] = []
    conflicts: list[str] = []
    config_src = target / "tools" / "config"
    if config_src.is_dir():
        for path in sorted(config_src.iterdir()):
            if not path.is_file():
                continue
            dest = f"config/{path.name}"
            if (target / dest).exists() and file_digest(target / dest) != file_digest(path):
                conflicts.append(f"{dest} 가 이미 있고 tools/config/{path.name} 와 내용이 다르다")
            moves.append((f"tools/config/{path.name}", dest))
    untrack = sorted({f for rel in DERIVED_DIRS for f in (tracked(target, rel) if is_git else [])})
    derived = [rel for rel in ("index",) if (target / rel).exists()]
    links = [rel for rel in SKILL_LINKS if is_git and tracked(target, rel)]
    local: dict[str, Any] = {"sources": [], "text": ""}
    sections: list[str] = []
    seen_added: set[str] = set()
    for name in ("INIT.md", *GUIDE_FILES):
        path = target / name
        if not path.is_file():
            continue
        found = local_lines(path.read_text(encoding="utf-8"), code)
        found["added"] = [line for line in found["added"] if line not in seen_added]
        seen_added.update(found["added"])
        if name != "INIT.md":   # CLAUDE.md·AGENTS.md 는 INIT.md 의 사본이었다 — INIT.md 에 이미 있는 차이는 다시 싣지 않는다
            init_text = (target / "INIT.md").read_text(encoding="utf-8") if (target / "INIT.md").is_file() else ""
            if path.read_text(encoding="utf-8") == init_text:
                continue
        if found["added"] or found["review"]:
            local["sources"].append({"file": name, "base": found["base"], "added": len(found["added"]),
                                     "review": len(found["review"])})
            sections.append(local_section(found, name))
    local["text"] = "\n\n".join(s for s in sections if s)
    create = [rel for rel in (P.LOCK_NAME, "LOCAL.md", *GUIDE_FILES) if not (target / rel).exists()]
    leftover = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir()
                      if p.name not in {".git", ".gitignore", "wiki", "raw", "config", "tools", "LOCAL.md",
                                        P.LOCK_NAME, *GUIDE_FILES, *(r.split("/")[0] for r in remove),
                                        "index", ".claude", ".codex", P.DERIVED_DIR,
                                        nested_code(target, code).split("/")[0]})
    return {"root": str(target), "code": str(code), "git": is_git, "remove": remove, "keep": keep, "move": moves,
            "untrack": untrack, "delete_derived": derived, "untrack_links": links, "create": create,
            "rewrite": [rel for rel in GUIDE_FILES if (target / rel).exists()], "local": local,
            "conflicts": conflicts, "leftover": leftover,
            "code_remotes": code_remotes(target, code) if is_git else []}


def page_versions(root: Path) -> list[str]:
    """wiki/**/*.json 의 page schema_version 들 (낮은 것부터)."""
    found: set[str] = set()
    for path in (root / "wiki").rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for page in value if isinstance(value, list) else [value]:
            if isinstance(page, dict) and page.get("schema_version"):
                found.add(str(page["schema_version"]))
    return sorted(found, key=P.schema_key)


def run_checks(data: Path, code: Path | None = None, commands: tuple[str, ...] = ("build", "validate", "lint"),
               python: str | None = None) -> dict[str, Any]:
    """코드 repo 의 CLI 로 데이터 root 에서 build·validate·lint 를 돌리고 요약한다."""
    import sys
    code = (code or P.CODE_ROOT).resolve()
    cli = [python or sys.executable, str(code / "scripts" / "llmwiki.py"), "--root", str(data)]
    out: dict[str, Any] = {}
    for command in commands:
        argv = [*cli, command] + (["--json"] if command in ("validate", "lint") else [])
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
        item: dict[str, Any] = {"exit": proc.returncode}
        if command in ("validate", "lint"):
            try:
                body = json.loads(proc.stdout)
                item.update(errors=len(body.get("errors") or []), warnings=len(body.get("warnings") or []),
                            first_errors=(body.get("errors") or [])[:5])
                if body.get("counts"):
                    item["counts"] = body["counts"]
            except ValueError:
                item["output"] = (proc.stdout + proc.stderr)[-800:]
        elif proc.returncode != 0:
            item["output"] = (proc.stdout + proc.stderr)[-800:]
        out[command] = item
    return out


def remove_path(target: Path, rel: str, is_git: bool) -> None:
    path = target / rel
    if is_git and tracked(target, rel):
        git(target, "rm", "-r", "-q", "--cached", "--", rel)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def adopt(target: Path, *, code: Path | None = None, apply: bool = False, commit: bool = True,
          check: Any = None) -> dict[str, Any]:
    """단일 repo 를 데이터 repo 로 바꾼다. 기본은 계획만 낸다.

    wiki/ 와 raw/ 는 건드리지 않는다 — 전후 sha256(raw 는 경로·크기) 목록이 다르면 멈춘다. `check` 는 바꾼 뒤
    돌릴 build·validate·lint 를 부르는 함수(root → 결과 dict)다.
    """
    plan = adopt_plan(target, code)
    plan["dry_run"] = not apply
    if not apply:
        return plan
    if plan["conflicts"]:
        raise ValueError("adopt 중단 — 먼저 정리할 것:\n" + "\n".join(plan["conflicts"]))
    target = Path(plan["root"])
    code_root = Path(plan["code"])
    is_git = plan["git"]
    before_wiki, before_raw = wiki_digests(target), raw_listing(target)
    for src, dest in plan["move"]:
        (target / dest).parent.mkdir(parents=True, exist_ok=True)
        if is_git and tracked(target, src):
            if (target / dest).exists():       # 같은 내용이 이미 있다 — 옛 자리만 뺀다
                git(target, "rm", "-q", "--", src)
            else:
                status, out = git(target, "mv", "--", src, dest)
                if status != 0:
                    raise RuntimeError(f"git mv {src} {dest} 실패: {out}")
        else:
            if (target / dest).exists():
                (target / src).unlink()
            else:
                os.replace(target / src, target / dest)
    for rel in plan["untrack"]:
        git(target, "rm", "-q", "--cached", "--", rel)
    for rel in plan["untrack_links"]:
        git(target, "rm", "-q", "--cached", "--", rel)
        link = target / rel
        if link.is_symlink():
            link.unlink()
    for rel in plan["remove"]:
        remove_path(target, rel, is_git)
    for rel in plan["delete_derived"]:
        remove_path(target, rel, is_git)
    for rel in CODE_DIRS:                      # 남긴 파일 사이에 빈 디렉터리가 남지 않게
        base = target / rel
        if base.is_dir() and not base.is_symlink():
            for folder in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                if not any(folder.iterdir()):
                    folder.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
    tools = target / "tools"
    for sub in (tools / "config", tools):
        if sub.is_dir() and not any(sub.iterdir()):
            sub.rmdir()
    local_path = target / "LOCAL.md"
    existing = local_path.read_text(encoding="utf-8") if local_path.is_file() else LOCAL_HEADER
    if plan["local"]["text"] and plan["local"]["text"] not in existing:
        existing = existing.rstrip("\n") + "\n\n" + plan["local"]["text"] + "\n"
    local_path.write_text(existing, encoding="utf-8")
    gitignore = target / ".gitignore"
    gitignore.write_text(gitignore_lines(gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""),
                         encoding="utf-8")
    # 옛 판 정본도 데이터 repo 가 될 수 있다 — lock 에는 정본의 판을 그대로 적고, 판을 옮기는 것은 migrate 의 일이다
    versions = page_versions(target)
    lock = refresh_lock(target, schema_version=versions[0] if versions else P.SCHEMA_VERSION, code=code_root)
    guides = sync_guides(target, code_root)
    after_wiki, after_raw = wiki_digests(target), raw_listing(target)
    if after_wiki != before_wiki or after_raw != before_raw:
        raise RuntimeError("adopt 중 wiki/ 또는 raw/ 가 바뀌었다 — 여기서 멈춘다 (git 으로 되돌려 확인하라)")
    if plan["code_remotes"]:
        plan["code_remotes"] = detach_code_remotes(target, plan["code_remotes"])
    report = {**plan, "lock": lock, "guides": guides,
              "wiki_unchanged": {"files": len(after_wiki), "sha256_identical": True},
              "raw_unchanged": {"files": len(after_raw)}}
    if check is not None:
        report["checks"] = check(target)
    if is_git and commit:
        git(target, "add", "-A", "--", ".")
        status, out = git(target, "commit", "-q", "-m",
                          f"llmwiki adopt: 코드를 덜어 내고 데이터 repo 로 바꾼다 (코드 v{lock['code_version']})")
        if status != 0 and "nothing to commit" not in out:
            raise RuntimeError(f"adopt 커밋 실패: {out}")
        report["commit"] = git(target, "rev-parse", "HEAD")[1]
    return report


# --------------------------------------------------------------------------- update (코드 pull 까지)
# update 는 두 단계다. 앞 단계(이 함수)는 지금 돌고 있는 코드로 데이터·코드 repo 를 검사하고 코드를
# `git pull --ff-only` 한다. 뒤 단계(migrate·지침·build·commit)는 **받아 온 새 코드**가 해야 하므로
# `llmwiki.py update --post-pull` 로 새 프로세스를 띄운다 — 옛 코드가 새 스키마를 옮길 수는 없다.
DATA_COMMIT_PATHS = ("wiki", "raw", "config", P.LOCK_NAME, "LOCAL.md", *GUIDE_FILES, ".gitignore")


class UpdateStop(Exception):
    """update 를 멈추는 조건. 메시지가 그대로 사용자에게 간다."""


def dirty(root: Path, *, untracked: bool = True) -> str:
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    status, out = git(root, *args)
    if status != 0:
        raise UpdateStop(f"git status 실패 ({root}): {out}")
    return out.strip()


def upstream_of(code: Path) -> str:
    status, out = git(code, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if status != 0 or not out.strip():
        raise UpdateStop(f"코드 repo 에 upstream 이 없다 — `git -C {code} branch --set-upstream-to origin/main` 처럼 잇는다")
    return out.strip().splitlines()[-1]


def pull_code(code: Path, *, dry_run: bool, say: Any = print) -> dict[str, Any]:
    """코드 repo 를 fast-forward 로만 받는다. 로컬 변경·로컬 commit 이 있으면 멈춘다."""
    if not is_own_repo(code):
        raise UpdateStop(f"코드 repo 가 git 저장소가 아니다: {code}")
    changes = dirty(code, untracked=False)
    if changes:
        raise UpdateStop(f"코드 repo 에 로컬 변경이 있다 — 커밋하거나 되돌린 뒤 다시 한다:\n{changes[:800]}")
    upstream = upstream_of(code)
    remote, _, branch = upstream.partition("/")
    before = git(code, "rev-parse", "HEAD")[1]
    if dry_run:
        status, out = git(code, "ls-remote", remote, f"refs/heads/{branch}", timeout=60.0)
        remote_head = out.split()[0] if status == 0 and out.split() else ""
        status, ahead = git(code, "rev-list", "--count", f"{upstream}..HEAD")
        if status == 0 and ahead.strip() not in ("", "0"):
            raise UpdateStop(f"코드 repo 에 upstream 에 없는 로컬 commit {ahead.strip()}개가 있다 — 먼저 정리한다")
        say(f"[dry-run] 코드 repo: git pull --ff-only ({upstream}) — {before[:7]} → "
            + (remote_head[:7] if remote_head else "알 수 없음"))
        return {"before": before, "after": before, "remote_head": remote_head, "pulled": False}
    status, out = git(code, "fetch", remote, timeout=300.0)
    if status != 0:
        raise UpdateStop(f"코드 repo fetch 실패: {out[-400:]}")
    status, ahead = git(code, "rev-list", "--count", f"{upstream}..HEAD")
    if status != 0 or ahead.strip() != "0":
        raise UpdateStop(f"코드 repo 에 upstream 에 없는 로컬 commit {ahead.strip()}개가 있다 — 먼저 정리한다")
    status, out = git(code, "pull", "--ff-only", timeout=300.0)
    if status != 0:
        raise UpdateStop(f"코드 repo git pull --ff-only 실패: {out[-400:]}")
    after = git(code, "rev-parse", "HEAD")[1]
    if after != before:
        # .pyc 는 소스의 (mtime 초, 크기) 로 낡았는지 본다. pull 이 같은 초에 같은 크기로 바꾼 파일
        # (`SCHEMA_VERSION = "1.1"` → `"1.2"`) 은 옛 바이트코드가 그대로 쓰인다 — 새 코드를 띄우기 전에 지운다.
        for cache in (code / "scripts").rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
    say(f"코드 repo: {before[:7]} → {after[:7]}" if after != before else "코드 repo: 이미 upstream 과 같다")
    return {"before": before, "after": after, "pulled": after != before}


def routine_push_target(data: Path) -> tuple[str, bool] | None:
    """주기 루틴이 이 데이터 repo 에 push 하도록 설치돼 있으면 (remote, True)."""
    base = Path(os.environ.get("LLMWIKI_STATE_DIR") or Path.home() / ".llmwiki")
    try:
        state = json.loads((base / "installed-routine").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not state.get("push"):
        return None
    try:
        if Path(str(state.get("root") or "")).resolve() != Path(data).resolve():
            return None
    except OSError:
        return None
    return str(state.get("remote") or "private"), True


def commit_data(data: Path, message: str) -> str:
    """데이터 repo 의 데이터 경로만 커밋한다. 바뀐 것이 없으면 빈 문자열."""
    paths = [rel for rel in DATA_COMMIT_PATHS if (data / rel).exists() or tracked(data, rel)]
    git(data, "add", "-A", "--", *paths)
    status, out = git(data, "diff", "--cached", "--quiet")
    if status == 0:
        return ""
    status, out = git(data, "commit", "-q", "-m", message)
    if status != 0:
        raise UpdateStop(f"데이터 repo 커밋 실패: {out}")
    return git(data, "rev-parse", "HEAD")[1]


def update_message(old_version: str, new_version: str, old_sha: str, new_sha: str,
                   old_schema: str, new_schema: str) -> str:
    return (f"코드 v{old_version}→v{new_version} ({old_sha[:7] or '-'}→{new_sha[:7] or '-'}) 갱신, "
            f"schema {old_schema}→{new_schema}")


# --------------------------------------------------------------------------- 제자리 갱신 (한 폴더)
# 설치한 폴더 하나에 코드와 위키가 같이 산다. update 는 공개 저장소의 새 코드를 그 폴더 안 코드 파일에
# 3-way 로 합친다 — 기준(base)은 지난번에 맞춘 upstream commit 이다. 사용자가 고친 코드·INIT.md 의 개인
# 규칙은 남고, 같은 자리를 양쪽이 고쳤으면 아무것도 쓰지 않고 멈춘다. 새 폴더를 만들지 않고 폴더·remote 이름도
# 바꾸지 않는다. 받아 온 upstream 은 숨은 ref 하나(refs/llmwiki/upstream)에만 둔다.
UPSTREAM_URL = "https://github.com/BrilliantTiger/llmwiki_json"
UPSTREAM_BRANCH = "main"
UPSTREAM_FILE = "tools/upstream.json"
UPSTREAM_REF = "refs/llmwiki/upstream"
ENV_UPSTREAM = "LLMWIKI_UPSTREAM"
# upstream 에도 있지만 이 폴더의 데이터인 자리 — 갱신이 손대지 않는다
NOT_CODE = ("wiki/", "raw/", "index/", "viewer/public/data/", "tools/config/", "bench/", UPSTREAM_FILE)


def is_code_path(rel: str) -> bool:
    return not any(rel == p.rstrip("/") or rel.startswith(p) for p in NOT_CODE)


def read_upstream(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((Path(root) / UPSTREAM_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def upstream_url(root: Path) -> str:
    """받아 올 곳: 환경변수 → 지난번 기록 → 공개 저장소를 가리키는 remote → 공개 저장소 주소."""
    if os.environ.get(ENV_UPSTREAM):
        return os.environ[ENV_UPSTREAM]
    recorded = str(read_upstream(root).get("url") or "")
    if recorded:
        return recorded
    want = remote_key(UPSTREAM_URL)
    for url in remotes(Path(root)).values():
        if remote_key(url) == want:
            return url
    return UPSTREAM_URL


def read_only_remote(root: Path, remote: str) -> bool:
    """remote 가 공개 코드 저장소(BrilliantTiger/llmwiki_json)인가. 그곳은 받기만 한다 — 위키를 밀지 않는다."""
    url = remotes(Path(root)).get(remote, "")
    return bool(url) and remote_key(url) == remote_key(UPSTREAM_URL)


READ_ONLY_PUSH = "은(는) 공개 코드 저장소(읽기 전용, 받기만 한다)라 위키를 push 하지 않는다 — 개인 저장소 remote 를 붙인다"


def fetch_upstream(root: Path, url: str, branch: str = UPSTREAM_BRANCH) -> str:
    status, out = git(Path(root), "fetch", "-q", "--no-tags", url, f"+refs/heads/{branch}:{UPSTREAM_REF}",
                      timeout=600.0)
    if status != 0:
        raise UpdateStop(f"upstream 을 받지 못했다 ({url}): {out[-400:]}")
    return git(Path(root), "rev-parse", UPSTREAM_REF)[1].strip()


def tree_files(root: Path, commit: str) -> dict[str, tuple[str, str]]:
    """commit 의 코드 파일 {경로: (mode, blob)}."""
    status, out = git(Path(root), "ls-tree", "-r", "-z", commit)
    files: dict[str, tuple[str, str]] = {}
    for entry in out.split("\0") if status == 0 else []:
        if not entry:
            continue
        meta, _, rel = entry.partition("\t")
        mode, kind, blob = meta.split()
        if kind == "blob" and is_code_path(rel):
            files[rel] = (mode, blob)
    return files


class LocalBlobs:
    """작업 트리 파일의 (mode, blob) — 한 번 계산한 것은 다시 계산하지 않는다."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cache: dict[str, tuple[str, str] | None] = {}

    def __call__(self, rel: str) -> tuple[str, str] | None:
        if rel not in self.cache:
            self.cache[rel] = self._compute(rel)
        return self.cache[rel]

    def _compute(self, rel: str) -> tuple[str, str] | None:
        path = self.root / rel
        if path.is_symlink():
            proc = subprocess.run(["git", "hash-object", "--stdin"], cwd=self.root, input=os.readlink(path).encode(),
                                  capture_output=True)
            return ("120000", proc.stdout.decode().strip())
        if not path.is_file():
            return None
        status, out = git(self.root, "hash-object", "--", rel)
        mode = "100755" if os.access(path, os.X_OK) else "100644"
        return (mode, out.strip()) if status == 0 else None


def same(a: tuple[str, str] | None, b: tuple[str, str] | None) -> bool:
    """내용이 같은가 — 실행 비트 차이는 보지 않는다(Windows 체크아웃은 비트가 없다)."""
    if a is None or b is None:
        return a is b
    return a[1] == b[1] and (a[0] == "120000") == (b[0] == "120000")


def find_base(root: Path, new: str, local: LocalBlobs, limit: int = 400) -> tuple[str, str]:
    """이 폴더의 코드가 어느 upstream commit 위에 있는지 (commit, 어떻게 찾았나).

    지난번 기록 → 같은 이력이면 merge-base → 이력이 따로면(파일을 복사해 만든 저장소) upstream commit 중
    코드 파일이 가장 많이 같은 것.
    """
    recorded = str(read_upstream(root).get("commit") or "")
    if recorded and git(Path(root), "cat-file", "-e", f"{recorded}^{{commit}}")[0] == 0:
        return recorded, "기록"
    status, out = git(Path(root), "merge-base", "HEAD", new)
    if status == 0 and out.strip():
        return out.strip(), "merge-base"
    status, out = git(Path(root), "rev-list", f"--max-count={limit}", new)
    best, best_diff = new, None
    for commit in out.split() if status == 0 else []:
        files = tree_files(root, commit)
        diff = sum(0 if same(local(rel), blob) else 1 for rel, blob in files.items())
        if best_diff is None or diff < best_diff:
            best, best_diff = commit, diff
            if diff == 0:
                break
    return best, f"가장 가까운 판 (코드 파일 {best_diff}개 차이)"


def blob_bytes(root: Path, blob: str) -> bytes:
    return subprocess.run(["git", "cat-file", "blob", blob], cwd=root, capture_output=True, check=True).stdout


def plan_sync(root: Path, base: str, new: str, local: LocalBlobs | None = None) -> dict[str, Any]:
    """base→new 의 코드 변경을 이 폴더에 얹을 계획. 쓰지 않는다."""
    root = Path(root)
    local = local or LocalBlobs(root)
    old, fresh = tree_files(root, base), tree_files(root, new)
    plan: dict[str, Any] = {"write": [], "delete": [], "merge": [], "kept": [], "conflicts": []}
    for rel in sorted(set(old) | set(fresh)):
        b, n, here = old.get(rel), fresh.get(rel), local(rel)
        if same(here, n):
            continue                                   # 이미 새 판과 같다
        if same(here, b):                              # 사용자가 손대지 않았다 — 새 판으로
            (plan["delete"].append(rel) if n is None else plan["write"].append((rel, n)))
            continue
        if same(b, n):                                 # upstream 은 그대로, 사용자만 고쳤다 — 남긴다
            plan["kept"].append(rel)
            continue
        if b and n and here and "120000" not in (b[0], n[0], here[0]):
            merged = merge_text(root, rel, b[1], n[1])
            if merged is None:                         # 줄을 더하기만 했으면 새 판의 같은 자리에 붙인다
                merged = merge_additions((root / rel).read_bytes(), blob_bytes(root, b[1]), blob_bytes(root, n[1]))
            if merged is not None:
                plan["merge"].append((rel, n[0], merged))
                continue
        plan["conflicts"].append(rel)
    return plan


def merge_text(root: Path, rel: str, base_blob: str, new_blob: str) -> bytes | None:
    """git merge-file 로 (이 폴더 파일, base, new) 를 합친다. 충돌이면 None."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="llmwiki-merge-") as tmp:
        base_path, new_path = Path(tmp) / "base", Path(tmp) / "new"
        base_path.write_bytes(blob_bytes(root, base_blob))
        new_path.write_bytes(blob_bytes(root, new_blob))
        proc = subprocess.run(["git", "merge-file", "-p", str(root / rel), str(base_path), str(new_path)],
                              capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def merge_additions(local: bytes, base: bytes, new: bytes) -> bytes | None:
    """이 폴더가 base 에 줄을 **더하기만** 했으면 그 줄들을 new 의 같은 자리에 붙인다. 아니면 None.

    INIT.md 끝에 개인 규칙을 더했는데 upstream 이 마지막 줄을 고치면 git 은 맞닿은 수정을 충돌로 본다. 더한
    줄은 앞 줄(없으면 뒷 줄)을 닻으로 삼아 new 에서 그 줄이 한 번만 나오는 자리를 찾는다. 끝에 더한 줄은 new 의
    끝에 붙인다. 닻을 못 찾거나, 지운·바꾼 줄이 있거나, upstream 도 같은 자리에 줄을 더했으면 None — 판정하지 않는다.
    """
    import difflib
    try:
        mine, old, fresh = (x.decode("utf-8").splitlines(keepends=True) for x in (local, base, new))
    except UnicodeDecodeError:
        return None
    ops = difflib.SequenceMatcher(None, old, mine, autojunk=False).get_opcodes()
    if any(tag in ("replace", "delete") for tag, *_ in ops):
        return None
    inserts = [(i1, mine[j1:j2]) for tag, i1, _, j1, j2 in ops if tag == "insert"]
    # upstream 도 같은 자리에 줄을 더했으면 어느 쪽이 먼저인지 판정할 수 없다 — 충돌로 둔다
    theirs = {i1 for tag, i1, *_ in difflib.SequenceMatcher(None, old, fresh, autojunk=False).get_opcodes()
              if tag == "insert"}
    if any(at in theirs for at, _ in inserts):
        return None
    out = list(fresh)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    placed: list[tuple[int, list[str]]] = []
    for at, lines in inserts:
        if at >= len(old):                             # 끝에 더했다
            placed.append((len(fresh), lines))
            continue
        anchor_before = old[at - 1] if at > 0 else None
        if anchor_before is not None and fresh.count(anchor_before) == 1:
            placed.append((fresh.index(anchor_before) + 1, lines))
        elif fresh.count(old[at]) == 1:
            placed.append((fresh.index(old[at]), lines))
        else:
            return None
    for index, lines in sorted(placed, key=lambda item: item[0], reverse=True):
        out[index:index] = lines
    return "".join(out).encode("utf-8")


def apply_sync(root: Path, plan: dict[str, Any]) -> None:
    root = Path(root)
    for rel, (mode, blob) in plan["write"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        data = blob_bytes(root, blob)
        if mode == "120000" and os.name != "nt":
            os.symlink(data.decode(), path)
            continue
        path.write_bytes(data)
        if mode == "100755":
            path.chmod(path.stat().st_mode | 0o111)
    for rel, mode, data in plan["merge"]:
        (root / rel).write_bytes(data)
    for rel in plan["delete"]:
        path = root / rel
        if path.is_symlink() or path.exists():
            path.unlink()
        parent = path.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def write_upstream(root: Path, url: str, commit: str, version: str) -> None:
    path = Path(root) / UPSTREAM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "branch": UPSTREAM_BRANCH, "commit": commit, "version": version},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tree_version(root: Path, commit: str) -> str:
    status, out = git(Path(root), "show", f"{commit}:{P.VERSION_FILE}")
    text = out.strip() if status == 0 else ""
    return text if P.SEMVER.match(text) else "0.0.0"


def commit_all(root: Path, message: str) -> str:
    """이 폴더의 바뀐 것 전부를 커밋한다(시작할 때 깨끗했으니 update 가 바꾼 것뿐이다). 없으면 빈 문자열."""
    git(Path(root), "add", "-A")
    status, _ = git(Path(root), "diff", "--cached", "--quiet")
    if status == 0:
        return ""
    status, out = git(Path(root), "commit", "-q", "-m", message)
    if status != 0:
        raise UpdateStop(f"커밋 실패: {out}")
    return git(Path(root), "rev-parse", "HEAD")[1].strip()


# --------------------------------------------------------------------------- llmwiki shim
# `llmwiki` 명령은 사용자 영역의 작은 shim 이다 — 코드 repo 의 llmwiki.py 를 부른다. shell profile 과
# 시스템 PATH 는 건드리지 않는다. 표지가 없는 같은 이름의 파일은 남의 것이라 덮어쓰지도 지우지도 않는다.
SHIM_MARKER = "llmwiki-shim: generated by the llmwiki installer"
ENV_SHIM_DIR = "LLMWIKI_SHIM_DIR"


def is_windows(windows: bool | None = None) -> bool:
    return os.name == "nt" if windows is None else windows


def shim_path(windows: bool | None = None) -> Path:
    """POSIX `~/.local/bin/llmwiki`, Windows `%LOCALAPPDATA%\\llmwiki\\llmwiki.cmd` (시험은 LLMWIKI_SHIM_DIR)."""
    win = is_windows(windows)
    override = os.environ.get(ENV_SHIM_DIR)
    if override:
        base = Path(override)
    elif win:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "llmwiki"
    else:
        base = Path(os.environ.get("HOME") or Path.home()) / ".local" / "bin"
    return base / ("llmwiki.cmd" if win else "llmwiki")


def shim_text(python: str, code: Path, data: Path | None, windows: bool | None = None) -> str:
    if is_windows(windows):
        from pathlib import PureWindowsPath
        script = PureWindowsPath(str(code)) / "scripts" / "llmwiki.py"
        lines = ["@echo off", f"rem {SHIM_MARKER}", f"rem code: {code}"]
        if data:
            lines += [f"rem data: {data}",
                      f'if not defined LLMWIKI_DEFAULT_ROOT set "LLMWIKI_DEFAULT_ROOT={data}"']
        lines.append(f'"{python}" "{script}" %*')
        return "\r\n".join(lines) + "\r\n"
    import shlex
    script = Path(code) / "scripts" / "llmwiki.py"
    lines = ["#!/bin/sh", f"# {SHIM_MARKER}", f"# code: {code}"]
    if data:
        lines += [f"# data: {data}",
                  f"LLMWIKI_DEFAULT_ROOT=${{LLMWIKI_DEFAULT_ROOT:-{shlex.quote(str(data))}}}",
                  "export LLMWIKI_DEFAULT_ROOT"]
    lines.append(f'exec {shlex.quote(python)} {shlex.quote(str(script))} "$@"')
    return "\n".join(lines) + "\n"


def shim_state(windows: bool | None = None) -> dict[str, Any]:
    """설치된 shim 의 자리·주인·가리키는 코드와 데이터."""
    path = shim_path(windows)
    state: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "ours": False, "code": "", "data": ""}
    if not path.is_file():
        return state
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return state
    state["ours"] = SHIM_MARKER in text
    for line in text.splitlines():
        for key in ("code", "data"):
            for prefix in (f"# {key}: ", f"rem {key}: "):
                if line.startswith(prefix):
                    state[key] = line[len(prefix):].strip()
    return state


def on_path(folder: Path) -> bool:
    entries = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    try:
        target = folder.resolve()
    except OSError:
        return False
    for entry in entries:
        try:
            if Path(entry).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False


def path_hint(folder: Path, windows: bool | None = None) -> str:
    """PATH 에 없을 때 사용자가 직접 할 일 (설치기는 profile·PATH 를 고치지 않는다)."""
    if is_windows(windows):
        return (f'PATH 에 "{folder}" 가 없다. 사용자 PATH 에 더하려면 (설치기는 하지 않는다):\n'
                f'  [Environment]::SetEnvironmentVariable("Path", "$env:Path;{folder}", "User")')
    return (f"PATH 에 {folder} 가 없다. 쓰는 셸의 profile 에 직접 더한다 (설치기는 하지 않는다):\n"
            f'  export PATH="{folder}:$PATH"')


def install_shim(python: str, *, code: Path | None = None, data: Path | None = None,
                 windows: bool | None = None, dry_run: bool = False) -> dict[str, Any]:
    code = Path(code or P.CODE_ROOT)
    code = code.resolve() if code.exists() else code      # Windows 모양을 다른 OS 에서 만들 때는 그대로 둔다
    data = Path(data).expanduser() if data else None
    data = data.resolve() if data and data.exists() else data
    path = shim_path(windows)
    text = shim_text(python, code, data, windows)
    if data and Path(data).exists() and code.exists():
        exclude_nested_code(Path(data), code, dry_run=dry_run)
    report: dict[str, Any] = {"path": str(path), "code": str(code), "data": str(data) if data else ""}
    state = shim_state(windows)
    if state["exists"] and not state["ours"]:
        report["status"] = "kept-foreign"
        return report
    try:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        current = None
    if current is not None and current.replace("\r\n", "\n") == text.replace("\r\n", "\n"):
        report["status"] = "current"
    elif dry_run:
        report["status"] = "would-write"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if not is_windows(windows):
            path.chmod(0o755)
        report["status"] = "written"
    report["on_path"] = on_path(path.parent)
    if not report["on_path"]:
        report["hint"] = path_hint(path.parent, windows)
    return report


def remove_shim(*, windows: bool | None = None, dry_run: bool = False) -> dict[str, Any]:
    state = shim_state(windows)
    report: dict[str, Any] = {"path": state["path"]}
    if not state["exists"]:
        report["status"] = "absent"
    elif not state["ours"]:
        report["status"] = "kept-foreign"
    elif dry_run:
        report["status"] = "would-remove"
    else:
        Path(state["path"]).unlink()
        report["status"] = "removed"
    return report
