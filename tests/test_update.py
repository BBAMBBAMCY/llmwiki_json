"""`llmwiki update` 의 전체 흐름과 멈춤 조건 — 임시 upstream(bare) · 코드 clone · 데이터 repo 로 돌린다."""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from tests.support import SplitCase, copy_code, git, git_init, install_fake_schema


class UpdateCase(SplitCase):
    """upstream(bare) ← 개발자 clone(`src`), 사용자 코드 clone(`code`), `llmwiki init` 으로 만든 데이터 repo."""

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.code)
        shutil.rmtree(self.data)
        self.src = copy_code(self.tmp / "src")
        git_init(self.src, "코드 v1.1.0")
        self.upstream = self.tmp / "upstream.git"
        git(self.tmp, "clone", "-q", "--bare", str(self.src), str(self.upstream))
        git(self.src, "remote", "add", "origin", str(self.upstream))
        git(self.src, "fetch", "-q", "origin")
        git(self.tmp, "clone", "-q", str(self.upstream), str(self.code))
        self.wiki("init", str(self.data), cwd=self.tmp)
        (self.data / "raw" / "note.md").write_text(
            "# 갱신 확인\n\n코드를 갱신한 뒤에도 주입돼야 하는 고유한 문장 marmotridge.\n", encoding="utf-8")
        self.wiki("ingest", "raw/note.md", "--type", "source", "--project", "alpha")
        git(self.data, "add", "-A")
        git(self.data, "commit", "-qm", "소스 하나")
        self.data_head = git(self.data, "rev-parse", "HEAD")
        self.code_head = git(self.code, "rev-parse", "HEAD")

    def release(self, steps: list[tuple[str, str]], version: str, *, broken: bool = False) -> str:
        """개발자가 upstream 에 가짜 migration 을 담은 새 판을 낸다."""
        install_fake_schema(self.src, steps, version=version, broken=broken)
        git(self.src, "commit", "-qam", f"코드 v{version}")
        git(self.src, "push", "-q", "origin", "HEAD:main")
        return git(self.src, "rev-parse", "HEAD")

    def update(self, *argv: str, expect: int | None = 0, env: dict[str, str] | None = None):
        return self.wiki("update", *argv, cwd=self.data, expect=expect, env=env)

    def page(self) -> dict:
        return json.loads((self.data / "wiki" / "sources" / "note.json").read_text(encoding="utf-8"))

    def lock(self) -> dict:
        return json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))


class UpdateFlowTest(UpdateCase):
    def test_a_release_with_a_migration_is_pulled_migrated_built_and_committed(self) -> None:
        new_head = self.release([("1.1", "1.2")], "1.2.0")
        out = self.update("--yes")
        self.assertIn("schema 1.1 → 1.2", out.stdout)
        # 코드 repo 는 fast-forward 됐고 깨끗하다
        self.assertEqual(git(self.code, "rev-parse", "HEAD"), new_head)
        self.assertEqual(git(self.code, "status", "--porcelain", "--untracked-files=no"), "")
        # 정본은 새 판으로 옮겨졌다
        self.assertEqual(self.page()["schema_version"], "1.2")
        self.assertTrue(self.page()["summary"].endswith("(1.2)"))
        # lock 이 새 코드를 가리킨다
        lock = self.lock()
        self.assertEqual((lock["schema_version"], lock["code_version"], lock["code_commit"]),
                         ("1.2", "1.2.0", new_head))
        # 데이터 repo 에 정해진 형식으로 한 커밋, 트리는 깨끗하다
        self.assertEqual(git(self.data, "rev-list", "--count", f"{self.data_head}..HEAD"), "1")
        self.assertEqual(git(self.data, "log", "-1", "--format=%s"),
                         f"코드 v1.1.0→v1.2.0 ({self.code_head[:7]}→{new_head[:7]}) 갱신, schema 1.1→1.2")
        self.assertEqual(git(self.data, "status", "--porcelain"), "")
        changed = git(self.data, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertIn("wiki/sources/note.json", changed)
        self.assertIn("llmwiki.lock", changed)
        self.assertNotIn(".llmwiki", " ".join(changed))
        self.assertEqual(self.wiki("validate").returncode, 0)
        # 갱신 뒤에도 hook 주입이 돈다
        payload = json.dumps({"prompt": "marmotridge 갱신 확인 문장", "hook_event_name": "UserPromptSubmit"})
        hook = self.run_code("llmwiki_context.py", "hook", stdin=payload)
        self.assertIn("note", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
        # 한 번 더 돌리면 이미 최신이다 — 커밋하지 않는다
        again = self.update("--yes")
        self.assertIn("이미 최신", again.stdout)
        self.assertEqual(git(self.data, "rev-list", "--count", f"{self.data_head}..HEAD"), "1")

    def test_a_code_only_release_updates_the_lock_without_migrating(self) -> None:
        (self.src / "INIT.md").write_text((self.src / "INIT.md").read_text(encoding="utf-8") + "\n- 새 규칙.\n",
                                          encoding="utf-8")
        (self.src / "VERSION").write_text("1.1.1\n", encoding="utf-8")
        git(self.src, "commit", "-qam", "코드 v1.1.1")
        git(self.src, "push", "-q", "origin", "HEAD:main")
        self.update()                                              # migrate 가 없으면 확인도 묻지 않는다
        self.assertEqual(self.lock()["code_version"], "1.1.1")
        self.assertIn("- 새 규칙.", (self.data / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn("schema 1.1→1.1", git(self.data, "log", "-1", "--format=%s"))

    def test_already_current_writes_nothing(self) -> None:
        before = {p: p.read_bytes() for p in sorted(self.data.rglob("*"))
                  if p.is_file() and ".git" not in p.parts and ".llmwiki" not in p.parts}
        out = self.update()
        self.assertIn("이미 최신", out.stdout)
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), self.data_head)
        self.assertEqual({p: p.read_bytes() for p in sorted(self.data.rglob("*"))
                          if p.is_file() and ".git" not in p.parts and ".llmwiki" not in p.parts}, before)

    def test_dry_run_writes_nothing(self) -> None:
        self.release([("1.1", "1.2")], "1.2.0")
        lock = (self.data / "llmwiki.lock").read_bytes()
        out = self.update("--dry-run")
        self.assertIn("[dry-run]", out.stdout)
        self.assertEqual(git(self.code, "rev-parse", "HEAD"), self.code_head)
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), self.data_head)
        self.assertEqual((self.data / "llmwiki.lock").read_bytes(), lock)
        self.assertEqual(git(self.data, "status", "--porcelain"), "")

    def test_migrating_asks_first_unless_yes(self) -> None:
        self.release([("1.1", "1.2")], "1.2.0")
        out = self.update(expect=2)                                # stdin 이 터미널이 아니다
        self.assertIn("확인이 필요하다", out.stderr)
        self.assertEqual(self.page()["schema_version"], "1.1")
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), self.data_head)

    def test_push_only_when_the_routine_pushes(self) -> None:
        remote = self.tmp / "private.git"
        git(self.tmp, "init", "-q", "--bare", str(remote))
        git(self.data, "remote", "add", "private", str(remote))
        git(self.data, "push", "-q", "private", "HEAD:main")
        self.release([("1.1", "1.2")], "1.2.0")
        state = self.tmp / "state"
        state.mkdir()
        (state / "installed-routine").write_text(json.dumps({"root": str(self.data), "push": True,
                                                             "remote": "private"}), encoding="utf-8")
        out = self.update("--yes", env={"LLMWIKI_STATE_DIR": str(state)})
        self.assertIn("push private/main: 완료", out.stdout)
        self.assertEqual(git(remote, "rev-parse", "main"), git(self.data, "rev-parse", "HEAD"))

    def test_without_the_routine_nothing_is_pushed(self) -> None:
        remote = self.tmp / "private.git"
        git(self.tmp, "init", "-q", "--bare", str(remote))
        git(self.data, "remote", "add", "private", str(remote))
        git(self.data, "push", "-q", "private", "HEAD:main")
        self.release([("1.1", "1.2")], "1.2.0")
        out = self.update("--yes", env={"LLMWIKI_STATE_DIR": str(self.tmp / "no-state")})
        self.assertIn("push 하지 않았다", out.stdout)
        self.assertEqual(git(remote, "rev-parse", "main"), self.data_head)


