"""비밀정보: validate error 와 build 정지 — 손으로 고친 JSON 이 viewer/public/data 로 새지 않는다."""
from __future__ import annotations

import json
import random

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


class PlaceholderTest(WorkspaceCase):
    """`<…>` 자리표시와 JSON key 이름은 비밀이 아니다 — 진짜 값은 여전히 걸린다."""

    NOT_SECRETS = ("password: <your password>", '"api_key": "<API_KEY>"', "token=<TOKEN>",
                   "--token <TOKEN>", "postgres://admin:<password>@db.local/app",
                   "-----BEGIN PRIVATE KEY-----\n<key>\n-----END PRIVATE KEY-----",
                   "Authorization: Bearer <token>",
                   '{"value_token": "eyJ0eXAi", "next_token": "abc", "max_token": 4096}',
                   # 수치·단위 낱말 조각을 가리키는 key
                   '{"value_token": "AMT", "unit_token": "AMTU"}',
                   "literal_token: STRING", "eof_token = EOF",
                   "page_token: abc123", "csrf_token=abc", "--next-token abc",
                   "token: value_token", "password: DB_PASSWORD", "secret: DB_SECRET", "api_key: settings.API_KEY",
                   # 산문 — 낱말 안의 `-token`·`-api-key` 는 CLI 플래그가 아니고, basic·bearer 뒤의 영단어는 토큰이 아니다
                   "access-token 을 발급한다", "x-api-key 헤더를 붙인다", "pre-auth 단계에서 막는다",
                   "basic authentication is used", "Basic examples follow", "Bearer instruments")
    SECRETS = {"password: hunter2": "password", "db_password=hunter2": "db_password",
               "GITHUB_TOKEN=ghx_live_123": "GITHUB_TOKEN", "refresh_token: abc": "refresh_token",
               "refreshToken: abc": "refreshToken", "id_token: eyJraWQ": "id_token", "token: abc123": "token",
               '{"password": "hunter2"}': "password", "client_secret = 'zzz'": "client_secret",
               "password: <x>hunter2": "password", "postgres://admin:pa55@db/app": "credential",
               "run (--token abc)": "token", "Basic dXNlcjpwYXNz": "credential",
               "Proxy-Authorization: Basic abcdefghij": "credential",
               "-----BEGIN PRIVATE KEY-----\n<key>\nMIIE\n-----END PRIVATE KEY-----": "credential"}

    def test_placeholders_and_key_names_are_not_secrets(self) -> None:
        for text in self.NOT_SECRETS:
            self.assertIsNone(llmwiki.T.secret_hit(text), text)
            self.assertEqual(llmwiki.redact(text), text, text)

    def test_real_values_are_still_caught(self) -> None:
        for text, label in self.SECRETS.items():
            self.assertEqual(llmwiki.T.secret_hit(text), label, text)
            self.assertIn(llmwiki.T.MASK, llmwiki.redact(text), text)

    def test_a_page_of_placeholders_ingests_validates_and_builds(self) -> None:
        body = "# 설정 예시\n\n" + "\n\n".join(f"`{line}`" if "\n" not in line else f"```\n{line}\n```"
                                              for line in self.NOT_SECRETS) + "\n"
        result = llmwiki.ingest(self.ws, self.write_raw("config.md", body))
        page = json.loads((self.root / result["dest"]).read_text(encoding="utf-8"))
        self.assertEqual(llmwiki.lint(self.ws)[0], [])
        self.assertEqual(len(page["blocks"]), len(self.NOT_SECRETS) + 1)

    def test_object_keys_name_fields_while_their_values_are_judged(self) -> None:
        page = make_page("alpha", "# Alpha\n\n본문.\n")
        page["blocks"][page["block_order"][1]]["data"].update(
            {"value_token": "eyJ0eXAi", "password": "hunter2", "api_key": "<API_KEY>"})
        where = [path for path, _label in llmwiki.T.secret_paths(page)]
        self.assertEqual(where, [f"/blocks/{page['block_order'][1]}/data/password"])

    def test_json_views_keep_their_shape_and_still_mask(self) -> None:
        page = make_page("alpha", '# Alpha\n\n설정은 password=abc" 로 끝난다.\n')
        page["blocks"][page["block_order"][1]]["data"]["password"] = "hunter2"
        view = llmwiki.T.redact_value(page)
        block = view["blocks"][page["block_order"][1]]
        self.assertEqual(block["data"]["password"], llmwiki.T.MASK)
        self.assertEqual(block["source_text"], '설정은 password=(접속 정보 생략)" 로 끝난다.')
        self.assertEqual(set(view), set(page))                 # key 는 손대지 않는다
        self.write_pages([page])
        out = ctx.get_page(self.root, "alpha", mode="page")     # 직렬화한 JSON 을 가리던 때는 여기서 깨졌다
        self.assertNotIn("hunter2", json.dumps(out, ensure_ascii=False))
        self.assertNotIn("abc\"", json.dumps(out, ensure_ascii=False))

    def test_the_inferred_summary_keeps_placeholders_and_identifiers(self) -> None:
        path = self.write_raw("note.md", "# 설정\n\n`token=<TOKEN>` 은 value_token 필드에 들어간다.\n")
        summary = llmwiki.page_from_markdown(self.ws, path)["summary"]
        self.assertEqual(summary, "token=<TOKEN> 은 value_token 필드에 들어간다.")
        self.assertIsNone(llmwiki.T.secret_hit(summary))


