"""데이터 root 해석과 분리 모드 배치 (scripts/llmwiki_paths.py).

모든 진입점이 같은 규칙을 쓰는지 본다: `--root` → `LLMWIKI_ROOT` → 위로 찾은 `llmwiki.lock`
→ `LLMWIKI_DEFAULT_ROOT`(shim) → 코드 repo 자신.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support import REPO, SplitCase, make_page

ENTRYPOINTS = (
    ("llmwiki.py", ("paths",)),
    ("llmwiki_context.py", ("doctor",)),
)


class RootPriorityTest(SplitCase):
    def setUp(self) -> None:
        super().setUp()
        self.other = self.tmp / "other"
        (self.other / "wiki").mkdir(parents=True)
        (self.other / "llmwiki.lock").write_text("{}\n", encoding="utf-8")
        self.nested = self.data / "wiki" / "concepts"

    def root_of(self, script: str, argv: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None,
                explicit: Path | None = None) -> str:
        args = (("--root", str(explicit)) if explicit else ()) + argv
        out = json.loads(self.run_code(script, *args, cwd=cwd, env=env).stdout)
        return out["root"]

    def test_every_entrypoint_follows_the_same_order(self) -> None:
        for script, argv in ENTRYPOINTS:
            with self.subTest(script=script):
                # 4. 아무것도 없으면 코드 repo 자신 (단일 repo 하위 호환)
                self.assertEqual(self.root_of(script, argv, cwd=self.tmp), str(self.code))
                # 3. 위로 올라가며 찾은 llmwiki.lock
                self.assertEqual(self.root_of(script, argv, cwd=self.nested), str(self.data))
                # 2. LLMWIKI_ROOT 가 lock 보다 앞선다
                self.assertEqual(self.root_of(script, argv, cwd=self.nested,
                                              env={"LLMWIKI_ROOT": str(self.other)}), str(self.other))
                # 1. --root 가 무엇보다 앞선다
                self.assertEqual(self.root_of(script, argv, cwd=self.nested,
                                              env={"LLMWIKI_ROOT": str(self.other)}, explicit=self.tmp),
                                 str(self.tmp))

    def test_shim_default_sits_between_the_lock_and_the_code_repo(self) -> None:
        env = {"LLMWIKI_DEFAULT_ROOT": str(self.other)}
        self.assertEqual(self.root_of("llmwiki.py", ("paths",), cwd=self.tmp, env=env), str(self.other))
        # 데이터 repo 안에서는 그 repo 의 lock 이 shim 기본값보다 앞선다
        self.assertEqual(self.root_of("llmwiki.py", ("paths",), cwd=self.nested, env=env), str(self.data))

    def test_routine_and_eval_use_the_shared_resolver(self) -> None:
        for name in ("llmwiki_routine.py", "llmwiki_eval.py", "llmwiki_context.py", "llmwiki.py",
                     "llmwiki_index.py"):
            source = (REPO / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertTrue("llmwiki_paths" in source or "C.resolve_root(" in source, name)
                self.assertNotIn("os.environ.get(ENV_ROOT) or DEFAULT_ROOT", source)

    def test_viewer_asks_the_python_resolver(self) -> None:
        data = (REPO / "viewer" / "scripts" / "wiki-data.ts").read_text(encoding="utf-8")
        self.assertIn('"paths"', data)
        for name in ("wiki-cli.ts", "watch-data.ts"):
            self.assertIn("resolveLayout", (REPO / "viewer" / "scripts" / name).read_text(encoding="utf-8"))
        vite = (REPO / "viewer" / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("resolveLayout(projectRoot)", vite)
        self.assertIn("layout.viewer_data", vite)
        compose = (REPO / "viewer" / "compose.yml").read_text(encoding="utf-8")
        self.assertIn("LLMWIKI_DATA", compose)
        self.assertIn("LLMWIKI_ROOT: /data", compose)
        docker = (REPO / "viewer" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV LLMWIKI_ROOT=/data", docker)
        self.assertNotIn("COPY wiki", docker)

    def test_viewer_layout_matches_the_python_answer(self) -> None:
        bun = shutil.which("bun")
        if not bun:
            self.skipTest("bun 없음")
        module = REPO / "viewer" / "scripts" / "wiki-data.ts"
        script = f"const m = await import({str(module)!r}); console.log(JSON.stringify(m.resolveLayout({str(self.code)!r})));"
        environ = {k: v for k, v in os.environ.items() if k != "LLMWIKI_ROOT"}
        proc = subprocess.run([bun, "-e", script], cwd=self.nested, capture_output=True, text=True,
                              timeout=60, env=environ)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        layout = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(layout["root"], str(self.data))
        self.assertEqual(layout["mode"], "split")
        self.assertEqual(layout["viewer_data"], str(self.data / ".llmwiki" / "viewer-data"))


class ConfigLocationTest(SplitCase):
    def test_split_reads_data_config_then_code_defaults(self) -> None:
        paths = json.loads(self.wiki("paths").stdout)
        self.assertEqual(paths["config"], str(self.data / "config"))
        # 데이터에 groups.json 이 없으면 코드의 기본값으로 build 한다
        self.write_data_page(make_page("a", "# A\n\n본문.\n", projects=["alpha"]))
        self.wiki("build")
        graph = json.loads((self.data / ".llmwiki" / "viewer-data" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(graph["groups"]["project"]["alpha"]["label"], "Alpha")
        # 데이터의 groups.json 이 생기면 그것을 쓴다
        own = json.loads((self.code / "tools" / "config" / "groups.json").read_text(encoding="utf-8"))
        own["project"]["alpha"]["label"] = "데이터 쪽 Alpha"
        (self.data / "config" / "groups.json").write_text(json.dumps(own, ensure_ascii=False), encoding="utf-8")
        self.wiki("build")
        graph = json.loads((self.data / ".llmwiki" / "viewer-data" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(graph["groups"]["project"]["alpha"]["label"], "데이터 쪽 Alpha")

    def test_vocabulary_and_glossary_never_fall_back_to_the_code_repo(self) -> None:
        """tags.json·glossary.json 은 위키마다 다른 내용이다 — 남의 어휘로 검사하면 안 된다."""
        (self.code / "tools" / "config" / "tags.json").write_text('{"tags": ["only-this"]}', encoding="utf-8")
        self.write_data_page(make_page("a", "# A\n\n본문.\n", tags=["free-tag"], projects=["alpha"]))
        out = self.wiki("lint", "--json", expect=None)
        self.assertNotIn("tags.json", out.stdout)
        (self.data / "config" / "tags.json").write_text('{"tags": ["only-this"]}', encoding="utf-8")
        out = self.wiki("lint", "--json", expect=None)
        self.assertIn("free-tag", out.stdout)

    def test_context_settings_come_from_the_data_config(self) -> None:
        self.write_data_page(make_page("pinned", "# Pinned\n\n늘 붙는 page 의 본문이다.\n", projects=["alpha"]))
        (self.data / "config" / "context.json").write_text('{"always": ["pinned"]}', encoding="utf-8")
        self.wiki("build")
        out = self.run_code("llmwiki_context.py", "context", "전혀 관계없는 질문 qqzz")
        self.assertIn("page:pinned", out.stdout)

    def test_ingest_registers_new_project_groups_in_the_data_config(self) -> None:
        (self.data / "raw" / "note.md").write_text("# Note\n\n새 프로젝트의 노트.\n", encoding="utf-8")
        before = (self.code / "tools" / "config" / "groups.json").read_bytes()
        self.wiki("ingest", "raw/note.md", "--type", "source", "--project", "brandnew")
        groups = json.loads((self.data / "config" / "groups.json").read_text(encoding="utf-8"))
        self.assertIn("brandnew", groups["project"])
        self.assertEqual((self.code / "tools" / "config" / "groups.json").read_bytes(), before)

    def test_single_repo_mode_keeps_tools_config(self) -> None:
        (self.data / "llmwiki.lock").unlink()
        paths = json.loads(self.wiki("--root", str(self.data), "paths").stdout)
        self.assertEqual(paths["mode"], "single")
        self.assertEqual(paths["config"], str(self.data / "tools" / "config"))


class DerivedLocationTest(SplitCase):
    def build_tree(self) -> dict[str, bytes]:
        self.wiki("build")
        base = self.data / ".llmwiki"
        return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*"))
                if p.is_file() and not p.name.startswith("search.work.") and not p.name.endswith(("-wal", "-shm"))}

    def test_split_mode_builds_under_dot_llmwiki_only(self) -> None:
        self.write_data_page(make_page("a", "# A\n\n[[b]] 를 본다.\n", projects=["alpha"]))
        self.write_data_page(make_page("b", "# B\n\n[[a]] 로 돌아온다.\n", projects=["alpha"]))
        first = self.build_tree()
        self.assertIn("index/search.sqlite", first)
        self.assertIn("index/catalog.json", first)
        self.assertIn("viewer-data/graph.json", first)
        self.assertFalse((self.data / "index").exists())
        self.assertFalse((self.data / "viewer").exists())
        self.assertFalse((self.code / "index").exists())
        self.assertFalse((self.code / "viewer").exists())
        # 결정성: 지우고 다시 build 해도 같은 바이트, 증분 build 도 cold 와 같은 바이트
        shutil.rmtree(self.data / ".llmwiki")
        self.assertEqual(self.build_tree(), first)
        page = json.loads((self.data / "wiki" / "concepts" / "a.json").read_text(encoding="utf-8"))
        page["summary"] = "바뀐 요약"
        (self.data / "wiki" / "concepts" / "a.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        self.wiki("build", "--changed", "wiki/concepts/a.json")
        base = self.data / ".llmwiki"
        incremental = {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*"))
                       if p.is_file() and not p.name.startswith("search.work.")
                       and not p.name.endswith(("-wal", "-shm"))}
        shutil.rmtree(base)
        self.assertEqual(self.build_tree(), incremental)

    def test_hook_reads_the_split_index(self) -> None:
        self.write_data_page(make_page("a", "# 분리 색인\n\n분리 모드 색인에서 찾아야 하는 고유한 문장 zebrafinch.\n",
                                       projects=["alpha"]))
        self.wiki("build")
        out = self.run_code("llmwiki_context.py", "search", "zebrafinch 분리 색인", "--json")
        found = json.loads(out.stdout)
        self.assertEqual(found["mode"], "index")
        self.assertEqual(found["results"][0]["id"], "page:a")


if __name__ == "__main__":
    unittest.main()
