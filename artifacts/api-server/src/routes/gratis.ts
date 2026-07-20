import { Router, type IRouter } from "express";

const router: IRouter = Router();

// 默认值必须与 gratis-register 的 GRATIS_ADMIN_PASSWORD 一致（当前为 yu123456）
// GRATIS_TARGET 必须与 gratis-register 实际监听端口一致（当前为 8093）
const GRATIS_TARGET = process.env["GRATIS_TARGET"] ?? "http://127.0.0.1:8093";
const GRATIS_ADMIN_TOKEN = process.env["GRATIS_ADMIN_TOKEN"] ?? "yu123456";

async function forward(
  path: string,
  method: string,
  body: unknown,
  needsAuth: boolean,
): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (needsAuth) headers["X-Admin-Token"] = GRATIS_ADMIN_TOKEN;
  const init: RequestInit = { method, headers };
  if (method !== "GET" && method !== "DELETE") {
    init.body = JSON.stringify(body ?? {});
  }
  try {
    const r = await fetch(`${GRATIS_TARGET}${path}`, init);
    const text = await r.text();
    let json: unknown;
    try {
      json = text ? JSON.parse(text) : {};
    } catch {
      json = { raw: text };
    }
    return { status: r.status, json };
  } catch (err) {
    return {
      status: 502,
      json: { error: "gratis-register unavailable", detail: String(err) },
    };
  }
}

// 公开健康检查 — 不需要管理员 token
router.get("/health", async (_req, res) => {
  const { status, json } = await forward("/health", "GET", null, false);
  res.status(status).json(json);
});

// 管理 API — 统一转发到本机 gratis-register:8093/admin/*，服务端注入 X-Admin-Token
router.all("/admin/*path", async (req, res) => {
  const suffix = req.path.replace(/^\/admin/, "");
  const { status, json } = await forward(`/admin${suffix}`, req.method, req.body, true);
  res.status(status).json(json);
});

router.all("/admin", async (req, res) => {
  const { status, json } = await forward("/admin", req.method, req.body, true);
  res.status(status).json(json);
});

export default router;
