"""CLI, search index, and hook share one text identity.

`link_key` / `implied_links` / `redact` used to live in two copies and had
already drifted. These tests pin the shared module: the same function object,
the same keys, the same edges, the same masks.
"""
from __future__ import annotations

import unittest

from tests.support import llmwiki, llmwiki_context as ctx, make_page

IDX = ctx.IDX


class SharedIdentityTest(unittest.TestCase):
    def test_cli_and_index_are_the_same_functions(self) -> None:
        self.assertIs(llmwiki.link_key, IDX.link_key)
        self.assertIs(llmwiki.implied_links, IDX.implied_links)
        self.assertIs(llmwiki.redact, IDX.redact)

    def test_page_prefix_is_stripped_after_casefold(self) -> None:
        self.assertEqual(llmwiki.link_key("Page:Alpha Platform"), "alpha-platform")
        self.assertEqual(llmwiki.link_key("page:alpha-platform"), "alpha-platform")
        self.assertEqual(llmwiki.link_key("alpha_platform"), "alpha-platform")
        self.assertEqual(llmwiki.link_key("[[Alpha Platform]]".strip("[]")), "alpha-platform")


class ImpliedLinksTest(unittest.TestCase):
    def test_bare_and_prefixed_sources_become_edges(self) -> None:
        page = make_page("topic", "# Topic\n\nSee [[Page:Alpha Platform]].\n",
                         sources=["foo", "page:Bar Topic", "user:2026-01-01", "raw:notes.md"])
        page["supersedes"] = ["old-page"]
        page["related"] = ["cousin"]
        got = {(link["target"], link["kind"]) for link in llmwiki.implied_links(page)}
        self.assertIn(("Page:Alpha Platform", "wiki"), got)
        self.assertIn(("foo", "source"), got)
        self.assertIn(("Bar Topic", "source"), got)
        self.assertIn(("old-page", "supersedes"), got)
        self.assertIn(("cousin", "related"), got)
        self.assertTrue(all(kind != "source" or target not in {"user:2026-01-01", "notes.md"}
                            for target, kind in got))
        self.assertEqual(llmwiki.implied_links(page), IDX.implied_links(page))

    def test_page_prefix_wikilink_resolves_like_the_slug(self) -> None:
        pages = [
            make_page("alpha-platform", "# Alpha Platform\n\n본문.\n"),
            make_page("note", "# Note\n\n[[Page:Alpha Platform]] 를 본다.\n"),
        ]
        lookup = llmwiki.page_lookup(pages)
        note = pages[1]
        wiki = next(link for link in llmwiki.implied_links(note) if link["kind"] == "wiki")
        self.assertIs(lookup[llmwiki.link_key(wiki["target"])], pages[0])


class RedactTest(unittest.TestCase):
    def test_named_and_bare_credentials_are_masked(self) -> None:
        self.assertIn("접속 정보 생략", llmwiki.redact("password: hunter2"))
        self.assertNotIn("hunter2", llmwiki.redact("password: hunter2"))
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", llmwiki.redact("Bearer eyJhbGciOiJIUzI1NiJ9"))
        self.assertNotIn("abcdefghijklmnop", llmwiki.redact("Authorization: Bearer abcdefghijklmnop"))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz0123",
                         llmwiki.redact("token sk-abcdefghijklmnopqrstuvwxyz0123"))
        self.assertIsNone(llmwiki.T.secret_hit("password 컬럼이 존재한다."))
