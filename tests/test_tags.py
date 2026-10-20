"""tag 통제 어휘: 허용 목록, 표기 변형 정규화, 검색 전용 keywords."""
from __future__ import annotations

import json

from tests.support import REPO, WorkspaceCase, llmwiki, make_page


def only(items: list[str], needle: str) -> list[str]:
    return [item for item in items if needle in item]


class TagKeyTest(WorkspaceCase):
    def test_spelling_variants_share_one_key(self) -> None:
        keys = {llmwiki.tag_key(tag) for tag in ("pre_v0_1-1", "pre-v0.1-1", "PRE V0.1 1", " pre/v0_1-1 ")}
        self.assertEqual(keys, {"pre-v0-1-1"})
        self.assertEqual(llmwiki.tag_key("검색_랭킹"), llmwiki.tag_key("검색-랭킹"))
        self.assertNotEqual(llmwiki.tag_key("pre-v0.1-1"), llmwiki.tag_key("pre-v0.1-2"))

    def test_canonical_tags_fold_variants_onto_one_spelling(self) -> None:
        spellings = llmwiki.tag_spellings([], ["pre-v0.1-1"])
        self.assertEqual(llmwiki.canonical_tags(["pre_v0_1-1", "x", "pre-v0.1-1", "X"], spellings),
                         ["pre-v0.1-1", "x"])

    def test_without_a_list_the_most_used_spelling_wins(self) -> None:
        pages = [make_page(f"p{i}", "# P\n", tags=[tag]) for i, tag in
                 enumerate(["pre_v0_1-1", "pre-v0.1-1", "pre-v0.1-1", "Beta", "beta"])]
        spellings = llmwiki.tag_spellings(pages)
        self.assertEqual(spellings["pre-v0-1-1"], "pre-v0.1-1")
        self.assertEqual(spellings["beta"], "Beta")               # 같은 수면 사전순 앞


class VocabularyCase(WorkspaceCase):
    def vocabulary(self, *tags: str) -> None:
        self.ws.tags_path.write_text(json.dumps({"tags": list(tags)}, ensure_ascii=False), encoding="utf-8")

    def warnings(self, pages: list[dict]) -> list[str]:
        self.write_pages(pages)
        llmwiki.build(self.ws)
        return only(llmwiki.lint(self.ws)[1], "tag '")


class VocabularyLintTest(VocabularyCase):
    def test_a_tag_outside_the_list_is_a_warning(self) -> None:
        self.vocabulary("release", "review")
        pages = [make_page("a", "# A\n\n[[b]]\n", tags=["release", "주간보고"]),
                 make_page("b", "# B\n\n[[a]]\n", tags=["review"])]
        self.assertEqual(self.warnings(pages), [
            "page:a: tag '주간보고' 가 tools/config/tags.json 에 없다 — 목록에 더하거나 keywords 로 옮긴다"])

    def test_a_spelling_variant_is_a_warning_with_or_without_a_list(self) -> None:
        pages = [make_page("a", "# A\n\n[[b]]\n", tags=["pre_v0_1-1"]),
                 make_page("b", "# B\n\n[[a]]\n", tags=["pre-v0.1-1"]),
                 make_page("c", "# C\n\n[[a]]\n", tags=["pre-v0.1-1"])]
        variant = ["page:a: tag 'pre_v0_1-1' 는 'pre-v0.1-1' 의 변형이다 — repair 가 한 표기로 모은다"]
        self.assertEqual(self.warnings(pages), variant)
        self.vocabulary("pre-v0.1-1")
        self.assertEqual(self.warnings(pages), variant)

    def test_without_a_list_nothing_else_is_checked(self) -> None:
        self.assertFalse(self.ws.tags_path.exists())
        pages = [make_page("a", "# A\n\n[[b]]\n", tags=["아무거나"]), make_page("b", "# B\n\n[[a]]\n", tags=["무엇이든"])]
        self.assertEqual(self.warnings(pages), [])

    def test_the_repo_tags_are_all_in_its_list(self) -> None:
        vocabulary = llmwiki.Workspace(REPO).load_tag_vocabulary()
        self.assertIsNotNone(vocabulary)
        for fixtures in (False, True):
            pages = llmwiki.Workspace(REPO, fixtures=fixtures).load_pages()
            self.assertEqual(llmwiki.tag_warnings(pages, vocabulary), [], fixtures)


class NormaliseOnWriteTest(VocabularyCase):
    def test_ingest_writes_the_listed_spelling_and_keeps_keywords_apart(self) -> None:
        self.vocabulary("pre-v0.1-1", "release")
        note = "---\ntype: concept\ntags: [pre_v0_1-1, Release, pre-v0.1-1]\nkeywords: [회의록, 2분기]\n---\n# Note\n\n본문.\n"
        page = json.loads((self.root / llmwiki.ingest(self.ws, self.write_raw("note.md", note))["dest"])
                          .read_text(encoding="utf-8"))
        self.assertEqual(page["tags"], ["pre-v0.1-1", "release"])
        self.assertEqual(page["keywords"], ["회의록", "2분기"])
        self.assertIn("keywords: [회의록, 2분기]", llmwiki.render_markdown(page))

    def test_repair_folds_existing_variants(self) -> None:
        pages = [make_page("a", "# A\n\n[[b]]\n", tags=["pre_v0_1-1", "x"]),
                 make_page("b", "# B\n\n[[a]]\n", tags=["pre-v0.1-1"]),
                 make_page("c", "# C\n\n[[a]]\n", tags=["pre-v0.1-1"])]
        self.write_pages(pages)
        before = llmwiki.find_page(self.ws, "a")
        dry = llmwiki.repair(self.ws, dry_run=True)
        self.assertEqual([(f["page_id"], f["verdict"]) for f in dry["fixed"]], [("page:a", "tags")])
        self.assertEqual(llmwiki.find_page(self.ws, "a"), before)
        llmwiki.repair(self.ws)
        page = llmwiki.find_page(self.ws, "a")
        self.assertEqual(page["tags"], ["pre-v0.1-1", "x"])
        self.assertEqual(page["history"][-1]["action"], "repaired")
        self.assertEqual(only(llmwiki.lint(self.ws)[1], "변형"), [])


class KeywordsTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        page = make_page("note", "# 회의 노트\n\n결정 사항을 적었다. [[other]]\n", tags=["회의"])
        page["keywords"] = ["쿼카동물원", "2분기"]
        self.write_pages([page, make_page("other", "# 다른 노트\n\n[[note]]\n", tags=["회의"])])

    def test_keywords_are_searched(self) -> None:
        llmwiki.build(self.ws)
        self.assertEqual(llmwiki.query(self.ws, "쿼카동물원")[0]["id"], "page:note")

    def test_keywords_never_colour_the_graph(self) -> None:
        payloads = llmwiki.project(self.ws)
        node = next(n for n in payloads["graph.json"]["nodes"] if n["id"] == "page:note")
        self.assertEqual(node["tags"], ["회의"])
        self.assertNotIn("keywords", node)

    def test_the_schema_accepts_keywords(self) -> None:
        page = llmwiki.find_page(self.ws, "note")
        self.assertEqual(llmwiki.validate_page(page, llmwiki.SchemaValidator.load(self.ws)), [])
        page["keywords"] = ["중복", "중복"]
        self.assertTrue(only(llmwiki.validate_page(page, llmwiki.SchemaValidator.load(self.ws)), "unique"))
