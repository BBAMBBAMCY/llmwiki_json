"""데이터 repo 관리: llmwiki.lock, 생성 지침 파일, skills 링크 (scripts/llmwiki_repo.py)."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import shutil

from tests.support import FROZEN_DATE, REPO, SplitCase, _load, copy_code, git, git_init

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


UPSTREAM_OLD_LINE = "- 보안정보는 저장하지 않고 `(접속 정보 생략)`으로 치환한다."
UPSTREAM_NEW_LINE = "- 보안정보는 저장하지 않고 `(접속 정보 생략)`으로 치환한다. (upstream 이 나중에 고친 줄)"


class AdoptTest(SplitCase):
    """기존 단일 repo(코드+데이터, 파생물까지 커밋된 옛 clone)를 데이터 repo 로 바꾼다."""

    def setUp(self) -> None:
        super().setUp()
        # 코드 repo 이력: 옛 INIT.md → 지금 INIT.md. 옛 clone 은 옛 판 위에 로컬 규칙을 더했다.
        current = (self.code / "INIT.md").read_text(encoding="utf-8")
        self.assertIn(UPSTREAM_OLD_LINE, current)
        for rel in ("viewer/package.json", "docs/install.md", "tests/support.py"):   # 코드 repo 의 나머지 자리
            (self.code / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.code / rel).write_text("{}\n", encoding="utf-8")
        git_init(self.code, "옛 판")
        (self.code / "INIT.md").write_text(current.replace(UPSTREAM_OLD_LINE, UPSTREAM_NEW_LINE), encoding="utf-8")
        git(self.code, "commit", "-qam", "upstream 이 INIT.md 를 고친다")
        self.old = self.tmp / "old-clone"
        copy_code(self.old)
        (self.old / "INIT.md").write_text(current.replace(
            "- 새 소스가 주장을 뒤집어도 삭제하지 않고 history와 supersedes 관계를 남긴다.",
            "- 새 소스가 주장을 뒤집어도 삭제하지 않고 history와 supersedes 관계를 남긴다. (이 위키는 분기마다 정리한다)")
            + "\n- 회의록 소스는 raw/meetings 에 둔다.\n- 회의록은 `python3 scripts/llmwiki.py lint` 로 보고 tag 는 "
              "`tools/config/tags.json` 에서 고른다.\n", encoding="utf-8")
        (self.old / ".dockerignore").write_text("node_modules\n", encoding="utf-8")
        # upstream 에 없는 이 저장소만의 코드 — adopt 가 지우면 안 된다
        (self.old / "scripts" / "my_tool.py").write_text("print('mine')\n", encoding="utf-8")
        (self.old / "viewer" / "src" / "lib").mkdir(parents=True)
        (self.old / "viewer" / "src" / "lib" / "mine.ts").write_text("export const mine = 1;\n", encoding="utf-8")
        (self.old / "viewer" / "node_modules" / "pkg").mkdir(parents=True)
        (self.old / "viewer" / "node_modules" / "pkg" / "index.js").write_text("x\n", encoding="utf-8")
        # 이 저장소처럼 CLAUDE.md·AGENTS.md 는 INIT.md 를 가리키는 symlink 다
        (self.old / "CLAUDE.md").symlink_to("INIT.md")
        (self.old / "AGENTS.md").symlink_to("INIT.md")
        shutil.copytree(REPO / "wiki", self.old / "wiki")
        (self.old / "raw").mkdir()
        (self.old / "raw" / "source.md").write_text("# 원본\n", encoding="utf-8")
        for rel in ("viewer/package.json", "docs/install.md", "tests/support.py"):
            (self.old / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.old / rel).write_text("{}\n", encoding="utf-8")
        (self.old / "tools" / "eval").mkdir(parents=True)
        (self.old / "tools" / "eval" / "my-questions.json").write_text("[]\n", encoding="utf-8")
        (self.old / ".claude").mkdir()
        (self.old / ".claude" / "skills").symlink_to("../.skills")
        (self.old / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        # 옛 루틴이 커밋한 파생물
        self.run_code("llmwiki.py", "build", env={"LLMWIKI_ROOT": str(self.old)})
        git_init(self.old)
        self.assertIn("index/catalog.json", git(self.old, "ls-files"))
        self.assertIn("viewer/public/data/graph.json", git(self.old, "ls-files"))
        self.before = {p.relative_to(self.old).as_posix(): p.read_bytes()
                       for p in sorted(self.old.rglob("*")) if p.is_file() and ".git" not in p.parts}

    def adopt(self, *argv: str, expect: int = 0) -> dict:
        return json.loads(self.wiki("adopt", str(self.old), *argv, "--json", cwd=self.tmp, expect=expect).stdout)

    def test_dry_run_lists_everything_and_changes_nothing(self) -> None:
        plan = self.adopt()
        self.assertTrue(plan["dry_run"])
        for rel in ("tests", "tools/schema", "tools/parity", "docs", ".skills", "INIT.md", "VERSION",
                    "scripts/llmwiki.py", "viewer/package.json"):
            self.assertIn(rel, plan["remove"])
        self.assertEqual(plan["keep"], ["scripts/my_tool.py", "viewer/src/lib/mine.ts"])
        self.assertNotIn("scripts/my_tool.py", plan["remove"])
        self.assertIn(["tools/config/groups.json", "config/groups.json"], plan["move"])
        self.assertIn("index/catalog.json", plan["untrack"])
        self.assertIn("viewer/public/data/graph.json", plan["untrack"])
        self.assertEqual(plan["untrack_links"], [".claude/skills"])
        self.assertIn("llmwiki.lock", plan["create"])
        self.assertIn("LOCAL.md", plan["create"])
        out = self.wiki("adopt", str(self.old), cwd=self.tmp).stdout
        self.assertIn("dry-run", out)
        after = {p.relative_to(self.old).as_posix(): p.read_bytes()
                 for p in sorted(self.old.rglob("*")) if p.is_file() and ".git" not in p.parts}
        self.assertEqual(after, self.before)
        self.assertEqual(git(self.old, "status", "--porcelain"), "")

    def test_local_rules_are_split_from_the_closest_upstream_version(self) -> None:
        local = self.adopt()["local"]
        text = local["text"]
        self.assertIn("- 회의록 소스는 raw/meetings 에 둔다.", text)               # 더한 줄 → 그대로
        # 옮긴 로컬 규칙도 데이터 repo 의 명령·경로로 읽힌다
        self.assertIn("`llmwiki lint` 로 보고 tag 는 `config/tags.json` 에서 고른다", text)
        self.assertNotIn("python3 scripts/llmwiki.py", text)
        self.assertIn("검토 필요", text)                                            # 고친 줄 → 검토 필요
        self.assertIn("(이 위키는 분기마다 정리한다)", text)
        # upstream 이 나중에 고친 줄은 로컬 수정이 아니다 — 옛 판과 비교했기 때문이다
        self.assertNotIn("upstream 이 나중에 고친 줄", text)
        self.assertNotIn(UPSTREAM_OLD_LINE, text)
        self.assertEqual([item["file"] for item in local["sources"]], ["INIT.md"])   # 사본(CLAUDE·AGENTS)은 한 번만

    def test_apply_turns_the_clone_into_a_data_repo(self) -> None:
        wiki_before = {rel: body for rel, body in self.before.items() if rel.startswith("wiki/")}
        report = self.adopt("--apply")
        self.assertTrue(report["wiki_unchanged"]["sha256_identical"])
        self.assertEqual(report["checks"]["build"]["exit"], 0)
        self.assertEqual(report["checks"]["validate"]["errors"], 0)
        self.assertIn("lint", report["checks"])
        for rel in ("tests", "tools/schema", "tools/parity", "docs", ".skills", "INIT.md", "index", "tools/config",
                    ".dockerignore", "scripts/llmwiki.py", "scripts/install.sh", "viewer/package.json",
                    "viewer/node_modules", "viewer/public"):
            self.assertFalse((self.old / rel).exists(), rel)
        # 이 저장소만의 코드는 그대로 남았다
        self.assertEqual((self.old / "scripts" / "my_tool.py").read_text(encoding="utf-8"), "print('mine')\n")
        self.assertTrue((self.old / "viewer" / "src" / "lib" / "mine.ts").is_file())
        self.assertEqual(sorted(p.name for p in (self.old / "scripts").iterdir()), ["my_tool.py"])
        self.assertTrue((self.old / "tools" / "eval" / "my-questions.json").is_file())   # 목록 밖은 남긴다
        self.assertTrue((self.old / "config" / "groups.json").is_file())
        self.assertEqual({p.relative_to(self.old).as_posix(): p.read_bytes()
                          for p in sorted((self.old / "wiki").rglob("*")) if p.is_file()}, wiki_before)
        self.assertEqual((self.old / "raw" / "source.md").read_text(encoding="utf-8"), "# 원본\n")
        local = (self.old / "LOCAL.md").read_text(encoding="utf-8")
        self.assertIn("회의록 소스는 raw/meetings", local)
        claude = (self.old / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude.startswith(R.GUIDE_HEADER))
        for name in ("CLAUDE.md", "AGENTS.md"):                       # symlink 를 끊고 제 파일로 썼다
            self.assertFalse((self.old / name).is_symlink(), name)
        self.assertFalse((self.old / "INIT.md").exists())             # 링크를 따라가 INIT.md 를 되살리지 않는다
        self.assertIn("회의록 소스는 raw/meetings", claude)
        self.assertTrue((self.old / ".claude" / "skills").is_symlink())
        self.assertEqual((self.old / ".claude" / "skills").resolve(), (self.code / ".skills").resolve())
        lock = json.loads((self.old / "llmwiki.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["code_path"], str(self.code))
        # 커밋됐고 트리가 깨끗하다; 파생물은 추적에서 빠지고 .llmwiki/ 에 다시 생겼다
        self.assertEqual(git(self.old, "status", "--porcelain"), "")
        files = git(self.old, "ls-files").splitlines()
        self.assertFalse([f for f in files if f.startswith(("index/", ".claude/", "viewer/public/"))])
        self.assertEqual([f for f in files if f.startswith(("scripts/", "viewer/"))],
                         ["scripts/my_tool.py", "viewer/src/lib/mine.ts"])
        self.assertIn("config/groups.json", files)
        self.assertIn("llmwiki.lock", files)
        self.assertTrue((self.old / ".llmwiki" / "index" / "search.sqlite").is_file())
        self.assertEqual(json.loads(self.wiki("paths", cwd=self.old / "wiki").stdout)["mode"], "split")
        # 한 번 더 하면 이미 데이터 repo 라고 거절한다
        self.assertIn("이미 데이터 repo", self.wiki("adopt", str(self.old), cwd=self.tmp, expect=2).stderr)

    def test_the_dir_defaults_to_the_recorded_data_root(self) -> None:
        """설치기를 --data 로 돌린 뒤의 `llmwiki adopt --dry-run` — shim 이 기록한 데이터를 본다."""
        plan = json.loads(self.wiki("adopt", "--json", cwd=self.tmp, env={"LLMWIKI_DEFAULT_ROOT": str(self.old)}).stdout)
        self.assertEqual(plan["root"], str(self.old))
        self.assertIn("adopt 할 저장소를 준다", self.wiki("adopt", cwd=self.tmp, expect=2).stderr)

    def track_code_origin(self) -> None:
        """공개 저장소를 clone 해 위키를 쌓은 옛 clone — origin 이 코드 저장소이고 main 이 그것을 따른다."""
        git(self.code, "remote", "add", "origin", "https://github.com/Example/llmwiki.git")
        git(self.old, "remote", "add", "origin", "git@github.com:example/llmwiki")
        git(self.old, "config", "branch.main.remote", "origin")
        git(self.old, "config", "branch.main.merge", "refs/heads/main")

    def test_a_remote_pointing_at_the_code_repo_stops_being_the_push_target(self) -> None:
        self.track_code_origin()
        private = self.tmp / "private.git"
        git(self.tmp, "init", "-q", "--bare", str(private))
        git(self.old, "remote", "add", "private", str(private))
        git(self.old, "push", "-q", "private", "HEAD:main")
        plan = self.adopt()
        self.assertEqual(plan["code_remotes"], [{"name": "origin", "url": "git@github.com:example/llmwiki"}])
        self.assertIn("remote origin", self.wiki("adopt", str(self.old), cwd=self.tmp).stdout)
        report = self.adopt("--apply")
        self.assertEqual(report["code_remotes"][0]["branch_upstream"], "private/main")
        # remote 이름·주소는 사용자의 것이다 — 그대로 둔다
        self.assertEqual(sorted(git(self.old, "remote").split()), ["origin", "private"])
        self.assertEqual(git(self.old, "remote", "get-url", "origin"), "git@github.com:example/llmwiki")
        self.assertEqual(git(self.old, "rev-parse", "--abbrev-ref", "@{u}"), "private/main")

    def test_without_a_private_remote_the_branch_upstream_is_dropped(self) -> None:
        self.track_code_origin()
        report = self.adopt("--apply")
        self.assertEqual(report["code_remotes"][0]["branch_upstream"], "")
        self.assertEqual(git(self.old, "remote").split(), ["origin"])
        self.assertEqual(git(self.old, "config", "--get", "branch.main.remote", check=False), "")

    def test_with_several_own_remotes_the_upstream_is_dropped_not_guessed(self) -> None:
        self.track_code_origin()
        for name in ("backup", "mine"):
            bare = self.tmp / f"{name}.git"
            git(self.tmp, "init", "-q", "--bare", str(bare))
            git(self.old, "remote", "add", name, str(bare))
        report = self.adopt("--apply")
        self.assertEqual(report["code_remotes"][0]["branch_upstream"], "")
        self.assertEqual(sorted(git(self.old, "remote").split()), ["backup", "mine", "origin"])

    def test_the_code_can_live_inside_the_existing_folder(self) -> None:
        """기존 설치 폴더를 그대로 쓴다 — 코드를 그 안(`llmwiki_json/`)에 clone 해도 adopt·데이터 git 이 맞는다."""
        nested = self.old / "llmwiki_json"
        shutil.move(str(self.code), str(nested))
        self.code = nested
        plan = self.adopt()                                  # 안에 둔 clone 은 커밋되지 않은 변경이 아니다
        self.assertNotIn("llmwiki_json/", plan["leftover"])
        self.assertNotIn("llmwiki_json", plan["remove"])
        before = git(nested, "rev-parse", "HEAD")
        self.adopt("--apply")
        self.assertEqual(git(nested, "rev-parse", "HEAD"), before)
        self.assertEqual(git(nested, "status", "--porcelain"), "")
        self.assertTrue((nested / "scripts" / "llmwiki.py").is_file())
        self.assertEqual(git(self.old, "status", "--porcelain"), "")
        self.assertNotIn("llmwiki_json", git(self.old, "ls-files"))
        exclude = (self.old / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn("/llmwiki_json/", exclude)
        self.assertNotIn("llmwiki_json", (self.old / ".gitignore").read_text(encoding="utf-8"))
        lock = json.loads((self.old / "llmwiki.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["code_path"], str(nested))

    def test_a_dirty_repo_is_refused(self) -> None:
        (self.old / "wiki" / "concepts" / "draft.json").write_text("[]", encoding="utf-8")
        self.assertIn("커밋되지 않은 변경", self.wiki("adopt", str(self.old), cwd=self.tmp, expect=2).stderr)


if __name__ == "__main__":
    unittest.main()
