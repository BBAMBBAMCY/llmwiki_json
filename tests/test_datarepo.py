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


class GuideTest(SplitCase):
    def setUp(self) -> None:
        super().setUp()
        (self.data / "LOCAL.md").write_text("# 이 위키만의 규칙\n\n- 회의록은 sources/meetings 에 둔다.\n", encoding="utf-8")

    def test_guides_are_init_plus_local_with_a_generated_header(self) -> None:
        self.wiki("guides")
        claude = (self.data / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual((self.data / "AGENTS.md").read_text(encoding="utf-8"), claude)
        first = claude.splitlines()[0]
        self.assertIn("생성 파일 — 직접 수정하지 말고 LOCAL.md를 수정하라", first)
        self.assertIn("# llmwiki_json — JSON 정본 LLM Wiki", claude)          # INIT.md
        self.assertIn("회의록은 sources/meetings 에 둔다", claude)             # LOCAL.md
        self.assertLess(claude.index("JSON 정본 LLM Wiki"), claude.index("회의록은"))
        # 데이터 repo 에는 코드가 없다 — 명령과 경로를 데이터 repo 의 것으로 옮겨 적는다
        self.assertNotIn("python3 scripts/llmwiki.py", claude)
        self.assertIn("`llmwiki build`", claude)
        self.assertIn("`config/groups.json`", claude)
        self.assertNotIn("tools/config/", claude)
        self.assertIn(".llmwiki/index/search.sqlite", claude)
        self.assertNotIn("## 개발 규칙", claude)
        # 기계마다 같은 글이다 — 절대경로를 싣지 않는다
        self.assertNotIn(str(self.tmp), claude)

    def test_unchanged_guides_are_not_rewritten(self) -> None:
        self.wiki("guides")
        path = self.data / "CLAUDE.md"
        os.utime(path, (1_000_000_000, 1_000_000_000))
        report = json.loads(self.wiki("guides").stdout)
        self.assertEqual(report["guides"], {"CLAUDE.md": "current", "AGENTS.md": "current"})
        self.assertEqual(path.stat().st_mtime, 1_000_000_000)
        (self.data / "LOCAL.md").write_text("# 바뀐 규칙\n", encoding="utf-8")
        report = json.loads(self.wiki("guides", "--dry-run").stdout)
        self.assertEqual(report["guides"]["CLAUDE.md"], "would-write")
        self.assertEqual(path.stat().st_mtime, 1_000_000_000)
        self.wiki("guides")
        self.assertIn("바뀐 규칙", path.read_text(encoding="utf-8"))

    def test_skills_point_at_the_code_repo(self) -> None:
        report = json.loads(self.wiki("guides").stdout)
        for rel in R.SKILL_LINKS:
            link = self.data / rel
            self.assertTrue(link.is_symlink(), rel)
            self.assertEqual(link.resolve(), (self.code / ".skills").resolve())
            self.assertEqual(report["skills"][rel], "relinked")
        again = json.loads(self.wiki("guides").stdout)
        self.assertEqual(set(again["skills"].values()), {"linked"})
        self.assertTrue(all(R.guides_current(self.data, self.code).values()))

    def test_without_symlinks_skills_are_copied_and_resynced(self) -> None:
        """Windows 에서 symlink 를 못 만들면 복사하고, update 때 다시 맞춘다."""
        env = {R.ENV_SKILLS_COPY: "1"}
        self.wiki("guides", env=env)
        link = self.data / ".claude" / "skills"
        self.assertFalse(link.is_symlink())
        self.assertTrue((link / "ingest" / "SKILL.md").is_file())
        self.assertTrue((link / R.COPY_MARKER).is_file())
        skill = self.code / ".skills" / "ingest" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\n추가된 줄\n", encoding="utf-8")
        os.environ[R.ENV_SKILLS_COPY] = "1"
        self.assertFalse(R.guides_current(self.data, self.code)[".claude/skills"])
        report = json.loads(self.wiki("guides", env=env).stdout)
        self.assertEqual(report["skills"][".claude/skills"], "copied")
        self.assertIn("추가된 줄", (link / "ingest" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.wiki("guides", env=env).stdout)["skills"][".claude/skills"],
                         "copied-current")

    def test_a_foreign_skills_dir_is_left_alone(self) -> None:
        mine = self.data / ".codex" / "skills"
        (mine / "own").mkdir(parents=True)
        (mine / "own" / "SKILL.md").write_text("내 스킬\n", encoding="utf-8")
        report = json.loads(self.wiki("guides").stdout)
        self.assertEqual(report["skills"][".codex/skills"], "kept-foreign")
        self.assertEqual((mine / "own" / "SKILL.md").read_text(encoding="utf-8"), "내 스킬\n")

    def test_single_repo_mode_refuses(self) -> None:
        out = self.wiki("--root", str(self.code), "guides", expect=2)
        self.assertIn("데이터 repo 가 아니다", out.stderr)

    def test_skills_use_the_mode_neutral_cli_name(self) -> None:
        for rel in (".skills/ingest/SKILL.md", ".skills/lint/SKILL.md"):
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("python3 scripts/llmwiki.py ", text.replace("`python3 scripts/llmwiki.py` 와 같다", ""))
            self.assertIn("`llmwiki ", text)


class InitTest(SplitCase):
    git_code = True

    def test_init_creates_a_committed_data_repo_skeleton(self) -> None:
        target = self.tmp / "my-wiki"
        report = json.loads(self.wiki("init", str(target), cwd=self.tmp).stdout)
        self.assertEqual(report["root"], str(target))
        for rel in ("wiki/sources", "wiki/entities", "wiki/concepts", "wiki/syntheses", "wiki/projects", "raw",
                    "config"):
            self.assertTrue((target / rel).is_dir(), rel)
        for rel in ("LOCAL.md", "llmwiki.lock", ".gitignore", "CLAUDE.md", "AGENTS.md", "config/groups.json"):
            self.assertTrue((target / rel).is_file(), rel)
        ignore = (target / ".gitignore").read_text(encoding="utf-8").splitlines()
        for line in (".llmwiki/", ".claude/skills", ".codex/skills"):
            self.assertIn(line, ignore)
        lock = json.loads((target / "llmwiki.lock").read_text(encoding="utf-8"))
        self.assertEqual((lock["schema_version"], lock["code_commit"], lock["code_path"]),
                         (P.SCHEMA_VERSION, self.code_commit, str(self.code)))
        groups = json.loads((target / "config" / "groups.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(groups["project"]), ["multi", "ungrouped"])     # 데모 프로젝트는 따라오지 않는다
        self.assertTrue((target / ".claude" / "skills").is_symlink())
        # git: 커밋 하나, 깨끗한 트리 (skills 링크와 파생물은 추적하지 않는다)
        self.assertEqual(git(target, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(git(target, "status", "--porcelain"), "")
        tracked = git(target, "ls-files").splitlines()
        self.assertIn("llmwiki.lock", tracked)
        self.assertIn("CLAUDE.md", tracked)
        self.assertNotIn(".claude/skills", tracked)
        # 바로 쓸 수 있다 — 위에서 찾은 lock 으로 root 를 정하고 파생물은 .llmwiki/ 에
        (target / "raw" / "note.md").write_text("# Note\n\n첫 소스의 본문.\n", encoding="utf-8")
        self.wiki("ingest", "raw/note.md", "--type", "source", "--project", "first", cwd=target)
        self.assertTrue((target / "wiki" / "sources" / "note.json").is_file())
        self.assertTrue((target / ".llmwiki" / "index" / "search.sqlite").is_file())
        self.assertEqual(self.wiki("validate", cwd=target).returncode, 0)
        self.assertEqual(git(self.code, "status", "--porcelain"), "")

    def test_init_refuses_a_non_empty_dir_and_the_code_repo(self) -> None:
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "x.txt").write_text("x", encoding="utf-8")
        self.assertIn("비어 있지 않다", self.wiki("init", str(busy), cwd=self.tmp, expect=2).stderr)
        self.assertIn("코드 repo 안", self.wiki("init", str(self.code / "data"), cwd=self.tmp, expect=2).stderr)

    def test_no_git_skips_the_commit(self) -> None:
        target = self.tmp / "plain"
        self.wiki("init", str(target), "--no-git", cwd=self.tmp)
        self.assertFalse((target / ".git").exists())
        self.assertTrue((target / "llmwiki.lock").is_file())


if __name__ == "__main__":
    unittest.main()
