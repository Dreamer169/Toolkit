import { Router, type IRouter, type Request } from "express";
import { randomUUID } from "crypto";

const router: IRouter = Router();

type SkillId = "cursor" | "outlook" | "mail" | "tokens" | "data" | "monitor" | "workflow" | "pool" | "captcha" | "waf-bypass" | "novproxy" | "sms";
type ParamType = "string" | "number" | "boolean" | "array" | "object";
type Safety = "safe" | "requires-confirmation";
type ToolParam = { name: string; type: ParamType; description: string; required?: boolean; default?: unknown; enum?: unknown[]; min?: number; max?: number };
type Tool = {
  name: string;
  label: string;
  description: string;
  skillId: SkillId;
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
  safety: Safety;
  tab: string;
  keywords: string[];
  parameters: ToolParam[];
  execute?: (ctx: { req: Request; tool: Tool; params: Record<string, unknown> }) => Promise<unknown>;
};
type Skill = { id: SkillId; title: string; tab: string; purpose: string; keywords: string[]; nextSteps: string[] };
type Session = { skillId: SkillId; messages: Array<{ role: "user" | "assistant"; content: string }>; updatedAt: number };

class ToolRegistry {
  private tools = new Map<string, Tool>();

  register(tool: Tool) {
    if (this.tools.has(tool.name)) throw new Error(`Tool 已存在: ${tool.name}`);
    this.tools.set(tool.name, tool);
    return tool;
  }

  get(name: string) {
    return this.tools.get(name);
  }

  all() {
    return [...this.tools.values()];
  }

  bySkill(skillId: SkillId) {
    return this.all().filter(tool => tool.skillId === skillId);
  }

  validate(toolName: string, input: Record<string, unknown> = {}) {
    const tool = this.get(toolName);
    if (!tool) return { valid: false, issues: [`Tool 不存在: ${toolName}`], normalizedParams: input };
    const issues: string[] = [];
    const normalizedParams: Record<string, unknown> = {};

    for (const param of tool.parameters) {
      const raw = input[param.name] ?? param.default;
      if ((raw === undefined || raw === null || raw === "") && param.required) {
        issues.push(`${param.name} 不能为空`);
        continue;
      }
      if (raw === undefined || raw === null || raw === "") continue;
      const value = normalizeValue(raw, param.type);
      if (!matchesType(value, param.type)) {
        issues.push(`${param.name} 必须是 ${param.type}`);
        continue;
      }
      if (param.enum?.length && !param.enum.includes(value)) issues.push(`${param.name} 必须是 ${param.enum.join(" / ")}`);
      if (typeof value === "number") {
        if (typeof param.min === "number" && value < param.min) issues.push(`${param.name} 不能小于 ${param.min}`);
        if (typeof param.max === "number" && value > param.max) issues.push(`${param.name} 不能大于 ${param.max}`);
      }
      normalizedParams[param.name] = value;
    }

    return { valid: issues.length === 0, issues, normalizedParams };
  }
}

const registry = new ToolRegistry();
const sessions = new Map<string, Session>();

