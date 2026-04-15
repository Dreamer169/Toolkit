#!/usr/bin/env python3
"""
Toolkit 主入口 - 协调 CF IP 探测与 Xray 配置更新
用法:
  python3 main.py            # 探测可用 IP 并更新 xray.json
  python3 main.py --dry-run  # 只探测，不写入配置
  python3 main.py --fallback # 直接用备用 IP 列表更新（跳过探测）
"""
import sys
import subprocess

def run_step(label, cmd):
    print(f'\n[*] {label}')
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0

def main():
    dry_run = '--dry-run' in sys.argv
    fallback = '--fallback' in sys.argv

    print('=== Toolkit 启动 ===')

    if fallback:
        print('[*] 使用备用 IP 列表模式（跳过探测）')
        import xray_update
        xray_update.run(ips=xray_update.FALLBACK_IPS)
        print('\n✅ 完成：xray.json 已用备用 IP 列表更新')
        return

    if not dry_run:
        ok = run_step('步骤 1/2: 探测可用 Cloudflare IP + 更新 xray.json', [sys.executable, '/workspaces/Toolkit/xray_update.py'])
        if ok:
            print('\n✅ 完成：xray.json 已用最新可用 IP 更新')
        else:
            print('\n❌ 更新失败，请检查日志')
            sys.exit(1)
    else:
        run_step('干跑模式: 只探测 IP，不写入', [sys.executable, '/workspaces/Toolkit/cf_ip_expand.py'])
        print('\n（干跑模式，未写入 xray.json）')

if __name__ == '__main__':
    main()
