# enable_imap_v5.py — 新人接手文档

> 最后更新：2026-05-13  
> 负责文件：`/data/Toolkit/artifacts/api-server/enable_imap_v5.py`  
> GitHub fork：`Dreamer169/Toolkit`（`YOUR_GITHUB_PAT_HERE`）

---

## 一、功能目标

对 Outlook 账号批量开启 **IMAP + POP 访问**，使第三方邮件客户端（如 Thunderbird、Foxmail）能拉取邮件。  
Microsoft 默认关闭这两项，且在设置页面强制要求用户通过 OAuth 重新登录，所以必须用 headless browser 模拟完整登录→导航→开关。

---

## 二、运行环境

| 项目 | 值 |
|------|-----|
| 服务器 | `45.205.27.69`，root 密码 `<PASSWORD_REDACTED>` |
| 项目路径 | `/data/Toolkit/artifacts/api-server/` |
| 数据库 | `postgresql://postgres:postgres@localhost/toolkit` |
| Browser | `chrome-headless-shell`（路径见 `launch_browser()`） |
| Python | 系统 python3，依赖 `patchright`、`psycopg2`、`xray_relay` |

---

## 三、快速上手

### 单账号测试（有密码）
```bash
cd /data/Toolkit/artifacts/api-server
python3 enable_imap_v5.py --email user@outlook.com --password "xxx"
```

### 单账号测试（从 DB 读）
```bash
python3 enable_imap_v5.py --account-id 3752
```

### 指定代理
```bash
python3 enable_imap_v5.py --account-id 3752 --proxy socks5://127.0.0.1:10910
```

### 结果
- 退出码 `0` = 成功，`1` = 失败  
- 日志打印到 stdout，含每步截图路径（`/tmp/imap5_*.png`）  
- 成功后 DB `accounts.tags` 追加 `imap_enabled`、`pop_enabled`

---

## 四、代码流程（从上到下）

```
main()
  └─ _setup_proxy(exit_ip, manual_proxy)   ← 选代理
  └─ enable_imap(email, password, proxy, …)
       ├─ _find_isp_proxy()                ← 找可用 ISP 端口（SOCKS5 握手探活）
       ├─ launch_browser(proxy=ISP)        ← 用 ISP 端口启动 browser（NOT CF VLESS）
       ├─ _do_fresh_login(page, …)         ← Step 0：完整 OAuth 登录流程
       ├─ _nav_to_imap_direct(page)        ← Step 1：导航到 /popimap 设置页
       ├─ [security cycle]                 ← Step 2-N：处理 reauth/proofs/code
       ├─ _toggle_imap(page)               ← Step N+2：开 IMAP
       ├─ _toggle_pop(page)                ← Step N+3：开 POP（同一页面）
       ├─ _save_settings(page)             ← Step N+4：点 Save
       └─ db_tag_imap_enabled(account_id) ← 写 DB tag
```

---

## 五、代理架构（最关键，曾是主要 bug 来源）

### 端口分类

| 端口范围 | 类型 | 说明 |
|----------|------|------|
| `10910–10914` | `tp-in`（美国 DC IP） | **首选**，~0.4s，能完整渲染 Microsoft React SPA |
| `10851,10853,10855,10859` | `ss-in` ISP 直连 | 备选（意大利/土耳其/俄罗斯/HK，`proxy:false`） |
| `10857` | `ss-in-7` CF-proxied | ❌ **绝对不能用**，CF 代理无法渲染 MS React |
| `10820–10829` | `in-socks` CF | ❌ 同上 |
| XrayRelay 动态端口 | CF VLESS tunnel | 只用于账号注册保持 IP 一致，**不用于 IMAP 开启** |

### 探活方式

`_probe_socks5(port)` — 真正的 SOCKS5 握手（`\x05\x01\x00` → `\x05\x00` → CONNECT 1.1.1.1:443），而非仅 TCP `connect()`。TCP 连通但 SOCKS5 握手失败的端口（如 10913）会被正确排除。

### `exit_ip` 账号的特殊处理

账号注册时用了 CF IP（存在 `accounts.exit_ip`），但开 IMAP 时：
1. `_setup_proxy()` 会启动 XrayRelay CF VLESS tunnel → 返回动态端口如 `socks5://127.0.0.1:25732`
2. **但 `enable_imap()` 内部会立即用 `_find_isp_proxy()` 覆盖为 ISP 端口**
3. Browser 全程使用 ISP 端口，CF VLESS tunnel 实际不被使用于 IMAP 会话

这是故意的：Microsoft IMAP 设置页的 SPA lazy-load 通过 CF VLESS 会失败（panel 不渲染，只显示 inbox 快捷键文本）。

---

## 六、曾经出现的 Bug 及修复历史

### Bug 1 — `10857` 被选中导致 SPA 不渲染（根本原因）【已修复 commit `8ac6b63`】
- **现象**：login 成功，但 IMAP 设置面板全是 inbox 文本，toggle 找不到任何 radio
- **根因**：`ISP_STATIC_PORTS` 里含 `10857`（CF-proxied），Microsoft React bundle 加载失败
- **修复**：优先 `[10910, 10911, 10912, 10914, 10851, 10853, 10855, 10859]`，去除 `10857`

