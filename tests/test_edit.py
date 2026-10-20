"""`edit`: one block rewritten from a markdown fragment, everything derived from it in step."""
from __future__ import annotations

import json

from tests.support import WorkspaceCase, llmwiki, llmwiki_context as ctx, make_page

BODY = "# Alpha\n\n첫 문단은 [[beta]] 를 본다.\n\n둘째 문단.\n"


class EditCase(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.page = make_page("alpha", BODY, projects=["beta"], tags=["t"])
        self.write_pages([self.page,
                          make_page("beta", "# Beta\n\n[[alpha]]\n", projects=["beta"], tags=["t"]),
                          make_page("gamma", "# Gamma\n\n[[alpha]]\n", projects=["beta"], tags=["t"])])
        llmwiki.build(self.ws)
        self.first = self.page["block_order"][1]

    def stored(self, slug: str = "alpha") -> dict:
        return llmwiki.find_page(self.ws, slug)


class EditTest(EditCase):
    def test_every_derived_field_follows_the_fragment(self) -> None:
        result = llmwiki.edit(self.ws, "alpha", self.first, "첫 문단은 이제 [[gamma]] 를 본다.")
        page = self.stored()
        new_id = result["block_id"]
        block = page["blocks"][new_id]
        self.assertEqual(block["source_text"], "첫 문단은 이제 [[gamma]] 를 본다.")
        self.assertEqual(block["data"], {"text": "첫 문단은 이제 [[gamma]] 를 본다."})
        self.assertEqual(block["fingerprint"], llmwiki.block_fingerprint("paragraph", block["source_text"]))
        self.assertEqual(block["refs"], ["gamma"])
        self.assertEqual(new_id, f"block:alpha:{block['fingerprint']}:1")
        wiki = [(l["target"], l["block_id"]) for l in page["links"] if l["kind"] == "wiki"]
        self.assertEqual(wiki, [("gamma", new_id)])
        self.assertEqual(result.get("dropped_links"), ["beta"])

    def test_the_old_id_stays_an_alias_and_get_still_finds_it(self) -> None:
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "새 문단.")["block_id"]
        page = self.stored()
        self.assertNotIn(self.first, page["blocks"])
        self.assertEqual(page["block_aliases"], {self.first: new_id})
        self.assertEqual(page["block_order"][1], new_id)       # 같은 자리
        _, block = llmwiki.resolve(self.ws, f"alpha#{self.first}")
        self.assertEqual(block["id"], new_id)
        _, block = llmwiki.resolve(self.ws, self.first)          # page 없이 옛 id 만
        self.assertEqual(block["id"], new_id)
        got = json.loads(self.cli("get", "alpha", "--block", self.first).stdout)
        self.assertEqual(got["source_text"], "새 문단.")

    def test_unchanged_content_keeps_the_id(self) -> None:
        result = llmwiki.edit(self.ws, "alpha", self.first, "첫 문단은 [[beta]] 를 본다.")
        self.assertEqual(result["block_id"], self.first)
        self.assertFalse(result["changed"])
        self.assertNotIn("block_aliases", self.stored())

    def test_a_chain_of_edits_keeps_the_alias_table_flat(self) -> None:
        b = llmwiki.edit(self.ws, "alpha", self.first, "둘째 판.")["block_id"]
        c = llmwiki.edit(self.ws, "alpha", b, "셋째 판.")["block_id"]
        self.assertEqual(self.stored()["block_aliases"], dict(sorted({self.first: c, b: c}.items())))

    def test_editing_back_revives_the_original_id(self) -> None:
        b = llmwiki.edit(self.ws, "alpha", self.first, "잠깐 바꿨다.")["block_id"]
        back = llmwiki.edit(self.ws, "alpha", b, "첫 문단은 [[beta]] 를 본다.")["block_id"]
        self.assertEqual(back, self.first)
        self.assertEqual(self.stored()["block_aliases"], {b: self.first})

    def test_an_old_alias_is_accepted_as_the_target(self) -> None:
        llmwiki.edit(self.ws, "alpha", self.first, "둘째 판.")
        c = llmwiki.edit(self.ws, "alpha", self.first, "셋째 판.")["block_id"]   # 옛 id 로 다시 지목
        self.assertEqual(self.stored()["blocks"][c]["source_text"], "셋째 판.")

    def test_a_duplicate_of_another_block_gets_the_next_occurrence(self) -> None:
        second = self.page["block_order"][2]
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "둘째 문단.")["block_id"]
        self.assertTrue(second.endswith(":1"))
        self.assertEqual(new_id, second[:-1] + "2")

    def test_curated_links_follow_the_block_they_anchor_to(self) -> None:
        page = self.stored()
        page["links"].append({"target": "gamma", "label": "gamma", "kind": "supersedes",
                              "block_id": self.first})
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "대체 근거 문단.")["block_id"]
        sup = [l for l in self.stored()["links"] if l["kind"] == "supersedes"]
        self.assertEqual(sup, [{"target": "gamma", "label": "gamma", "kind": "supersedes",
                                "block_id": new_id}])

    def test_the_kind_can_change_and_resolution_survives_a_rewrite(self) -> None:
        listed = llmwiki.edit(self.ws, "alpha", self.first, "- 하나\n- 둘")["block_id"]
        block = self.stored()["blocks"][listed]
        self.assertEqual(block["kind"], "list")
        self.assertEqual(block["data"]["items"], ["하나", "둘"])
        conflict = llmwiki.edit(self.ws, "alpha", listed, "> ⚠️ 상충: 두 문서가 다르다.")["block_id"]
        page = self.stored()
        page["blocks"][conflict]["resolution"] = {"status": "resolved", "current": "A"}
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])
        again = llmwiki.edit(self.ws, "alpha", conflict, "> ⚠️ 상충: 두 문서의 수치가 다르다.")["block_id"]
        self.assertEqual(self.stored()["blocks"][again]["resolution"], {"status": "resolved", "current": "A"})

    def test_history_updated_log_and_index_follow(self) -> None:
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "고유어휘쿼카 문단.")["block_id"]
        page = self.stored()
        self.assertEqual(page["history"][-1]["action"], "edited")
        self.assertIn(new_id, page["history"][-1]["note"])
        self.assertEqual(llmwiki.read_log(self.ws)[-1]["action"], "edit")
        hits = llmwiki.query(self.ws, "고유어휘쿼카")
        self.assertEqual(hits[0]["id"], "page:alpha")
        self.assertIn(new_id, hits[0]["blocks"])
        self.assertEqual(llmwiki.stale_index(self.ws), [])

    def test_a_fragment_must_be_exactly_one_block(self) -> None:
        for fragment in ("", "한 문단.\n\n두 문단."):
            with self.assertRaisesRegex(llmwiki.WikiError, "block 하나"):
                llmwiki.edit(self.ws, "alpha", self.first, fragment)

    def test_credentials_are_refused(self) -> None:
        with self.assertRaisesRegex(llmwiki.WikiError, "보안정보"):
            llmwiki.edit(self.ws, "alpha", self.first, "password: hunter2")
        self.assertEqual(self.stored(), self.page)

    def test_dry_run_writes_nothing(self) -> None:
        before = self.stored()
        result = llmwiki.edit(self.ws, "alpha", self.first, "미리 보기.", dry_run=True)
        self.assertEqual(result["block"]["source_text"], "미리 보기.")
        self.assertEqual(self.stored(), before)
        self.assertEqual(llmwiki.read_log(self.ws), [])

    def test_unknown_block_is_reported(self) -> None:
        with self.assertRaisesRegex(llmwiki.WikiError, "block not found"):
            llmwiki.edit(self.ws, "alpha", "block:alpha:nope", "x")

    def test_a_shard_holding_several_pages_keeps_the_others(self) -> None:
        llmwiki.edit(self.ws, "alpha", self.first, "바뀐 문단.")
        shard = json.loads(next((self.root / "wiki").rglob("pages.json")).read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in shard], ["page:alpha", "page:beta", "page:gamma"])