const skills: Skill[] = [
  { id: "cursor", title: "Cursor 自动注册", tab: "cursor-register", purpose: "负责 Cursor 账号自动注册、任务查询、失败任务清理、机器 ID 和代理辅助配置。", keywords: ["cursor", "注册", "自动注册", "机器", "账号创建"], nextSteps: ["进入 Cursor 自动注册页", "确认注册数量、邮箱来源、代理策略", "启动任务后到实时监控页跟踪状态"] },
  { id: "outlook", title: "Outlook 工作流", tab: "outlook", purpose: "负责 Outlook 账号注册、OAuth 授权、账号有效性验证、批量清洗和邮件读取。", keywords: ["outlook", "hotmail", "oauth", "授权", "清洗", "验证账号", "邮件读取"], nextSteps: ["进入 Outlook 工作流页", "先检查验证码和代理配置", "批量清洗前先 dry_run 或小批量验证"] },
  { id: "mail", title: "邮件中心", tab: "mail-center", purpose: "负责临时邮箱、批量邮箱、Outlook 邮件三列阅读与验证码提取。", keywords: ["邮件", "收件箱", "验证码", "临时邮箱", "mail", "inbox", "otp"], nextSteps: ["进入邮件中心", "选择账号与文件夹", "需要验证码时使用搜索和预览快速提取"] },
  { id: "tokens", title: "Key / Token 检测", tab: "keycheck", purpose: "负责 OpenAI、Claude、Gemini 等密钥检测、批量检测和 Token 转发管理。", keywords: ["key", "token", "密钥", "检测", "余额", "批量检测", "sub2api"], nextSteps: ["进入 Key 验证或批量检测页", "粘贴待测 Key/Token", "检测后导出有效结果"] },
  { id: "data", title: "数据管理中心", tab: "data-manager", purpose: "负责账号、身份、邮箱、代理、验证码配置等持久化数据管理。", keywords: ["数据", "账号", "身份", "代理", "持久化", "导入", "导出", "数据库"], nextSteps: ["进入数据管理中心", "选择要管理的数据类型", "导入或删除前先确认影响范围"] },
  { id: "monitor", title: "实时监控", tab: "monitor", purpose: "负责查看任务状态、成功率、失败原因和系统运行情况。", keywords: ["监控", "状态", "任务", "日志", "失败", "成功率", "进度"], nextSteps: ["进入实时监控页", "按任务类型过滤", "对失败任务查看错误原因后重试"] },
  { id: "workflow", title: "完整工作流", tab: "full-workflow", purpose: "负责串联代理、身份、邮箱、注册、验证、入库和结果导出。", keywords: ["工作流", "一键", "全流程", "自动化", "串联", "pipeline"], nextSteps: ["进入完整工作流页", "按顺序检查代理、邮箱、验证码、注册配置", "先小批量跑通再扩大数量"] },
  { id: "pool", title: "OpenAI 账号池编排器", tab: "openai-pool", purpose: "负责 OpenAI 账号池批量编排、账号状态维护和池化结果管理。", keywords: ["账号池", "openai", "pool", "池", "编排", "chatgpt", "gpt", "gpt账号", "team", "team注册"], nextSteps: ["进入 GPT/账号池编排器", "确认邮箱、代理、验证码和注册数量", "先走 1 个账号测试，成功后再扩大批量"] },
  { id: "captcha", title: "验证码识别", tab: "captcha", purpose: "负责文字点选验证码(YOLO+ONNX)和数字验证码(CNN)识别。", keywords: ["验证码", "captcha", "识别", "文字点选", "cnn", "yolo", "onnx", "图片验证码", "hcaptcha"], nextSteps: ["进入验证码识别页", "选择文字点选或数字验证码模式", "上传验证码图片后点击识别"] },
  { id: "waf-bypass", title: "CF WAF 绕过", tab: "waf-bypass", purpose: "负责使用 pydoll-python 零 WebDriver 绕过 Cloudflare WAF / Turnstile / Managed Challenge。", keywords: ["waf", "cloudflare", "cf", "绕过", "bypass", "turnstile", "pydoll", "爬取", "scrape"], nextSteps: ["进入 CF WAF 绕过工具页", "确认 pydoll 服务状态 :8766", "输入目标 URL 后点击开始绕过"] },
  { id: "novproxy", title: "Novproxy 工作流", tab: "novproxy", purpose: "负责 novproxy 代理账号注册、登录、流量诊断和 pydoll 自动化注册流程。", keywords: ["novproxy", "代理", "注册", "proxy", "流量", "socks5", "pydoll注册", "登录", "诊断"], nextSteps: ["进入 Novproxy 工作流页", "先诊断已有账号状态", "注册时使用 Gmail 邮箱（Outlook 无法收验证码）"] },
  { id: "sms", title: "短信接收中心", tab: "sms-center", purpose: "负责从 jiemahao.com / smsreceivefree.xyz 获取免费手机号码并接收短信验证码。", keywords: ["sms", "短信", "手机号", "验证码", "接收短信", "jiemahao", "smsreceivefree", "号码"], nextSteps: ["进入短信接收中心", "选择来源后点击号码", "等待 30~60 秒绕过 Cloudflare 后读取短信"] }
];

