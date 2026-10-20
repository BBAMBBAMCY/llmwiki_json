import type { WikiPage } from "../types";

const esc = (value: unknown) => String(value ?? "").replace(
  /[&<>"']/g,
  (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[char] ?? char,
);

const links = (value: unknown) => esc(value).replace(
  /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/g,
  (_match, target: string, alias?: string) => `<button class="wiki-inline-link" data-target="${target}">${alias || target}</button>`,
);

/** 링크 비교 키 — 대소문자·공백·`page:` 접두를 무시한다.
 *  scripts/llmwiki.py 의 link_key 와 같은 규칙이라 그래프와 본문이 같은 곳으로 간다. */
export function linkKey(value: string): string {
  let text = String(value ?? "").normalize("NFC").trim().toLowerCase();
  if (text.startsWith("page:")) text = text.slice(5);
  return text.replace(/[\s_]+/g, "-").replace(/^-+|-+$/g, "");
}

/** scripts/llmwiki.py 의 flow_list 와 같은 표기 — ingest 가 같은 항목으로 다시 읽는다. */
const flowItem = (item: string) => (!/[,[\]"']/.test(item) ? item : item.includes('"') ? `'${item}'` : `"${item}"`);
const flow = (items: string[]) => items.map(flowItem).join(", ");

export function pageMarkdown(page: WikiPage): string {
  const rows = [
    "---",
    `type: ${page.type}`,
    `created: ${page.created}`,
    `updated: ${page.updated}`,
    `tags: [${flow(page.tags)}]`,
    `projects: [${flow(page.projects)}]`,
    `sources: [${flow(page.sources)}]`,
  ];
  for (const kind of ["supersedes", "related"]) {
    const targets = [...new Set(page.links.filter((link) => link.kind === kind).map((link) => link.target))];
    if (targets.length) rows.push(`${kind}: [${flow(targets)}]`);
  }
  if (page.raw_ref) rows.push(`raw: ${page.raw_ref}`);
  if (page.summary) rows.push(`summary: "${page.summary.split(/\s+/).filter(Boolean).join(" ")}"`);
  const frontmatter = [...rows, "---"].join("\n");
  const body = page.block_order.map((id) => page.blocks[id].source_text).join("\n\n");
  return `${frontmatter}\n\n${body}\n`;
}

export function pageHtml(page: WikiPage): string {
  return page.block_order.map((id) => {
    const block = page.blocks[id]; const data = block.data;
    if (block.kind === "heading") { const level = Math.min(6, Math.max(1, Number(data.level ?? 2))); return `<h${level}>${links(data.text)}</h${level}>`; }
    if (["paragraph", "markdown", "raw"].includes(block.kind)) return `<p>${links(data.text ?? block.source_text).replace(/\n/g, "<br>")}</p>`;
    if (block.kind === "code") return `<pre><code>${esc(data.text)}</code></pre>`;
    if (block.kind === "table") return `<pre class="raw-structure">${esc(block.source_text)}</pre>`;
    if (block.kind === "list") return `<pre class="raw-structure">${esc(data.text ?? block.source_text)}</pre>`;
    if (["quote", "conflict", "current"].includes(block.kind)) return `<blockquote class="${block.kind}">${links(data.text)}</blockquote>`;
    return block.kind === "thematic_break" ? "<hr>" : `<pre>${esc(block.source_text)}</pre>`;
  }).join("");
}