class EditCliTest(EditCase):
    def test_text_file_and_stdin_feed_the_same_fragment(self) -> None:
        out = json.loads(self.cli("edit", "alpha", "--block", self.first, "--text", "텍스트로.").stdout)
        fragment = self.root / "fragment.md"
        fragment.write_text("파일로.\n", encoding="utf-8")
        out = json.loads(self.cli("edit", "alpha", "--block", out["block_id"], "--file", str(fragment)).stdout)
        proc = self.cli("edit", "alpha", "--block", out["block_id"], stdin="표준 입력으로.\n")
        new_id = json.loads(proc.stdout)["block_id"]
        self.assertEqual(self.stored()["blocks"][new_id]["source_text"], "표준 입력으로.")

    def test_errors_exit_two(self) -> None:
        self.cli("edit", "alpha", "--block", self.first, "--text", "a\n\nb", expect=2)


class AliasContextTest(EditCase):
    def test_llmwiki_get_resolves_an_old_id(self) -> None:
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "맥락용 새 문단.")["block_id"]
        out = ctx.get_page(self.root, "alpha", blocks=[self.first])
        self.assertEqual([b["id"] for b in out["blocks"]], [new_id])
        self.assertNotIn("missing", out)

    def test_alias_table_is_checked(self) -> None:
        page = self.stored()
        live = page["block_order"][1]
        page["block_aliases"] = {live: live, "block:alpha:old:1": "block:alpha:ghost:1"}
        errors = " ".join(llmwiki.validate_page(page, llmwiki.SchemaValidator.load(self.ws)))
        self.assertIn("is a live block id", errors)
        self.assertIn("points at unknown block", errors)
