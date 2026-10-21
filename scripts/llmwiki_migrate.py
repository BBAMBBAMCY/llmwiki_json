"""정본 page 의 스키마 판을 올리는 변환 레지스트리.

판 하나를 올리는 변환은 `MIGRATIONS[(옛 판, 새 판)] = 함수` 로 등록한다. 함수는 page 하나(dict)를 받아
새 판의 page 를 돌려준다 — 입력을 고치지 않고, 같은 입력이면 언제나 같은 출력이다. 여러 판을 건너야 하면
`chain()` 이 등록된 변환을 이어 붙인다. 어느 page 를 바꿀지, 어디에 쓸지, 쓰기 전에 무엇을 검사할지는
`llmwiki.py migrate` 가 정한다 — 이 모듈은 표준 라이브러리만 쓰고 파일을 건드리지 않는다.

`RELEASES` 는 코드 판(VERSION, semver) → 그 판이 쓰는 스키마 판이다. 스키마 판이 바뀌는 릴리스는 minor
이상을 올린다 (tests/test_migrate.py 가 이 표와 VERSION, MIGRATIONS 를 대조한다).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

try:  # package import
    from . import llmwiki_paths as P
except ImportError:  # run as a script or loaded from its file path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import llmwiki_paths as P  # type: ignore[no-redef]

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def to_1_1(page: dict[str, Any]) -> dict[str, Any]:
    """1.0 → 1.1: `block_aliases`(edit 가 바꾼 block 의 옛 id → 새 id), `keywords`(색상에 쓰지 않는 자유 낱말),
    block kind `open_question` 이 정식 필드가 된 판.

    1.0 정본에도 이 필드들이 이미 들어 있을 수 있다(판을 올리기 전에 코드가 먼저 썼다). 그래서 변환은
    모양만 맞춘다 — 빈 `block_aliases`·`keywords` 는 지우고(없는 것과 같은 뜻), 있는 것은 정렬·중복 제거한다.
    본문·id·updated 는 그대로다.
    """
    out = copy.deepcopy(page)
    out["schema_version"] = "1.1"
    aliases = out.get("block_aliases")
    if isinstance(aliases, dict) and aliases:
        out["block_aliases"] = dict(sorted((str(k), str(v)) for k, v in aliases.items()))
    else:
        out.pop("block_aliases", None)
    keywords = out.get("keywords")
    if isinstance(keywords, list) and keywords:
        seen: list[str] = []
        for word in keywords:
            text = str(word).strip()
            if text and text not in seen:
                seen.append(text)
        out["keywords"] = seen
    else:
        out.pop("keywords", None)
    return out


MIGRATIONS: dict[tuple[str, str], Migration] = {
    ("1.0", "1.1"): to_1_1,
}

RELEASES: dict[str, str] = {
    "1.0.0": "1.0",
    "1.1.0": "1.1",
}


def chain(source: str, target: str | None = None) -> list[tuple[str, str, Migration]]:
    """source 판에서 target 판(기본: 코드의 판)까지 이어지는 변환들. 길이 없으면 ValueError."""
    target = target or P.SCHEMA_VERSION
    steps: list[tuple[str, str, Migration]] = []
    current = source
    guard = 0
    while current != target:
        nexts = sorted((b for (a, b) in MIGRATIONS if a == current), key=P.schema_key)
        nexts = [b for b in nexts if P.schema_key(b) <= P.schema_key(target)]
        if not nexts:
            raise ValueError(f"schema {source} → {target} 로 가는 변환이 등록돼 있지 않다 ({current} 에서 끊긴다)")
        step = nexts[-1]
        steps.append((current, step, MIGRATIONS[(current, step)]))
        current = step
        guard += 1
        if guard > 100:
            raise ValueError("변환 레지스트리에 고리가 있다")
    return steps


def migrate_page(page: dict[str, Any], target: str | None = None) -> dict[str, Any]:
    """page 하나를 target 판으로. 이미 그 판이면 그대로(사본)."""
    current = str(page.get("schema_version") or "")
    out = copy.deepcopy(page)
    for _, _, step in chain(current, target):
        out = step(out)
    return out


def final_version() -> str:
    """등록된 변환이 닿는 가장 높은 판."""
    versions = {b for (_, b) in MIGRATIONS} | {a for (a, _) in MIGRATIONS}
    return max(versions, key=P.schema_key) if versions else P.SCHEMA_VERSION


def page_diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """page 하나가 어떻게 바뀌는지 짧은 줄들로. 같으면 빈 목록."""
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key == "blocks":
            continue
        if key not in after:
            lines.append(f"- {key}")
        elif key not in before:
            lines.append(f"+ {key}")
        elif before[key] != after[key]:
            if isinstance(before[key], (str, int, float)) and isinstance(after[key], (str, int, float)):
                lines.append(f"~ {key}: {before[key]} → {after[key]}")
            else:
                lines.append(f"~ {key}")
    old_blocks = before.get("blocks") if isinstance(before.get("blocks"), dict) else {}
    new_blocks = after.get("blocks") if isinstance(after.get("blocks"), dict) else {}
    changed = sorted(bid for bid in set(old_blocks) | set(new_blocks) if old_blocks.get(bid) != new_blocks.get(bid))
    if changed:
        lines.append(f"~ blocks: {len(changed)}개 ({', '.join(changed[:3])}{' …' if len(changed) > 3 else ''})")
    return lines
