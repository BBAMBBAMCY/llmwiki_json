"""Repository invariants for the ingested canonical wiki and demo fixtures."""
from __future__ import annotations

import json
import re
import unittest

from tests.support import REPO, llmwiki


class IngestedCorpusTest(unittest.TestCase):
    def test_canonical_store_holds_real_pages(self) -> None:
        shards = [p for p in (REPO / "wiki").rglob("*.json")
                  if not p.name.startswith(".")]
        self.assertGreater(len(shards), 0, "raw ingest must populate canonical JSON shards")
        self.assertTrue(all("fixtures" not in p.parts for p in shards))

    def test_demo_and_canonical_data_are_separate(self) -> None:
        ws = llmwiki.Workspace(REPO, fixtures=True)
        self.assertTrue(ws.load_pages())
        canonical = llmwiki.Workspace(REPO).load_pages()
        self.assertTrue(canonical)
        self.assertTrue(all(entry.get("note") != "demo fixture"
                            for page in canonical for entry in page["history"]))

    def test_raw_keeps_only_the_committed_skeleton(self) -> None:
        """raw/ 의 소스 폴더는 사용자마다 다른 로컬 심링크라 추적하지 않는다.

        폴더 목록을 고정하면 소스를 하나 붙일 때마다 이 시험이 깨진다. 저장소가
        보장하는 것은 README 와 assets/ 뼈대, 그리고 나머지를 무시한다는 규칙뿐이다.
        """
        raw = REPO / "raw"
        self.assertTrue((raw / "README.md").is_file())
        self.assertTrue((raw / "assets").is_dir())
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        for rule in ("raw/*", "!raw/README.md", "!raw/assets/"):
            self.assertIn(rule, ignored)

    def test_log_records_the_ingest(self) -> None:
        rows = llmwiki.read_log(llmwiki.Workspace(REPO), limit=0)
        self.assertTrue(rows)
        self.assertIn("ingest", {row["action"] for row in rows})


class SourceLayoutTest(unittest.TestCase):
    def test_backend_owns_only_scripts_and_tests(self) -> None:
        for path in (REPO / "scripts" / "llmwiki.py", REPO / "tests" / "support.py",
                     REPO / "tools" / "schema" / "page.schema.json",
                     REPO / "tools" / "config" / "groups.json"):
            self.assertTrue(path.exists(), path)

    def test_package_scripts_call_the_cli(self) -> None:
        package = json.loads((REPO / "viewer" / "package.json").read_text(encoding="utf-8"))
        scripts = package["scripts"]
        self.assertIn("scripts/wiki-cli.ts build", scripts["build:data"])
        self.assertIn("scripts/wiki-cli.ts build --fixtures", scripts["build:data:demo"])
        self.assertIn("--tests", scripts["test"])
        cli = (REPO / "viewer" / "scripts" / "wiki-cli.ts").read_text(encoding="utf-8")
        self.assertIn("scripts/llmwiki.py", cli)
        self.assertIn("unittest", cli)

    def test_viewer_never_hardcodes_an_interpreter_name(self) -> None:
        """`python3` 는 어느 기계에나 있는 이름이 아니다 — 해석은 resolvePython 한 곳에서만 한다."""
        resolver = REPO / "viewer" / "scripts" / "wiki-data.ts"
        comments = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
        literal = re.compile(r"[\"']py(thon3?|)[\"']")
        for path in sorted((REPO / "viewer").rglob("*.ts")):
            if "node_modules" in path.parts or path == resolver:
                continue
            code = comments.sub("", path.read_text(encoding="utf-8"))
            self.assertIsNone(literal.search(code), path)
        package = (REPO / "viewer" / "package.json").read_text(encoding="utf-8")
        self.assertIsNone(literal.search(package))
        self.assertIn("LLMWIKI_PYTHON", resolver.read_text(encoding="utf-8"))

    def test_viewer_holds_only_typescript_sources(self) -> None:
        derived = {"node_modules", "dist"}   # 받아 온 것과 구운 것은 소스가 아니다
        for path in sorted((REPO / "viewer").rglob("*.js*")):
            if derived & set(path.parts) or path.suffix == ".json":
                continue
            self.fail(f"viewer must stay TypeScript-only: {path}")

    def test_the_readme_describes_no_one_machine(self) -> None:
        """README 는 프레임워크 설명이다 — 한 기계의 운영 기록(개인 LaunchAgent 이름 등)을 담지 않는다."""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bcom\.[a-z0-9-]+\.llmwiki", readme))     # 기계에 설치한 LaunchAgent 이름
        self.assertNotIn("이 컴퓨터에는", readme)

    def test_derived_artifacts_are_gitignored(self) -> None:
        ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("viewer/public/data/", ignored)
        self.assertIn("index/*.json", ignored)
        self.assertIn("index/search.sqlite*", ignored)


