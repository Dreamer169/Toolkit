# unitool.ai Referral 完整操作手册

  > **最后更新**: 2026-05-06
  > **版本**: v2.1（chain 模式：自动串联多个 ref_code）
  > **适合新人快速接手**

  ---

  ## 一、概览

  ### Referral 机制
  - unitool.ai 推荐计划：每邀请一人注册，master 账号获得 **+10 tokens**
  - 每个推荐链接（ref_code）最多邀请 **10 人**（官方硬上限）
  - 推荐链接格式：`https://unitool.ai/ref/{ref_code}`

  ### 账号类型

  | 类型 | 说明 | DB tag |
  |------|------|--------|
  | master | 持有 ref_code，邀请别人 | `unitool_ref_master` |
  | referral | 通过推荐链接注册，给 master 贡献 tokens | `unitool_via_ref` |
  | ref 激活 | referral 账号也能再成为 master | `unitool_ref_activated` |

  ### 当前状态（2026-05-06）

  | 项目 | 值 |
  |------|----|
  | 现有 master | sarahrivera639@outlook.com |
  | master ref_code | `xjfjk` |
  | ref_url | https://unitool.ai/ref/xjfjk |
  | 已使用 referral 次数 | 0 / 10 |
  | 可用新 Outlook 账号 | 10 个 |

  ---

  ## 二、脚本地图

  ```
  /root/Toolkit/scripts/
  ├── unitool_ref_pipeline.py   ★ 主脚本：Referral 完整流水线（本手册围绕此脚本）
  ├── unitool_pipeline.py         注册单个账号到 unitool（供 master 注册使用）
  ├── unitool_register.py         单账号注册 v1（支持 --ref-code，pipeline 内部调用）
  ├── unitool_reflink.py          获取任意账号的 ref_code（curl /api/auth/session）
  ├── unitool_login.py            登录获取 ssid cookie（备用）
  └── unitool_reg_v2.py           持续循环注册服务（PM2 持久运行，自动注册 unitool）
  ```

  调用关系：

  ```
  unitool_ref_pipeline.py
    ├─ unitool_register.py --ref-code   # 访问 ref URL 然后注册每个 referral
    ├─ unitool_pipeline.py --batch 1    # 注册 master（无 master 时）
    ├─ unitool_login.py                 # 备用：登录获取 ssid
    └─ unitool_reflink.py               # 获取任意账号 ref_code
  ```

  ---

  ## 三、前置条件（必读）

  ### 3.1 Outlook 账号必须已入库

  Outlook 账号注册走 API 触发（两步）：

  ```bash
  # Step A: 生成身份 + 密码
  curl -s "http://localhost:8081/api/tools/workflow/prepare"

  # Step B: 注册 Outlook + 写入 DB（含 refresh_token）
  curl -s -X POST "http://localhost:8081/api/tools/outlook/register" \
    -H "Content-Type: application/json" \
    -d '{"count": 1}'

  # 验证可用账号数
  python3 -c "
  import psycopg2
  conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
  cur = conn.cursor()
  cur.execute(\"SELECT count(*) FROM accounts WHERE platform='outlook' AND status='active' AND refresh_token IS NOT NULL AND (tags IS NULL OR tags NOT LIKE '%unitool%')\")
  print('可用 outlook 账号:', cur.fetchone()[0])
  conn.close()
  "
  ```

  accounts 表关键字段：

  | 字段 | 说明 |
  |------|------|
  | platform | 'outlook' |
  | email | 账号邮箱 |
  | password | 登录密码 |
  | refresh_token | Graph API token，用于读取邮件（验证链接） |
  | tags | 逗号分隔状态标签（见下节） |
  | notes | 存储 ssid、ref_code（格式: unitool_ssid=xxx） |

  ### 3.2 依赖服务必须在线

  ```bash
  pm2 list | grep -E 'api-server|xvfb|unitool'
  # 必须: api-server(online) + xvfb(online)
  ```

  ---

  ## 四、Referral 完整流程（一键自动）

  ### 4.1 最简单：直接用已知 ref_code 跑 10 个 referral

  ```bash
  cd /root/Toolkit/scripts
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10
  ```

  内部执行顺序：
  1. 验证 ref_code（已知则跳过 master 注册）
  2. 循环最多 10 次，每次：
     - 从 DB 取一个未注册 outlook 账号
     - 访问 https://unitool.ai/ref/xjfjk（写入推荐 cookie）
     - 注册 unitool（邮件验证，搜索 Inbox + JunkEmail）
     - 获取该账号自己的 ref_code 并存 DB

  输出格式：
  ```
  [REF_OK]   1|email@example.com|ssid_prefix...
  [REF_CODE] 1|email@example.com|their_ref_code|https://unitool.ai/ref/their_ref_code
  [REF_FAIL] 2|email2@example.com|reason
  [DONE]     9/10 ref_code=xjfjk
  ```

  ### 4.2 从 master 邮箱自动获取 ref_code

  ```bash
  python3 unitool_ref_pipeline.py \
    --master-email sarahrivera639@outlook.com \
    --batch 10
  ```

  ### 4.3 传 master ssid（最快）

  ```bash
  python3 unitool_ref_pipeline.py \
    --master-ssid <your_ssid_here> \
    --batch 5
  ```

  ### 4.4 全自动（DB 无 master 时，自动注册一个再跑 referral）

  ```bash
  python3 unitool_ref_pipeline.py --batch 10
  ```

  ### 4.5 跳过 referral 账号 ref_code 激活（节省时间）

  ```bash
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10 --no-activate-ref
  ```

  ### 4.6 [v2.1] Chain 模式：无限扩展，跨 ref_code 自动串联

  一个 ref_code 最多邀请 10 人。Chain 模式下，当前 ref_code 跑满后自动切换到
  本次新激活的 referral 账号的 ref_code 继续注册，直到完成目标数量或耗尽账号。

  ```bash
  # 跑 50 个 referral（自动串联最多 5 个 ref_code）
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 50 --chain

  # 限制链路深度（最多切换 3 次 ref_code，即最多注册 40 个）
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 50 --chain --max-depth 3
  ```

  Chain 切换流程：
  ```
  ref_code=xjfjk  →  注册10人  →  切换到 referral_1 的 ref_code
                  →  注册10人  →  切换到 referral_11 的 ref_code
                  →  注册10人  →  ...
  ```

  Chain 输出格式：
  ```
  [REF_OK]   1|email|ssid...
  [REF_CODE] 1|email|ref_code_A|https://unitool.ai/ref/ref_code_A
  ...（10次后）
  [CHAIN]    1|xjfjk|ref_code_A|email_of_new_master
  [REF_OK]   11|email2|ssid...
  ...
  [DONE]     50/50 ref_code=xjfjk depth=4
  ```

  ---

  ## 五、分步手动操作

  需要调试单个步骤时使用：

  ### Step 1: 注册 master（若没有现成的）
  ```bash
  python3 unitool_pipeline.py --batch 1
  # 输出: [OK] email|ssid  然后写入 DB
  ```

  ### Step 2: 获取 master 的 ref_code
  ```bash
  python3 unitool_reflink.py --email sarahrivera639@outlook.com
  # 输出: [OK] xjfjk|https://unitool.ai/ref/xjfjk|email|uid
  ```

  ### Step 3: 注册单个 referral
  ```bash
  python3 unitool_register.py --email <referral_email> --ref-code xjfjk
  # 输出: [OK] email|ssid
  ```

  ### Step 4: 激活 referral 账号自己的 ref_code
  ```bash
  python3 unitool_reflink.py --email <referral_email>
  # 输出: [OK] <their_ref_code>|https://unitool.ai/ref/<their_ref_code>|...
  ```

  ---

  ## 六、通过 API 调用

  api-server 在 localhost:8081，外部通过 ngrok 暴露。

  ### 获取 ref_code
  ```bash
  curl "http://localhost:8081/api/tools/unitool/reflink?email=sarahrivera639@outlook.com"
  # 返回: { "success": true, "refCode": "xjfjk", "refUrl": "https://unitool.ai/ref/xjfjk" }
  ```

  ### 启动 referral 流水线（异步 Job）
  ```bash
  curl -X POST "http://localhost:8081/api/tools/unitool/ref-pipeline" \
    -H "Content-Type: application/json" \
    -d '{ "batch": 10, "refCode": "xjfjk" }'
  # 返回: { "success": true, "jobId": "abc123" }
  ```

  ### 轮询任务进度
  ```bash
  curl "http://localhost:8081/api/tools/unitool/ref-pipeline/abc123"
  # 返回: { "status": "running|done|failed", "okCount": 7, "refCode": "xjfjk",
  #          "logs": [...], "results": [...] }
  ```

  ---

  ## 七、DB 查询常用 SQL

  连接：`psql postgresql://postgres:postgres@localhost/toolkit`

  ```sql
  -- 查看 master 账号及其 ref_code
  SELECT id, email,
         substring(notes from 'unitool_ref_code=([A-Za-z0-9_-]+)') AS ref_code,
         tags
  FROM accounts WHERE tags LIKE '%unitool_ref_master%'
  ORDER BY id DESC LIMIT 10;

  -- 统计每个 master 已邀请几人（上限 10）
  SELECT id, email,
    (length(COALESCE(notes,'')) - length(replace(COALESCE(notes,''), 'ref_registered=', '')))
    / length('ref_registered=') AS refs_used
  FROM accounts WHERE tags LIKE '%unitool_ref_master%';

  -- 查看 referral 账号（通过推荐注册的）
  SELECT id, email, tags FROM accounts
  WHERE tags LIKE '%unitool_via_ref%'
  ORDER BY id DESC LIMIT 20;

  -- 已激活自己 ref_code 的账号（可作为新 master 使用）
  SELECT id, email,
         substring(notes from 'unitool_ref_code=([A-Za-z0-9_-]+)') AS ref_code
  FROM accounts WHERE tags LIKE '%unitool_ref_activated%'
  ORDER BY id DESC;

  -- 可用未注册 outlook 账号数
  SELECT count(*) FROM accounts
  WHERE platform='outlook' AND status='active'
    AND refresh_token IS NOT NULL
    AND (tags IS NULL OR (
      tags NOT LIKE '%unitool%' AND
      tags NOT LIKE '%token_invalid%'
    ));
  ```

  ---

  ## 八、日志位置

  | 日志 | 路径 |
  |------|------|
  | ref_pipeline 主日志 | `/tmp/unitool_ref_pipeline.log` |
  | unitool_pipeline 日志 | `/tmp/unitool_pipeline.log` |
  | unitool_reg_v2 stdout | `/tmp/unitool_reg_v2_out.log` |

  ```bash
  # 实时监控
  tail -f /tmp/unitool_ref_pipeline.log

  # PM2 日志
  pm2 logs unitool_reg_v2 --lines 20 --nostream
  pm2 logs api-server     --lines 30 --nostream
  ```

  ---

  ## 九、故障排查

  ### 没有可用的 outlook 账号
  通过 API 注册更多 Outlook 账号（Section 3.1）。

  ### Turnstile failed
  CF Turnstile 绕过失败：
  - `pm2 status xvfb` — DISPLAY :99 必须 online
  - `/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome` — Chrome 必须存在

  ### verify_email_not_found
  验证邮件 300s 内未到。脚本已同时搜索 Inbox + JunkEmail（2026-04 修复）。重试即可。

  ### already_registered
  该邮箱已注册过 unitool，自动跳过并标记 `unitool_fail`。

  ### no_ref_code（ssid 过期）
  重新登录获取新 ssid：
  ```bash
  python3 unitool_login.py --email sarahrivera639@outlook.com --password <pw> --no-headless
  # 拿到新 ssid 后
  python3 unitool_ref_pipeline.py --master-ssid <new_ssid> --batch 10
  ```

  ### quota_full（ref_code 额度满）
  换一个已注册账号的 ref_code：
  ```bash
  # 查所有有 ref_code 的账号（unitool_ref_activated tag）
  python3 unitool_reflink.py --email <another_registered_account>
  ```

  ---

  ## 十、PM2 服务一览

  | PM2 名称 | 功能 | 状态 |
  |----------|------|------|
  | `unitool_reg_v2` | 持续循环注册 unitool（从 DB 取 outlook 账号） | online |
  | `unitool-proxy` | v5.1 代理服务，pool 13账号 84模型，自动重登 | online |
  | `api-server` | REST API（Express 5） | online |
  | `xvfb` | 虚拟显示服务（pydoll Chrome 必须） | online |

  ---

  ## 十一、新人接手检查清单

  - [ ] `pm2 list` — 确认 api-server、xvfb、unitool_reg_v2、unitool-proxy 均 online
  - [ ] 查 DB 可用 outlook 账号数（Section 七最后一条 SQL）
  - [ ] 查 master ref_code 剩余次数（Section 七 stats SQL）
  - [ ] 验证 master ssid 有效：`python3 unitool_reflink.py --email sarahrivera639@outlook.com`
  - [ ] 如 ssid 过期：`python3 unitool_login.py --email sarahrivera639@outlook.com --password <pw> --no-headless`
  - [ ] 就绪后执行：`python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10`
  - [ ] 实时监控：`tail -f /tmp/unitool_ref_pipeline.log`
  