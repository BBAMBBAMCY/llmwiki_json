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
# Every pattern's last group is the credential itself; redact replaces only
# that span, so the name, separator and quotes around it keep their shape.
# Named credentials: `password: x`, `"api_key": "x"`, `--token=x`.
SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
    r"secret|connection[_-]?string|private[_-]?key|client[_-]?secret|token)"
    r"\"?\s*[:=]\s*\"?([^\s,;\"']+)")
SECRET_MASK = MASK
# Values that are credentials even without a name — auth headers, PEM, known
# token prefixes, URL userinfo, CLI flags.
SECRET_EXTRA = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._\-+/=]{8,})"),
    re.compile(r"(?s)(-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|"
               r"xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[A-Za-z0-9_\-]{20,})"),
    re.compile(r"\b[a-z][a-z0-9+.\-]*://([^\s/:@]+:[^\s/@]+)@"),
    re.compile(r"(?i)--?(?:password|passwd|token|api[_-]?key|secret|auth)\b\s+(\S+)"),
)
SECRET_PATTERNS = (SECRET, *SECRET_EXTRA)


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


def _value_start(match: re.Match[str]) -> int:
    return match.start(match.re.groups)


def exempt(match: re.Match[str]) -> bool:
    """A value that is already the mask is not a credential — redact twice, get the same text."""
    return match.string.startswith(MASK, _value_start(match))


def _mask(match: re.Match[str]) -> str:
    if exempt(match):
        return match.group(0)
    start, end = _value_start(match) - match.start(), match.end(match.re.groups) - match.start()
    return match.group(0)[:start] + MASK + match.group(0)[end:]


def redact(text: str) -> str:
    """Mask anything that looks like a credential. Strip control characters too.

    Idempotent: a masked value is left alone, so the index, the renderer and
    the hook can each apply it without `(접속 정보 생략) 정보 생략)` piling up.
    """
    text = CONTROL.sub("", str(text or ""))
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_mask, text)
    return text


def secret_hit(text: str) -> str | None:
    """Short label if `text` looks like it holds a credential, else None.

    The label is the field name (`password`, `api_key`) or `credential` — never
    the secret value itself. Masked values do not count.
    """
    text = CONTROL.sub("", str(text or ""))
    for match in SECRET.finditer(text):
        if not exempt(match):
            return match.group(1)
    for pattern in SECRET_EXTRA:
        if any(not exempt(match) for match in pattern.finditer(text)):
            return "credential"
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
            hits.extend(secret_paths(item, f"{path}/{pointer_token(key)}"))
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
