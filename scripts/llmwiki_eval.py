#!/usr/bin/env python3
"""랭킹 평가 — 질문 세트 하나로 검색과 주입을 잰다.

입력은 `[{"q": "질문", "gold_slug": "slug"}]` JSON 파일이다. `gold_slug` 가 없거나 null 이면
위키와 무관한 질문이고, 목록이면 그중 하나면 맞은 것이다.

내는 숫자는 셋이다.
- gold 주입률: 답이 있는 질문 중 gold page 의 본문 block(B 줄)이 주입된 비율
- 1위 비율: 답이 있는 질문 중 검색 1위가 gold page 인 비율
- 무관 질문의 평균 주입 바이트: 무관 질문마다 주입된 UTF-8 바이트의 평균 (넣지 않으면 0)

색인은 정본(`wiki/**/*.json`)에서 메모리로 한 번 굽고 모든 질문에 다시 쓴다. 검색·투영·렌더는 훅의
메모리 경로(`llmwiki_context.build_indexed`)와 같다. 고정(always) page 는 싣지 않는다 — 검색을 재는
것이기 때문이다. 정본도 색인 파일도 쓰지 않는다. 무주입 문턱은 훅처럼 기본 꺼짐이고, `--silence`·`--hint`
로 켜서 같은 질문 세트로 득실(gold 주입률 대 무관 질문 주입)을 잴 수 있다. 대역 사전
(`tools/config/glossary.json`)이 있으면 훅처럼 질문을 넓힌다 — `--no-glossary` 로 끄고 사전의 득실을 잰다.

    python3 scripts/llmwiki_eval.py tools/eval/demo-questions.json
    python3 scripts/llmwiki_eval.py my-questions.json --root ~/llmwiki --json --details
    python3 scripts/llmwiki_eval.py my-questions.json --root ~/llmwiki --silence 770   # 문턱을 켰을 때
    python3 scripts/llmwiki_eval.py my-questions.json --root ~/llmwiki --no-glossary   # 사전 없이
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

try:  # package import (bench: `from scripts import llmwiki_eval`)
    from . import llmwiki_context as C
    from . import llmwiki_index as IDX
except ImportError:  # run as a script or loaded from its file path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import llmwiki_context as C  # type: ignore[no-redef]
    import llmwiki_index as IDX  # type: ignore[no-redef]


def slug_of(value: Any) -> str:
    text = str(value or "").strip()
    return text[5:] if text.startswith("page:") else text


def load_questions(path: Path) -> list[dict[str, Any]]:
    """`[{q, gold_slug}]` → `[{q, gold: [slug…]}]`. gold 가 비면 무관 질문이다."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise SystemExit(f"{path}: 질문 파일은 [{{q, gold_slug}}] 배열이다")
    out = []
    for number, item in enumerate(value):
        question = str((item or {}).get("q") or "").strip() if isinstance(item, dict) else ""
        if not question:
            raise SystemExit(f"{path}: {number}번 항목에 q 가 없다")
        gold = item.get("gold_slug")
        golds = [gold] if isinstance(gold, str) else list(gold or [])
        out.append({"q": question, "gold": [slug_of(g) for g in golds if slug_of(g)]})
    return out


