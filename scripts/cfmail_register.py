#!/usr/bin/env python3
"""
cfmail_register.py - full unitool registration pipeline via cfmail

Flow:
  1. Generate realistic name -> create cfmail address (curl admin API)
  2. INSERT account to DB (platform='cfmail', refresh_token=cfmail JWT)
  3. Call unitool http_register or unitool_register.py
  4. Poll CF D1 raw_mails for verification email (using correct address JWT)
  5. Decode quoted-printable -> extract verify URL
  6. Click verify via RESI proxy -> extract ssid
  7. Save ssid, output [OK] email|ssid or [FAIL] reason
"""

import sys, os, re, json, time, random, subprocess, quopri
import urllib.request, urllib.parse, argparse, psycopg2

# -- Config -------------------------------------------------------------------
DB_URL      = "postgresql://postgres:postgres@localhost/toolkit"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CFMAIL_INSTANCES = [
    {
        "host":       "mail-api.jonjim.eu.cc",
        "domain":     "jonjim.eu.cc",
        "site_auth":  "8GKNFyLCo0pL7drOqKZQ6jGB",
        "admin_auth": "360cb32181e4ef281afb3b63",
    },
    {
        "host":       "mail-api.hackerjim.eu.cc",
        "domain":     "hackerjim.eu.cc",
        "site_auth":  "ak4yJVQ8szp8H5jS3Mx6Y1sm",
        "admin_auth": "ufmTbatyzZ0jkKrDvYhIc281",
    },
]

CF_TOKEN = "cfat_1nsWRzCWTK6ezNt6zDzVuW5OckDeFFaZPnY9MzOm962c7b75"
CF_ACC   = "f7a0cd49eddc664419f9a783be8ce73d"
CF_D1_ID = "f6cab1c2-a473-40a1-b289-06d5360cc246"

RESI_PORTS  = list(range(10851, 10860))
UNITOOL_PW  = "Unitool@2024!"

# -- Real-name word lists -----------------------------------------------------
FIRST_NAMES = [
    "james","john","robert","michael","william","david","richard","joseph",
    "thomas","charles","christopher","daniel","matthew","anthony","mark",
    "donald","steven","paul","andrew","joshua","kevin","brian","george",
    "edward","ronald","timothy","jason","jeffrey","ryan","jacob",
    "gary","nicholas","eric","jonathan","stephen","larry","justin",
    "scott","brandon","benjamin","samuel","raymond","gregory","frank",
    "mary","patricia","jennifer","linda","barbara","susan","jessica",
    "sarah","karen","lisa","nancy","betty","margaret","sandra",
    "ashley","emily","kimberly","donna","michelle","carol","amanda",
    "melissa","deborah","stephanie","rebecca","sharon","laura",
]
LAST_NAMES = [
    "smith","johnson","williams","brown","jones","garcia","miller",
    "davis","rodriguez","martinez","hernandez","lopez","gonzalez",
    "wilson","anderson","thomas","taylor","moore","jackson","martin",
    "lee","perez","thompson","white","harris","sanchez","clark",
    "ramirez","lewis","robinson","walker","young","allen","king",
    "wright","scott","torres","nguyen","hill","flores","green",
    "adams","nelson","baker","hall","rivera","campbell","mitchell",
    "carter","roberts","turner","phillips","evans","diaz","parker",
]

def generate_name():
    """Return firstname.lastname<2-4 digit suffix> e.g. james.wilson847"""
    first  = random.choice(FIRST_NAMES)
    last   = random.choice(LAST_NAMES)
    suffix = str(random.randint(10, 9999)) if random.random() > 0.10 else ""
    return first + "." + last + suffix

# -- Logging ------------------------------------------------------------------
def log(msg):
    print("[" + time.strftime("%H:%M:%S") + "] " + msg, flush=True)