class CredentialWordSlugTest(WorkspaceCase):
    """slug 가 자격증명 낱말로 끝나는 page — `block:access-token:<16hex>:1` 은 비밀이 아니다."""

    def setUp(self) -> None:
        super().setUp()
        self.pages = [make_page(slug, f"# {slug}\n\n{slug} 를 다루는 문단. [[guide]]\n", tags=["t"])
                      for slug in ("password", "access-token", "api-key")]
        self.pages.append(make_page("guide", "# Guide\n\n[[password]] [[access-token]] [[api-key]]\n", tags=["t"]))
        self.write_pages(self.pages)

    def test_such_a_page_validates_and_builds(self) -> None:
        for page in self.pages:
            self.assertEqual(llmwiki.T.secret_paths(page), [], page["id"])
        self.assertEqual(llmwiki.build(self.ws)["pages"], 4)
        self.assertEqual(llmwiki.lint(self.ws)[0], [])

    def test_its_block_ids_survive_the_hook_views(self) -> None:
        page = self.pages[1]
        bid = page["block_order"][1]
        self.assertEqual(llmwiki.redact(bid), bid)
        out = ctx.get_page(self.root, "access-token", blocks=[bid])
        self.assertEqual([b["id"] for b in out["blocks"]], [bid])

    def test_a_real_value_after_the_same_word_is_still_caught(self) -> None:
        self.assertEqual(llmwiki.T.secret_hit("access-token: 5828861393e20db8"), "access-token")


