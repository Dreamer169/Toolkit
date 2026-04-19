# 子节点（Replit 隐藏代理节点）部署指南

## 架构
账号注册工具 → SOCKS5:127.0.0.1:1090 → http_socks5_bridge.py → Replit HTTP Tunnel API → 目标服务器

## 步骤

### 1. 新建 Replit 实例
1. Fork Toolkit Replit 项目（当前实例已有 HTTP Tunnel 代码）
2. Secrets → SESSION_SECRET = <随机字符串>，用作 tunnel token
3. 启动 "API Server" workflow
4. 验证: GET https://<DOMAIN>/api/healthz → {"status":"ok"}
5. 获取配置: GET https://<DOMAIN>/api/proxy/info

### 2. 更新 VPS Bridge 指向新实例
```bash
# 获取新实例的 proxy_token
curl https://<NEW_DOMAIN>/api/proxy/info

# 更新 pm2 环境变量并重启
BASE_URL='https://<NEW_DOMAIN>' \
TUNNEL_TOKEN='<proxy_token 值>' \
pm2 restart http-socks5-bridge --update-env

# 持久化保存
pm2 save
```

### 3. 更新 replit_proxy.json
```bash
cat > /root/Toolkit/replit_proxy.json << JSON
{
  "proxy_url": "https://<NEW_DOMAIN>",
  "proxy_token": "<proxy_token>",
  "socks5_port": 1090,
  "exit_ip": "<通过代理测得的IP>",
  "platform": "google-cloud",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
```

### 4. 验证
```bash
# 通过 Replit 隧道的出口 IP（应为 GCP IP，非 VPS IP）
curl --socks5 127.0.0.1:1090 https://api.ipify.org

# VPS 直连 IP（对比用）
curl https://api.ipify.org
```

## API 端点参考

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/healthz | GET | 健康检查 |
| /api/proxy/info | GET | 代理配置（无需认证） |
| /api/tunnel/open | POST | 开启 TCP 连接 → {ok,id,sessionId} |
| /api/tunnel/write/:id | POST | 写数据（raw binary，?token=） |
| /api/tunnel/read/:id | GET | 流式读数据（HTTP chunked streaming） |
| /api/tunnel/:id | DELETE | 关闭会话 |
| /api/tunnel/sessions | GET | 列出活跃会话 |

认证：Header `x-tunnel-secret: <token>` 或 Query `?secret=<token>` 或 `?token=<token>`

## 注意事项
- 免费 Replit 域名（.riker.replit.dev）随重启变化，重启后需重新执行步骤 2-3
- 付费 Replit 部署后域名（.replit.app）固定，无需频繁更新
- 每个 Replit 实例有独立 GCP 出口 IP，可多实例并行使用
- SESSION_SECRET = proxy_token，通过 /api/proxy/info 读取（无需在 VPS 明文硬编码）
