#!/usr/bin/env python3
"""Shared text identity for the CLI, the search index, and the hook.

`link_key`, `implied_links`, and `redact` used to live in two copies that had
already drifted: `[[Page:Alpha]]` resolved in the index and not in the viewer
graph, a bare `sources: [foo]` became an edge in one place only, and ingest
refused fewer credential shapes than the index would mask. One module owns
those rules so the graph, the ranker, and the secret check see the same text.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
LINK_SEPARATOR = re.compile(r"[\s_]+")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MASK = "(접속 정보 생략)"
# Named credentials: `password: x`, `"api_key": "x"`, `--token=x`.
SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
    r"secret|connection[_-]?string|private[_-]?key|client[_-]?secret|token)"
    r"[\"']?\s*[:=]\s*[\"']?([^\s,;\"']+)")
SECRET_MASK = MASK
# (pattern, name group, value group, secret group). redact replaces the value
# group, so the name, separator and quotes around it keep their shape; the
# secret group is the part a placeholder or an earlier mask has to cover. A
# name group means the match is a `name: value` pair, judged by its name too.
SECRET_RULES = (
    (SECRET, 1, 2, 2),
    # Values that are credentials even without a name — auth headers, PEM,
    # known token prefixes, URL userinfo, CLI flags.
    (re.compile(r"(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._\-+/=]{8,})"), 0, 1, 1),
    (re.compile(r"(?s)(-----BEGIN [A-Z ]*PRIVATE KEY-----(.*?)-----END [A-Z ]*PRIVATE KEY-----)"), 0, 1, 2),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|"
                r"xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[A-Za-z0-9_\-]{20,})"), 0, 1, 1),
    (re.compile(r"\b[a-z][a-z0-9+.\-]*://([^\s/:@]+:([^\s/@]+))@"), 0, 1, 2),
    (re.compile(r"(?i)--?(password|passwd|token|api[_-]?key|secret|auth)\b\s+(\S+)"), 1, 2, 2),
)
SECRET_EXTRA = tuple(rule[0] for rule in SECRET_RULES[1:])
# `<your token>` 은 자리표시다. 값 자리에서 시작해 값을 덮어야 한다 — 뒤에 붙은 닫는 문장부호(`` ` ``·괄호·마침표)만 남아도 된다.
PLACEHOLDER = re.compile(r"<[^<>\n]{1,200}>")
PLACEHOLDER_TAIL = re.compile(r"[`)\]}>.,!?:;'\"*_~]*")
# password·secret·api_key 는 무엇이 앞에 붙어도 자격증명 이름이다(db_password, JWT_SECRET). token 만
# 다르다 — value_token·next_token·max_token 은 자격증명이 아니다. 앞 낱말이 이 중 하나일 때만 그렇다.
TOKEN_QUALIFIERS = frozenset({
    "access", "admin", "api", "app", "auth", "bearer", "bot", "ci", "client", "deploy", "gh", "github",
    "gitlab", "hf", "id", "job", "jwt", "npm", "oauth", "personal", "private", "pypi", "refresh", "secret",
    "service", "session", "slack", "user", "vault"})
# 값이 이런 낱말을 품은 식별자면(`token: value_token`, `password: DB_PASSWORD`) 비밀이 아니라 key 의 이름이다.
CREDENTIAL_WORDS = frozenset({"apikey", "credential", "credentials", "key", "passwd", "password", "secret",
                              "token"})
CREDENTIAL_KEY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
                            r"connection[_-]?string|private[_-]?key|client[_-]?secret|token)$")
IDENTIFIER_CHAR = re.compile(r"[A-Za-z0-9_.\-]")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
SEGMENT_BREAK = re.compile(r"[_.\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def norm(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def sha_bytes(data: bytes, length: int = 64) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def sha(text: str, length: int = 64) -> str:
    return sha_bytes(str(text).encode("utf-8"), length)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def link_key(value: str) -> str:
    """Case, spacing, and a leading `page:` are the same page.

    Casefold first so `Page:Alpha Platform`, `page:alpha-platform`, and
    `alpha_platform` all collapse to `alpha-platform`.
    """
    text = norm(value).casefold()
    if text.startswith("page:"):
        text = text[5:]
    return LINK_SEPARATOR.sub("-", text).strip("-")


def refs_in(text: str) -> list[str]:
    return sorted({norm(m.group(1)) for m in WIKILINK.finditer(text)})


def strip_ref_prefix(ref: str) -> tuple[str, str]:
    """`source:handbook` -> ('source', 'handbook'); a bare value keeps an empty prefix."""
    prefix, separator, rest = norm(ref).partition(":")
    if not separator or not norm(rest):
        return "", norm(ref)
    return prefix.lower(), norm(rest)


def reference_links(sources: Iterable[str] = (), supersedes: Iterable[str] = (),
                    related: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Frontmatter relations become graph edges, not just strings.

    `user:` and `raw:` name evidence outside the wiki and stay off the graph.
    """
    links: list[dict[str, Any]] = []
    for kind, refs in (("source", sources), ("supersedes", supersedes), ("related", related)):
        for ref in refs:
            prefix, target = strip_ref_prefix(ref)
            if prefix in {"user", "raw"} or not target:
                continue
            links.append({"target": target, "label": target, "kind": kind})
    return links


