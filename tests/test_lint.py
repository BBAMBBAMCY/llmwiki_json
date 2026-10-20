"""Lint: schema validation, missing links, conflicts, orphans, stale index."""
from __future__ import annotations

import json

from tests.support import REPO, WorkspaceCase, llmwiki, make_page


def only(items: list[str], needle: str) -> list[str]:
    return [item for item in items if needle in item]


class ValidatePageTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.validator = llmwiki.SchemaValidator.load(self.ws)

    def check(self, page: dict) -> list[str]:
        return llmwiki.validate_page(page, self.validator)

    def test_a_well_formed_page_has_no_errors(self) -> None:
        self.assertEqual(self.check(make_page("alpha", "# Alpha\n\n[[beta]]\n")), [])

    def test_missing_required_keys(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        del page["tags"]
        self.assertEqual(only(self.check(page), "missing tags"), ["page:alpha: missing tags"])

    def test_schema_version_type_and_dates(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        page["schema_version"] = "2.0"
        page["type"] = "nope"
        page["created"] = "2026/08/19"
        errors = " ".join(self.check(page))
        self.assertIn("schema_version must be 1.0", errors)
        self.assertIn("invalid type nope", errors)
        self.assertIn("created must be YYYY-MM-DD", errors)

    def test_id_must_track_slug(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        page["slug"] = "beta"
        self.assertTrue(only(self.check(page), "id must be"))

    def test_block_order_must_match_blocks(self) -> None:
        page = make_page("alpha", "# Alpha\n\n문단.\n")
        page["block_order"].append("block:alpha:ghost")
        self.assertTrue(only(self.check(page), "block_order mismatch"))

    def test_block_key_and_kind_are_checked(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        bid = page["block_order"][0]
        page["blocks"][bid]["kind"] = "nope"
        page["blocks"][bid]["id"] = "block:other"
        errors = " ".join(self.check(page))
        self.assertIn("invalid block kind", errors)
        self.assertIn("block key/id mismatch", errors)

    def test_source_ref_formats(self) -> None:
        good = make_page("alpha", "# Alpha\n", sources=["user:2026-08-19", "source:x", "raw:y.md"])
        self.assertEqual(self.check(good), [])
        bad = make_page("alpha", "# Alpha\n", sources=["대화에서 들음"])
        self.assertTrue(only(self.check(bad), "invalid source ref"))

    def test_link_kind_and_block_id_are_checked(self) -> None:
        page = make_page("alpha", "# Alpha\n\n[[beta]]\n")
        page["links"][0]["kind"] = "nope"
        page["links"][0]["block_id"] = "block:ghost"
        errors = " ".join(self.check(page))
        self.assertIn("invalid kind", errors)
        self.assertIn("unknown block_id", errors)

    def test_unknown_top_level_property_is_rejected_by_the_schema(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        page["extra"] = 1
        self.assertTrue(only(self.check(page), "unexpected property 'extra'"))

    def test_resolution_status_enum(self) -> None:
        page = make_page("alpha", "# Alpha\n\n> ⚠️ 상충: 다르다.\n")
        bid = page["block_order"][-1]
        page["blocks"][bid]["resolution"]["status"] = "maybe"
        self.assertTrue(only(self.check(page), "invalid resolution status"))


class SchemaValidatorTest(WorkspaceCase):
    def test_mirrors_the_schema_file_enums(self) -> None:
        schema = json.loads((self.root / "tools" / "schema" / "page.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["type"]["enum"]), llmwiki.ALLOWED_TYPES)
        self.assertEqual(set(schema["$defs"]["block"]["properties"]["kind"]["enum"]),
                         llmwiki.ALLOWED_BLOCKS)
        self.assertEqual(set(schema["$defs"]["link"]["properties"]["kind"]["enum"]),
                         llmwiki.ALLOWED_LINK_KINDS)

    def test_reports_type_pattern_and_uniqueness(self) -> None:
        validator = llmwiki.SchemaValidator({"type": "object", "properties": {
            "a": {"type": "string", "pattern": "^x"},
            "b": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}}})
        errors = validator.validate({"a": "yy", "b": [1, 1, "z"]})
        self.assertTrue(any("does not match" in e for e in errors))
        self.assertTrue(any("expected type integer" in e for e in errors))
        self.assertTrue(any("unique" in e for e in errors))

    def test_resolves_refs(self) -> None:
        validator = llmwiki.SchemaValidator({"$ref": "#/$defs/x",
                                             "$defs": {"x": {"type": "string"}}})
        self.assertEqual(validator.validate("ok"), [])
        self.assertTrue(validator.validate(3))


class LintTest(WorkspaceCase):
    def build_and_lint(self) -> tuple[list[str], list[str]]:
        llmwiki.build(self.ws)
        return llmwiki.lint(self.ws)

    def test_clean_wiki_reports_nothing(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n", projects=["beta"]),
                          make_page("beta", "# Beta\n\n[[alpha]]\n", projects=["beta"])])
        errors, warnings = self.build_and_lint()
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_link_target_is_an_error(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[nowhere]]\n", projects=["beta"])])
        errors, _ = self.build_and_lint()
        self.assertEqual(only(errors, "missing link target"),
                         ["page:alpha: missing link target [[nowhere]]"])

    def test_unresolved_conflict_is_a_warning(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n\n> ⚠️ 상충: 값이 다르다.\n"),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        errors, warnings = self.build_and_lint()
        self.assertEqual(errors, [])
        self.assertEqual(len(only(warnings, "unresolved conflict")), 1)

    def test_resolved_conflict_is_not_warned_about(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n\n> ✅ 현행: 최신을 따른다.\n"),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        _, warnings = self.build_and_lint()
        self.assertEqual(only(warnings, "conflict"), [])

    def test_orphan_pages_are_warned_about(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n"),
                          make_page("beta", "# Beta\n"),
                          make_page("lonely", "# Lonely\n")])
        _, warnings = self.build_and_lint()
        self.assertEqual(only(warnings, "orphan"),
                         ["page:lonely: orphan (no inbound or outbound links)"])

    def test_home_index_and_log_pages_may_be_unlinked(self) -> None:
        self.write_pages([make_page("home", "# Home\n", type="home"),
                          make_page("catalog", "# Catalog\n", type="index"),
                          make_page("history", "# History\n", type="log")])
        _, warnings = self.build_and_lint()
        self.assertEqual(only(warnings, "orphan"), [])

    def test_empty_summary_is_a_warning(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n", summary=""),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        _, warnings = self.build_and_lint()
        self.assertEqual(only(warnings, "empty summary"), ["page:alpha: empty summary"])

    def test_duplicate_slug_is_an_error(self) -> None:
        page = make_page("alpha", "# Alpha\n")
        self.write_pages([page], name="a.json")
        self.write_pages([page], name="b.json")
        errors, _ = llmwiki.lint(self.ws)
        self.assertTrue(only(errors, "duplicate slug"))

    def test_duplicate_block_id_is_an_error(self) -> None:
        alpha = make_page("alpha", "# Alpha\n")
        beta = make_page("beta", "# Beta\n")
        bid = alpha["block_order"][0]
        beta["blocks"][bid] = dict(alpha["blocks"][bid])
        beta["block_order"].append(bid)
        self.write_pages([alpha, beta])
        errors, _ = llmwiki.lint(self.ws)
        self.assertTrue(only(errors, "duplicate block id"))

    def test_dangling_page_source_ref_is_an_error(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n", sources=["page:ghost"]),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        errors, _ = llmwiki.lint(self.ws)
        self.assertEqual(only(errors, "source ref"), ["page:alpha: source ref page:ghost has no page"])

    def test_missing_index_is_reported(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n"),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        _, warnings = llmwiki.lint(self.ws)
        self.assertIn("index/map.json missing — run build", warnings)

    def test_stale_index_is_reported_and_clears_after_rebuild(self) -> None:
        pages = [make_page("alpha", "# Alpha\n\n[[beta]]\n"), make_page("beta", "# Beta\n\n[[alpha]]\n")]
        self.write_pages(pages)
        llmwiki.build(self.ws)
        self.assertEqual(llmwiki.stale_index(self.ws), [])

        pages.append(make_page("gamma", "# Gamma\n\n[[alpha]]\n"))
        self.write_pages(pages)
        self.assertEqual(llmwiki.stale_index(self.ws), ["index/map.json stale — run build"])
        llmwiki.build(self.ws)
        self.assertEqual(llmwiki.stale_index(self.ws), [])

    def test_lint_does_not_write_anything(self) -> None:
        self.write_pages([make_page("alpha", "# Alpha\n\n[[beta]]\n"),
                          make_page("beta", "# Beta\n\n[[alpha]]\n")])
        llmwiki.build(self.ws)
        snapshot = {p: p.read_bytes() for p in sorted(self.root.rglob("*.json"))}
        llmwiki.lint(self.ws)
        self.assertEqual({p: p.read_bytes() for p in sorted(self.root.rglob("*.json"))}, snapshot)


class LinkToleranceLintTest(WorkspaceCase):
    def build_and_lint(self) -> tuple[list[str], list[str]]:
        llmwiki.build(self.ws)
        return llmwiki.lint(self.ws)

    def test_title_link_is_not_reported_missing(self) -> None:
        self.write_pages([make_page("alpha-platform", "# Alpha Platform\n\n첫 워크스트림.\n"),
                          make_page("note", "# Note\n\n[[Alpha Platform]] 참조.\n")])
        errors, _ = self.build_and_lint()
        self.assertEqual(only(errors, "missing link target"), [])

    def test_block_link_missing_from_the_links_array_is_an_error(self) -> None:
        alpha = make_page("alpha", "# Alpha\n\n[[beta]] 참조.\n")
        alpha["links"] = []
        self.write_pages([alpha, make_page("beta", "# Beta\n\n[[alpha]] 참조.\n")])
        errors, warnings = self.build_and_lint()
        self.assertEqual(errors, ["page:alpha: block link [[beta]] missing from links"])
        self.assertEqual(only(warnings, "missing from links"), [])

    def test_source_reference_keeps_a_page_off_the_orphan_list(self) -> None:
        self.write_pages([make_page("handbook", "# Handbook\n\n원본.\n"),
                          make_page("note", "# Note\n\n본문.\n", sources=["page:handbook"])])
        _, warnings = self.build_and_lint()
        self.assertEqual(only(warnings, "orphan"), [])

    def test_source_reference_outside_the_wiki_is_not_an_error(self) -> None:
        self.write_pages([make_page("note", "# Note\n\n본문.\n",
                                    sources=["user:2026-08-19", "raw:notes.md", "source:외부자료"])])
        errors, _ = self.build_and_lint()
        self.assertEqual(errors, [])


class ConsistencyTest(WorkspaceCase):
    """source_text 가 정본이다 — 거기서 다시 만든 값과 다른 파생 필드는 validate error 다."""

    BODY = "# Alpha\n\n첫 문단 [[beta]].\n\n- 하나\n- 둘\n\n> ⚠️ 상충: 두 문서가 다르다.\n"

    def setUp(self) -> None:
        super().setUp()
        self.page = make_page("alpha", self.BODY)
        self.para, self.items, self.conflict = self.page["block_order"][1:4]

    def errors(self, page: dict) -> list[str]:
        return llmwiki.consistency_errors(page)

    def test_a_parsed_page_is_consistent(self) -> None:
        self.assertEqual(self.errors(self.page), [])

    def test_fingerprint_must_be_the_source_text_hash(self) -> None:
        self.page["blocks"][self.para]["fingerprint"] = "0" * 16
        self.assertTrue(only(self.errors(self.page), "!= block_fingerprint(kind, source_text)"))

    def test_data_must_be_what_the_source_text_parses_to(self) -> None:
        self.page["blocks"][self.para]["data"]["text"] = "검색만 보는 다른 문장."
        self.assertEqual(only(self.errors(self.page), "data != parse"),
                         [f"page:alpha:{self.para}: data != parse(source_text)"])
        self.page["blocks"][self.items]["data"]["items"].append("셋")
        self.assertEqual(len(only(self.errors(self.page), "data != parse")), 2)

    def test_source_text_must_read_as_one_block_of_the_same_kind(self) -> None:
        block = self.page["blocks"][self.para]
        block["source_text"] = "- 목록이 됐다"
        self.assertTrue(only(self.errors(self.page), "parses as list, not paragraph"))
        block["source_text"] = "한 문단.\n\n두 문단."
        self.assertTrue(only(self.errors(self.page), "exactly one block"))

    def test_conflict_current_raw_and_markdown_may_carry_extra_data(self) -> None:
        self.page["blocks"][self.conflict]["data"]["claims"] = ["A 기준", "B 기준"]
        self.assertEqual(self.errors(self.page), [])

    def test_refs_follow_the_source_text(self) -> None:
        self.page["blocks"][self.para]["refs"] = ["gamma"]
        self.assertTrue(only(self.errors(self.page), "refs ['gamma']"))

    def test_block_ids_carry_slug_fingerprint_and_occurrence(self) -> None:
        page = make_page("alpha", "# Alpha\n\n문단.\n")
        old = page["block_order"][1]
        legacy = "block:alpha:p1"
        page["blocks"][legacy] = dict(page["blocks"].pop(old), id=legacy)
        page["block_order"][1] = legacy
        self.assertEqual(only(self.errors(page), "block id"),
                         [f"page:alpha:{legacy}: block id is not block:<slug>:<16hex>:<n>"])

    def test_content_and_fingerprint_changed_but_the_id_did_not(self) -> None:
        block = self.page["blocks"][self.para]
        block["source_text"] = block["data"]["text"] = "바뀐 문단 [[beta]]."
        block["fingerprint"] = llmwiki.block_fingerprint("paragraph", block["source_text"])
        self.assertEqual(only(self.errors(self.page), "block id"),
                         [f"page:alpha:{self.para}: block id does not carry its fingerprint"])

    def test_wiki_links_are_exactly_the_body_links(self) -> None:
        self.page["links"] = [{"target": "Gamma", "kind": "wiki"},
                              {"target": "beta", "kind": "related"}]
        self.assertEqual(only(self.errors(self.page), "link"),
                         ["page:alpha: block link [[beta]] missing from links",
                          "page:alpha: wiki link [[Gamma]] is not in the body"])

    def test_link_keys_tolerate_case_and_spacing(self) -> None:
        self.page["links"][0]["target"] = "Beta"
        self.assertEqual(self.errors(self.page), [])

    def test_validate_reports_drift_but_build_still_projects(self) -> None:
        self.page["blocks"][self.para]["data"]["text"] = "다른 말."
        self.write_pages([self.page, make_page("beta", "# Beta\n\n[[alpha]]\n")])
        self.assertEqual(json.loads(self.cli("build").stdout)["pages"], 2)
        proc = self.cli("validate", expect=1)
        self.assertIn(f"ERROR page:alpha:{self.para}: data != parse(source_text)", proc.stdout)

    def test_ingest_refuses_a_drifted_json_page(self) -> None:
        self.page["blocks"][self.para]["fingerprint"] = "0" * 16
        path = self.root / "raw" / "alpha.json"
        path.write_text(json.dumps(self.page, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(llmwiki.WikiError, "block_fingerprint"):
            llmwiki.ingest(self.ws, path)
        self.assertEqual(list((self.root / "wiki").rglob("*.json")), [])

    def test_the_repo_demo_and_fixture_pages_are_consistent(self) -> None:
        for fixtures in (False, True):
            for _rel, page in llmwiki.Workspace(REPO, fixtures=fixtures).load_documents():
                self.assertEqual(self.errors(page), [], page["id"])
