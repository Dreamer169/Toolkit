import app from "./app";
import { logger } from "./lib/logger";
import { selfRegister } from "./routes/tunnel";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const server = app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
  logger.info(
    { url: `http://localhost:${port}` },
    "Stream relay URL (HTTP poll: /api/stream/open|read|write)",
  );
  setTimeout(selfRegister, 3_000).unref();
});

server.on("error", (err) => {
  logger.error({ err }, "Server error");
});
