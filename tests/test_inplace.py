"""제자리 갱신 — 설치한 폴더 하나(코드+위키)에서 `llmwiki update` 가 새 코드를 그 자리에 얹는다."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import FROZEN_NOW, REPO, copy_code, git, git_init, llmwiki

TEXT = "scripts/llmwiki_text.py"


class InPlaceUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="llmwiki-inplace-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # upstream: 지금 코드 + 데모 위키 → 두 번째 commit 에서 코드가 바뀐다
        self.work = copy_code(self.tmp / "upstream-work")
        shutil.copytree(REPO / "wiki", self.work / "wiki")
        shutil.copytree(REPO / "raw", self.work / "raw")
        (self.work / "tools" / "obsolete.txt").write_text("곧 지운다\n", encoding="utf-8")
        self.first = git_init(self.work, "upstream 1")
        self.upstream = self.tmp / "upstream.git"
        git(self.tmp, "clone", "-q", "--bare", str(self.work), str(self.upstream))

    def advance_upstream(self, init_line: str = "- upstream 이 더한 규칙.\n") -> str:
        text = (self.work / TEXT).read_text(encoding="utf-8")
        (self.work / TEXT).write_text(text + "\n# upstream 이 더한 줄\n", encoding="utf-8")
        init = (self.work / "INIT.md").read_text(encoding="utf-8")
        (self.work / "INIT.md").write_text(init.replace("# llmwiki_json", "# llmwiki_json\n\n" + init_line.rstrip("\n"), 1)
                                           if "# llmwiki_json" in init else init_line + init, encoding="utf-8")
        (self.work / "tools" / "obsolete.txt").unlink()
        (self.work / "tools" / "added.txt").write_text("새 파일\n", encoding="utf-8")
        (self.work / "VERSION").write_text("9.9.0\n", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "upstream 2")
        git(self.work, "push", "-q", str(self.upstream), "HEAD:main")
        return git(self.work, "rev-parse", "HEAD")

    def install_folder(self, *, shared_history: bool = True) -> Path:
        """사용자가 설치해 쓰던 폴더 — 위키를 쌓고 INIT.md 끝에 자기 규칙을 더했다."""
        folder = self.tmp / "opmd" / "llmwiki"
        if shared_history:
            git(self.tmp, "clone", "-q", str(self.upstream), str(folder))
        else:                                             # 파일을 복사해 만든 개인 저장소 — 이력이 따로다
            shutil.copytree(self.work, folder, ignore=shutil.ignore_patterns(".git"))
            git_init(folder, "내 저장소")
        with (folder / "INIT.md").open("a", encoding="utf-8") as fh:
            fh.write("\n- 내 위키는 회의록을 raw/meetings 에 둔다.\n")
        (folder / "wiki" / "concepts" / "mine.txt").write_text("내 데이터\n", encoding="utf-8")
        git(folder, "add", "-A")
        git(folder, "commit", "-qm", "내 위키")
        return folder

    def update(self, folder: Path, *argv: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "LLMWIKI_UPSTREAM": str(self.upstream), llmwiki.ENV_NOW: FROZEN_NOW,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        for name in ("LLMWIKI_ROOT", "LLMWIKI_DEFAULT_ROOT"):
            env.pop(name, None)
        proc = subprocess.run([sys.executable, str(folder / "scripts" / "llmwiki.py"), "--root", str(folder), "update",
                               "--yes", *argv], capture_output=True, text=True, env=env, cwd=folder)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return proc

    def assert_updated(self, folder: Path, new: str) -> None:
        self.assertIn("# upstream 이 더한 줄", (folder / TEXT).read_text(encoding="utf-8"))
        init = (folder / "INIT.md").read_text(encoding="utf-8")
        self.assertIn("upstream 이 더한 규칙", init)                 # upstream 수정과
        self.assertIn("내 위키는 회의록을 raw/meetings 에 둔다", init)  # 내 규칙이 함께 있다
        self.assertFalse((folder / "tools" / "obsolete.txt").exists())
        self.assertTrue((folder / "tools" / "added.txt").is_file())
        self.assertEqual((folder / "wiki" / "concepts" / "mine.txt").read_text(encoding="utf-8"), "내 데이터\n")
        self.assertEqual(json.loads((folder / "tools" / "upstream.json").read_text(encoding="utf-8"))["commit"], new)
        self.assertEqual(git(folder, "status", "--porcelain"), "")
        self.assertIn("코드 v", git(folder, "log", "-1", "--format=%s"))

    def test_the_installed_folder_is_updated_in_place(self) -> None:
        folder = self.install_folder()
        new = self.advance_upstream()
        dry = self.update(folder, "--dry-run").stdout
        self.assertIn(TEXT, dry)
        self.assertNotIn("# upstream 이 더한 줄", (folder / TEXT).read_text(encoding="utf-8"))
        out = self.update(folder).stdout
        self.assertIn("merge-base", out)
        self.assert_updated(folder, new)
        self.assertEqual(sorted(p.name for p in (self.tmp / "opmd").iterdir()), ["llmwiki"])   # 새 폴더 없음
        self.assertEqual(git(folder, "remote").split(), ["origin"])                          # remote 그대로
        self.assertIn("이미 최신", self.update(folder).stdout)

    def test_a_copied_repo_finds_its_base_by_content(self) -> None:
        folder = self.install_folder(shared_history=False)
        new = self.advance_upstream()
        out = self.update(folder).stdout
        self.assertIn("가장 가까운 판", out)
        self.assert_updated(folder, new)

    def test_my_own_code_edit_survives(self) -> None:
        folder = self.install_folder()
        mine = folder / "scripts" / "llmwiki_index.py"
        mine.write_text(mine.read_text(encoding="utf-8") + "\n# 내가 고친 줄\n", encoding="utf-8")
        git(folder, "commit", "-qam", "내 코드 수정")
        self.advance_upstream()
        self.assertIn("남긴다", self.update(folder).stdout)
        self.assertIn("# 내가 고친 줄", mine.read_text(encoding="utf-8"))

    def test_rules_appended_right_under_an_upstream_edit_are_kept(self) -> None:
        """upstream 이 INIT.md 마지막 줄을 고치고 사용자는 그 바로 밑에 규칙을 더했다 — git 은 충돌로 보지만
        사용자는 줄을 더하기만 했으니 새 판 끝에 붙인다."""
        folder = self.install_folder()
        init = (self.work / "INIT.md").read_text(encoding="utf-8").rstrip("\n").splitlines()
        init[-1] = init[-1] + " (upstream 이 고친 마지막 줄)"
        (self.work / "INIT.md").write_text("\n".join(init) + "\n", encoding="utf-8")
        git(self.work, "commit", "-qam", "upstream 이 마지막 줄을 고친다")
        git(self.work, "push", "-q", str(self.upstream), "HEAD:main")
        out = self.update(folder).stdout
        self.assertIn("합친다", out)
        text = (folder / "INIT.md").read_text(encoding="utf-8")
        self.assertIn("(upstream 이 고친 마지막 줄)", text)
        self.assertTrue(text.rstrip("\n").endswith("- 내 위키는 회의록을 raw/meetings 에 둔다."))

    def test_a_conflict_writes_nothing(self) -> None:
        folder = self.install_folder()
        target = folder / TEXT
        target.write_text(target.read_text(encoding="utf-8") + "\n# 내가 같은 자리에 쓴 줄\n", encoding="utf-8")
        git(folder, "commit", "-qam", "같은 자리")
        head = git(folder, "rev-parse", "HEAD")
        self.advance_upstream()
        proc = self.update(folder, expect=1)
        self.assertIn("충돌", proc.stdout + proc.stderr)
        self.assertIn(TEXT, proc.stdout)
        self.assertEqual(git(folder, "rev-parse", "HEAD"), head)
        self.assertEqual(git(folder, "status", "--porcelain"), "")
        self.assertTrue((folder / "tools" / "obsolete.txt").exists())

    def test_a_dirty_folder_is_refused(self) -> None:
        folder = self.install_folder()
        (folder / "wiki" / "concepts" / "draft.txt").write_text("x", encoding="utf-8")
        self.assertIn("커밋되지 않은 변경", self.update(folder, expect=1).stderr)


if __name__ == "__main__":
    unittest.main()
