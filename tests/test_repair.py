"""`repair`: drifted blocks are re-derived only where history/updated say which side is newer."""
from __future__ import annotations

import json

from tests.support import WorkspaceCase, llmwiki, make_page

NOTE = "---\ntype: concept\ntags: [t]\nprojects: [beta]\n---\n# Note\n\n첫 문단은 [[beta]] 를 본다.\n\n둘째 문단.\n"


class RepairCase(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_pages([make_page("beta", "# Beta\n\n[[note]]\n", projects=["beta"], tags=["t"])],
                         name="beta.json")
        self.dest = self.root / llmwiki.ingest(self.ws, self.write_raw("note.md", NOTE))["dest"]
        self.para = self.page()["block_order"][1]

    def page(self) -> dict:
        return json.loads(self.dest.read_text(encoding="utf-8"))

    def save(self, page: dict) -> None:
        self.dest.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")

    def drift(self, *, source: str | None = None, data: str | None = None, updated: str | None = None,
              note: bool = False) -> None:
        """손으로 JSON 을 고친 것처럼 — 한쪽(또는 양쪽)만 바꾸고 지문·id 는 그대로 둔다."""
        page = self.page()
        block = page["blocks"][self.para]
        if source is not None:
            block["source_text"] = source
        if data is not None:
            block["data"]["text"] = data
        if updated:
            page["updated"] = updated
        if note:
            page["history"].append({"at": page["updated"], "action": "updated", "actor": "agent"})
        self.save(page)

    def errors(self) -> list[str]:
        return llmwiki.lint(self.ws)[0]


class RepairTest(RepairCase):
    def test_newer_data_rewrites_the_source_text(self) -> None:
        self.drift(data="첫 문단은 이제 [[gamma]] 와 [[beta]] 를 본다.", updated="2026-08-25")
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["data"])
        page = self.page()
        new_id = report["fixed"][0]["new_id"]
        block = page["blocks"][new_id]
        self.assertEqual(block["source_text"], "첫 문단은 이제 [[gamma]] 와 [[beta]] 를 본다.")
        self.assertEqual(block["refs"], ["beta", "gamma"])
        self.assertEqual(page["block_aliases"], {self.para: new_id})
        self.assertIn("gamma", {l["target"] for l in page["links"] if l["kind"] == "wiki"})
        self.assertEqual(page["history"][-1]["action"], "repaired")
        self.assertEqual(page["updated"], "2026-08-25")           # repair 는 내용을 새로 쓰지 않는다
        self.assertEqual([e for e in self.errors() if "missing link target" not in e], [])
        self.assertEqual(llmwiki.stale_index(self.ws), [])

    def test_newer_source_text_is_parsed_into_data(self) -> None:
        self.drift(source="첫 문단은 [[beta]] 를 다시 본다.", note=True)   # 같은 날이라도 history 가 적었다
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["source_text"])
        block = self.page()["blocks"][report["fixed"][0]["new_id"]]
        self.assertEqual(block["data"], {"text": "첫 문단은 [[beta]] 를 다시 본다."})
        self.assertEqual(self.errors(), [])

    def test_only_derived_fields_drifted_needs_no_history(self) -> None:
        self.drift(source="둘 다 고친 문단 [[beta]].", data="둘 다 고친 문단 [[beta]].")
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["derived"])
        self.assertEqual(report["undecided"], [])
        self.assertEqual(self.errors(), [])

    def test_links_follow_the_block_that_was_rederived(self) -> None:
        self.drift(data="링크가 빠진 새 말.", updated="2026-08-25")
        llmwiki.repair(self.ws)
        self.assertEqual([l for l in self.page()["links"] if l["kind"] == "wiki"], [])
        self.assertEqual(self.errors(), [])

    def test_a_change_nobody_recorded_is_only_listed(self) -> None:
        self.drift(data="기록 없이 바꾼 말.")
        before = self.dest.read_bytes()
        report = llmwiki.repair(self.ws)
        self.assertEqual(report["fixed"], [])
        self.assertEqual([u["block_id"] for u in report["undecided"]], [self.para])
        self.assertIn("변경 기록이 없다", report["undecided"][0]["reason"])
        self.assertEqual(self.dest.read_bytes(), before)

    def test_both_sides_rewritten_cannot_be_decided(self) -> None:
        self.drift(source="한쪽 새 글.", data="다른 쪽 새 글.", updated="2026-08-25")
        report = llmwiki.repair(self.ws)
        self.assertEqual(report["fixed"], [])
        self.assertIn("어느 쪽이 원본인지 알 수 없다", report["undecided"][0]["reason"])

    def test_the_ingest_snapshot_identifies_the_original_when_the_fingerprint_is_lost(self) -> None:
        page = self.page()
        page["blocks"][self.para]["fingerprint"] = "f" * 16            # 지문까지 망가뜨렸다
        page["blocks"][self.para]["data"]["text"] = "새 주장 [[beta]]."
        page["updated"] = "2026-08-25"
        self.save(page)
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["data"])

    def test_dry_run_prints_the_diff_and_writes_nothing(self) -> None:
        self.drift(data="미리 볼 말.", updated="2026-08-25")
        before = self.dest.read_bytes()
        proc = self.cli("repair", "--dry-run")
        self.assertIn("--- source_text", proc.stdout)
        self.assertIn("+++ data", proc.stdout)
        self.assertIn("-첫 문단은 [[beta]] 를 본다.", proc.stdout)
        self.assertIn("+미리 볼 말.", proc.stdout)
        self.assertIn("1 fixed, 0 undecided (dry-run", proc.stdout)
        self.assertEqual(self.dest.read_bytes(), before)
        self.assertEqual(llmwiki.read_log(self.ws)[-1]["action"], "ingest")

    def test_undecided_blocks_make_the_cli_exit_one(self) -> None:
        self.drift(data="기록 없이 바꾼 말.")
        proc = self.cli("repair", expect=1)
        self.assertIn("판정할 수 없는 1건", proc.stdout)
        self.assertIn(f"page:note#{self.para}", proc.stdout)
        report = json.loads(self.cli("repair", "--json", expect=1).stdout)
        self.assertEqual(len(report["undecided"]), 1)

    def test_a_clean_wiki_has_nothing_to_repair(self) -> None:
        self.assertEqual(llmwiki.repair(self.ws), {"dry_run": False, "fixed": [], "undecided": [], "pages": []})
        self.cli("repair")


