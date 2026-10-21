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


if __name__ == "__main__":
    unittest.main()
