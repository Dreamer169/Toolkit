#!/usr/bin/env python3
# proxy_guard.py -- auto-block any new 0.0.0.0-bound proxy port via iptables
import subprocess, time, logging, os, re

LOG_FILE = "/var/log/proxy_guard.log"
WHITELIST_PORTS = {
    22, 80, 443,
    3000,           # frontend
    8081, 8082,     # api-server (node, dual-stack)
    8083, 8084, 8085, 8086, 8091,  # obvious-proxy, airforce-register, apex-search
    8092,           # browser-model
    8765, 8766, 8767,  # captcha-api, pydoll-bypass, text-captcha
    9999,           # remote-exec
}
SCAN_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format="[proxy_guard] %(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("proxy_guard")

def get_listening_ports():
    result = {}
    try:
        out = subprocess.check_output(["ss", "-tlnp"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines()[1:]:
            m = re.search(r"(?:0\.0\.0\.0|\*|::):(\d+)", line)
            if not m:
                continue
            port = int(m.group(1))
            if "100." in line or "127.0.0.53" in line:
                continue
            proc = re.search(r"users:\(\(\"([^\"]+)\"", line)
            result[port] = proc.group(1) if proc else "unknown"
    except Exception as e:
        log.error("ss failed: %s", e)
    return result

def is_blocked(port):
    try:
        out = subprocess.check_output(["iptables", "-L", "INPUT", "-n"],
                                      text=True, stderr=subprocess.DEVNULL)
        return (f"dpt:{port}" in out or f",{port}," in out
                or f",{port}" in out or f"{port}," in out)
    except Exception:
        return False

def block_port(port, proc_name):
    try:
        subprocess.run(
            ["iptables", "-I", "INPUT", "-p", "tcp", "--dport", str(port),
             "!", "-s", "127.0.0.1", "-j", "DROP"],
            check=True, stderr=subprocess.DEVNULL
        )
        os.makedirs("/etc/iptables", exist_ok=True)
        rules = subprocess.check_output(["iptables-save"], text=True)
        with open("/etc/iptables/rules.v4", "w") as f:
            f.write(rules)
        log.warning("BLOCKED port %d (process=%s) -- iptables DROP added & saved", port, proc_name)
    except Exception as e:
        log.error("Failed to block port %d: %s", port, e)

def scan():
    ports = get_listening_ports()
    for port, proc in ports.items():
        if port in WHITELIST_PORTS:
            continue
        if is_blocked(port):
            continue
        log.warning("UNBLOCKED public port detected: %d (%s)", port, proc)
        block_port(port, proc)

def main():
    log.info("started -- whitelist=%s interval=%ds", sorted(WHITELIST_PORTS), SCAN_INTERVAL)
    while True:
        try:
            scan()
        except Exception as e:
            log.error("scan error: %s", e)
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
