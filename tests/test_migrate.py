"""스키마 판 옮기기: `llmwiki.py migrate`, 판이 다를 때의 쓰기 거부와 hook fail-open."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.support import FROZEN_DATE, REPO, SplitCase, _load, install_fake_schema, llmwiki, make_page

M = _load("llmwiki_migrate", REPO / "scripts" / "llmwiki_migrate.py")
P = _load("llmwiki_paths", REPO / "scripts" / "llmwiki_paths.py")


def old_page(slug: str, body: str, version: str = "1.0", **kwargs) -> dict:
    page = make_page(slug, body, projects=["alpha"], **kwargs)
    page["schema_version"] = version
    return page


class RegistryTest(unittest.TestCase):
    def test_the_current_schema_is_reachable_from_every_registered_version(self) -> None:
        versions = {a for a, _ in M.MIGRATIONS} | {b for _, b in M.MIGRATIONS}
        for version in versions:
            if P.schema_key(version) <= P.schema_key(P.SCHEMA_VERSION):
                steps = M.chain(version)
                self.assertEqual(steps[-1][1] if steps else version, P.SCHEMA_VERSION)
        self.assertEqual(M.final_version(), P.SCHEMA_VERSION)

    def test_an_unregistered_version_has_no_path(self) -> None:
        with self.assertRaises(ValueError):
            M.chain("0.3")

    def test_to_1_1_only_shapes_the_page(self) -> None:
        page = old_page("a", "# A\n\n본문.\n")
        page["block_aliases"] = {}
        page["keywords"] = ["x", "x", " y "]
        out = M.to_1_1(page)
        self.assertEqual(out["schema_version"], "1.1")
        self.assertNotIn("block_aliases", out)
        self.assertEqual(out["keywords"], ["x", "y"])
        self.assertEqual({k: v for k, v in out.items() if k not in ("schema_version", "keywords", "block_aliases")},
                         {k: v for k, v in page.items() if k not in ("schema_version", "keywords", "block_aliases")})
        self.assertEqual(page["schema_version"], "1.0")             # 입력은 그대로
        self.assertEqual(M.to_1_1(out), out)                        # 같은 판에 다시 걸어도 같다


class VersionRuleTest(unittest.TestCase):
    """VERSION(semver) 과 스키마 판의 대응. 스키마 판이 바뀌는 릴리스는 minor 이상을 올린다."""

    def test_version_file_is_semver_and_maps_to_the_current_schema(self) -> None:
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(P.code_version(REPO), version)
        self.assertIn(version, M.RELEASES, "VERSION 을 올렸으면 RELEASES 에도 적는다")
        self.assertEqual(M.RELEASES[version], P.SCHEMA_VERSION)
        self.assertEqual(M.final_version(), P.SCHEMA_VERSION)
        self.assertEqual(max(M.RELEASES, key=P.semver), version, "VERSION 이 가장 새 릴리스다")

    def test_a_schema_change_bumps_at_least_the_minor_version(self) -> None:
        releases = sorted(M.RELEASES.items(), key=lambda item: P.semver(item[0]))
        for (old, old_schema), (new, new_schema) in zip(releases, releases[1:]):
            with self.subTest(old=old, new=new):
                self.assertGreaterEqual(P.schema_key(new_schema), P.schema_key(old_schema))
                if new_schema != old_schema:
                    self.assertGreater(P.semver(new)[:2], P.semver(old)[:2],
                                       f"{old}→{new} 에서 schema {old_schema}→{new_schema} 인데 patch 만 올렸다")
                    self.assertIn((old_schema, new_schema), M.MIGRATIONS)

    def test_this_release_changed_the_schema(self) -> None:
        self.assertGreaterEqual(P.semver(P.code_version(REPO)), (1, 1, 0))


class MigrateCommandTest(SplitCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_data_page(old_page("a", "# A\n\n[[b]] 를 본다.\n"))
        self.write_data_page(old_page("b", "# B\n\n[[a]] 로 돌아온다.\n"))
        lock = json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))
        lock["schema_version"] = "1.0"
        (self.data / "llmwiki.lock").write_text(json.dumps(lock) + "\n", encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.data).as_posix(): p.read_bytes()
                for p in sorted((self.data / "wiki").rglob("*")) if p.is_file()}

    def test_dry_run_is_the_default_and_writes_nothing(self) -> None:
        before = self.snapshot()
        out = self.wiki("migrate").stdout
        self.assertIn("page:a", out)
        self.assertIn("~ schema_version: 1.0 → 1.1", out)
        self.assertIn("2 page", out)
        self.assertIn("dry-run", out)
        report = json.loads(self.wiki("migrate", "--dry-run", "--json").stdout)
        self.assertEqual(report["pages_changed"], 2)
        self.assertEqual(report["steps"], ["1.0→1.1"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / ".llmwiki" / "migrate").exists())

    def test_apply_validates_a_staged_copy_then_swaps_it_in(self) -> None:
        report = json.loads(self.wiki("migrate", "--apply", "--json").stdout)
        self.assertFalse(report["dry_run"])
        self.assertEqual(report["lock"], "1.1")
        page = json.loads((self.data / "wiki" / "concepts" / "a.json").read_text(encoding="utf-8"))
        self.assertEqual(page["schema_version"], "1.1")
        self.assertEqual(json.loads((self.data / "llmwiki.lock").read_text(encoding="utf-8"))["schema_version"], "1.1")
        self.assertEqual(self.wiki("validate").returncode, 0)
        log = (self.data / "wiki" / "log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "migrate")
        self.assertFalse((self.data / ".llmwiki" / "migrate").exists())
        # 두 번째는 할 일이 없다
        again = json.loads(self.wiki("migrate", "--apply", "--json").stdout)
        self.assertEqual(again["pages_changed"], 0)

    def test_several_steps_are_chained(self) -> None:
        install_fake_schema(self.code, [("1.1", "1.2"), ("1.2", "1.3")], version="1.3.0")
        report = json.loads(self.wiki("migrate", "--json").stdout)
        self.assertEqual(report["steps"], ["1.0→1.1", "1.1→1.2", "1.2→1.3"])
        self.wiki("migrate", "--apply")
        page = json.loads((self.data / "wiki" / "concepts" / "a.json").read_text(encoding="utf-8"))
        self.assertEqual(page["schema_version"], "1.3")
        self.assertTrue(page["summary"].endswith("(1.2) (1.3)"))

    def test_a_staged_copy_that_fails_validate_leaves_the_original(self) -> None:
        install_fake_schema(self.code, [("1.1", "1.2")], version="1.2.0", broken=True)
        before = self.snapshot()
        lock_before = (self.data / "llmwiki.lock").read_bytes()
        out = self.wiki("migrate", "--apply", expect=2)
        self.assertIn("원본을 그대로 둔다", out.stderr)
        self.assertIn("invalid type nope", out.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.data / "llmwiki.lock").read_bytes(), lock_before)
        self.assertFalse((self.data / ".llmwiki" / "migrate").exists())

    def test_data_newer_than_the_code_is_refused_even_by_migrate(self) -> None:
        self.write_data_page(old_page("c", "# C\n\n[[a]] 를 본다.\n", version="9.0"))
        out = self.wiki("migrate", expect=2)
        self.assertIn("코드를 먼저 갱신하라", out.stderr)
        (self.data / "raw" / "n.md").write_text("# N\n\n본문.\n", encoding="utf-8")
        out = self.wiki("ingest", "raw/n.md", "--type", "source", "--project", "alpha", expect=2)
        self.assertIn("코드를 먼저 갱신하라", out.stderr)


class WriteGateTest(SplitCase):
    """판이 다르면 쓰기는 멈추고, 읽기와 hook 주입은 계속 돈다 (fail-open)."""

    def setUp(self) -> None:
        super().setUp()
        self.write_data_page(old_page("gate", "# 판 검사\n\n판이 다른 정본에서도 주입되어야 하는 문장 kestrelwing.\n"))
        (self.data / "raw" / "n.md").write_text("# N\n\n본문.\n", encoding="utf-8")

    def test_writes_are_refused(self) -> None:
        for argv in (("ingest", "raw/n.md", "--type", "source", "--project", "alpha"),
                     ("edit", "gate", "--block", "x", "--text", "바꾼 문단"),
                     ("repair",)):
            with self.subTest(argv=argv[0]):
                out = self.wiki(*argv, expect=2)
                self.assertIn(f"{argv[0]} 거부", out.stderr)
                self.assertIn("llmwiki migrate", out.stderr)
        self.assertFalse((self.data / "wiki" / "sources" / "n.json").exists())
        # dry-run 은 아무것도 쓰지 않으니 막지 않는다
        self.wiki("ingest", "raw/n.md", "--type", "source", "--project", "alpha", "--dry-run")

    def test_reads_and_the_hook_keep_working(self) -> None:
        # build 는 validate error 가 있으면 멈춘다 — 색인이 없어도 query·hook 은 정본에서 메모리 색인을 만든다
        self.assertIn("schema_version must be", self.wiki("build", expect=2).stderr)
        self.assertIn("kestrelwing", self.wiki("get", "gate").stdout)
        self.assertIn("kestrelwing", self.wiki("render", "gate").stdout)
        self.assertIn("page:gate", self.wiki("query", "kestrelwing 판 검사").stdout)
        validate = self.wiki("validate", expect=1)               # 읽기 명령은 돌고, 판이 다르다고 알린다
        self.assertIn("schema_version must be", validate.stdout)
        payload = json.dumps({"prompt": "kestrelwing 판 검사 문장", "hook_event_name": "UserPromptSubmit",
                              "cwd": str(self.data)})
        out = self.run_code("llmwiki_context.py", "hook", stdin=payload)
        self.assertIn("gate", json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
