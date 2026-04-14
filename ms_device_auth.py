#!/usr/bin/env python3
"""
Microsoft Graph API Device Code Flow 一次性授权
- 不需要付费服务
- 不需要IMAP密码
- 官方OAuth2设备授权流程
- 获取到的 refresh_token 保存在 /tmp/ms_tokens.json
"""
import json, time, sys
import urllib.request, urllib.parse

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"  # Azure CLI公共client_id，无需注册
TENANT   = "consumers"
SCOPE    = "offline_access Mail.Read"

def device_code_flow(email: str):
    # Step 1: 申请设备码
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    device_code = resp["device_code"]
    interval   = resp.get("interval", 5)
    expires_in = resp.get("expires_in", 900)
    print("\n" + "="*60)
    print("✅ 设备授权码已生成")
    print("="*60)
    print(f"\n📱 请在浏览器中打开:\n   {resp['verification_uri']}")
    print(f"\n🔑 输入代码: {resp['user_code']}")
    print(f"\n（{email} 账号登录，授权 Mail.Read 权限）")
    print("="*60 + "\n")

    # Step 2: 轮询等待用户授权
    deadline = time.time() + expires_in
    print("⏳ 等待您完成授权（最多15分钟）...", flush=True)
    while time.time() < deadline:
        time.sleep(interval)
        token_data = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        }).encode()
        try:
            tok_req = urllib.request.Request(
                f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            tok = json.loads(urllib.request.urlopen(tok_req, timeout=15).read())
            if "access_token" in tok:
                out = "/tmp/ms_tokens.json"
                with open(out, "w") as f:
                    json.dump({
                        "email": email,
                        "access_token": tok["access_token"],
                        "refresh_token": tok.get("refresh_token",""),
                        "expires_at": time.time() + tok.get("expires_in", 3600),
                    }, f, indent=2)
                print(f"\n✅ 授权成功！Token已保存到 {out}")
                print(f"   refresh_token有效期: ~90天")
                return tok
            elif tok.get("error") == "authorization_pending":
                print(".", end="", flush=True)
                continue
            else:
                print(f"\n❌ 错误: {tok}")
                sys.exit(1)
        except Exception as e:
            print(f"\n请求失败: {e}")
            continue
    print("\n❌ 超时，请重新运行")
    sys.exit(1)

if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "jack.rogers91@outlook.com"
    device_code_flow(email)