class ReviewedRulesTest(WorkspaceCase):
    """검토에서 나온 모양들 — 옛 규칙이 잡던 것은 계속 잡고, 산문·코드·자리표시는 build 를 막지 않는다."""

    CAUGHT = {
        "--db-password hunter2": "db-password", "--admin-password 'P@ssw0rd!'": "admin-password",
        "az vm create --admin-password Hunter2Hunter2": "admin-password", "--auth-token abc123xyz": "auth-token",
        "--client-secret zzz9": "client-secret",
        "csrf_token=abc&username=bob&password=hunter2": "password",
        "https://x/cb?page_token=abc&access_token=ya29.a0AfH6SMBx": "access_token",
        "api_key: key-3ax6xnjp29jd6fds4gc373sgvjxteol0": "api_key",
        "NOTION_TOKEN=secret_9fJ2kLmNoPqRsTuVwXyZ0123456789": "NOTION_TOKEN",
        "password: Super-Secret-123": "password", "password: Password_2024": "password",
        "password: my.secret.pw": "password", "root_token: hvs.CAESIJlU9JMYEhOPYv4": "root_token",
        "telegram_token: 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw": "telegram_token",
        "sas_token=sv=2021-06-08&ss=b&sig=abc%2B": "sas_token", '"SecurityToken": "FQoGZXIvYXdz"': "SecurityToken",
        '"Authorization": "Bearer abcdefghijklmnopqr"': "credential", "password: (접속 정보 생략)hunter2": "password",
        "page_token: password=hunter2": "password", "password: password: hunter2": "password",
        "redis://:pa55@localhost:6379": "credential"}
    HARMLESS = (
        "requests.post(url, data={'username': user, 'password': pw})", "'token': 발급된 값을 헤더에 넣는다.",
        "password:\n  type: string", "interface User { password: string; }", "token: String!",
        "token = get_token()", "password = getpass.getpass()", "token: ${{ secrets.GITHUB_TOKEN }}",
        "password: {{ password }}", "password=$DB_PASSWORD", "password: %PASSWORD%", "secret: true",
        "password: null", "api_key: None", "password: required", "token: ...", "password: *****", "api_key: XXXXXXXX",
        "password: 사용자 비밀번호", "password: 8자 이상", "Password: 필수", "token: 토큰", "context window (token: 128k)",
        "`block:access-token:5828861393e20db8:1`", "block:password:5828861393e20db8:1의 내용",
        "(block:password:5828861393e20db8:1)", "[[access-token#block:access-token:5828861393e20db8:1]]",
        "basic read/write access", "Basic JavaScript knowledge", "a basic 2-column layout", "basic HTML/CSS 지식",
        "sk-learn-tutorials-overview-page", "--auth oauth", "`password`: 사용자 비밀번호")

    def test_old_catches_stay_caught(self) -> None:
        for text, label in self.CAUGHT.items():
            self.assertEqual(llmwiki.T.secret_hit(text), label, text)
            masked = llmwiki.redact(text)
            self.assertIn(llmwiki.T.MASK, masked, text)
            self.assertIsNone(llmwiki.T.secret_hit(masked), text)

    def test_prose_code_and_markers_are_harmless(self) -> None:
        for text in self.HARMLESS:
            self.assertIsNone(llmwiki.T.secret_hit(text), text)
            self.assertEqual(llmwiki.redact(text), text, text)

    def test_generated_summaries_do_not_invent_secrets(self) -> None:
        bodies = {"login.md": "# 로그인 API\n\n- `username`: 사용자 ID\n- `password`: 사용자 비밀번호\n",
                  "token.md": "# 토큰\n\n**Token**: 로그인하면 발급된다.\n",
                  "long.md": "# 설정\n\n" + "가" * 266 + " password: <your-password> 를 넣는다.\n"}
        for name, body in bodies.items():
            self.assertIsNone(llmwiki.T.secret_hit(body), name)
            page = json.loads((self.root / llmwiki.ingest(self.ws, self.write_raw(name, body))["dest"])
                              .read_text(encoding="utf-8"))
            self.assertIsNone(llmwiki.T.secret_hit(page["summary"]), name)

    def test_credential_keys_with_numbers_and_lists(self) -> None:
        value = {"password": 12345678, "api_key": ["sk_live_abcdef123456", "<KEY>"], "count": 3, "token": 7}
        self.assertEqual(llmwiki.T.secret_paths(value), [("/password", "password"), ("/api_key/0", "api_key")])
        self.assertEqual(llmwiki.T.redact_value(value), {"password": llmwiki.T.MASK,
                                                         "api_key": [llmwiki.T.MASK, "<KEY>"], "count": 3, "token": 7})

    def test_redact_is_idempotent_on_glued_and_nested_shapes(self) -> None:
        frags = ["password", "token", "api_key", "secret", "client_secret", "Authorization", "Bearer", ":", "=",
                 " ", '"', "'", "`", "<x>", "(", ")", "[", "-", "--", "_", "&", "://", "@", "hunter2", "ABCdef123",
                 "ghp_abcdefghijklmnopqrstu", "xoxb-abcdefghijkl", "AIzaabcdefghijklmnopqrstuv",
                 "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----", llmwiki.T.MASK, "(접속", "value_token",
                 "next_", "DB_PASSWORD", "0123456789abcdef:1", "block:", "가나다", "*"]
        rng = random.Random(20261020)
        for _ in range(5000):
            text = "".join(rng.choice(frags) for _ in range(rng.randint(1, 10)))
            once = llmwiki.redact(text)
            self.assertEqual(llmwiki.redact(once), once, text)
            self.assertIsNone(llmwiki.T.secret_hit(once), text)
