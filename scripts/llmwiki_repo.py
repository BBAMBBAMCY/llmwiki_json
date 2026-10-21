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
CODE_FILES = ("INIT.md", "VERSION")
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


def local_section(found: dict[str, Any], source: str) -> str:
    """local_lines 의 결과를 LOCAL.md 에 붙일 글로."""
    parts: list[str] = []
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
        status, out = git(target, "status", "--porcelain")
        if status != 0 or out.strip():
            raise ValueError(f"{target} 에 커밋되지 않은 변경이 있다 — 먼저 커밋하거나 치운다:\n{out[:800]}")
    remove = [rel for rel in (*CODE_DIRS, *CODE_FILES) if (target / rel).exists() or (target / rel).is_symlink()]
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
                                        "index", ".claude", ".codex", P.DERIVED_DIR})
    return {"root": str(target), "code": str(code), "git": is_git, "remove": remove, "move": moves,
            "untrack": untrack, "delete_derived": derived, "untrack_links": links, "create": create,
            "rewrite": [rel for rel in GUIDE_FILES if (target / rel).exists()], "local": local,
            "conflicts": conflicts, "leftover": leftover}


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