class RepairLinksAndLegacyTest(WorkspaceCase):
    def test_missing_body_links_are_added_and_stray_ones_kept(self) -> None:
        page = make_page("alpha", "# Alpha\n\n[[beta]] 참조.\n")
        page["links"] = [{"target": "gamma", "kind": "wiki"}]
        self.write_pages([page, make_page("beta", "# Beta\n\n[[alpha]]\n"), make_page("gamma", "# Gamma\n")])
        report = llmwiki.repair(self.ws)
        links = llmwiki.find_page(self.ws, "alpha")["links"]
        self.assertEqual({l["target"] for l in links}, {"beta", "gamma"})
        self.assertEqual(len(report["undecided"]), 1)
        self.assertIn("wiki link [[gamma]] is not in the body", report["undecided"][0]["problems"][0])

    def test_hand_written_ids_and_fingerprints_become_canonical(self) -> None:
        page = make_page("mt", "# MT\n\n> ⚠️ 상충: 두 기준이 다르다 [[beta]].\n")
        conflict = page["block_order"][1]
        legacy = dict(page["blocks"].pop(conflict), id="block:mt:c1", fingerprint="demo-mt-c1")
        legacy["data"]["claims"] = ["전체판", "MVP"]
        page["blocks"]["block:mt:c1"] = legacy
        page["block_order"][1] = "block:mt:c1"
        page["links"][0]["block_id"] = "block:mt:c1"
        self.write_pages([page, make_page("beta", "# Beta\n\n[[mt]]\n")])
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["derived"])
        stored = llmwiki.find_page(self.ws, "mt")
        self.assertEqual(stored["block_order"][1], conflict)
        self.assertEqual(stored["blocks"][conflict]["data"]["claims"], ["전체판", "MVP"])
        self.assertEqual(stored["blocks"][conflict]["resolution"], {"status": "unresolved"})
        self.assertEqual(stored["block_aliases"], {"block:mt:c1": conflict})
        self.assertEqual(stored["links"][0]["block_id"], conflict)
        self.assertEqual(llmwiki.resolve(self.ws, "block:mt:c1")[1]["id"], conflict)
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_markdown_can_be_rebuilt_for_every_parsed_kind(self) -> None:
        body = ("# 제목\n\n문단.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n- 하나\n- 둘\n\n1. 첫\n2. 둘\n\n"
                "```py\nprint(1)\n```\n\n> 인용\n> 둘째 줄\n\n---\n")
        blocks, order = llmwiki.parse_blocks("page:x", body)
        for bid in order:
            block = blocks[bid]
            raw = llmwiki.render_data("page:x", block["kind"], block["data"])
            self.assertIsNotNone(raw, block["kind"])
            self.assertEqual(llmwiki.parse_one("page:x", raw)["data"], block["data"], block["kind"])