# -- cfmail address creation --------------------------------------------------
def cfmail_create(name, inst):
    """Create cfmail address via admin API. Returns {"ok", "email", "jwt"} or {"ok":False}."""
    url = "https://" + inst["host"] + "/jimhacker/new_address"
    cmd = [
        "curl", "-sS", "-X", "POST", url,
        "-H", "x-custom-auth: " + inst["site_auth"],
        "-H", "x-admin-auth: "  + inst["admin_auth"],
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"name": name}),
        "--max-time", "20",
    ]
    try:
        out  = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=25)
        data = json.loads(out)
        if data.get("address") or data.get("email"):
            email = data.get("address") or data.get("email") or (name + "@" + inst["domain"])
            jwt   = data.get("jwt") or data.get("token") or ""
            return {"ok": True, "email": email, "jwt": jwt}
        return {"ok": False, "error": str(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# -- CF D1 inbox poll ---------------------------------------------------------
def poll_d1(email_addr, after_ts=0.0, max_wait=120):
    """Query CF D1 for unitool verify email. Returns verify_url or ''."""
    api = ("https://api.cloudflare.com/client/v4/accounts/"
           + CF_ACC + "/d1/database/" + CF_D1_ID + "/query")
    schedule = [8, 10, 12, 15, 15, 15, 20, 20]
    elapsed  = 0
    for wait in schedule:
        if elapsed >= max_wait:
            break
        sleep = min(wait, max_wait - elapsed)
        log("poll: wait " + str(sleep) + "s (" + str(elapsed) + "/" + str(max_wait) + "s)")
        time.sleep(sleep); elapsed += sleep
        sql = ("SELECT raw FROM raw_mails WHERE address='" + email_addr
               + "' ORDER BY id DESC LIMIT 3")
        try:
            body = json.dumps({"sql": sql}).encode()
            req  = urllib.request.Request(api, data=body, headers={
                "Authorization": "Bearer " + CF_TOKEN,
                "Content-Type":  "application/json",
            })
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
            rows = resp["result"][0]["results"]
            log("poll: D1 rows=" + str(len(rows)))
        except Exception as e:
            log("poll: D1 err: " + str(e)); continue
        for row in rows:
            raw = row.get("raw", "")
            if not raw:
                continue
            try:
                decoded = quopri.decodestring(raw.encode()).decode("utf-8", errors="replace")
            except Exception:
                decoded = raw
            urls = re.findall(
                r"https://unitool\.ai/api/auth/email\?token=[A-Za-z0-9._\-]+", decoded)
            if not urls:
                urls = re.findall(
                    r"https://unitool\.ai[^\s\"'<>]*token=[^\s\"'<>]*", decoded)
            if urls:
                log("poll: found verify URL: " + urls[0][:80])
                return urls[0]
        log("poll: [" + str(elapsed) + "s] no mail yet")
    log("poll: timeout " + str(max_wait) + "s")
    return ""

# -- Click verify link --------------------------------------------------------
def click_verify(verify_url, email):
    """Click verify URL via RESI proxy. Returns ssid or ''."""
    port = RESI_PORTS[hash(email) % len(RESI_PORTS)]
    ekey = re.sub(r"[^a-z0-9]", "_", email.lower())[:24]
    ck   = "/tmp/cfmail_ck_" + ekey + ".txt"
    hdr  = "/tmp/cfmail_hdr_" + ekey + ".txt"
    for f in [ck, hdr]:
        try: os.remove(f)
        except: pass
    cmd = [
        "curl", "-sS", "-L", "--max-redirs", "8",
        "--socks5-hostname", "127.0.0.1:" + str(port),
        "-c", ck, "-b", ck, "-D", hdr,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url,
    ]
    log("click_verify: port=" + str(port))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try: proc.communicate(timeout=35)
        except subprocess.TimeoutExpired: proc.kill(); proc.communicate()
    except Exception as e:
        log("click_verify: curl err: " + str(e)); return ""
    ssid = ""
    if os.path.exists(hdr):
        for line in open(hdr, encoding="utf-8", errors="ignore"):
            if "unitool-ssid" in line.lower() and "set-cookie" in line.lower():
                m = re.search(r"unitool-ssid=([^;\s]+)", line, re.I)
                if m: ssid = m.group(1); break
    if not ssid and os.path.exists(ck):
        for line in open(ck, encoding="utf-8", errors="ignore"):
            if "unitool-ssid" in line.lower():
                parts = line.strip().split("\t")
                ssid  = parts[-1] if parts else ""; break
    log("click_verify: ssid len=" + str(len(ssid)))
    return ssid

# -- DB helpers ---------------------------------------------------------------
def db_connect():
    return psycopg2.connect(DB_URL)

def db_upsert_account(email, jwt):
    """Insert cfmail account, return id."""
    conn = db_connect(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO accounts "
        "(platform, email, password, refresh_token, status, tags, created_at, updated_at) "
        "VALUES ('cfmail', %s, %s, %s, 'active', 'unitool_processing', NOW(), NOW()) "
        "ON CONFLICT (platform, email) DO UPDATE "
        "  SET refresh_token = EXCLUDED.refresh_token, "
        "      tags = 'unitool_processing', updated_at = NOW()",
        (email, UNITOOL_PW, jwt))
    conn.commit()
    cur.execute("SELECT id FROM accounts WHERE email=%s", (email,))
    row = cur.fetchone(); conn.close()
    return row[0] if row else 0

def db_mark_ok(account_id, ssid):
    conn = db_connect(); cur = conn.cursor()
    note = "\ncfmail_ssid_len=" + str(len(ssid)) + " at=" + time.strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "UPDATE accounts SET tags='unitool_registered', "
        "notes=COALESCE(notes,'') || %s, updated_at=NOW() WHERE id=%s",
        (note, account_id))
    conn.commit(); conn.close()

def db_mark_fail(account_id, reason):
    conn = db_connect(); cur = conn.cursor()
    cur.execute(
        "UPDATE accounts SET tags='unitool_fail,' || %s, updated_at=NOW() WHERE id=%s",
        (reason[:80], account_id))
    conn.commit(); conn.close()

def persist_ssid(email, ssid):
    label = re.sub(r"[^a-z0-9]", "_", email.lower())
    try:
        os.makedirs("/data/unitool_ssids", exist_ok=True)
        open("/data/unitool_ssids/" + label + ".txt", "w").write(ssid)
        log("ssid: wrote /data/unitool_ssids/" + label + ".txt")
    except Exception as e:
        log("ssid: /data write err: " + str(e))
    try:
        data = json.dumps({"ssid": ssid, "label": email}).encode()
        req  = urllib.request.Request(
            "http://localhost:8089/add-ssid", data=data,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        log("ssid: proxy push OK")
    except Exception as e:
        log("ssid: proxy push err (non-fatal): " + str(e))

# -- unitool registration -----------------------------------------------------
def run_unitool_register(email, ref_code):
    """
    Register email on unitool via http_register.
    Uses dynamic import (same way chain_v3 does) with RESI port rotation.
    NO internal Outlook/Graph verification -- caller handles verify via cfmail D1.
    Returns {"ok": bool, "reason": str}
    """
    import importlib.util as _ilu
    http_reg_path = os.path.join(SCRIPTS_DIR, "unitool_http_register.py")

    if not os.path.exists(http_reg_path):
        return {"ok": False, "reason": "no_http_register_script"}

    # Method 1: dynamic import  (avoids subprocess arg-name issues)
    try:
        _spec = _ilu.spec_from_file_location("unitool_http_register", http_reg_path)
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _fn   = _mod.http_register          # synchronous wrapper in that module
        port  = random.choice(RESI_PORTS)
        log("reg: dynamic-import http_register email=" + email + " port=" + str(port))
        for _attempt in range(3):
            result = _fn(email, UNITOOL_PW, ref_code, port)
            log("reg: attempt=" + str(_attempt+1)
                + " ok=" + str(result.get("ok"))
                + " err=" + str(result.get("error",""))[:60])
            if result.get("ok"):
                return {"ok": True}
            err = str(result.get("error_type") or result.get("error") or "").lower()
            if any(p in err for p in (
                    "already_reg","already_registered","email_already",
                    "already_use","user with like email")):
                return {"ok": False, "reason": "already_registered"}
            # proxy/bypass error: rotate port and retry
            if any(p in err for p in ("bypass","timeout","proxy","socks","connection")):
                port = random.choice(RESI_PORTS)
                continue
            break
        reason = str(result.get("error_type") or result.get("error") or "http_reg_fail")
        return {"ok": False, "reason": reason}
    except KeyboardInterrupt:
        raise
    except Exception as _e:
        log("reg: dynamic-import exc: " + str(_e))

    # Method 2: subprocess CLI with correct --ref flag (fallback)
    log("reg: fallback subprocess CLI")
    args = ["python3", http_reg_path, "--email", email, "--password", UNITOOL_PW]
    if ref_code:
        args += ["--ref", ref_code]
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.STDOUT, timeout=120).decode(errors="replace")
        if "=== RESULT ===" in out:
            raw_json = out.split("=== RESULT ===")[-1].strip()
            try:
                data = json.loads(raw_json)
                if data.get("ok"):
                    return {"ok": True}
                err = str(data.get("error_type") or data.get("error") or "unknown")
                if any(p in err.lower() for p in ("already","email_already")):
                    return {"ok": False, "reason": "already_registered"}
                return {"ok": False, "reason": err}
            except json.JSONDecodeError:
                pass
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout_subprocess_reg"}
    except Exception as _e2:
        log("reg: subprocess exc: " + str(_e2))

    return {"ok": False, "reason": "reg_all_methods_failed"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref-code",  default="")
    parser.add_argument("--domain",    default="jonjim",
                        choices=["jonjim", "hackerjim"])
    parser.add_argument("--max-wait",  type=int, default=120)
    args = parser.parse_args()

    inst     = CFMAIL_INSTANCES[0] if args.domain == "jonjim" else CFMAIL_INSTANCES[1]
    ref_code = args.ref_code

    # Step 1: create cfmail address
    email = jwt = ""
    for attempt in range(5):
        name   = generate_name()
        log("cfmail: create " + name + "@" + inst["domain"] + " (attempt " + str(attempt+1) + ")")
        result = cfmail_create(name, inst)
        if result["ok"]:
            email = result["email"]
            jwt   = result["jwt"]
            log("cfmail: OK email=" + email + " jwt_len=" + str(len(jwt)))
            break
        log("cfmail: fail: " + result["error"])
        time.sleep(3)

    if not email or not jwt:
        print("[FAIL] cfmail_create|address_create_failed", flush=True)
        sys.exit(1)

    # Step 2: write DB
    account_id = db_upsert_account(email, jwt)
    log("db: account_id=" + str(account_id))

    reg_ts = time.time()

    # Step 3: register unitool
    reg = run_unitool_register(email, ref_code)
    if not reg["ok"]:
        reason = reg.get("reason", "unknown")
        log("reg: FAIL " + reason)
        if account_id: db_mark_fail(account_id, reason)
        print("[FAIL] " + email + "|" + reason, flush=True)
        sys.exit(1)
    log("reg: unitool register OK")

    # Step 4: poll D1 for verify email
    verify_url = poll_d1(email, after_ts=reg_ts, max_wait=args.max_wait)
    if not verify_url:
        log("poll: no verify email")
        if account_id: db_mark_fail(account_id, "verify_email_not_found")
        print("[FAIL] " + email + "|verify_email_not_found", flush=True)
        sys.exit(1)

    # Step 5: click verify
    ssid = click_verify(verify_url, email)
    if not ssid:
        log("verify: no ssid on first attempt, retry in 5s")
        time.sleep(5)
        ssid = click_verify(verify_url, email)

    if not ssid:
        log("verify: FAIL - no ssid after 2 attempts")
        if account_id: db_mark_fail(account_id, "no_ssid_after_click")
        print("[FAIL] " + email + "|no_ssid_after_click", flush=True)
        sys.exit(1)

    log("verify: ssid len=" + str(len(ssid)))

    # Step 6: persist
    if account_id: db_mark_ok(account_id, ssid)
    persist_ssid(email, ssid)

    print("[OK] " + email + "|" + ssid, flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
