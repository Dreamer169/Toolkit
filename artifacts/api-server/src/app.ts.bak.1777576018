import express, { type Express, type ErrorRequestHandler } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
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


  app.get("/", (_req, res) => {
    res.setHeader("Content-Type", "text/html");
    res.send([
      '<!DOCTYPE html><html lang="zh">',
      '<head><meta charset="UTF-8"><title>AI Proxy Gateway</title>',
      '<style>',
      'body{background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:40px;max-width:700px;margin:0 auto}',
      'h1{color:#a78bfa;margin-bottom:4px}',
      'code{background:#1e293b;border:1px solid #334155;border-radius:6px;padding:2px 8px;color:#818cf8}',
      '.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin:16px 0}',
      '.g{color:#34d399}.m{color:#94a3b8;margin:0}',
      '</style></head><body>',
      '<h1>&#9889; AI Proxy Gateway</h1>',
      '<p class="m">OpenAI Compatible · Sub-Node Connection Interface</p>',
      '<div class="card">',
      '<p class="g">&#10003; Service Running</p>',
      '<p><code>GET  /health</code></p>',
      '<p><code>GET  /v1/models</code></p>',
      '<p><code>POST /v1/chat/completions</code></p>',
      '<p><code>GET  /api/healthz</code></p>',
      '<p><code>GET  /api/gateway/nodes/status</code></p>',
      '</div>',
      '<div class="card">Base URL: <code>https://strive-phoney-vocalize.ngrok-free.dev</code></div>',
      '</body></html>',
    ].join(""));
  });
  
const jsonErrorHandler: ErrorRequestHandler = (err, _req, res, next) => {
  if (err.type === "entity.parse.failed") {
    res.status(400).json({ error: "Invalid JSON body" });
  } else {
    next(err);
  }
};
app.use(jsonErrorHandler);

export default app;