### Bug 2 — `_nav_to_imap_direct` 点不到「Forwarding and IMAP」【已修复 commit `e84c271`】
- **现象**：gear 菜单点开后，「Forwarding and IMAP」选项点击无效或命中父容器
- **根因**：`get_by_text()` 默认 `exact=False`，匹配到包含该文字的父节点
- **修复**：`get_by_text("Forwarding and IMAP", exact=True)` + JS `directText()` 辅助精确匹配

### Bug 3 — 0-byte SPA 被误判为错误页跳过【已修复】
- **现象**：SPA 正在加载时 `body.innerHTML.length == 0`，被旧判断 skip 掉
- **修复**：`_blen == 0` → 继续等 60s；只有 `_blen 1-800 且含错误关键词` 才 skip

### Bug 4 — TCP 探活误放 dead 端口（如 10913）【已修复】
- **现象**：`socket.create_connection` 成功但 SOCKS5 握手失败，端口被选中后请求全部超时
- **修复**：`_probe_socks5()` 做完整 SOCKS5 握手验证

### Bug 5 — exit_ip 账号 CF VLESS 导致 IMAP panel 不渲染【已修复】
- **现象**：exit_ip 账号 login 成功，URL 正确到 `/popimap`，但 toggle 返回 `not-found`（page text 是 inbox）
- **根因**：Browser 用 XrayRelay CF VLESS 启动，`_do_fresh_login` 里 `isp_proxy` 参数从未真正生效（`getattr(ctx, "_proxy_server", isp_proxy)` 恒返回默认值导致条件恒 False）
- **修复**：在 `enable_imap()` 内 `launch_browser()` 之前就用 ISP 端口覆盖（`_session_isp` override）

---

## 七、DB Schema 相关

```sql
-- 查看账号
SELECT id, email, exit_ip, proxy_port, tags
FROM accounts
WHERE platform = 'outlook'
ORDER BY id DESC LIMIT 20;

-- 找还没开 IMAP 的账号
SELECT id, email FROM accounts
WHERE platform = 'outlook'
  AND NOT ('imap_enabled' = ANY(COALESCE(tags, '{}')));

-- 手动确认已开
SELECT id, email, tags FROM accounts WHERE id = 3793;
```

成功后 `tags` 字段会追加 `imap_enabled` 和 `pop_enabled`（`db_tag_imap_enabled()` 同时写两个）。

---

## 八、关键文件索引

| 文件 | 作用 |
|------|------|
| `enable_imap_v5.py` | 主文件（本文档所述） |
| `xray_relay.py` | XrayRelay 封装；`_STATIC_PORTS` 定义 ISP 端口分类 |
| `outlook_register.py` | 参考：residential fallback 模式（ISP ctx 切换） |
| `auto_device_code.py` | 参考：`_RESIDENTIAL_PORTS`、`_pick_residential_proxy` 模式 |
| `outlook_retoken.py` | 参考：XrayRelay `saved_exit_ip` 用法 |

---

## 九、未解决 / 观察中的问题

| 问题 | 状态 |
|------|------|
| `_do_fresh_login` 里 ISP ctx switch 代码逻辑恒 False（死代码） | 已被 ISP override 绕过，低优先级清理 |
| `10913` 等 dead tp-in 端口会随时增减 | `_probe_socks5` 已运行时排除，无需手动维护 |
| exit_ip 账号 POP toggle 实测结果 | 测试中（见下方进度） |
| 安全挑战（reauth/proofs）流程 | 代码在 cycle 里处理，未在本批测试触发 |

---

## 十、当前测试进度

| 账号 | 类型 | 最新结果 |
|------|------|----------|
| `aiden_mitchell70@outlook.com` (id=3752) | 无 exit_ip | ✅ `[enable-imap+pop] ✅ SUCCESS` |
| `d.carter281@outlook.com` (id=3793) | exit_ip=104.16.199.213 | 测试中（ISP override 修复后首跑） |

---

## 十一、批量跑法（TODO）

目前 `main()` 只支持单账号。批量跑需在外部循环调用（示例）：

```bash
python3 - << 'PYEOF'
import subprocess, psycopg2, psycopg2.extras
conn = psycopg2.connect("postgresql://postgres:postgres@localhost/toolkit")
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("""
    SELECT id FROM accounts
    WHERE platform='outlook'
      AND password IS NOT NULL AND password != ''
      AND NOT ('imap_enabled' = ANY(COALESCE(tags,'{}')))
    LIMIT 10
""")
for row in cur.fetchall():
    r = subprocess.run(
        ["python3", "enable_imap_v5.py", "--account-id", str(row["id"])],
        cwd="/data/Toolkit/artifacts/api-server"
    )
    print(f"id={row['id']} exit={r.returncode}")
PYEOF
```