const param = {
  count: { name: "count", type: "number" as ParamType, description: "任务数量", required: true, default: 1, min: 1, max: 100 },
  jobId: { name: "jobId", type: "string" as ParamType, description: "任务 ID", required: true },
  ids: { name: "ids", type: "array" as ParamType, description: "账号 ID 列表", required: true, default: [] },
  dryRun: { name: "dry_run", type: "boolean" as ParamType, description: "只预览影响，不真正修改", default: true },
  proxy: { name: "proxy", type: "string" as ParamType, description: "可选代理地址" },
  email: { name: "email", type: "string" as ParamType, description: "可选邮箱账号" },
  platform: { name: "platform", type: "string" as ParamType, description: "平台", required: true, default: "openai", enum: ["openai", "anthropic", "gemini", "azure", "deepseek"] },
  key: { name: "key", type: "string" as ParamType, description: "待检测 Key", required: true },
  tokens: { name: "tokens", type: "array" as ParamType, description: "待批量检测 Token 列表", required: true, default: [] },
  dataType: { name: "type", type: "string" as ParamType, description: "数据类型", required: true, default: "accounts" },
  folder: { name: "folder", type: "string" as ParamType, description: "邮件文件夹", default: "inbox" }
};

function add(tool: Omit<Tool, "execute">) {
  registry.register({ ...tool, execute: executeHttpTool });
}

