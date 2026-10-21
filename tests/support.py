"""Shared fixtures for the llmwiki_json backend tests.

Every test runs against a throwaway workspace so `wiki/` and
`wiki/log.jsonl` in the real repo are never written to.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
# 이 저장소의 wiki/ 가 raw/demo/ 를 ingest 한 데모 위키인가. 실데이터 위키(이 프레임워크를 들인 개인 저장소)
# 에서는 데모의 내용에 대한 시험(데모 질문 세트, 데모 tag 목록, 데모 page 의 통합 품질)을 건너뛴다.
DEMO_WIKI = (REPO / "raw" / "demo").is_dir()
SCRIPT = REPO / "scripts" / "llmwiki.py"
CONTEXT_SCRIPT = REPO / "scripts" / "llmwiki_context.py"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


llmwiki = _load("llmwiki", SCRIPT)
llmwiki_context = _load("llmwiki_context", CONTEXT_SCRIPT)

FROZEN_NOW = "2026-08-19T00:00:00+00:00"
FROZEN_DATE = "2026-08-19"


def make_page(slug: str, body: str, *, type: str = "concept", projects: Iterable[str] = (),
              tags: Iterable[str] = (), sources: Iterable[str] = (), summary: str = "요약",
              created: str = FROZEN_DATE, snapshot: str | None = None) -> dict[str, Any]:
    """Build a schema-valid page from Markdown body text."""
    page_id = f"page:{slug}"
    blocks, order = llmwiki.parse_blocks(page_id, body)
    title = next((b["data"]["text"] for b in blocks.values()
                  if b["kind"] == "heading" and b["data"].get("level") == 1), slug)
    page = {
        "schema_version": llmwiki.SCHEMA_VERSION, "id": page_id, "slug": slug, "title": title, "type": type,
        "created": created, "updated": created, "tags": list(tags), "projects": list(projects),
        "sources": list(sources), "raw_ref": None, "summary": summary,
        "blocks": blocks, "block_order": order,
        "links": llmwiki.page_links(page_id, blocks, order),
        "history": [{"at": created, "action": "created", "actor": "test"}],
    }
    if snapshot is not None:
        page["source_snapshot"] = {"format": "markdown", "text": snapshot,
                                   "sha256": llmwiki.sha(snapshot)}
    return page


DEMO_BODY = {
    "alpha": "# Alpha\n\n[[beta]]를 참조한다.\n",
    "beta": "# Beta\n\n[[alpha]]로 되돌아온다.\n\n> ⚠️ 상충: 두 문서의 수치가 다르다.\n",
    "gamma": "# Gamma\n\n아무도 링크하지 않는 고아 페이지.\n",
}


class WorkspaceCase(unittest.TestCase):
    """Base case providing an isolated workspace rooted in a temp dir."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="llmwiki-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in ("tools/config/groups.json", "tools/schema/page.schema.json"):
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dest)
        for rel in ("raw", "wiki/sources", "wiki/entities", "wiki/concepts",
                    "wiki/syntheses", "wiki/projects", "tests/fixtures/pages", "index",
                    "viewer/public/data"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        self._env = dict(os.environ)
        os.environ[llmwiki.ENV_NOW] = FROZEN_NOW
        self.addCleanup(self._restore_env)
        self.ws = llmwiki.Workspace(self.root)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    # ---------------------------------------------------------------- helpers
    def write_pages(self, pages: Iterable[dict[str, Any]], *, name: str = "pages.json",
                    fixtures: bool = False) -> Path:
        pages = list(pages)
        if fixtures:
            target = self.root / "tests" / "fixtures" / "pages" / name
        else:
            folder = llmwiki.PAGE_DIRS.get(pages[0]["type"], "concepts") if pages else "concepts"
            target = self.root / "wiki" / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def write_demo(self, **overrides: str) -> list[dict[str, Any]]:
        bodies = {**DEMO_BODY, **overrides}
        pages = [make_page(slug, body, projects=["공통"] if slug == "gamma" else ["beta"])
                 for slug, body in bodies.items()]
        self.write_pages(pages)
        return pages

    def write_raw(self, name: str, text: str) -> Path:
        path = self.root / "raw" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read_json(self, rel: str) -> Any:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def cli(self, *argv: str, expect: int = 0,
            stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, llmwiki.ENV_ROOT: str(self.root), llmwiki.ENV_NOW: FROZEN_NOW}
        proc = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True,
                              text=True, env=env, input=stdin)
        self.assertEqual(proc.returncode, expect,
                         f"argv={argv}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def context_cli(self, *argv: str, stdin: str = "", cwd: Path | None = None,
                    expect: int = 0, env: dict[str, str] | None = None,
                    ) -> subprocess.CompletedProcess[str]:
        """Run scripts/llmwiki_context.py against the throwaway workspace."""
        environ = {**os.environ, llmwiki.ENV_ROOT: str(self.root)}
        environ.update(env or {})
        proc = subprocess.run([sys.executable, str(CONTEXT_SCRIPT), *argv], input=stdin,
                              capture_output=True, text=True, env=environ,
                              cwd=str(cwd) if cwd else None)
        self.assertEqual(proc.returncode, expect,
                         f"argv={argv}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc


# --------------------------------------------------------------------------- 두 repo 운영
# 코드 repo 에 실리는 것. 분리 모드 시험은 이것만 임시 코드 repo 로 복사한다 — 데모 위키는 따라가지 않는다.
CODE_PATHS = ("scripts", "tools/schema", "tools/config", "tools/parity", "VERSION", "INIT.md", ".skills", ".gitignore")


def copy_code(dest: Path) -> Path:
    """이 저장소의 코드 부분만 dest 에 복사한다 (wiki/·raw/·index/ 없이)."""
    dest.mkdir(parents=True, exist_ok=True)
    for rel in CODE_PATHS:
        source = REPO / rel
        if source.is_dir():
            shutil.copytree(source, dest / rel, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif source.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / rel)
    return dest


def git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 실패 ({cwd}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def git_init(path: Path, message: str = "처음") -> str:
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD")


class SplitCase(unittest.TestCase):
    """임시 디렉터리에 코드 repo(`code/`)와 데이터 repo(`data/`)를 따로 둔다.

    데이터 repo 는 `llmwiki.lock` 이 있는 분리 모드 골격이다. 명령은 언제나 임시 코드 repo 의
    스크립트로 돌린다 — 이 저장소의 정본·설정은 읽지도 쓰지도 않는다.
    """

    git_code = False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="llmwiki-split-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.code = copy_code(self.tmp / "code")
        if self.git_code:
            self.code_commit = git_init(self.code)
        self.data = self.tmp / "data"
        for rel in ("wiki/sources", "wiki/concepts", "raw", "config"):
            (self.data / rel).mkdir(parents=True, exist_ok=True)
        (self.data / "llmwiki.lock").write_text(json.dumps({
            "lock_version": 1, "schema_version": llmwiki.SCHEMA_VERSION, "code_version": "0.0.0",
            "code_commit": "", "code_path": str(self.code), "updated": FROZEN_DATE}) + "\n", encoding="utf-8")
        self._env = dict(os.environ)
        for name in ("LLMWIKI_ROOT", "LLMWIKI_DEFAULT_ROOT"):
            os.environ.pop(name, None)
        os.environ[llmwiki.ENV_NOW] = FROZEN_NOW
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def run_code(self, script: str, *argv: str, cwd: Path | None = None, expect: int | None = 0,
                 env: dict[str, str] | None = None, stdin: str | None = None,
                 ) -> subprocess.CompletedProcess[str]:
        """임시 코드 repo 의 scripts/<script> 를 돌린다. 기본 cwd 는 데이터 repo (lock 으로 root 를 찾는다)."""
        environ = {k: v for k, v in os.environ.items() if k not in ("LLMWIKI_ROOT", "LLMWIKI_DEFAULT_ROOT")}
        environ[llmwiki.ENV_NOW] = FROZEN_NOW
        for key, value in (("GIT_AUTHOR_NAME", "test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
                           ("GIT_COMMITTER_NAME", "test"), ("GIT_COMMITTER_EMAIL", "test@example.invalid")):
            environ.setdefault(key, value)
        environ.update(env or {})
        proc = subprocess.run([sys.executable, str(self.code / "scripts" / script), *argv],
                              cwd=str(cwd or self.data), capture_output=True, text=True,
                              env=environ, input=stdin)
        if expect is not None:
            self.assertEqual(proc.returncode, expect,
                             f"{script} {argv}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def wiki(self, *argv: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self.run_code("llmwiki.py", *argv, **kwargs)

    def write_data_page(self, page: dict[str, Any], folder: str = "concepts") -> Path:
        target = self.data / "wiki" / folder / f"{page['slug']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


def fake_migration_source(source: str, target: str, *, broken: bool = False) -> str:
    """임시 코드 repo 의 llmwiki_migrate.py 끝에 붙일 가짜 변환 (source → target).

    판을 올리고 summary 끝에 표지를 붙인다 — dry-run 의 diff 와 apply 결과에서 눈에 보이게. `broken` 이면
    validate 가 거절할 page 를 만든다 (type 을 없는 값으로)."""
    name = "_fake_" + target.replace(".", "_")
    body = (f"def {name}(page):\n"
            f"    out = dict(page)\n"
            f"    out['schema_version'] = {target!r}\n"
            f"    out['summary'] = (out.get('summary') or '') + ' ({target})'\n"
            + ("    out['type'] = 'nope'\n" if broken else "")
            + "    return out\n\n\n"
            f"MIGRATIONS[({source!r}, {target!r})] = {name}\n")
    return "\n\n" + body


def install_fake_schema(code: Path, steps: list[tuple[str, str]], *, version: str,
                        broken: bool = False) -> None:
    """임시 코드 repo 의 스키마를 steps 의 마지막 판으로 올리고 VERSION 을 version 으로 바꾼다."""
    migrate = code / "scripts" / "llmwiki_migrate.py"
    text = migrate.read_text(encoding="utf-8")
    for source, target in steps:
        text += fake_migration_source(source, target, broken=broken)
    final = steps[-1][1]
    text += f"\nRELEASES[{version!r}] = {final!r}\n"
    migrate.write_text(text, encoding="utf-8")
    paths = code / "scripts" / "llmwiki_paths.py"
    source = paths.read_text(encoding="utf-8")
    import re as _re
    source, count = _re.subn(r'(?m)^SCHEMA_VERSION = "[^"]+"$', f'SCHEMA_VERSION = "{final}"', source)
    assert count == 1
    paths.write_text(source, encoding="utf-8")
    (code / "VERSION").write_text(version + "\n", encoding="utf-8")
