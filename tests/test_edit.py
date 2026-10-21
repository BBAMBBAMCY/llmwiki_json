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

    def test_an_empty_fragment_deletes_nothing(self) -> None:
        for fragment in ("", "\n\n"):
            with self.assertRaisesRegex(llmwiki.WikiError, "지우지 않는다"):
                llmwiki.edit(self.ws, "alpha", self.first, fragment)
        self.assertEqual(self.stored(), self.page)

    def test_a_block_appended_after_keeps_the_old_id_where_the_content_stayed(self) -> None:
        result = llmwiki.edit(self.ws, "alpha", self.first, "첫 문단은 [[beta]] 를 본다.\n\n- 덧붙인 항목")
        page = self.stored()
        self.assertEqual(result["block_id"], self.first)
        [added] = result["inserted"]
        self.assertEqual(page["block_order"][1:3], [self.first, added])
        self.assertEqual(page["blocks"][added]["kind"], "list")
        self.assertNotIn("block_aliases", page)
        self.assertEqual(llmwiki.consistency_errors(page), [])

    def test_a_split_block_hands_the_old_id_to_its_first_part(self) -> None:
        result = llmwiki.edit(self.ws, "alpha", self.first, "앞 절반 [[beta]].\n\n뒤 절반.")
        page = self.stored()
        first, second = page["block_order"][1:3]
        self.assertEqual((result["block_id"], result["inserted"]), (first, [second]))
        self.assertEqual(page["block_aliases"], {self.first: first})
        self.assertEqual(llmwiki.resolve(self.ws, self.first)[1]["source_text"], "앞 절반 [[beta]].")

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
        self.cli("edit", "alpha", "--block", self.first, "--text", "", expect=2)
        self.cli("edit", "alpha", "--block", "block:alpha:nope", "--text", "x", expect=2)


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


class EditKeepsWhatTheBodyDoesNotSayTest(EditCase):
    def setUp(self) -> None:
        super().setUp()
        new_id = llmwiki.edit(self.ws, "alpha", self.first, "> ⚠️ 상충: 두 문서의 수치가 다르다.")["block_id"]
        page = self.stored()
        page["blocks"][new_id]["resolution"] = {"status": "resolved", "current": "B"}
        page["blocks"][new_id]["data"]["claims"] = ["A 기준", "B 기준"]
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])
        self.conflict = new_id

    def test_claims_and_judgement_survive_a_rewrite(self) -> None:
        new_id = llmwiki.edit(self.ws, "alpha", self.conflict, "> ⚠️ 상충: 두 문서의 수치가 다르다(2판).")["block_id"]
        block = self.stored()["blocks"][new_id]
        self.assertEqual(block["data"]["claims"], ["A 기준", "B 기준"])
        self.assertEqual(block["resolution"], {"status": "resolved", "current": "B"})

    def test_the_old_id_goes_to_the_block_of_the_same_kind(self) -> None:
        result = llmwiki.edit(self.ws, "alpha", self.conflict, "배경 문단.\n\n> ⚠️ 상충: 두 문서의 수치가 다르다(2판).")
        page = self.stored()
        heir = page["blocks"][result["block_id"]]
        self.assertEqual(heir["kind"], "conflict")
        self.assertEqual(heir["resolution"], {"status": "resolved", "current": "B"})
        self.assertEqual(llmwiki.resolve(self.ws, self.conflict)[1]["kind"], "conflict")
        self.assertEqual([page["blocks"][b]["kind"] for b in result["inserted"]], ["paragraph"])


class ResolutionCurrentFollowsTest(EditCase):
    """상충 판정이 현행 block 을 id 로 가리키면(`resolution.current`), 그 block 을 고쳐도 가리킴이 끊기지 않는다."""

    def setUp(self) -> None:
        super().setUp()
        self.conflict = llmwiki.edit(self.ws, "alpha", self.first, "> ⚠️ 상충: 두 문서의 수치가 다르다.")["block_id"]
        self.current = self.stored()["block_order"][2]                     # 둘째 문단이 현행 주장이다
        page = self.stored()
        page["blocks"][self.conflict]["resolution"] = {"status": "resolved", "current": self.current}
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])

    def test_edit_repoints_the_judgement_at_the_new_id(self) -> None:
        new_id = llmwiki.edit(self.ws, "alpha", self.current, "둘째 문단(고친 판).")["block_id"]
        self.assertNotEqual(new_id, self.current)
        page = self.stored()
        self.assertEqual(page["blocks"][self.conflict]["resolution"]["current"], new_id)
        self.assertEqual(llmwiki.validate_page(page, llmwiki.SchemaValidator.load(self.ws)), [])

    def test_readers_follow_an_old_pointer(self) -> None:
        """예전에 고쳐져 가리킴이 옛 id 로 남은 정본도 get · render --section · llmwiki_get 은 지금 id 를 보인다."""
        new_id = llmwiki.edit(self.ws, "alpha", self.current, "둘째 문단(고친 판).")["block_id"]
        page = self.stored()
        page["blocks"][self.conflict]["resolution"]["current"] = self.current   # 옛 id 로 되돌린 정본
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])
        self.assertEqual(llmwiki.resolve(self.ws, f"alpha#{self.conflict}")[1]["resolution"]["current"], new_id)
        heading = self.stored()["block_order"][0]
        shown = [b for b in llmwiki.section(self.stored(), heading) if b["id"] == self.conflict]
        self.assertEqual(shown[0]["resolution"]["current"], new_id)
        out = ctx.get_page(self.root, "alpha", blocks=[self.conflict], mode="blocks")
        self.assertEqual(out["blocks"][0]["resolution"]["current"], new_id)
        self.assertEqual(self.stored()["blocks"][self.conflict]["resolution"]["current"], self.current)  # 정본은 그대로


class EditGuardsTest(EditCase):
    def test_an_unclosed_fence_is_refused(self) -> None:
        with self.assertRaisesRegex(llmwiki.WikiError, "닫히지 않은"):
            llmwiki.edit(self.ws, "alpha", self.first, "```sh\nmake build")
        llmwiki.edit(self.ws, "alpha", self.first, "```sh\nmake build\n```")

    def test_the_title_follows_its_h1(self) -> None:
        heading = self.page["block_order"][0]
        result = llmwiki.edit(self.ws, "alpha", heading, "# Alpha Platform")
        self.assertEqual((result["title"], self.stored()["title"]), ("Alpha Platform", "Alpha Platform"))
        self.assertEqual(llmwiki.find_page(self.ws, "Alpha Platform")["id"], "page:alpha")

    def test_a_title_set_apart_from_the_h1_stays(self) -> None:
        page = self.stored()
        page["title"] = "알파 별칭"
        self.write_pages([page, self.stored("beta"), self.stored("gamma")])
        result = llmwiki.edit(self.ws, "alpha", page["block_order"][0], "# Alpha Platform")
        self.assertNotIn("title", result)
        self.assertEqual(self.stored()["title"], "알파 별칭")