add({ name: "cursor.register.start", label: "启动 Cursor 注册任务", description: "创建 Cursor 账号自动注册任务", skillId: "cursor", method: "POST", path: "/api/tools/cursor/register", safety: "requires-confirmation", parameters: [param.count, param.proxy, param.email], tab: "cursor-register", keywords: ["cursor", "注册", "启动", "创建"] });
add({ name: "cursor.register.status", label: "查询 Cursor 注册任务", description: "按 jobId 查询 Cursor 注册任务状态", skillId: "cursor", method: "GET", path: "/api/tools/cursor/register/:jobId", safety: "safe", parameters: [param.jobId], tab: "cursor-register", keywords: ["cursor", "查询", "状态", "任务"] });
add({ name: "cursor.register.cancel", label: "取消 Cursor 注册任务", description: "取消指定 Cursor 注册任务", skillId: "cursor", method: "DELETE", path: "/api/tools/cursor/register/:jobId", safety: "requires-confirmation", parameters: [param.jobId], tab: "cursor-register", keywords: ["cursor", "取消", "删除", "停止"] });
add({ name: "cursor.machine.generate", label: "生成机器 ID", description: "生成 Cursor 机器 ID 辅助数据", skillId: "cursor", method: "GET", path: "/api/tools/machine-id/generate", safety: "safe", parameters: [], tab: "machine-reset", keywords: ["机器", "machine", "id"] });
add({ name: "outlook.register.start", label: "启动 Outlook 注册任务", description: "创建 Outlook 账号注册任务", skillId: "outlook", method: "POST", path: "/api/tools/outlook/register", safety: "requires-confirmation", parameters: [param.count, param.proxy], tab: "outlook", keywords: ["outlook", "注册", "hotmail"] });
add({ name: "outlook.register.status", label: "查询 Outlook 注册任务", description: "按 jobId 查询 Outlook 注册任务状态", skillId: "outlook", method: "GET", path: "/api/tools/outlook/register/:jobId", safety: "safe", parameters: [param.jobId], tab: "outlook", keywords: ["outlook", "查询", "状态"] });
add({ name: "outlook.accounts.verify", label: "批量验证 Outlook 账号", description: "验证 Outlook 账号有效性", skillId: "outlook", method: "POST", path: "/api/tools/outlook/verify-accounts", safety: "safe", parameters: [param.ids], tab: "outlook", keywords: ["outlook", "验证", "有效", "清洗"] });
add({ name: "outlook.oauth.batch", label: "批量 OAuth 授权", description: "启动 Outlook 批量 OAuth 授权", skillId: "outlook", method: "POST", path: "/api/tools/outlook/batch-oauth/start", safety: "requires-confirmation", parameters: [param.ids], tab: "outlook", keywords: ["oauth", "授权", "批量"] });
add({ name: "outlook.accounts.purgeInvalid", label: "清洗无效 Outlook 账号", description: "清理无效 Outlook 账号，默认 dry_run", skillId: "outlook", method: "POST", path: "/api/tools/outlook/purge-invalid", safety: "requires-confirmation", parameters: [param.dryRun], tab: "outlook", keywords: ["清洗", "删除", "无效", "purge"] });
add({ name: "mail.temp.create", label: "创建临时邮箱", description: "创建临时邮箱地址", skillId: "mail", method: "POST", path: "/api/tools/email/create", safety: "safe", parameters: [], tab: "mail-center", keywords: ["临时邮箱", "邮件", "创建"] });
add({ name: "mail.temp.messages", label: "读取临时邮箱邮件", description: "读取临时邮箱收件箱", skillId: "mail", method: "GET", path: "/api/tools/email/messages", safety: "safe", parameters: [], tab: "mail-center", keywords: ["邮件", "验证码", "临时邮箱", "读取"] });
add({ name: "mail.outlook.messages", label: "读取 Outlook 邮件", description: "读取 Outlook 邮箱邮件", skillId: "mail", method: "POST", path: "/api/tools/outlook/messages", safety: "safe", parameters: [param.folder], tab: "mail-center", keywords: ["outlook", "邮件", "验证码", "读取"] });
add({ name: "token.key.check", label: "单个 Key 检测", description: "检测单个 API Key 状态", skillId: "tokens", method: "POST", path: "/api/tools/key-check", safety: "safe", parameters: [param.platform, param.key], tab: "keycheck", keywords: ["key", "密钥", "检测", "余额"] });
add({ name: "token.batch.check", label: "批量 Token 检测", description: "批量检测 Token 有效性", skillId: "tokens", method: "POST", path: "/api/tools/token-batch-check", safety: "safe", parameters: [param.platform, param.tokens], tab: "tokencheck", keywords: ["token", "批量", "检测"] });
add({ name: "data.list", label: "查询数据", description: "查询数据管理中心记录", skillId: "data", method: "GET", path: "/api/data/:type", safety: "safe", parameters: [param.dataType], tab: "data-manager", keywords: ["数据", "查询", "列表"] });
add({ name: "data.import", label: "导入数据", description: "导入账号、代理或配置数据", skillId: "data", method: "POST", path: "/api/data/:type/import", safety: "requires-confirmation", parameters: [param.dataType], tab: "data-manager", keywords: ["数据", "导入"] });
add({ name: "monitor.overview", label: "查看任务总览", description: "查看当前任务和系统状态", skillId: "monitor", method: "GET", path: "/api/tools/monitor/overview", safety: "safe", parameters: [], tab: "monitor", keywords: ["监控", "状态", "总览"] });
add({ name: "workflow.plan", label: "生成工作流检查清单", description: "按完整工作流返回准备事项", skillId: "workflow", method: "GET", path: "/api/agent/workflow/checklist", safety: "safe", parameters: [], tab: "full-workflow", keywords: ["工作流", "清单", "准备"] });
add({ name: "pool.open", label: "打开账号池编排器", description: "进入 OpenAI 账号池编排器", skillId: "pool", method: "GET", path: "/openai-pool", safety: "safe", parameters: [], tab: "openai-pool", keywords: ["账号池", "openai", "pool"] });
add({ name: "openai.pool.status", label: "查看 OpenAI 账号池状态", description: "读取 OpenAI Pool Orchestrator 当前任务、Token 和运行状态", skillId: "pool", method: "GET", path: "/openai-pool/api/status", safety: "safe", parameters: [], tab: "openai-pool", keywords: ["openai", "pool", "账号池", "状态", "gpt"] });
add({ name: "gpt.team.start", label: "启动 1 个 GPT/Team 账号注册测试", description: "通过 ChatGPT Team 注册面板启动最小批量注册任务", skillId: "pool", method: "POST", path: "/team-all-in-one/api/start", safety: "requires-confirmation", parameters: [param.count, { name: "workers", type: "number" as ParamType, description: "并发线程数", required: true, default: 1, min: 1, max: 3 }, param.proxy], tab: "team-register", keywords: ["gpt", "chatgpt", "team", "注册", "走个", "账号", "测试"] });
add({ name: "gpt.team.status", label: "查看 GPT/Team 注册任务状态", description: "读取 ChatGPT Team 注册面板当前任务进度", skillId: "pool", method: "GET", path: "/team-all-in-one/api/status", safety: "safe", parameters: [], tab: "team-register", keywords: ["gpt", "chatgpt", "team", "状态", "进度", "任务"] });
add({ name: "gpt.team.accounts", label: "查看已注册 GPT/Team 账号", description: "读取 ChatGPT Team 面板已保存账号列表", skillId: "pool", method: "GET", path: "/team-all-in-one/api/accounts", safety: "safe", parameters: [], tab: "team-register", keywords: ["gpt", "chatgpt", "team", "账号", "结果", "列表"] });
add({ name: "captcha.text.health", label: "检查文字验证码服务", description: "检查 Text_select_captcha (port 8767) 服务状态", skillId: "captcha", method: "GET", path: "/api/tools/text-captcha/health", safety: "safe", parameters: [], tab: "captcha", keywords: ["验证码", "服务", "状态", "文字点选", "health"] });
add({ name: "captcha.text.identify", label: "识别文字点选验证码", description: "使用 YOLO+ONNX 识别文字点选验证码坐标", skillId: "captcha", method: "POST", path: "/api/tools/text-captcha/identify", safety: "safe", parameters: [{ name: "dataType", type: "number" as ParamType, description: "验证码类型 1|2", required: true, default: 1, min: 1, max: 2 }, { name: "imageSource", type: "string" as ParamType, description: "图片 URL 或 base64", required: true }], tab: "captcha", keywords: ["验证码", "识别", "文字点选", "坐标", "yolo"] });
add({ name: "waf.health", label: "检查 WAF 绕过服务", description: "检查 pydoll 服务 (port 8766) 运行状态", skillId: "waf-bypass", method: "GET", path: "/api/tools/waf/healthz", safety: "safe", parameters: [], tab: "waf-bypass", keywords: ["waf", "pydoll", "服务", "状态", "health"] });
add({ name: "waf.bypass", label: "CF WAF 绕过访问", description: "使用 pydoll 绕过 Cloudflare WAF 访问目标 URL", skillId: "waf-bypass", method: "POST", path: "/api/tools/waf/bypass", safety: "safe", parameters: [{ name: "url", type: "string" as ParamType, description: "目标 URL", required: true }, { name: "headless", type: "boolean" as ParamType, description: "无头模式", default: true }], tab: "waf-bypass", keywords: ["waf", "cloudflare", "绕过", "bypass", "访问", "url"] });
add({ name: "waf.scrape", label: "CF 隐蔽爬取提取", description: "绕过 CF 并用 CSS 选择器提取页面字段", skillId: "waf-bypass", method: "POST", path: "/api/tools/waf/scrape", safety: "safe", parameters: [{ name: "url", type: "string" as ParamType, description: "目标 URL", required: true }, { name: "selectors", type: "object" as ParamType, description: "CSS 选择器字典", required: true }], tab: "waf-bypass", keywords: ["爬取", "scrape", "css", "提取", "字段"] });
add({ name: "novproxy.register", label: "启动 Novproxy 注册任务", description: "通过 pydoll 自动注册 novproxy 账号（传入账号对数组）", skillId: "novproxy", method: "POST", path: "/api/tools/novproxy/register", safety: "requires-confirmation", parameters: [{ name: "accounts", type: "array" as ParamType, description: "账号对列表，每项 [email, password]", required: true, default: [] }, param.proxy, { name: "delay", type: "number" as ParamType, description: "每账号间隔秒数", default: 3, min: 1, max: 30 }], tab: "novproxy", keywords: ["novproxy", "注册", "pydoll", "代理"] });
add({ name: "novproxy.status", label: "查看 Novproxy 账号状态", description: "从数据库读取已保存的 novproxy 账号列表及代理凭据", skillId: "novproxy", method: "GET", path: "/api/tools/novproxy/status", safety: "safe", parameters: [], tab: "novproxy", keywords: ["novproxy", "账号", "状态", "代理", "列表", "凭据"] });
add({ name: "novproxy.diagnose", label: "诊断 Novproxy 账号", description: "诊断 novproxy 账号流量、邮件验证和代理状态（有 token 可免密）", skillId: "novproxy", method: "POST", path: "/api/tools/novproxy/diagnose", safety: "safe", parameters: [param.email, { name: "password", type: "string" as ParamType, description: "账号密码（有 token 时可省略）" }, { name: "token", type: "string" as ParamType, description: "已登录 token（有则免密）" }], tab: "novproxy", keywords: ["novproxy", "诊断", "diagnose", "流量", "激活"] });
add({ name: "sms.numbers", label: "获取免费手机号列表", description: "从 jiemahao.com 获取可用手机号码", skillId: "sms", method: "GET", path: "/api/tools/sms/numbers", safety: "safe", parameters: [{ name: "country", type: "string" as ParamType, description: "国家代码 all|us|gb|ca", default: "all" }], tab: "sms-center", keywords: ["短信", "手机号", "号码", "列表", "sms"] });
add({ name: "sms.messages", label: "读取短信验证码", description: "读取指定手机号收到的短信（jiemahao）", skillId: "sms", method: "POST", path: "/api/tools/sms/messages", safety: "safe", parameters: [{ name: "phoneId", type: "number" as ParamType, description: "手机号数字 ID（从 sms.numbers 返回的 id 字段）", required: true, min: 1 }], tab: "sms-center", keywords: ["短信", "验证码", "读取", "消息", "sms"] });
add({ name: "smsrf.numbers", label: "获取 smsreceivefree 号码", description: "从 smsreceivefree.xyz 获取可用手机号", skillId: "sms", method: "GET", path: "/api/tools/smsrf/numbers", safety: "safe", parameters: [], tab: "sms-center", keywords: ["短信", "号码", "smsreceivefree", "免费"] });
add({ name: "smsrf.messages", label: "读取 smsreceivefree 短信", description: "读取 smsreceivefree.xyz 指定号码的短信", skillId: "sms", method: "POST", path: "/api/tools/smsrf/messages", safety: "safe", parameters: [{ name: "phone", type: "string" as ParamType, description: "手机号码（如 +1...）", required: true }], tab: "sms-center", keywords: ["短信", "验证码", "读取", "smsreceivefree"] });

