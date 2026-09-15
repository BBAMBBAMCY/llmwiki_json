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

# Named credentials: `password: x`, `"api_key": "x"`, `--token=x`.
SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
    r"secret|connection[_-]?string|private[_-]?key|client[_-]?secret|token)"
    r"\"?\s*[:=]\s*\"?[^\s,;\"']+")
SECRET_MASK = r"\1: (접속 정보 생략)"
# Values that are credentials even without a name — auth headers, PEM, known
# token prefixes, URL userinfo, CLI flags.
SECRET_EXTRA = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"), r"\1 (접속 정보 생략)"),
    (re.compile(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"),
     "(접속 정보 생략)"),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|"
                r"xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[A-Za-z0-9_\-]{20,})"),
     "(접속 정보 생략)"),
    (re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@"), r"\1(접속 정보 생략)@"),
    (re.compile(r"(?i)(--?(?:password|passwd|token|api[_-]?key|secret|auth)\b)\s+\S+"),
     r"\1 (접속 정보 생략)"),
)


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


def redact(text: str) -> str:
    """Mask anything that looks like a credential. Strip control characters too."""
    text = CONTROL.sub("", str(text or ""))
    text = SECRET.sub(SECRET_MASK, text)
    for pattern, mask in SECRET_EXTRA:
        text = pattern.sub(mask, text)
    return text


def secret_hit(text: str) -> str | None:
    """Short label if `text` looks like it holds a credential, else None.

    The label is the field name (`password`, `api_key`) or `credential` — never
    the secret value itself.
    """
    text = CONTROL.sub("", str(text or ""))
    match = SECRET.search(text)
    if match:
        return match.group(1)
    for pattern, _mask in SECRET_EXTRA:
        if pattern.search(text):
            return "credential"
    return None


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