class UpdateStopTest(UpdateCase):
    def test_a_dirty_data_repo_stops_before_pulling(self) -> None:
        self.release([("1.1", "1.2")], "1.2.0")
        (self.data / "LOCAL.md").write_text("고치는 중\n", encoding="utf-8")
        out = self.update("--yes", expect=1)
        self.assertIn("데이터 repo 에 커밋되지 않은 변경", out.stderr)
        self.assertEqual(git(self.code, "rev-parse", "HEAD"), self.code_head)
        self.assertEqual(self.page()["schema_version"], "1.1")

    def test_a_local_commit_in_the_code_repo_stops(self) -> None:
        self.release([("1.1", "1.2")], "1.2.0")
        (self.code / "LOCAL-NOTE").write_text("x\n", encoding="utf-8")
        git(self.code, "add", "LOCAL-NOTE")
        git(self.code, "commit", "-qm", "로컬 commit")
        out = self.update("--yes", expect=1)
        self.assertIn("로컬 commit", out.stderr)
        self.assertEqual(self.page()["schema_version"], "1.1")
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), self.data_head)

    def test_local_changes_in_the_code_repo_stop(self) -> None:
        (self.code / "INIT.md").write_text("고쳐 버린 규칙\n", encoding="utf-8")
        out = self.update("--yes", expect=1)
        self.assertIn("코드 repo 에 로컬 변경", out.stderr)

    def test_a_validate_error_stops_without_committing(self) -> None:
        # 이미 있던 error(없는 링크)는 migrate 를 막지 않지만, 커밋 전의 validate 가 막는다
        draft = self.tmp / "broken.md"
        draft.write_text("---\ntype: concept\nprojects: [alpha]\ntags: [x]\n---\n# 깨진 링크\n\n[[nowhere]] 를 본다.\n",
                         encoding="utf-8")
        self.wiki("ingest", str(draft), expect=None)
        self.assertTrue((self.data / "wiki" / "concepts" / "broken.json").is_file())
        git(self.data, "add", "-A")
        git(self.data, "commit", "-qm", "깨진 page")
        head = git(self.data, "rev-parse", "HEAD")
        self.release([("1.1", "1.2")], "1.2.0")
        out = self.update("--yes", expect=1)
        self.assertIn("missing link target", out.stdout)
        self.assertIn("커밋하지 않고 멈춘다", out.stderr)
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), head)

    def test_a_migration_that_breaks_validate_leaves_the_data_alone(self) -> None:
        self.release([("1.1", "1.2")], "1.2.0", broken=True)
        out = self.update("--yes", expect=None)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("원본을 그대로 둔다", out.stderr)
        self.assertEqual(self.page()["schema_version"], "1.1")
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), self.data_head)
        self.assertEqual(git(self.data, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
