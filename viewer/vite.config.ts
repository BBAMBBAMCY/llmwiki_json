import { defineConfig } from "vite";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { cpSync, existsSync, readFileSync, statSync } from "node:fs";
import { extname, resolve, sep } from "node:path";
import { createWikiData, resolveLayout, runWikiQuery } from "./scripts/wiki-data";

const projectRoot = resolve(__dirname, "..");
// 데이터 root 는 llmwiki_paths 규칙 그대로다 (LLMWIKI_ROOT → 위로 찾은 llmwiki.lock → 이 저장소).
const layout = resolveLayout(projectRoot);
const TYPES: Record<string, string> = {".json": "application/json; charset=utf-8", ".jsonl": "application/x-ndjson"};

/** 분리 모드의 파생물은 `<data>/.llmwiki/viewer-data` 에 있다 — 개발 서버는 `/data/*` 를 거기서 주고,
 *  `vite build` 는 그 내용을 `dist/data` 로 복사한다. 단일 repo 모드에서는 public/data 가 그 자리다. */
function splitViewerData(): Plugin {
  const base = resolve(layout.viewer_data);
  return {
    name: "llmwiki-split-data",
    apply: () => layout.mode === "split",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/data/")) return next();
        const rel = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname.slice("/data/".length));
        const file = resolve(base, rel);
        if (!file.startsWith(base + sep) || !existsSync(file) || !statSync(file).isFile()) return next();
        res.statusCode = 200;
        res.setHeader("Content-Type", TYPES[extname(file)] ?? "application/octet-stream");
        res.setHeader("Cache-Control", "no-store");
        res.end(readFileSync(file));
      });
    },
    writeBundle(options) {
      if (existsSync(base)) cpSync(base, resolve(options.dir ?? resolve(__dirname, "dist"), "data"), {recursive: true});
    },
  };
}

/** 개발 서버에서 wiki 변경을 감시해 파생물을 다시 만들고 열린 화면에 통지한다.
 *  build 큐는 정적 배포용 감시자와 같은 모듈을 쓴다. */
function liveWikiData(): Plugin {
  return {
    name: "llmwiki-live-data",
    configureServer(server) {
      const {build, schedule} = createWikiData({
        projectRoot,
        layout,
        onBuilt: () => server.ws.send({type: "custom", event: "llmwiki:data", data: {at: Date.now()}}),
      });
      const wikiRoot = layout.wiki;
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/query")) return next();
        const url = new URL(req.url, "http://127.0.0.1");
        try {
          const body = JSON.stringify(runWikiQuery(
            projectRoot, url.searchParams.get("q") ?? "", Number(url.searchParams.get("limit") || 50), layout));
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
  plugins: [liveWikiData(), splitViewerData(), react(), tailwindcss()],
  resolve: { alias: { "@": resolve(__dirname, "src") } },
  publicDir: resolve(__dirname, "public"),
  build: { outDir: resolve(__dirname, "dist"), emptyOutDir: true },
  server: { host: true, port: 5173, strictPort: true },
});