def implied_links(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Declared links + `[[wikilinks]]` in blocks + sources/supersedes/related.

    The viewer graph and the search diffusion graph both call this. A page that
    only cites `sources: [foo]` still gets a source edge; `[[Page:Alpha]]`
    resolves the same as `[[alpha]]`.
    """
    links = [dict(link) for link in page.get("links") or [] if norm(link.get("target"))]
    seen = {(link_key(link["target"]), link.get("kind", "wiki")) for link in links}

    def add(target: str, kind: str, block_id: str = "") -> None:
        key = (link_key(target), kind)
        if not key[0] or key in seen:
            return
        seen.add(key)
        link: dict[str, Any] = {"target": norm(target), "label": norm(target), "kind": kind}
        if block_id:
            link["block_id"] = block_id
        links.append(link)

    blocks = page.get("blocks") or {}
    for bid in page.get("block_order") or list(blocks):
        block = blocks.get(bid) or {}
        refs = block.get("refs")
        if refs is None:
            refs = refs_in(str(block.get("source_text", "")))
        for ref in refs:
            add(ref, "wiki", str(bid))
    for link in reference_links(page.get("sources") or (),
                                page.get("supersedes") or (),
                                page.get("related") or ()):
        add(link["target"], link["kind"])
    return links


def segments(name: str) -> list[str]:
    """`nextPageToken`, `NEXT_PAGE_TOKEN`, `next-page.token` -> ['next', 'page', 'token']."""
    return [part.lower() for part in SEGMENT_BREAK.split(name) if part]


def credential_name(identifier: str, word: str) -> bool:
    """Does `identifier`, which ends in the credential word `word`, name a credential?

    A bare `token`, an env-style `GITHUB_TOKEN` and `refresh_token` do; a JSON
    key like `value_token` or `next_token` does not. Every other word
    (password, secret, api_key, …) names a credential whatever precedes it.
    """
    if word.lower() != "token":
        return True
    name = identifier.strip("-\"'")
    if name.lower() == "token" or name.isupper():
        return True
    head = segments(name[:len(name) - len(word)])
    return bool(head) and head[-1] in TOKEN_QUALIFIERS


def credential_key(key: str) -> bool:
    """A JSON object key whose string value is a credential: `password`, `client_secret`."""
    match = CREDENTIAL_KEY.search(str(key))
    return bool(match) and credential_name(str(key), match.group(1))


def key_reference(value: str) -> bool:
    """`token: value_token`, `password: DB_PASSWORD` — the value names a field or a variable."""
    value = value.rstrip("`)]}>.,!?:;'\"*~")       # `token: value_token` 의 닫는 backtick 은 이름이 아니다
    return bool(IDENTIFIER.fullmatch(value)) and any(part in CREDENTIAL_WORDS for part in segments(value))


def placeholder_covers(text: str, start: int, end: int) -> bool:
    """`<…>` begins the value at `start` and runs at least to its end."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    found = PLACEHOLDER.match(text, start)
    return bool(found) and (found.end() >= end or PLACEHOLDER_TAIL.fullmatch(text, found.end(), end) is not None)


def plain_value(text: str) -> bool:
    """A string that would hold a real credential — not empty, not masked, no placeholder, no field name."""
    value = text.strip()
    return bool(value) and not value.startswith(MASK) and not placeholder_covers(value, 0, len(value)) \
        and not key_reference(value)


def identifier_at(text: str, end: int, start: int) -> str:
    """The whole identifier that ends at `end` (its credential word starts at `start`)."""
    while start > 0 and IDENTIFIER_CHAR.match(text[start - 1]):
        start -= 1
    return text[start:end]


def exempt(match: re.Match[str], name: int, value: int, secret: int) -> bool:
    """Not a credential after all: already masked (redact stays idempotent), a `<…>`
    placeholder, a value that names a field, or a name like `value_token`."""
    text = match.string
    if text.startswith(MASK, match.start(secret)):
        return True
    if placeholder_covers(text, match.start(secret), match.end(secret)):
        return True
    if not name:
        return False
    return key_reference(match.group(value)) or not credential_name(
        identifier_at(text, match.end(name), match.start(name)), match.group(name))


def redact(text: str) -> str:
    """Mask anything that looks like a credential. Strip control characters too.

    Idempotent: a masked value is left alone, so the index, the renderer and
    the hook can each apply it without `(접속 정보 생략) 정보 생략)` piling up.
    """
    text = CONTROL.sub("", str(text or ""))
    for pattern, name, value, secret in SECRET_RULES:
        def mask(match: re.Match[str]) -> str:
            if exempt(match, name, value, secret):
                return match.group(0)
            start, end = match.start(value) - match.start(), match.end(value) - match.start()
            return match.group(0)[:start] + MASK + match.group(0)[end:]
        text = pattern.sub(mask, text)
    return text


def redact_value(value: Any) -> Any:
    """`redact` for a JSON value: strings are masked one by one and object keys are left
    alone, while a string under a credential key (`"password": "x"`) is masked whole.

    Masking the serialised JSON instead could eat the backslash of an escaped
    quote and leave text that no longer parses.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: MASK if isinstance(item, str) and credential_key(key) and plain_value(item)
                else redact_value(item) for key, item in value.items()}
    return value


def secret_hit(text: str) -> str | None:
    """Short label if `text` looks like it holds a credential, else None.

    The label is the field name (`password`, `db_password`) or `credential` —
    never the secret value itself. Masked values and placeholders do not count.
    """
    text = CONTROL.sub("", str(text or ""))
    for pattern, name, value, secret in SECRET_RULES:
        for match in pattern.finditer(text):
            if not exempt(match, name, value, secret):
                return identifier_at(text, match.end(name), match.start(name)).strip("-\"'") if name \
                    else "credential"
    return None


def pointer_token(key: Any) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def secret_paths(value: Any, path: str = "") -> list[tuple[str, str]]:
    """(RFC 6901 pointer, label) for every string inside `value` that looks like a credential.

    A canonical page is copied verbatim to viewer/public/data, so a credential
    anywhere in it — body, summary, history, the ingested snapshot — leaks.
    """
    hits: list[tuple[str, str]] = []
    if isinstance(value, str):
        label = secret_hit(value)
        if label:
            hits.append((path or "/", label))
    elif isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}/{pointer_token(key)}"
            # JSON key 는 이름만이지 값이 아니다 — `"value_token": "…"` 는 안 되고 `"password": "…"` 는 된다.
            if isinstance(item, str) and credential_key(key) and plain_value(item):
                hits.append((where, str(key)))
            hits.extend(secret_paths(item, where))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(secret_paths(item, f"{path}/{index}"))
    return hits


def block_text(block: dict[str, Any]) -> str:
    """Search/display body: data.text/statement if present, else source_text."""
    data = block.get("data") or {}
    if isinstance(data, dict):
        for key in ("text", "statement"):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key]
    text = block.get("source_text")
    return text if isinstance(text, str) else ""


def unresolved(block: dict[str, Any]) -> bool:
    resolution = block.get("resolution")
    status = resolution.get("status") if isinstance(resolution, dict) else None
    return block.get("kind") == "conflict" and status != "resolved"
