# Unitool 运营文档
> 最后更新: 2026-05-14 00:44 UTC

---

## 一、系统架构

### 主要组件

| 组件 | 文件 | pm2 ID | 说明 |
|------|------|--------|------|
| 主链 | `/data/Toolkit/scripts/unitool_chain_v3.py` | 231 | 注册+验证+ref_code 全流程 |
| 代理服务 | — | 230 (`unitool-proxy`) | RESI 代理池管理 |
| 救援验证 | `/data/Toolkit/scripts/unitool_verify_rescue.py` | 213 | 补救验证失败账号 |
| Xvfb 虚拟屏 | — | 218 | Chrome 无头所需 |
| 自动补充 | `/data/Toolkit/scripts/auto_ref_replenish.py` | cron | 每4h 从 proxyscrape 补充代理 |

### 数据库
- **连接**: `postgresql://postgres:postgres@localhost/toolkit`
- **主表**: `accounts`
- **关键字段**:
  - `tags`: 字符串，包含 `unitool_registered` / `unitool_ref_activated`
  - `notes`: JSON字符串，包含 `unitool_ssid` / `unitool_ref_code` / `unitool_email`

### 日志位置
| 日志 | 路径 |
|------|------|
| chain_v3 标准输出 | `/tmp/unitool_chain_v3_out.log` |
| chain_v3 错误输出 | `/tmp/unitool_chain_v3_err.log` |
| 自动补充日志 | `/tmp/auto_ref_replenish.log` |
| 代理扫描日志 | `/tmp/proxyscrape_cron.log` |

---

## 二、账号现状（2026-05-14 00:44 UTC）

| 指标 | 数量 |
|------|------|
| Unitool 账号总数 | 2,806 |
| 已注册 (unitool_registered) | **1,680** (59.9%) |
| 待注册 | **1,126** (40.1%) |
| 已注册且有自己的 ref_code | 217 (12.9% of registered) |
| 已注册但无 ref_code | 1,463 (87.1% of registered) |
| ref_code 已激活 | 216 |
| 外部代理池当前条目 | 40 (SOCKS5+HTTP混合) |

### 注册速度
- chain_v3 每账号约 **5~7 分钟**（含邮件验证等待90s）
- 按当前速度，剩余 1,126 个账号约需 **94~132 小时**（单进程）

---

## 三、代理体系

### RESI 内部端口（已全部耗尽用于 ref_code）
- 端口范围: 10851–10859（9个端口）
- 状态: **全部 IP 已被 unitool 记录，无法再创建新 ref_code**

### Webshare 商业代理
- 用户: `rccqykvf` / 密码: `66qn2mk76mm9`
- API Key: `9v6521m9i5bjm5zvrvsrlp0xkx9okputzvboqkor`
- 总量: 9 个 IP，**全部已用于 ref_code 创建**（2个之前用，7个本次用）
- 格式: `http://rccqykvf:66qn2mk76mm9@{ip}:{port}`

### 外部代理池 `/tmp/resi_pool_external.json`
- 40 条混合格式：
  - `ip:port`（SOCKS5，proxyscrape 免费代理）
  - `http://user:pass@ip:port`（HTTP，Webshare）
  - `user:pass@ip:port`（HTTP带认证，另外供应商）
- **已知问题**: 免费 SOCKS5 代理不支持 HTTPS CONNECT 隧道，无法用于 Chrome/pydoll 注册

### Proxyscrape 免费代理池
- URL: `https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=socks5`
- 每次约返回 ~5,201 条，实际存活 ~663 条，唯一出口 IP ~174 个
- 89.5% 来自 `206.123.156.x` 网段（每个端口对应一个唯一出口 IP）
- **限制**: 仅 SOCKS5，不支持 HTTPS CONNECT → 不可用于 Chrome 注册
- **可用于**: 直接 HTTP 创建 ref_code（不经过 Chrome）

---

## 四、ref_code 机制

### 核心约束
- unitool.ai **按出口 IP 全局记录** — 同一出口 IP 只能创建一次 ref_code，不区分账号
- 成功条件: `POST /api/ref-codes` → 返回 201，记录到 DB
- 失败信号: `{"error":"ip-already-existed"}` → 该 IP 永久无效

### ref_code 额度
- 每个 ref_code 可被使用 10 次（10个账号注册时填写）
- 当前可用 ref_code 池: **200 个**，最多可供 2000 个账号注册使用
- 使用最多的: `VOahj`(6/10), `sWTzX`(6/10)，其余 198 个均为 0/10

### ref_code 创建失败的根因
目前新注册账号无法获得自己的 ref_code，因为：
1. RESI 端口全部 IP 已被 unitool 记录
2. Webshare 9 个 IP 全部已用
3. 免费 SOCKS5 代理无法做 HTTPS CONNECT
4. → **只能依赖现有 200 个 ref_code 池轮流使用**