function normalizeValue(value: unknown, type: ParamType) {
  if (type === "number" && typeof value === "string" && value.trim()) return Number(value);
  if (type === "boolean" && typeof value === "string") return ["true", "1", "yes", "是"].includes(value.toLowerCase());
  if (type === "array" && typeof value === "string") return value.split(/\n|,/).map(item => item.trim()).filter(Boolean);
  return value;
}

function matchesType(value: unknown, type: ParamType) {
  if (type === "array") return Array.isArray(value);
  if (type === "object") return typeof value === "object" && value !== null && !Array.isArray(value);
  return typeof value === type;
}

function action(tool: Tool) {
  return {
    name: tool.name,
    label: tool.label,
    description: tool.description,
    method: tool.method,
    path: tool.path,
    safety: tool.safety,
    tab: tool.tab,
    parameters: tool.parameters,
    bodyHint: Object.fromEntries(tool.parameters.filter(item => item.default !== undefined).map(item => [item.name, item.default]))
  };
}

function scoreSkill(message: string, skill: Skill) {
  const text = message.toLowerCase();
  let score = skill.keywords.reduce((sum, keyword) => sum + (text.includes(keyword.toLowerCase()) ? 3 : 0), 0);
  for (const tool of registry.bySkill(skill.id)) {
    score += tool.keywords.reduce((sum, keyword) => sum + (text.includes(keyword.toLowerCase()) ? 2 : 0), 0);
    score += text.includes(tool.name.toLowerCase()) ? 5 : 0;
  }
  return score;
}

