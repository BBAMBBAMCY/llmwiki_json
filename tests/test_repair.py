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
        self.assertEqual(llmwiki.repair(self.ws), {"dry_run": False, "fixed": [], "undecided": [], "pages": [],
                                                   "pending_file": ""})
        self.cli("repair")
        self.cli("repair", "--apply")

    def test_undecided_blocks_are_listed_in_repair_pending(self) -> None:
        pending = self.root / ".llmwiki" / "repair-pending.json"
        self.drift(data="기록 없이 바꾼 말.")
        before = self.dest.read_bytes()
        proc = self.cli("repair", "--dry-run", expect=1)                    # dry-run 도 목록은 남긴다 (보고서다)
        self.assertIn(".llmwiki/repair-pending.json", proc.stdout)
        listed = json.loads(pending.read_text(encoding="utf-8"))
        self.assertEqual(listed["count"], 1)
        self.assertEqual((listed["items"][0]["page_id"], listed["items"][0]["block_id"]), ("page:note", self.para))
        self.assertIn("변경 기록이 없다", listed["items"][0]["reason"])
        self.assertTrue(listed["items"][0]["diff"])
        self.assertEqual(self.dest.read_bytes(), before)                    # 정본은 그대로
        # 판정 불가가 사라지면 지난 목록도 지운다
        self.drift(data="첫 문단은 [[beta]] 를 본다.")
        self.cli("repair", "--apply")
        self.assertFalse(pending.exists())

    def test_dry_run_and_apply_do_not_mix(self) -> None:
        self.cli("repair", "--dry-run", "--apply", expect=2)


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
        body = ("# 제목\n\n문단.\n\n| a | b |\n| --- | --- |\n| 1 | str \\| None |\n\n- 하나\n- 둘\n\n1. 첫\n2. 둘\n\n"
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


class RepairShapeTest(WorkspaceCase):
    """예전 지침대로 손으로 쓴 JSON — 같은 내용을 다른 모양으로 적었으면 판정 없이 source_text 에서 다시 만든다."""

    def hand_written(self, body: str, name: str, **data: object) -> dict:
        """`body` 의 둘째 block 을 손으로 쓴 것처럼 — 이름 붙인 id, 맞지 않는 지문, 모양이 다른 data."""
        page = make_page("hw", body)
        bid = page["block_order"][1]
        block = dict(page["blocks"].pop(bid), id=f"block:hw:{name}", fingerprint=f"hand-{name}", data=dict(data))
        page["blocks"][block["id"]] = block
        page["block_order"][1] = block["id"]
        for link in page["links"]:
            if link.get("block_id") == bid:
                link["block_id"] = block["id"]
        self.write_pages([page])
        return block

    def repaired(self) -> tuple[dict, dict]:
        report = llmwiki.repair(self.ws)
        page = llmwiki.find_page(self.ws, "hw")
        return report, page["blocks"][page["block_order"][1]]

    def test_a_table_written_without_its_rule_row_is_rederived(self) -> None:
        old = self.hand_written("# HW\n\n| 이름 | 수 |\n| :--- | ---: |\n| a | 1 |\n", "tbl",
                                rows=[["이름", "수"], ["a", "1"]])
        report, block = self.repaired()
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["shape"])
        self.assertEqual(block["source_text"], old["source_text"])
        self.assertEqual(block["data"], llmwiki.parse_one("page:hw", old["source_text"])["data"])
        self.assertEqual(llmwiki.find_page(self.ws, "hw")["block_aliases"], {old["id"]: block["id"]})
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_a_list_written_as_items_only_is_rederived(self) -> None:
        self.hand_written("# HW\n\n1. 하나\n2. 둘\n", "lst", items=["하나", "둘"])
        report, block = self.repaired()
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["shape"])
        self.assertEqual(block["data"], {"ordered": True, "items": ["하나", "둘"], "text": "1. 하나\n2. 둘"})
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_a_different_cell_or_item_is_still_undecided(self) -> None:
        for body, data in (("# HW\n\n| 이름 | 수 |\n| --- | --- |\n| a | 1 |\n", {"rows": [["이름", "수"], ["a", "2"]]}),
                           ("# HW\n\n| 이름 | 수 |\n| --- | --- |\n| a | 1 |\n", {"rows": [["이름", "수"], ["a", "1"]],
                                                                               "header": ["이름", "수"]}),
                           ("# HW\n\n- 하나\n- 둘\n", {"items": ["하나", "셋"]}),
                           ("# HW\n\n- 하나\n- 둘\n", {"items": ["하나", "둘"], "ordered": True})):
            with self.subTest(data=data):
                self.hand_written(body, "x", **data)
                report, _block = self.repaired()
                self.assertEqual(report["fixed"], [])
                self.assertEqual([u["verdict"] for u in report["undecided"]], ["undecided"])

    def test_a_conflict_written_without_its_marker_gets_one(self) -> None:
        text = "두 문서의 수치가 다르다 — [[beta]] 는 12, 이 문서는 15."
        old = self.hand_written(f"# HW\n\n{text}\n", "cf", text=text, claims=["12", "15"])
        old["kind"], old["resolution"] = "conflict", {"status": "resolved", "current": "15"}
        page = llmwiki.find_page(self.ws, "hw")
        page["blocks"][old["id"]] = old
        self.write_pages([page, make_page("beta", "# Beta\n\n[[hw]]\n")])
        report, block = self.repaired()
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["marker"])
        self.assertEqual(block["source_text"], f"> ⚠️ 상충: {text}")
        self.assertEqual((block["kind"], block["data"]), ("conflict", {"text": f"⚠️ 상충: {text}", "claims": ["12", "15"]}))
        self.assertEqual(block["resolution"], {"status": "resolved", "current": "15"})
        self.assertEqual(block["refs"], ["beta"])
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_a_current_block_written_as_a_bare_quote_gets_its_marker(self) -> None:
        old = self.hand_written("# HW\n\n> 배포는 목요일이다.\n> 금요일은 하지 않는다.\n", "cur",
                                text="배포는 목요일이다.\n금요일은 하지 않는다.")
        old["kind"] = "current"
        page = llmwiki.find_page(self.ws, "hw")
        page["blocks"][old["id"]] = old
        self.write_pages([page])
        report, block = self.repaired()
        self.assertEqual([f["verdict"] for f in report["fixed"]], ["marker"])
        self.assertEqual(block["source_text"], "> ✅ 현행: 배포는 목요일이다.\n> 금요일은 하지 않는다.")
        self.assertEqual(block["kind"], "current")

    def test_a_bare_conflict_whose_text_differs_is_still_undecided(self) -> None:
        old = self.hand_written("# HW\n\n두 문서의 수치가 다르다.\n", "cf", text="두 문서의 수치가 같다.")
        old["kind"] = "conflict"
        page = llmwiki.find_page(self.ws, "hw")
        page["blocks"][old["id"]] = old
        self.write_pages([page])
        report, _block = self.repaired()
        self.assertEqual((report["fixed"], [u["verdict"] for u in report["undecided"]]), ([], ["undecided"]))


