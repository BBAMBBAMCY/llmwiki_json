"""비밀정보: validate error 와 build 정지 — 손으로 고친 JSON 이 viewer/public/data 로 새지 않는다."""
from __future__ import annotations

import json

from tests.support import WorkspaceCase, llmwiki, make_page

SECRET = "sk-live-should-never-leak-0123456789"


class ValidateSecretTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.validator = llmwiki.SchemaValidator.load(self.ws)
        self.page = make_page("alpha", "# Alpha\n\n본문.\n")
        self.para = self.page["block_order"][1]

    def errors(self, page: dict) -> list[str]:
        return [e for e in llmwiki.validate_page(page, self.validator) if "보안정보" in e]

    def test_a_credential_written_into_a_block_is_an_error(self) -> None:
        block = self.page["blocks"][self.para]
        block["source_text"] = block["data"]["text"] = f"api_key: {SECRET}"
        errors = self.errors(self.page)
        self.assertEqual(errors, [
            f"page:alpha: 보안정보로 보이는 'api_key' 값이 /blocks/{self.para}/data/text 에 있다 — "
            "'(접속 정보 생략)'으로 치환한다",
            f"page:alpha: 보안정보로 보이는 'api_key' 값이 /blocks/{self.para}/source_text 에 있다 — "
            "'(접속 정보 생략)'으로 치환한다"])
        self.assertNotIn(SECRET, " ".join(errors))           # 오류 문장에 값 자체는 없다

    def test_every_field_is_scanned(self) -> None:
        self.page["summary"] = f"Bearer {SECRET}"
        self.page["history"].append({"at": "2026-08-19", "action": "note", "actor": "agent",
                                     "note": f"token={SECRET}"})
        self.page["source_snapshot"] = {"format": "markdown", "text": f"password: {SECRET}", "sha256": "x"}
        where = {e.split(" 값이 ")[1].split(" 에 있다")[0] for e in self.errors(self.page)}
        self.assertEqual(where, {"/summary", "/history/1/note", "/source_snapshot/text"})

    def test_a_clean_page_has_no_secret_error(self) -> None:
        self.assertEqual(self.errors(self.page), [])
        self.assertEqual(self.errors(make_page("schema", "# 스키마\n\npassword 컬럼이 있다. (접속 정보 생략)\n")), [])


class BuildRefusesSecretsTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.pages = [make_page("alpha", "# Alpha\n\n[[beta]] 본문.\n"), make_page("beta", "# Beta\n\n[[alpha]]\n")]
        self.shard = self.write_pages(self.pages)
        llmwiki.build(self.ws)
        self.viewer = self.root / "viewer" / "public" / "data"
        self.before = {p: p.read_bytes() for p in sorted(self.viewer.rglob("*")) if p.is_file()}

    def leak(self) -> None:
        """손으로 JSON 에 비밀을 넣었다 — ingest 의 원문 검사를 거치지 않은 길."""
        bid = self.pages[0]["block_order"][1]
        self.pages[0]["blocks"][bid]["source_text"] = f"[[beta]] password: {SECRET}"
        self.pages[0]["blocks"][bid]["data"]["text"] = f"[[beta]] password: {SECRET}"
        self.write_pages(self.pages)

    def assert_viewer_untouched(self) -> None:
        after = {p: p.read_bytes() for p in sorted(self.viewer.rglob("*")) if p.is_file()}
        self.assertEqual(after, self.before)
        self.assertFalse(any(SECRET.encode() in data for data in after.values()))

    def test_a_full_build_stops(self) -> None:
        self.leak()
        with self.assertRaisesRegex(llmwiki.WikiError, "보안정보로 보이는 'password'"):
            llmwiki.build(self.ws, full=True)
        self.assert_viewer_untouched()

    def test_an_incremental_build_stops(self) -> None:
        self.leak()
        with self.assertRaisesRegex(llmwiki.WikiError, "보안정보"):
            llmwiki.build(self.ws, changed=[self.ws.rel(self.shard)])
        self.assert_viewer_untouched()

    def test_the_cli_exits_two_and_validate_exits_one(self) -> None:
        self.leak()
        proc = self.cli("build", expect=2)
        self.assertIn("보안정보", proc.stderr)
        self.assertNotIn(SECRET, proc.stderr)
        self.assertIn("보안정보", self.cli("validate", expect=1).stdout)
        self.assert_viewer_untouched()

    def test_the_build_resumes_once_the_value_is_masked(self) -> None:
        self.leak()
        bid = self.pages[0]["block_order"][1]
        self.pages[0]["blocks"][bid]["source_text"] = "[[beta]] password 는 (접속 정보 생략)"
        self.pages[0]["blocks"][bid]["data"]["text"] = "[[beta]] password 는 (접속 정보 생략)"
        self.write_pages(self.pages)
        self.assertEqual(llmwiki.build(self.ws)["pages"], 2)
        shard = json.loads((self.viewer / "pages" / "alpha.json").read_text(encoding="utf-8"))
        self.assertIn("(접속 정보 생략)", shard["blocks"][bid]["source_text"])
