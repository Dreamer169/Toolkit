import { Router } from "express";
import { logger } from "../lib/logger.js";

const router = Router();

interface CfEmailInstance {
  name: string;
  domain: string;
  apiUrl: string;
  frontendUrl: string;
  sitePassword: string;
  adminPassword: string;
}

const INSTANCES: CfEmailInstance[] = [
  {
    name: "jonjim",
    domain: "jonjim.eu.cc",
    apiUrl: "https://mail-api.jonjim.eu.cc",
    frontendUrl: "https://mail.jonjim.eu.cc",
    sitePassword: process.env["JONJIM_SITE_PASSWORD"] || "",
    adminPassword: process.env["JONJIM_ADMIN_PASSWORD"] || "",
  },
  {
    name: "hackerjim",
    domain: "hackerjim.eu.cc",
    apiUrl: "https://mail-api.hackerjim.eu.cc",
    frontendUrl: "https://mail.hackerjim.eu.cc",
    sitePassword: process.env["HACKERJIM_SITE_PASSWORD"] || "",
    adminPassword: process.env["HACKERJIM_ADMIN_PASSWORD"] || "",
  },
];

async function cfFetch(inst: CfEmailInstance, path: string, options: RequestInit = {}) {
  const url = `${inst.apiUrl}${path}`;
  const headers: Record<string, string> = {
    "x-custom-auth": inst.sitePassword,
    "x-admin-auth": inst.adminPassword,
    ...(options.headers as Record<string, string> || {}),
  };
  return fetch(url, { ...options, headers });
}

// GET /api/cf-email/instances — all instances with live stats
router.get("/cf-email/instances", async (_req, res) => {
  const results = await Promise.all(
    INSTANCES.map(async (inst) => {
      try {
        const [sr, statR] = await Promise.all([
          cfFetch(inst, "/open_api/settings"),
          cfFetch(inst, "/admin/statistics"),
        ]);
        const settings = sr.ok ? await sr.json() as Record<string, unknown> : null;
        const stats = statR.ok ? await statR.json() as Record<string, unknown> : null;
        return {
          name: inst.name,
          domain: inst.domain,
          frontendUrl: inst.frontendUrl,
          status: statR.ok ? "ok" : "error",
          title: settings?.title,
          needAuth: settings?.needAuth,
          stats,
        };
      } catch (e: unknown) {
        return { name: inst.name, domain: inst.domain, status: "unreachable", error: String(e) };
      }
    })
  );
  res.json({ instances: results });
});

// GET /api/cf-email/:name/statistics
router.get("/cf-email/:name/statistics", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const r = await cfFetch(inst, "/admin/statistics");
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

// GET /api/cf-email/:name/addresses
router.get("/cf-email/:name/addresses", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const { limit = "20", offset = "0" } = req.query as Record<string, string>;
  const r = await cfFetch(inst, `/admin/address?limit=${limit}&offset=${offset}`);
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

// POST /api/cf-email/:name/addresses
router.post("/cf-email/:name/addresses", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const { name, domain } = req.body as { name: string; domain?: string };
  const r = await cfFetch(inst, "/admin/new_address", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, domain: domain || inst.domain }),
  });
  const text = await r.text();
  res.status(r.status).json(r.ok ? JSON.parse(text) : { error: text });
});

// DELETE /api/cf-email/:name/addresses/:id
router.delete("/cf-email/:name/addresses/:id", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const r = await cfFetch(inst, `/admin/delete_address/${req.params["id"]}`, { method: "DELETE" });
  const text = await r.text();
  res.status(r.status).json(r.ok ? JSON.parse(text) : { error: text });
});

// DELETE /api/cf-email/:name/addresses/:id/inbox
router.delete("/cf-email/:name/addresses/:id/inbox", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const r = await cfFetch(inst, `/admin/clear_inbox/${req.params["id"]}`, { method: "DELETE" });
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

// GET /api/cf-email/:name/mails
router.get("/cf-email/:name/mails", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const { limit = "20", offset = "0", address } = req.query as Record<string, string>;
  const qs = new URLSearchParams({ limit, offset, ...(address ? { address } : {}) });
  const r = await cfFetch(inst, `/admin/mails?${qs}`);
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

// DELETE /api/cf-email/:name/mails/:id
router.delete("/cf-email/:name/mails/:id", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const r = await cfFetch(inst, `/admin/mails/${req.params["id"]}`, { method: "DELETE" });
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

// POST /api/cf-email/:name/addresses/:id/reset-password
router.post("/cf-email/:name/addresses/:id/reset-password", async (req, res) => {
  const inst = INSTANCES.find(i => i.name === req.params["name"]);
  if (!inst) return res.status(404).json({ error: "instance not found" });
  const r = await cfFetch(inst, `/admin/address/${req.params["id"]}/reset_password`, { method: "POST" });
  res.status(r.status).json(r.ok ? await r.json() : { error: await r.text() });
});

export default router;