---

## 五、Cron 任务

```
0 */4 * * *  python3 /data/Toolkit/scripts/auto_ref_replenish.py  # 每4h 补充代理→创建 ref_code
0 */2 * * *  python3 /root/Toolkit/scripts/proxyscrape_manager.py --max 20  # 扫代理存活
*/3 * * * *  /root/monitor.sh  # VPS 监控
0 */4 * * *  outlook autocheck  # Outlook 邮件检查（limit=50）
5  8 * * *   python3 /data/Toolkit/scripts/ip2free_daily_tasks.py
10 8 * * *   proxy_manager.py sync-db
0  9 * * 0   proxy_manager.py webshare-sync（每周日）
0  3 * * *   nightly fingerprint test
*/5 * * * *  Xvfb :99 watchdog
```

---

## 六、chain_v3 工作流程

```
账号队列（待注册）
  ↓
[ref 池加载] 读取所有账号 ref_code 按 conversions 排序 → 200 个可用
  ↓
[快速路径] unitool_http_register v3.2 --ref-code {最优ref_code}
  ├─ Webshare HTTP 代理（自动检测格式）
  └─ RESI SOCKS5 代理（端口 10851）
  ↓
[注册成功] pydoll Chrome 完成 turnstile + 表单提交
  ↓
[ssid 获取] 内联等待验证邮件 90s → 解析 ssid（264字节 JWT）
  ↓
[Step7a] 通过代理为新账号创建专属 ref_code
  ├─ 扫描外部代理池（每次20个）
  ├─ 失败 → "ip-already-existed"（当前必然失败）
  └─ 成功 → 写入 DB notes.unitool_ref_code
  ↓
[Step7b] run_reflink 读取已有 ref_code（Step7a失败时兜底）
  └─ 当前也失败（因为Step7a未能创建）
  ↓
账号状态: registered=YES, ref_code=NONE
```

---

## 七、已知问题 & 待解决

### P0 - ref_code 无法为新注册账号创建
- **现象**: 每次 Step7a 返回 `ip-already-existed`，Step7b 返回 `no_ref_code`
- **根因**: 所有可用代理的出口 IP 均已被 unitool 记录
- **日志统计**: 本次运行已有 411 次 `ip-already-existed` 错误（896次注册成功中）
- **影响**: 1,463 个已注册账号无自己的 ref_code，无法成为推荐人

### P1 - 免费代理不支持 HTTPS CONNECT
- proxyscrape SOCKS5 代理无法用于 Chrome/pydoll 发起的 HTTPS 请求
- 仅可用于直接 HTTP 客户端（curl/requests）

### P2 - 外部代理池消耗殆尽
- `/tmp/resi_pool_external.json` 仍有40条，但大多数免费IP已失效或IP已被记录
- Webshare 9个 IP 全部耗尽

---

## 八、关键 Git 提交记录

| Hash | 说明 |
|------|------|
| `94dc056` | fix: ext_proxy 自动检测 HTTP/SOCKS5 格式；pool 扫描从8增至20；add auto_ref_replenish.py |
| `ec7af6d` | feat: add auto_ref_replenish.py |
| `6f1aeaa` | fix: chain_v3 CF fallback log；reflink 移除死端口 10870-10879 |
| `98e0b11` | fix: unitool-proxy v5.41 防止死亡ssid复活 |

---

## 九、操作手册

### 重启 chain_v3
```bash
# 如果 pm2 restart 报 not found，改用：
pm2 delete unitool_chain_v3
cd /data/Toolkit && pm2 start ecosystem.config.cjs --only unitool_chain_v3
```

### 实时查看注册进度
```bash
tail -f /tmp/unitool_chain_v3_out.log | grep -E "\[main\]|注册成功|ref_code|ip-already"
```

### 查询 DB 状态
```bash
psql postgresql://postgres:postgres@localhost/toolkit -c "
SELECT 
  COUNT(*) FILTER (WHERE tags::text LIKE %unitool_registered%) as registered,
  COUNT(*) FILTER (WHERE tags::text NOT LIKE %unitool_registered%) as pending,
  COUNT(*) FILTER (WHERE notes::text LIKE %unitool_ref_code%) as has_ref_code,
  COUNT(*) as total
FROM accounts WHERE tags::text LIKE %unitool% OR notes::text LIKE %unitool%;"
```

### 手动触发 ref 补充
```bash
python3 /data/Toolkit/scripts/auto_ref_replenish.py 2>&1 | tail -20
```

### 查看 pm2 状态
```bash
pm2 list | grep -E "unitool|xvfb|zombie"
```
