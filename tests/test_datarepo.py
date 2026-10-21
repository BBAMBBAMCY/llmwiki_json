"""데이터 repo 관리: llmwiki.lock, 생성 지침 파일, skills 링크 (scripts/llmwiki_repo.py)."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.support import FROZEN_DATE, REPO, SplitCase, _load, git

R = _load("llmwiki_repo", REPO / "scripts" / "llmwiki_repo.py")
P = _load("llmwiki_paths", REPO / "scripts" / "llmwiki_paths.py")


class LockTest(SplitCase):
    git_code = True

    def test_lock_records_the_code_it_was_aligned_with(self) -> None:
        payload = R.refresh_lock(self.data, code=self.code)
        lock = json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))
        self.assertEqual(list(lock), ["lock_version", "schema_version", "code_version", "code_commit",
                                      "code_path", "updated"])
        self.assertEqual(lock["lock_version"], 1)
        self.assertEqual(lock["schema_version"], P.SCHEMA_VERSION)
        self.assertEqual(lock["code_version"], (self.code / "VERSION").read_text(encoding="utf-8").strip())
        self.assertEqual(lock["code_commit"], self.code_commit)
        self.assertRegex(lock["code_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(lock["code_path"], str(self.code))
        self.assertEqual(lock["updated"], FROZEN_DATE)
        self.assertTrue(payload["changed"])

    def test_an_unchanged_lock_is_not_rewritten(self) -> None:
        R.refresh_lock(self.data, code=self.code)
        before = (self.data / "llmwiki.lock").read_bytes()
        os.environ["LLMWIKI_NOW"] = "2030-01-01"                    # 날짜만 다르면 다시 쓰지 않는다
        self.assertFalse(R.refresh_lock(self.data, code=self.code)["changed"])
        self.assertEqual((self.data / "llmwiki.lock").read_bytes(), before)
        (self.code / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        self.assertTrue(R.refresh_lock(self.data, code=self.code)["changed"])
        lock = json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))
        self.assertEqual((lock["code_version"], lock["updated"]), ("9.9.9", "2030-01-01"))

    def test_schema_version_is_kept_unless_given(self) -> None:
        lock = json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))
        lock["schema_version"] = "1.0"
        (self.data / "llmwiki.lock").write_text(json.dumps(lock), encoding="utf-8")
        self.assertEqual(R.refresh_lock(self.data, code=self.code)["schema_version"], "1.0")
        self.assertEqual(R.refresh_lock(self.data, code=self.code, schema_version="1.1")["schema_version"], "1.1")

    def test_a_code_dir_that_is_not_its_own_repo_has_no_commit(self) -> None:
        inner = self.code / "nested"
        inner.mkdir()
        self.assertEqual(R.code_commit(inner), "")
        self.assertEqual(R.code_commit(self.code), git(self.code, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