function selectSkill(message: string) {
  return [...skills].sort((a, b) => scoreSkill(message, b) - scoreSkill(message, a))[0] || skills[0];
}

function selectTools(message: string, skill: Skill) {
  const text = message.toLowerCase();
  const ranked = registry.bySkill(skill.id).map(tool => ({ tool, score: tool.keywords.reduce((sum, keyword) => sum + (text.includes(keyword.toLowerCase()) ? 2 : 0), 0) + (text.includes(tool.name.toLowerCase()) ? 5 : 0) })).sort((a, b) => b.score - a.score);
  const matched = ranked.filter(item => item.score > 0).map(item => item.tool);
  return (matched.length ? matched : ranked.map(item => item.tool)).slice(0, 5);
}

function buildReply(message: string, skill: Skill, tools: Tool[]) {
  const safe = tools.filter(tool => tool.safety === "safe");
  const guarded = tools.filter(tool => tool.safety === "requires-confirmation");
  const lines = [
    `已通过动态 Skill/Tool 注册表路由到「${skill.title}」。`,
    skill.purpose,
    `当前匹配到 ${tools.length} 个已注册 Tool：${tools.map(tool => tool.label).join("、")}。`,
    safe.length ? `可直接验证或读取：${safe.map(tool => tool.label).join("、")}。` : "",
    guarded.length ? `需要二次确认或 dry-run：${guarded.map(tool => tool.label).join("、")}。` : "",
    `建议下一步：${skill.nextSteps[0]}`
  ].filter(Boolean);
  if (/删除|清洗|purge|delete|取消|导入|启动|注册/.test(message)) lines.push("检测到可能改变数据或启动任务的意图，已进入参数校验/确认流程，不会静默执行。");
  return lines.join("\n");
}