class RepairLegacyTableTest(WorkspaceCase):
    """예전 파서는 칸을 `|` 로 먼저 나눠 `\\|` 가 칸을 갈랐다 — 그렇게 읽힌 data 는 다시 읽는다."""

    def test_a_table_read_by_the_old_parser_is_reread(self) -> None:
        page = make_page("tbl", "# T\n\n| 칸 | 값 |\n| --- | --- |\n| 타입 | str \\| None |\n\n[[beta]]\n")
        bid = page["block_order"][1]
        block = page["blocks"][bid]
        self.assertEqual(block["data"]["rows"][2], ["타입", "str | None"])
        block["data"] = {"rows": llmwiki.legacy_table_rows(block["source_text"])}   # 예전 파서가 쓴 그대로
        self.assertEqual(block["data"]["rows"][2], ["타입", "str \\", "None"])
        self.write_pages([page, make_page("beta", "# Beta\n\n[[tbl]]\n")])
        self.assertIn(f"page:tbl:{bid}: data != parse(source_text)", llmwiki.lint(self.ws)[0])
        report = llmwiki.repair(self.ws)
        self.assertEqual([(f["block_id"], f["verdict"]) for f in report["fixed"]], [(bid, "parser")])
        stored = llmwiki.find_page(self.ws, "tbl")
        self.assertEqual(stored["block_order"][1], bid)                       # 본문이 같으니 id 도 같다
        self.assertEqual(stored["blocks"][bid]["data"]["rows"][2], ["타입", "str | None"])
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_a_hand_written_table_that_differs_is_still_undecided(self) -> None:
        page = make_page("tbl", "# T\n\n| 칸 | 값 |\n| --- | --- |\n| 타입 | str \\| None |\n")
        bid = page["block_order"][1]
        page["blocks"][bid]["data"] = {"rows": [["칸", "값"], ["---", "---"], ["타입", "int"]]}
        self.write_pages([page])
        self.assertEqual([u["verdict"] for u in llmwiki.repair(self.ws)["undecided"]], ["undecided"])
