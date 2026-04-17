import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { createProxyMiddleware, fixRequestBody } from "http-proxy-middleware";
import router from "./routes";
import gatewayRouter from "./routes/gateway";
import { logger } from "./lib/logger";
import path from "path";
import { fileURLToPath } from "url";
import { existsSync } from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

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
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api/gateway", gatewayRouter);

app.use("/api", router);

app.use(
  "/team-all-in-one",
  createProxyMiddleware({
    target: "http://localhost:5000",
    changeOrigin: true,
    pathRewrite: { "^/team-all-in-one": "" },
    on: {
      error: (_err, _req, res) => {
        (res as express.Response).status(502).send("ChatGPT 注册面板未启动，请稍后重试");
      },
    },
  }),
);

app.use(
  "/openai-pool",
  createProxyMiddleware({
    target: "http://localhost:8000",
    changeOrigin: true,
    pathRewrite: { "^/openai-pool": "" },
    on: {
      error: (_err, _req, res) => {
        (res as express.Response).status(502).send("OpenAI 账号池编排器未启动，请稍后重试");
      },
    },
  }),
);

app.use(
  "/api/fakemail",
  createProxyMiddleware({
    target: "http://localhost:6100",
    changeOrigin: true,
    pathRewrite: { "^/api/fakemail": "" },
    on: {
      error: (_err, _req, res) => {
        (res as express.Response).status(502).json({ success: false, error: "FakeMail 服务未启动" });
      },
    },
  }),
);

const frontendDist = path.resolve(__dirname, "../../ai-toolkit/dist/public");
if (existsSync(frontendDist)) {
  app.use(express.static(frontendDist));
  app.get("/{*path}", (_req, res) => {
    res.sendFile(path.join(frontendDist, "index.html"));
  });
}

export default app;