async function executeHttpTool({ req, tool, params }: { req: Request; tool: Tool; params: Record<string, unknown> }) {
  if (tool.name === "workflow.plan") return { checklist: workflowChecklist() };
  if (tool.name === "pool.open") return { tab: tool.tab, message: "账号池编排器是前端页面入口，无需后端执行。" };
  const path = tool.path.replace(/:([A-Za-z0-9_]+)/g, (_, key) => encodeURIComponent(String(params[key] ?? "")));
  const init: RequestInit = { method: tool.method, headers: { "Content-Type": "application/json" } };
  if (!["GET", "DELETE"].includes(tool.method)) init.body = JSON.stringify(params);
  const response = await fetch(`${req.protocol}://${req.get("host")}${path}`, init);
  const text = await response.text();
  try {
    return { status: response.status, ok: response.ok, data: JSON.parse(text) };
  } catch {
    return { status: response.status, ok: response.ok, data: text };
  }
}

function workflowChecklist() {
  return ["确认代理池可用并完成小样本测试", "确认 Outlook/临时邮箱来源可读取验证码", "确认 Cursor 或目标平台注册参数", "先运行 1-3 个账号的小批量任务", "在实时监控查看失败原因和成功率", "成功后入库并导出可用结果"];
}

router.get("/agent/skills", (_req, res) => {
  res.json({ success: true, skills: skills.map(skill => ({ ...skill, tools: registry.bySkill(skill.id).map(action) })) });
});

