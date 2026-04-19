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

const jsonErrorHandler: ErrorRequestHandler = (err, _req, res, next) => {
  if (err.type === "entity.parse.failed") {
    res.status(400).json({ error: "Invalid JSON body" });
  } else {
    next(err);
  }
};
app.use(jsonErrorHandler);

export default app;
