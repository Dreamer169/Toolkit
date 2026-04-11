import { Router, type IRouter } from "express";

const router: IRouter = Router();

const MAILTM_BASE = "https://api.mail.tm";

async function mailtmFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${MAILTM_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string> ?? {}),
    },
  });
  const text = await res.text();
  try {
    return { ok: res.ok, status: res.status, data: JSON.parse(text) };
  } catch {
    return { ok: res.ok, status: res.status, data: text };
  }
}

router.get("/tools/email/domains", async (req, res) => {
  try {
    const result = await mailtmFetch("/domains");
    const domains = result.data?.["hydra:member"] ?? [];
    res.json({ success: true, domains: domains.map((d: { domain: string }) => d.domain) });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/email/create", async (req, res) => {
  try {
    const { address, password } = req.body as { address?: string; password?: string };
    if (!address || !password) {
      res.status(400).json({ success: false, error: "address 和 password 不能为空" });
      return;
    }
    const result = await mailtmFetch("/accounts", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    if (!result.ok) {
      res.json({ success: false, error: result.data?.detail ?? result.data ?? "创建失败" });
      return;
    }
    const tokenResult = await mailtmFetch("/token", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    res.json({
      success: true,
      account: { address, id: result.data.id },
      token: tokenResult.data?.token,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/email/token", async (req, res) => {
  try {
    const { address, password } = req.body as { address?: string; password?: string };
    if (!address || !password) {
      res.status(400).json({ success: false, error: "address 和 password 不能为空" });
      return;
    }
    const result = await mailtmFetch("/token", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    if (!result.ok) {
      res.json({ success: false, error: result.data?.detail ?? "登录失败" });
      return;
    }
    res.json({ success: true, token: result.data.token });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/email/messages", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    if (!token) {
      res.status(400).json({ success: false, error: "缺少 x-mail-token 请求头" });
      return;
    }
    const result = await mailtmFetch("/messages", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!result.ok) {
      res.json({ success: false, error: "获取邮件失败，Token 可能已过期" });
      return;
    }
    const messages = (result.data?.["hydra:member"] ?? []).map((m: {
      id: string;
      from: { address: string; name: string };
      subject: string;
      intro: string;
      createdAt: string;
      seen: boolean;
    }) => ({
      id: m.id,
      from: m.from,
      subject: m.subject,
      intro: m.intro,
      createdAt: m.createdAt,
      seen: m.seen,
    }));
    res.json({ success: true, messages, total: result.data?.["hydra:totalItems"] ?? 0 });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/email/messages/:id", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    const { id } = req.params as { id: string };
    if (!token) {
      res.status(400).json({ success: false, error: "缺少 x-mail-token" });
      return;
    }
    const result = await mailtmFetch(`/messages/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!result.ok) {
      res.json({ success: false, error: "获取邮件详情失败" });
      return;
    }
    res.json({ success: true, message: result.data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.delete("/tools/email/account", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    const { accountId } = req.body as { accountId?: string };
    if (!token || !accountId) {
      res.status(400).json({ success: false, error: "缺少参数" });
      return;
    }
    await mailtmFetch(`/accounts/${accountId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    res.json({ success: true });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/key-check", async (req, res) => {
  try {
    const { platform, key } = req.body as { platform?: string; key?: string };
    if (!platform || !key) {
      res.status(400).json({ success: false, error: "platform 和 key 不能为空" });
      return;
    }

    let valid = false;
    let info: Record<string, unknown> = {};
    let error = "";

    if (platform === "openai") {
      try {
        const r = await fetch("https://api.openai.com/v1/models", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { data?: Array<{ id: string }> ; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "claude") {
      try {
        const r = await fetch("https://api.anthropic.com/v1/models", {
          headers: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
          },
        });
        const data = await r.json() as { data?: Array<{ id: string }>; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "gemini") {
      try {
        const r = await fetch(
          `https://generativelanguage.googleapis.com/v1/models?key=${key}`
        );
        const data = await r.json() as { models?: Array<{ name: string }>; error?: { message: string } };
        if (r.ok && data.models) {
          valid = true;
          info = { modelCount: data.models.length, firstModel: data.models[0]?.name };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "openai-token") {
      try {
        const r = await fetch("https://api.openai.com/v1/me", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { email?: string; name?: string; error?: { message: string } };
        if (r.ok && data.email) {
          valid = true;
          info = { email: data.email, name: data.name };
        } else {
          error = data.error?.message ?? "无效的 Token";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else {
      res.status(400).json({ success: false, error: "不支持的平台" });
      return;
    }

    res.json({ success: true, valid, info, error });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/token-batch-check", async (req, res) => {
  try {
    const { tokens, platform } = req.body as { tokens?: string[]; platform?: string };
    if (!tokens || !Array.isArray(tokens) || tokens.length === 0) {
      res.status(400).json({ success: false, error: "tokens 不能为空" });
      return;
    }
    const limited = tokens.slice(0, 20);
    const results = await Promise.allSettled(
      limited.map(async (token) => {
        const trimmed = token.trim();
        if (!trimmed) return { token: trimmed, valid: false, error: "空值" };
        try {
          const endpoint =
            platform === "claude"
              ? "https://api.anthropic.com/v1/models"
              : "https://api.openai.com/v1/models";
          const headers: Record<string, string> =
            platform === "claude"
              ? { "x-api-key": trimmed, "anthropic-version": "2023-06-01" }
              : { Authorization: `Bearer ${trimmed}` };
          const r = await fetch(endpoint, { headers });
          const data = await r.json() as { data?: unknown[]; models?: unknown[]; error?: { message: string } };
          if (r.ok && (data.data || data.models)) {
            return { token: trimmed.slice(0, 12) + "...", valid: true };
          }
          return {
            token: trimmed.slice(0, 12) + "...",
            valid: false,
            error: data.error?.message ?? "无效",
          };
        } catch (e: unknown) {
          return { token: trimmed.slice(0, 12) + "...", valid: false, error: String(e) };
        }
      })
    );
    const output = results.map((r) =>
      r.status === "fulfilled" ? r.value : { valid: false, error: "请求失败" }
    );
    res.json({
      success: true,
      results: output,
      summary: {
        total: output.length,
        valid: output.filter((r) => r.valid).length,
        invalid: output.filter((r) => !r.valid).length,
      },
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

export default router;