router.get("/agent/tools", (_req, res) => {
  res.json({ success: true, tools: registry.all().map(action) });
});

router.post("/agent/validate", (req, res) => {
  const { toolName, params } = req.body as { toolName?: string; params?: Record<string, unknown> };
  if (!toolName) {
    res.status(400).json({ success: false, error: "toolName 不能为空" });
    return;
  }
  const tool = registry.get(toolName);
  const validation = registry.validate(toolName, params || {});
  res.status(validation.valid ? 200 : 400).json({ success: validation.valid, tool: tool ? action(tool) : null, ...validation });
});

router.post("/agent/execute", async (req, res) => {
  const { toolName, params, confirm, dryRun } = req.body as { toolName?: string; params?: Record<string, unknown>; confirm?: boolean; dryRun?: boolean };
  if (!toolName) {
    res.status(400).json({ success: false, error: "toolName 不能为空" });
    return;
  }
  const tool = registry.get(toolName);
  if (!tool) {
    res.status(404).json({ success: false, error: `Tool 不存在: ${toolName}` });
    return;
  }
  const validation = registry.validate(toolName, params || {});
  if (!validation.valid) {
    res.status(400).json({ success: false, tool: action(tool), ...validation });
    return;
  }
  if (dryRun || (tool.safety === "requires-confirmation" && !confirm)) {
    res.status(tool.safety === "requires-confirmation" && !confirm && !dryRun ? 409 : 200).json({ success: dryRun || tool.safety === "safe", requiresConfirmation: tool.safety === "requires-confirmation" && !confirm, dryRun: true, tool: action(tool), normalizedParams: validation.normalizedParams, wouldCall: { method: tool.method, path: tool.path, params: validation.normalizedParams } });
    return;
  }
  try {
    const output = await tool.execute?.({ req, tool, params: validation.normalizedParams });
    res.json({ success: true, tool: action(tool), output });
  } catch (error) {
    res.status(500).json({ success: false, error: error instanceof Error ? error.message : "Tool 执行失败" });
  }
});

router.get("/agent/workflow/checklist", (_req, res) => {
  res.json({ success: true, checklist: workflowChecklist() });
});

router.post("/agent/chat", (req, res) => {
  const { message, sessionId } = req.body as { message?: string; sessionId?: string };
  if (!message?.trim()) {
    res.status(400).json({ success: false, error: "message 不能为空" });
    return;
  }
  const id = sessionId || randomUUID();
  const skill = selectSkill(message);
  const matchedTools = selectTools(message, skill);
  const reply = buildReply(message, skill, matchedTools);
  const current = sessions.get(id) || { skillId: skill.id, messages: [], updatedAt: Date.now() };
  current.skillId = skill.id;
  current.updatedAt = Date.now();
  current.messages.push({ role: "user", content: message }, { role: "assistant", content: reply });
  sessions.set(id, current);
  res.json({ success: true, sessionId: id, registry: { skillCount: skills.length, toolCount: registry.all().length }, skill: { id: skill.id, title: skill.title, tab: skill.tab, purpose: skill.purpose }, reply, actions: matchedTools.map(action), nextSteps: skill.nextSteps, history: current.messages.slice(-10) });
});

router.get("/agent/sessions/:sessionId", (req, res) => {
  const session = sessions.get(req.params.sessionId);
  if (!session) {
    res.status(404).json({ success: false, error: "session 不存在" });
    return;
  }
  res.json({ success: true, session });
});

export default router;
