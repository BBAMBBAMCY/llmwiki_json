/** 정적 배포(nginx)용 감시자. `bun run watch:data` 또는 compose 의 watcher 서비스가 띄운다.
 *
 * 개발 서버는 vite 플러그인이 같은 일을 하므로 이 파일이 필요 없다.
 * 뷰어 검색(`/api/query`)도 여기서 색인을 탄다 — nginx 가 이 포트로 프록시한다. */
import { resolve } from "node:path";
import { pollWikiData, resolveLayout, runWikiQuery } from "./wiki-data";

const projectRoot = resolve(import.meta.dirname, "..", "..");
// 분리 모드면 데이터 repo(LLMWIKI_ROOT 또는 위로 찾은 llmwiki.lock) 의 wiki/ 를 보고 .llmwiki/viewer-data 에 굽는다.
const layout = resolveLayout(projectRoot);
const intervalMs = Number(process.env.LLMWIKI_WATCH_INTERVAL_MS ?? 2000);
const queryPort = Number(process.env.LLMWIKI_QUERY_PORT ?? 8788);

console.log(`[llmwiki-watch] 데이터 ${layout.root} (${layout.mode}) → ${layout.viewer_data}`);
pollWikiData({
  projectRoot,
  layout,
  intervalMs,
  onBuilt: () => console.log(`[llmwiki-watch] build 완료 ${new Date().toLocaleTimeString()}`),
});

Bun.serve({
  port: queryPort,
  hostname: "0.0.0.0",
  fetch(req) {
    const url = new URL(req.url);
    if (url.pathname !== "/query") return new Response("not found", {status: 404});
    try {
      const body = runWikiQuery(
        projectRoot, url.searchParams.get("q") ?? "", Number(url.searchParams.get("limit") || 50), layout);
      return Response.json(body, {headers: {"Cache-Control": "no-store"}});
    } catch (error) {
      return Response.json({error: error instanceof Error ? error.message : String(error)}, {status: 500});
    }
  },
});
console.log(`[llmwiki-watch] query :${queryPort}`);