class UpgradeGuideTest(unittest.TestCase):
    """docs/upgrade.md 는 AI 에이전트가 그대로 실행하는 절차서다 — 적힌 명령·옵션·키가 실제로 있어야 한다."""

    guide = (REPO / "docs" / "upgrade.md").read_text(encoding="utf-8")

    def test_installer_options_exist_in_both_installers(self) -> None:
        sh = (REPO / "scripts" / "install.sh").read_text(encoding="utf-8")
        ps1 = (REPO / "scripts" / "install.ps1").read_text(encoding="utf-8")
        used = set(re.findall(r"install\.sh[^\n`]*", self.guide))
        long_opts = {o for line in used for o in re.findall(r"(?<![\w-])--[a-z][a-z-]+", line)}
        self.assertTrue({"--here", "--yes", "--ingest-routine"} <= long_opts, long_opts)
        for opt in long_opts | set(re.findall(r"`(--[a-z][a-z-]+) <", self.guide)):
            self.assertIn(opt, sh, f"install.sh 에 {opt} 가 없다")
            self.assertIn(opt, ps1, f"install.ps1 에 {opt} 가 없다")
        for opt in re.findall(r"install\.ps1[^\n`]*", self.guide):
            for name in re.findall(r"(?<![\w-])-([A-Z]\w+)", opt):
                self.assertIn(f"-{name}", ps1, f"install.ps1 에 -{name} 가 없다")

    def test_cli_subcommands_exist(self) -> None:
        cli = (REPO / "scripts" / "llmwiki.py").read_text(encoding="utf-8")
        commands = set(re.findall(r"llmwiki\.py (?:--root W )?(\w+)", self.guide))
        self.assertTrue({"update", "repair"} <= commands, commands)
        self.assertIn("def update_in_place(", cli)                 # 2단계가 이 이름으로 새 코드를 알아본다
        self.assertIn("update_in_place", self.guide)
        for name in commands:
            self.assertIn(f'add_parser("{name}"', cli, f"llmwiki {name} 가 없다")

    def test_routine_state_keys_match_what_the_installer_records(self) -> None:
        routine = (REPO / "scripts" / "llmwiki_routine.py").read_text(encoding="utf-8")
        for key in ("agent", "interval", "push", "remote"):
            self.assertIn(f"`{key}`", self.guide)
            self.assertIn(f'"{key}": {key}', routine)

    def test_readme_and_install_doc_point_to_it(self) -> None:
        self.assertIn("docs/upgrade.md", (REPO / "README.md").read_text(encoding="utf-8"))
        self.assertIn("upgrade.md", (REPO / "docs" / "install.md").read_text(encoding="utf-8"))

    def test_the_guide_invents_no_folder_or_remote_name(self) -> None:
        """경로·remote 이름은 사용자의 것이다 — 절차서가 지어 붙인 이름(옛 `llmwiki-code`)을 쓰지 않는다."""
        for text in (self.guide, (REPO / "README.md").read_text(encoding="utf-8")):
            self.assertNotIn("llmwiki-code", text)
        self.assertNotIn("remote rename", self.guide)

    def test_the_upgrade_stays_in_the_installed_folder(self) -> None:
        """설치한 폴더 그대로 — 코드 repo 를 다른 폴더에 두는 절차(adopt·--data)를 쓰지 않는다."""
        from tests.support import _load
        repo = _load("llmwiki_repo_for_guide", REPO / "scripts" / "llmwiki_repo.py")
        self.assertIn("설치한 폴더 그대로 업그레이드한다", self.guide)
        for gone in ("adopt", "--data", "-Data"):
            self.assertNotIn(gone, self.guide)
        for rel in ("wiki/", "raw/", "tools/config/"):           # 절차서가 말하는 '손대지 않는 자리' = 코드가 정한 것
            self.assertIn(rel, repo.NOT_CODE)
            self.assertIn(f"`{rel}`", self.guide)


class AppContractTest(unittest.TestCase):
    """The graph app reads graph.json, stats.json and pages/*.json — keep their shape."""

    def setUp(self) -> None:
        self.payloads = llmwiki.project(llmwiki.Workspace(REPO, fixtures=True))

    def test_graph_node_fields_match_the_typescript_interface(self) -> None:
        expected = {"id", "slug", "label", "type", "created", "updated", "projects", "tags", "group", "summary",
                    "incoming", "outgoing", "degree", "unresolved_conflicts", "orphan",
                    "data_url", "x", "y"}
        for node in self.payloads["graph.json"]["nodes"]:
            self.assertEqual(set(node), expected)

    def test_graph_edge_fields(self) -> None:
        for edge in self.payloads["graph.json"]["edges"]:
            self.assertEqual(set(edge), {"id", "source", "target", "kind"})

    def test_graph_carries_the_group_config(self) -> None:
        groups = self.payloads["graph.json"]["groups"]
        self.assertEqual(set(groups), {"project", "type", "tag_palette"})
        for group in groups["project"].values():
            self.assertEqual(set(group), {"label", "color", "match"})

    def test_stats_fields(self) -> None:
        self.assertEqual(set(self.payloads["stats.json"]),
                         {"pages", "blocks", "edges", "unresolved_conflicts"})

    def test_map_entry_fields(self) -> None:
        for entry in self.payloads["map.json"]["pages"].values():
            self.assertEqual(set(entry), {"source", "pointer", "data_url", "sha256"})
