"""랭킹 평가 스크립트: gold 주입률, 1위 비율, 무관 질문의 평균 주입 바이트."""
from __future__ import annotations

import json
import subprocess
import sys

from tests.support import REPO, WorkspaceCase, _load, make_page

EVAL_SCRIPT = REPO / "scripts" / "llmwiki_eval.py"
DEMO_QUESTIONS = REPO / "tools" / "eval" / "demo-questions.json"
ev = _load("llmwiki_eval", EVAL_SCRIPT)


class EvalTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_pages([
            make_page("릴리스", "# 릴리스\n\n릴리스는 목요일에 나간다. [[검토]]\n", tags=["t"]),
            make_page("검토", "# 검토\n\n검토자는 작성자가 아닌 한 사람이다. [[릴리스]]\n", tags=["t"]),
        ])

    def questions(self, rows: list[dict]) -> object:
        path = self.root / "questions.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return path

    def test_the_three_numbers(self) -> None:
        path = self.questions([{"q": "릴리스는 무슨 요일에 나가나", "gold_slug": "릴리스"},
                               {"q": "검토자는 누구인가", "gold_slug": "page:검토"},
                               {"q": "검토자 요일", "gold_slug": ["없는-page"]},
                               {"q": "zzqq xxyy", "gold_slug": None},
                               {"q": "릴리스 목요일"}])
        report = ev.evaluate(self.root, ev.load_questions(path))
        self.assertEqual((report["questions"], report["answerable"], report["unrelated"]), (5, 3, 2))
        self.assertEqual(report["gold_injected"], round(2 / 3, 4))
        self.assertEqual(report["top1"], round(2 / 3, 4))
        unrelated = [row["bytes"] for row in report["rows"] if not row["gold"]]
        self.assertEqual(unrelated[0], 0)                       # 한 토큰도 겹치지 않으면 무주입
        self.assertGreater(unrelated[1], 0)
        self.assertEqual(report["unrelated_bytes_mean"], round(sum(unrelated) / 2, 1))

    def test_the_canonical_store_is_only_read(self) -> None:
        before = {p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}
        ev.evaluate(self.root, [{"q": "릴리스", "gold": ["릴리스"]}])
        self.assertEqual({p: p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}, before)

    def test_a_malformed_question_file_is_refused(self) -> None:
        for rows in ({"q": "x"}, [{"gold_slug": "a"}]):
            with self.assertRaises(SystemExit):
                ev.load_questions(self.questions(rows))

    def test_injected_pages_are_read_from_the_body_lines(self) -> None:
        text = "<llmwiki-context v=3>\nP a concept\nB a#x:1 cur | 본문\nE a#x:1 related→b\nP old concept sup→a\nB c#y:1 cur | 셋\n"
        self.assertEqual(ev.injected_slugs(text), ["a", "c"])


class DemoSetTest(WorkspaceCase):
    def test_the_demo_questions_point_at_demo_pages(self) -> None:
        rows = json.loads(DEMO_QUESTIONS.read_text(encoding="utf-8"))
        slugs = {page["slug"] for page in json_pages()}
        self.assertTrue(any(row["gold_slug"] is None for row in rows))
        for row in rows:
            self.assertTrue(row["q"].strip())
            if row["gold_slug"]:
                self.assertIn(row["gold_slug"], slugs)

    def test_the_cli_reports_on_the_demo_wiki(self) -> None:
        proc = subprocess.run([sys.executable, str(EVAL_SCRIPT), str(DEMO_QUESTIONS), "--root", str(REPO), "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(set(report), {"root", "pages", "max_bytes", "questions", "answerable", "unrelated",
                                       "gold_injected", "top1", "unrelated_bytes_mean"})
        self.assertGreaterEqual(report["gold_injected"], 0.8)
        proc = subprocess.run([sys.executable, str(EVAL_SCRIPT), str(DEMO_QUESTIONS), "--root", str(REPO)],
                              capture_output=True, text=True)
        self.assertIn("gold 주입률", proc.stdout)


def json_pages() -> list[dict]:
    pages = []
    for path in sorted((REPO / "wiki").rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        pages.extend(value if isinstance(value, list) else [value])
    return pages