def injected_slugs(text: str) -> list[str]:
    """주입 본문에서 본문 block(B 줄)이 실린 page 의 slug, 처음 나온 순서대로."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("B "):
            slug = line[2:].split("#", 1)[0]
            if slug not in out:
                out.append(slug)
    return out


def rate(flags: Iterable[bool]) -> float | None:
    values = [1.0 if flag else 0.0 for flag in flags]
    return round(statistics.fmean(values), 4) if values else None


def evaluate(root: Path, questions: list[dict[str, Any]], *, max_bytes: int = C.MAX_BYTES,
             max_tokens: int = C.MAX_TOKENS, silence_t: float = 0.0, hint_t: float = 0.0,
             glossary: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    docs = IDX.load_docs(root / "wiki", root)
    if not docs:
        raise SystemExit(f"{root}/wiki 에 page 가 없다")
    words = IDX.load_glossary(root) if glossary else None
    idx = IDX.configure(IDX.build_memory(docs, glossary=words), root)
    rows: list[dict[str, Any]] = []
    try:
        for item in questions:
            text, result, _pages = C.build_indexed(
                idx, root, item["q"], "", set(), max_bytes=max_bytes, max_tokens=max_tokens,
                limit=C.GRAPH_PAGES, cut=C.GRAPH_CUT, silence_t=silence_t, hint_t=hint_t,
                row_chars=C.MAX_BLOCK_CHARS, mode="memory")
            rows.append({"q": item["q"], "gold": item["gold"],
                         "top": result.hits[0].slug if result.hits else "",
                         "injected": injected_slugs(text), "bytes": len(text.encode("utf-8"))})
    finally:
        idx.close()
    answerable = [row for row in rows if row["gold"]]
    unrelated = [row for row in rows if not row["gold"]]
    for row in answerable:
        row["gold_injected"] = any(gold in row["injected"] for gold in row["gold"])
        row["top1"] = row["top"] in row["gold"]
    return {
        "root": str(root), "pages": len(docs), "max_bytes": max_bytes, "silence": silence_t, "hint": hint_t,
        "glossary_groups": len(words.groups) if words else 0,
        "questions": len(rows), "answerable": len(answerable), "unrelated": len(unrelated),
        "gold_injected": rate(row["gold_injected"] for row in answerable),
        "top1": rate(row["top1"] for row in answerable),
        "unrelated_bytes_mean": round(statistics.fmean(row["bytes"] for row in unrelated), 1)
        if unrelated else None,
        "unrelated_silent": rate(row["bytes"] == 0 for row in unrelated),
        "rows": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    answerable = report["answerable"]
    hits = sum(1 for row in report["rows"] if row.get("gold_injected"))
    tops = sum(1 for row in report["rows"] if row.get("top1"))
    print(f"질문 {report['questions']} (답 있음 {answerable}, 무관 {report['unrelated']}) — "
          f"page {report['pages']}, 예산 {report['max_bytes']} B")
    print(f"gold 주입률          {report['gold_injected']} ({hits}/{answerable})")
    print(f"1위 비율             {report['top1']} ({tops}/{answerable})")
    print(f"무관 질문 평균 주입  {report['unrelated_bytes_mean']} B (무주입 {report['unrelated_silent']})")
    if report["glossary_groups"]:
        print(f"대역 사전            {report['glossary_groups']} 묶음 ({IDX.GLOSSARY_CONFIG})")
    if report["silence"] or report["hint"]:
        print(f"문턱                 silence {report['silence']} · hint {report['hint']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llmwiki_eval", description=__doc__.splitlines()[0])
    parser.add_argument("questions", help="[{q, gold_slug}] JSON 파일")
    parser.add_argument("--root", help="위키 데이터 root (기본: $LLMWIKI_ROOT → 위로 찾은 llmwiki.lock → 이 저장소)")
    parser.add_argument("--max-bytes", type=int, default=C.MAX_BYTES)
    parser.add_argument("--max-tokens", type=int, default=C.MAX_TOKENS)
    parser.add_argument("--silence", type=float, default=0.0, help="무주입 문턱 raw_top×coverage (기본 0 = 끔)")
    parser.add_argument("--hint", type=float, default=0.0, help="주소만 문턱 (기본 0 = 끔)")
    parser.add_argument("--no-glossary", action="store_false", dest="glossary",
                        help=f"{IDX.GLOSSARY_CONFIG} 로 질문을 넓히지 않는다 (사전의 득실을 잴 때)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--details", action="store_true", help="--json 에 질문마다의 결과를 싣는다")
    args = parser.parse_args(argv)
    root = C.resolve_root(args.root)
    report = evaluate(root, load_questions(Path(args.questions)), max_bytes=args.max_bytes,
                      max_tokens=args.max_tokens, silence_t=args.silence, hint_t=args.hint,
                      glossary=args.glossary)
    if args.as_json:
        if not args.details:
            report = {key: value for key, value in report.items() if key != "rows"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
