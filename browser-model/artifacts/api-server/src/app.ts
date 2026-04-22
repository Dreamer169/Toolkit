import express, { type Express } from "express";
import cors from "cors";
import path from "node:path";
import fs from "node:fs";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) { return { id: req.id, method: req.method, url: req.url?.split("?")[0] }; },
      res(res) { return { statusCode: res.statusCode }; },
    },
  }),
);
app.use(cors());

const skipForProxy = (mw: express.RequestHandler): express.RequestHandler => (req, res, next) => {
  if (req.path === "/api/proxy") return next();
  return mw(req, res, next);
};
app.use(skipForProxy(express.json()));
app.use(skipForProxy(express.urlencoded({ extended: true })));

app.use("/api", router);

// Serve built frontend (vite output) + SPA fallback
const FRONTEND_DIR = process.env["FRONTEND_DIR"]
  || path.resolve(process.cwd(), "public");
if (fs.existsSync(FRONTEND_DIR)) {
  logger.info({ FRONTEND_DIR }, "Serving frontend");
  app.use(express.static(FRONTEND_DIR, { index: false, maxAge: "1h" }));
  app.get(/^(?!\/api\/).*/, (_req, res, next) => {
    const idx = path.join(FRONTEND_DIR, "index.html");
    if (!fs.existsSync(idx)) return next();
    res.setHeader("cache-control", "no-store");
    res.sendFile(idx);
  });
} else {
  logger.warn({ FRONTEND_DIR }, "Frontend dir not found, only /api will be served");
}

export default app;
