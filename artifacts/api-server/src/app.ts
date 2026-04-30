import express, { type Express, type ErrorRequestHandler } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { createProxyMiddleware } from "http-proxy-middleware";
import router from "./routes";
import tunnelRouter from "./routes/tunnel";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.urlencoded({ extended: true }));

app.use("/api", tunnelRouter);
app.use(express.json());
app.use("/api", router);

app.get("/health", (_req, res) => {
  res.json({ ok: true, name: "replit-subnode", tunnel: true });
});

app.get("/v1/models", (_req, res) => {
  res.json({
    object: "list",
    data: [{ id: "tunnel-proxy", object: "model", owned_by: "subnode" }],
  });
});

// v8.83 — frontend 反代: 所有未被上面路由消费的请求转发到本机前端 dev server (:3000)
// 这样 api-server (:8081) 单一公网入口同时承载 API + 前端，省一条 frp 隧道
const FRONTEND_TARGET = process.env["FRONTEND_PROXY_TARGET"] ?? "http://127.0.0.1:3000";
const _frontendProxy = createProxyMiddleware({
  target: FRONTEND_TARGET,
  changeOrigin: true,
  ws: true,
  xfwd: true,
  on: {
    error: (err, _req, res) => {
      logger.warn({ err: String(err) }, "[frontend-proxy] upstream :3000 unavailable");
      const r = res as unknown as { headersSent?: boolean; writeHead?: (s: number, h: Record<string, string>) => void; end?: (b: string) => void };
      if (r.writeHead && !r.headersSent) {
        r.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
        r.end?.("frontend dev server (:3000) is not running");
      }
    },
  },
});
app.use(_frontendProxy);

const jsonErrorHandler: ErrorRequestHandler = (err, _req, res, next) => {
  if (err.type === "entity.parse.failed") {
    res.status(400).json({ error: "Invalid JSON body" });
  } else {
    next(err);
  }
};
app.use(jsonErrorHandler);

export default app;
