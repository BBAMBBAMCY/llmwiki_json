"""데이터 repo 관리 — lock, 생성 지침 파일, skills 링크.

데이터 repo(`llmwiki.lock` 이 있는 root)는 코드를 갖지 않는다. 이 모듈은 코드 repo 쪽에서 데이터 repo 를
맞춘다: 어느 코드(판·커밋·경로)로 맞췄는지 lock 에 적고, 에이전트 지침(CLAUDE.md·AGENTS.md)을
`INIT.md`(코드) + `LOCAL.md`(데이터) 로 만들고, `.claude/skills`·`.codex/skills` 가 코드의 `.skills` 를
가리키게 한다. 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import filecmp
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
            same = path.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        report["guides"][name] = "current" if same else ("would-write" if dry_run else "written")
        if not same and not dry_run:
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
