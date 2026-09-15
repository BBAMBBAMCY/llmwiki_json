import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";
import { createWikiData, runWikiQuery } from "./scripts/wiki-data";

const projectRoot = resolve(__dirname, "..");

/** 개발 서버에서 wiki 변경을 감시해 파생물을 다시 만들고 열린 화면에 통지한다.
 *  build 큐는 정적 배포용 감시자와 같은 모듈을 쓴다. */
function liveWikiData(): Plugin {
  return {
    name: "llmwiki-live-data",
    configureServer(server) {
      const {build, schedule} = createWikiData({
        projectRoot,
        onBuilt: () => server.ws.send({type: "custom", event: "llmwiki:data", data: {at: Date.now()}}),
      });
      const wikiRoot = resolve(projectRoot, "wiki");
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/query")) return next();
        const url = new URL(req.url, "http://127.0.0.1");
        try {
          const body = JSON.stringify(runWikiQuery(
            projectRoot, url.searchParams.get("q") ?? "", Number(url.searchParams.get("limit") || 50)));
          res.statusCode = 200;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          res.end(body);
        } catch (error) {
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({error: error instanceof Error ? error.message : String(error)}));
        }
      });
      server.watcher.add(wikiRoot);
      server.watcher.on("all", (_event, path) => {
        if (path.startsWith(wikiRoot) && path.endsWith(".json")) schedule(path);
      });
      build();
    },
  };
}

export default defineConfig({
  root: resolve(__dirname),
  plugins: [liveWikiData(), react(), tailwindcss()],
  resolve: { alias: { "@": resolve(__dirname, "src") } },
  publicDir: resolve(__dirname, "public"),
  build: { outDir: resolve(__dirname, "dist"), emptyOutDir: true },
  server: { host: true, port: 5173, strictPort: true },
});
