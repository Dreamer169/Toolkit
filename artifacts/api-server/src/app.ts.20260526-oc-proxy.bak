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
// v9.17: CORS 支持 credentials — 动态反射请求 origin，允许浏览器携带 Cookie/Authorization
// 注意：credentials:true 时 origin 不能是 "*"，必须精确匹配
app.use(cors({
  origin: (origin, callback) => {
    // origin 为 undefined = 服务端直调（curl/server-to-server），直接放行
    callback(null, origin ?? true);
  },
  credentials: true,
  allowedHeaders: [
    "Content-Type", "Authorization", "X-Requested-With",
    "Accept", "Origin", "X-Token",
  ],
  exposedHeaders: ["Content-Disposition", "X-Total-Count"],
  methods: ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
  maxAge: 86400, // OPTIONS 预检缓存 24h，减少浏览器预检次数
}));
app.use(express.urlencoded({ extended: true, limit: "4mb" }));

app.use("/api", tunnelRouter);
app.use(express.json({ limit: "4mb" }));
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

// v9.16: /api/v1/** → sub2api(:8080) 统一入口反代（浏览器只需连接 :8081 一个端口）
const SUB2API_TARGET = process.env["SUB2API_TARGET"] ?? "http://127.0.0.1:8080";
const _sub2apiProxy = createProxyMiddleware({
  target: SUB2API_TARGET,
  changeOrigin: true,
  // Express 挂载在 /api/v1 时会剥掉前缀，pathRewrite 把 /api/v1 补回去
  pathRewrite: (path: string) => `/api/v1${path}`,
  on: {
    // 明确透传认证相关 header（http-proxy-middleware 默认已转发，这里加白名单兜底）
    proxyReq: (proxyReq, req) => {
      const authHdr = (req as import("http").IncomingMessage).headers["authorization"];
      if (authHdr && !proxyReq.getHeader("authorization")) {
        proxyReq.setHeader("authorization", authHdr);
      }
      const tokenHdr = (req as import("http").IncomingMessage).headers["x-token"];
      if (tokenHdr && !proxyReq.getHeader("x-token")) {
        proxyReq.setHeader("x-token", tokenHdr);
      }
    },
    // 若 sub2api 自身也带了 CORS header，先移除，由 api-server cors() 统一管理
    proxyRes: (proxyRes) => {
      const drop = [
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-headers",
        "access-control-allow-methods",
        "access-control-max-age",
      ];
      for (const h of drop) delete proxyRes.headers[h];
    },
    error: (err, _req, res) => {
      logger.warn({ err: String(err) }, "[sub2api-proxy] upstream :8080 unavailable");
      const r = res as unknown as { headersSent?: boolean; writeHead?: (s: number, h: Record<string, string>) => void; end?: (b: string) => void };
      if (r.writeHead && !r.headersSent) {
        r.writeHead(502, { "content-type": "application/json" });
        r.end?.(JSON.stringify({ code: -1, message: "sub2api service unavailable" }));
      }
    },
  },
});
app.use("/api/v1", _sub2apiProxy);

// v9.15: 未匹配的 /api/** 路由直接返回 404，防止被 frontend proxy 转发到 Vite
// 再被 Vite proxy 转回 api-server 造成死循环（431 根本原因）
app.use("/api", (_req, res) => {
  res.status(404).json({ error: "API route not found" });
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
