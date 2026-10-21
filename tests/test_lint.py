"""Lint: schema validation, missing links, conflicts, orphans, stale index."""
from __future__ import annotations

import json
import unittest

from tests.support import DEMO_WIKI, REPO, WorkspaceCase, llmwiki, make_page


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
        self.assertIn(f"schema_version must be {llmwiki.SCHEMA_VERSION}", errors)
        self.assertIn("invalid type nope", errors)
        self.assertIn("created must be YYYY-MM-DD", errors)

    def test_schema_version_lives_in_one_constant(self) -> None:
        """page 의 판은 llmwiki_paths.SCHEMA_VERSION 하나에서 온다 — validate·ingest·스키마 파일에 따로 적지 않는다."""
        paths = (REPO / "scripts" / "llmwiki_paths.py").read_text(encoding="utf-8")
        self.assertRegex(paths, r'(?m)^SCHEMA_VERSION = "\d+\.\d+"$')
        source = (REPO / "scripts" / "llmwiki.py").read_text(encoding="utf-8")
        self.assertIn("SCHEMA_VERSION = P.SCHEMA_VERSION", source)
        self.assertNotRegex(source, r'schema_version"\]? ?!= ?"\d')
        self.assertNotRegex(source, r'"schema_version": "\d')
        schema = json.loads((REPO / "tools" / "schema" / "page.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("const", schema["properties"]["schema_version"])
        page = make_page("alpha", "# Alpha\n")
        self.assertEqual(page["schema_version"], llmwiki.SCHEMA_VERSION)
        self.assertFalse(only(self.check(page), "schema_version"))

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


class CompileQualityTest(WorkspaceCase):
    """소스를 한 번 통합해 두는 위키의 냄새 — 전부 경고다."""

    def warnings(self, pages: list[dict]) -> list[str]:
        self.write_pages(pages)
        llmwiki.build(self.ws)
        return llmwiki.lint(self.ws)[1]

    def test_a_table_dump_summary_is_flagged(self) -> None:
        dumped = make_page("spec", "# Spec\n\n| 이름 | 나이 |\n| --- | --- |\n| 홍길동 | 30 |\n\n[[note]]\n",
                           summary="")
        table = dumped["block_order"][1]                       # 옛 ingest 가 표를 그대로 요약으로 삼았다
        dumped["summary"] = llmwiki.summary_from_blocks({table: dumped["blocks"][table]}, [table])
        header = make_page("header", "# H\n\n[[note]]\n", summary="항목 값 이름 홍길동")
        clean = make_page("note", "# Note\n\n[[spec]] [[header]]\n", summary="사양을 한 문장으로 말한다.")
        found = only(self.warnings([dumped, header, clean]), "table dump")
        self.assertEqual(found, ["page:header: summary starts with a table dump — 한 문장 요약으로 바꾼다",
                                 "page:spec: summary starts with a table dump — 한 문장 요약으로 바꾼다"])

    def test_the_inferred_summary_skips_a_leading_table(self) -> None:
        page = make_page("spec", "# Spec\n\n| 항목 | 값 |\n| --- | --- |\n| 이름 | 홍길동 |\n\n사양 설명 문단.\n")
        self.assertEqual(llmwiki.summary_from_blocks(page["blocks"], page["block_order"]), "사양 설명 문단.")
        only_table = make_page("table", "# T\n\n| a | b |\n| --- | --- |\n")
        self.assertEqual(llmwiki.summary_from_blocks(only_table["blocks"], only_table["block_order"]),
                         "a b --- ---")

    def test_a_source_that_reaches_no_concept_is_flagged(self) -> None:
        pages = [make_page("raw-a", "# A\n\n[[raw-b]] 만 본다.\n", type="source"),
                 make_page("raw-b", "# B\n\n[[topic]] 을 정리했다.\n", type="source"),
                 make_page("topic", "# Topic\n\n개념.\n", type="concept")]
        self.assertEqual(only(self.warnings(pages), "no concept"),
                         ["page:raw-a: source links to no concept, entity or synthesis — 아직 통합되지 않았다"])

    def test_compile_debt_counts_newer_sources_on_either_side(self) -> None:
        concept = make_page("topic", "# Topic\n\n개념.\n", sources=["page:cited"], created="2026-08-01")
        pages = [concept,
                 make_page("mentions", "# M\n\n[[topic]] 새 사실.\n", type="source", created="2026-08-10"),
                 make_page("cited", "# C\n\n[[topic]] 근거.\n", type="source", created="2026-08-05"),
                 make_page("older", "# O\n\n[[topic]] 옛 사실.\n", type="source", created="2026-07-01")]
        self.assertEqual(only(self.warnings(pages), "compile debt"),
                         ["page:topic: compile debt 2 — 이 concept 보다 새 source: cited, mentions"])

    def test_a_page_over_200_kb_is_flagged(self) -> None:
        big = make_page("big", "# Big\n\n" + "가" * 36_000 + "\n\n[[small]]\n")     # 본문이 source_text·data 두 벌
        near = make_page("near", "# Near\n\n" + "나" * 33_000 + "\n\n[[small]]\n")
        small = make_page("small", "# Small\n\n[[big]] [[near]]\n")
        size = len(json.dumps(big, ensure_ascii=False, indent=2).encode("utf-8"))
        self.assertGreater(size, 200 * 1024)
        self.assertEqual(only(self.warnings([big, near, small]), "KB"),
                         [f"page:big: page is {size // 1024} KB (> 200 KB) — 나눈다"])

    def test_a_replacing_sentence_needs_a_supersedes_link(self) -> None:
        claims = make_page("policy-v2", "# 정책 2판\n\n이 판은 [[policy-v1]]을 대체한다.\n")
        declared = make_page("policy-v3", "# 정책 3판\n\n이 판은 [[policy-v2]]를 대체한다.\n")
        declared["links"].append({"target": "policy-v2", "label": "policy-v2", "kind": "supersedes"})
        code = make_page("script", "# Script\n\n```py\ns.replaces('a', 'b')  # replaces\n```\n\n[[policy-v1]]\n")
        old = make_page("policy-v1", "# 정책 1판\n\n[[script]]\n")
        found = only(self.warnings([claims, declared, code, old]), "supersedes 링크가 없다")
        self.assertEqual(found, [f"page:policy-v2:{claims['block_order'][1]}: 본문이 '을 대체한다' 라고 하지만 "
                                 "supersedes 링크가 없다"])

    def clean(self, *, fixtures: bool) -> None:
        pages = llmwiki.Workspace(REPO, fixtures=fixtures).load_pages()
        self.assertEqual(llmwiki.compile_warnings(pages, llmwiki.page_lookup(pages)), [])

    @unittest.skipUnless(DEMO_WIKI, "wiki/ 가 데모 위키가 아니다 — 실데이터의 경고는 lint 가 알린다")
    def test_the_demo_wiki_has_none_of_these(self) -> None:
        self.clean(fixtures=False)

    def test_the_fixture_pages_have_none_of_these(self) -> None:
        self.clean(fixtures=True)


class OpenQuestionTest(WorkspaceCase):
    """아직 답이 없는 질문은 상충과 다른 일이다 — owner·due 를 갖고 lint 가 따로 센다."""

    BODY = ("# Alpha\n\n[[beta]]\n\n> ⚠️ 상충: 두 문서의 수치가 다르다.\n\n"
            "> ❓ 미결: 배포 주기를 격주로 바꿀 것인가?\n> owner: release-team\n> due: 2026-08-01\n\n"
            "> ❓ 미결: 누가 승인하나?\n> 담당: 플랫폼팀\n\n"
            "> ❓ 미결: 아직 아무도 모른다.\n")

    def setUp(self) -> None:
        super().setUp()
        self.page = make_page("alpha", self.BODY)
        self.questions = [self.page["blocks"][b] for b in self.page["block_order"]
                          if self.page["blocks"][b]["kind"] == "open_question"]

    def test_the_parser_reads_owner_and_due(self) -> None:
        self.assertEqual([(q["data"]["owner"], q["data"]["due"]) for q in self.questions],
                         [("release-team", "2026-08-01"), ("플랫폼팀", None), ("", None)])
        self.assertTrue(self.questions[0]["data"]["text"].startswith("❓ 미결: 배포 주기를"))
        self.assertNotIn("resolution", self.questions[0])
        self.assertEqual(llmwiki.consistency_errors(self.page), [])
        self.assertEqual(llmwiki.validate_page(self.page, llmwiki.SchemaValidator.load(self.ws)), [])

    def test_only_the_marker_with_its_word_makes_a_question(self) -> None:
        page = make_page("q", "# Q\n\n> 고객이 물었다: 이게 맞나요❓\n\n> ❓ 왜 격주 배포인가? 위험을 줄이려고.\n\n"
                              "> ❓ 미결: 누가 승인하나?\n> owner:\n> due: 2026-09-01\n")
        kinds = [page["blocks"][b]["kind"] for b in page["block_order"][1:]]
        self.assertEqual(kinds, ["quote", "quote", "open_question"])
        data = page["blocks"][page["block_order"][-1]]["data"]
        self.assertEqual((data["owner"], data["due"]), ("", "2026-09-01"))   # 빈 owner 가 다음 줄을 먹지 않는다

    def test_owner_and_due_are_required_fields(self) -> None:
        bid = next(b for b in self.page["block_order"] if self.page["blocks"][b]["kind"] == "open_question")
        data = self.page["blocks"][bid]["data"]
        del data["owner"]
        data["due"] = "다음 주"
        errors = " ".join(llmwiki.validate_page(self.page, llmwiki.SchemaValidator.load(self.ws)))
        self.assertIn("open_question needs data.owner", errors)
        self.assertIn("open_question needs data.due", errors)

    def test_lint_counts_conflicts_and_open_questions_apart(self) -> None:
        self.write_pages([self.page, make_page("beta", "# Beta\n\n[[alpha]]\n")])
        llmwiki.build(self.ws)
        _errors, warnings = llmwiki.lint(self.ws)
        self.assertEqual(len(only(warnings, "unresolved conflict")), 1)
        found = only(warnings, "open question")
        self.assertEqual(len(found), 3)
        self.assertTrue(any("(owner release-team, due 2026-08-01) — 기한 지남" in w for w in found))
        self.assertTrue(any("(owner 없음, due 없음)" in w for w in found))
        self.assertEqual(llmwiki.issue_counts(llmwiki.Workspace(self.root).load_pages()),
                         {"unresolved_conflicts": 1, "open_questions": 3})
        self.assertIn("미판정 상충 1, 미결 질문 3", self.cli("lint").stdout)
        self.assertEqual(json.loads(self.cli("lint", "--json").stdout)["counts"],
                         {"unresolved_conflicts": 1, "open_questions": 3})

    def test_edit_changes_owner_and_due_through_the_markdown(self) -> None:
        self.write_pages([self.page, make_page("beta", "# Beta\n\n[[alpha]]\n")])
        bid = next(b for b in self.page["block_order"] if self.page["blocks"][b]["kind"] == "open_question")
        new_id = llmwiki.edit(self.ws, "alpha", bid,
                              "> ❓ 미결: 배포 주기를 격주로 바꿀 것인가?\n> owner: platform\n> due: 2026-12-01")["block_id"]
        data = llmwiki.find_page(self.ws, "alpha")["blocks"][new_id]["data"]
        self.assertEqual((data["owner"], data["due"]), ("platform", "2026-12-01"))

    def test_it_renders_as_its_own_blockquote_and_round_trips(self) -> None:
        self.assertIn('<blockquote class="open_question">', llmwiki.render_html(self.page))
        question = self.questions[0]
        raw = llmwiki.render_data("page:alpha", "open_question", question["data"])
        self.assertEqual(llmwiki.parse_one("page:alpha", raw)["data"], question["data"])

    def test_the_schema_knows_the_kind(self) -> None:
        schema = json.loads((self.root / "tools" / "schema" / "page.schema.json").read_text(encoding="utf-8"))
        self.assertIn("open_question", schema["$defs"]["block"]["properties"]["kind"]["enum"])