class RepairWitnessTest(RepairCase):
    """원본 증거는 정식 id 가 품은 지문이다 — fingerprint 필드는 validate 가 알려 준 값으로 손대기 쉽다."""

    def test_a_fingerprint_fixed_by_hand_does_not_let_old_data_win(self) -> None:
        new_id = llmwiki.edit(self.ws, "note", self.para, "edit 로 고친 문단.")["block_id"]   # snapshot 에 없는 block
        page = self.page()
        block = page["blocks"][new_id]
        block["source_text"] = "사람이 새로 쓴 최신 문단."
        block["fingerprint"] = llmwiki.block_fingerprint("paragraph", block["source_text"])   # validate 가 알려 준 값
        page["updated"] = "2026-08-25"
        self.save(page)
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["source_text"])
        body = [b["source_text"] for b in self.page()["blocks"].values()]
        self.assertIn("사람이 새로 쓴 최신 문단.", body)
        self.assertNotIn("edit 로 고친 문단.", body)


class RepairQuotedKindsTest(WorkspaceCase):
    BODY = "---\ntype: concept\ntags: [t]\n---\n# Pol\n\n> ⚠️ 상충: A 는 3, B 는 5.\n\n[[beta]]\n"

    def setUp(self) -> None:
        super().setUp()
        self.write_pages([make_page("beta", "# Beta\n\n[[pol]]\n")], name="beta.json")
        self.dest = self.root / llmwiki.ingest(self.ws, self.write_raw("pol.md", self.BODY))["dest"]
        page = json.loads(self.dest.read_text(encoding="utf-8"))
        self.conflict = page["block_order"][1]
        page["blocks"][self.conflict]["data"]["claims"] = ["A 기준", "B 기준"]
        self.dest.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")

    def drift(self, *, updated: str | None) -> None:
        page = json.loads(self.dest.read_text(encoding="utf-8"))
        page["blocks"][self.conflict]["source_text"] = "> ⚠️ 상충: A 는 4, B 는 5."
        if updated:
            page["updated"] = updated
        self.dest.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")

    def test_a_conflict_whose_text_drifted_is_not_passed_off_as_derived(self) -> None:
        self.drift(updated=None)
        report = llmwiki.repair(self.ws)
        self.assertEqual(report["fixed"], [])
        self.assertIn("변경 기록이 없다", report["undecided"][0]["reason"])

    def test_a_newer_conflict_text_keeps_the_claims(self) -> None:
        self.drift(updated="2026-08-25")
        report = llmwiki.repair(self.ws)
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["source_text"])
        page = llmwiki.find_page(self.ws, "pol")
        block = page["blocks"][report["fixed"][0]["new_id"]]
        self.assertEqual(block["data"], {"text": "⚠️ 상충: A 는 4, B 는 5.", "claims": ["A 기준", "B 기준"]})
        self.assertEqual(block["resolution"], {"status": "unresolved"})
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_a_quote_the_parser_now_reads_differently_is_reclassified(self) -> None:
        page = make_page("old", "# Old\n\n> ❓ 미결: 격주로 바꿀 것인가?\n> owner: release\n\n[[beta]]\n")
        bid = page["block_order"][1]
        quote = dict(page["blocks"].pop(bid))                     # 옛 파서는 이것을 quote 로 읽었다
        quote["kind"] = "quote"
        quote["data"] = {"text": quote["data"]["text"]}
        quote["fingerprint"] = llmwiki.block_fingerprint("quote", quote["source_text"])
        quote["id"] = llmwiki.block_id_of("page:old", quote["fingerprint"], 1)
        page["blocks"][quote["id"]] = quote
        page["block_order"][1] = quote["id"]
        self.write_pages([page], name="old.json")
        report = llmwiki.repair(self.ws)
        self.assertEqual([(f["block_id"], f["verdict"]) for f in report["fixed"]], [(quote["id"], "parser")])
        stored = llmwiki.find_page(self.ws, "old")
        new = stored["blocks"][stored["block_order"][1]]
        self.assertEqual((new["kind"], new["data"]["owner"]), ("open_question", "release"))
        self.assertEqual(stored["block_aliases"], {quote["id"]: new["id"]})
