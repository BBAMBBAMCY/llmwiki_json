"""비밀정보: validate error 와 build 정지 — 손으로 고친 JSON 이 viewer/public/data 로 새지 않는다."""
from __future__ import annotations

import json

from tests.support import WorkspaceCase, llmwiki, llmwiki_context as ctx, make_page

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


SHAPES = ("password: hunter2", "--password hunter2", "--token=abc123", "Authorization: Bearer abcdefghijklmnop",
          "postgres://user:pa55@db.local/app", "sk-abcdefghijklmnopqrstuvwxyz0123", '"api_key": "abc123"',
          "client_secret = zzz", "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
          "token=x; password=y, secret: z")


class RedactIdempotencyTest(WorkspaceCase):
    def test_redacting_again_changes_nothing(self) -> None:
        for shape in SHAPES:
            once = llmwiki.redact(shape)
            self.assertIn(llmwiki.T.MASK, once, shape)
            self.assertEqual(llmwiki.redact(once), once, shape)
            self.assertEqual(llmwiki.redact(llmwiki.redact(once)), once, shape)
            self.assertNotIn("정보 생략) 정보 생략)", llmwiki.redact(llmwiki.redact(once)), shape)

    def test_masked_text_is_no_longer_a_secret(self) -> None:
        for shape in SHAPES:
            self.assertIsNotNone(llmwiki.T.secret_hit(shape), shape)
            self.assertIsNone(llmwiki.T.secret_hit(llmwiki.redact(shape)), shape)

    def test_only_the_value_is_replaced(self) -> None:
        self.assertEqual(llmwiki.redact('{"api_key": "abc123", "n": 1}'),
                         '{"api_key": "(접속 정보 생략)", "n": 1}')
        self.assertEqual(llmwiki.redact("password=hunter2;"), "password=(접속 정보 생략);")

    def test_a_masked_page_validates_and_builds(self) -> None:
        page = make_page("alpha", "# Alpha\n\n" + llmwiki.redact("password: hunter2, token=abc") + "\n")
        self.assertEqual(llmwiki.validate_page(page, llmwiki.SchemaValidator.load(self.ws)), [])
        self.write_pages([page])
        self.assertEqual(llmwiki.build(self.ws)["pages"], 1)

    def test_the_hook_output_carries_one_mask_per_secret(self) -> None:
        leaky = make_page("키-메모", "# 키 메모\n\napi_key: sk-live-should-never-leak 키 회전은 금요일이다.\n",
                          summary="키 회전.")
        self.write_pages([leaky])                     # build 없이 — 메모리 색인이 저장·렌더에서 거듭 redact 한다
        text, _result, _pages = ctx.build_context(self.root, "키 메모 회전 금요일")
        self.assertIn("api_key: (접속 정보 생략) 키 회전", text)
        self.assertNotIn("정보 생략) 정보 생략)", text)
