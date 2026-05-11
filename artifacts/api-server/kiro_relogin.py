#!/usr/bin/env python3
"""
kiro_relogin.py  --  Re-authenticate existing kiro accounts using stored email+password.

Root cause: step12f_device_auth silently failed during initial registration,
so refresh_token is empty. Access tokens expire in 8h.

Strategy:
  1. step1: InitiateLogin → redirect URL
  2. step2: follow redirect chain, get wsh
  3. step3: submit email with action_id=None (let AWS detect existing user)
     → AWS detects existing account → returns password challenge with publicKey
  4. JWE-encrypt stored password → submit
  5. step11: final login
  6. step12: OIDC auth code → fresh access_token + refresh_token
  7. Update DB: token=access_token, refresh_token, expires_at=now+8h

Usage:
  python3 kiro_relogin.py [--limit N] [--id ACCOUNT_ID] [--dry-run]
"""

import sys, os, time, json, random, argparse, traceback
import psycopg2
from datetime import datetime, timedelta, timezone

# Add server path for kiro_core imports
sys.path.insert(0, "/data/Toolkit/artifacts/api-server")
from kiro_core import KiroRegister, encrypt_password_jwe, SIGNIN, DIR_ID, UA, _uuid

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

LOG_PREFIX = "[RELOGIN]"

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    lvl_map = {"INFO": "INFO ", "OK": "OK   ", "ERR": "ERROR", "WARN": "WARN ", "DBG": "DBG  "}
    print(f"{LOG_PREFIX} [{ts}] [{lvl_map.get(level, level):<5}] {msg}", flush=True)

def get_accounts(conn, limit=10, account_id=None):
    """Get kiro accounts that need token refresh: expired tokens + stored password."""
    cur = conn.cursor()
    if account_id:
        cur.execute("""
            SELECT id, email, password, sub_status, token, refresh_token,
                   notes::jsonb->>'source_outlook_refresh_token' as ol_rt
            FROM accounts
            WHERE id = %s AND platform = 'kiro'
        """, (account_id,))
    else:
        cur.execute("""
            SELECT id, email, password, sub_status, token, refresh_token,
                   notes::jsonb->>'source_outlook_refresh_token' as ol_rt
            FROM accounts
            WHERE platform = 'kiro'
              AND sub_status IN ('pending', 'suspended')
              AND password IS NOT NULL AND password != ''

            ORDER BY id
            LIMIT %s
        """, (limit,))
    rows = cur.fetchall()
    cur.close()
    return rows

def update_token(conn, account_id, access_token, refresh_token, expires_at=None, sub_status=None):  # expires_at ignored (column does not exist in DB)
    """Update account tokens in DB."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    if sub_status:
        cur.execute("""
            UPDATE accounts SET
                token = %s,
                refresh_token = %s,
                sub_status = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (access_token, refresh_token, sub_status, account_id))
    else:
        cur.execute("""
            UPDATE accounts SET
                token = %s,
                refresh_token = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (access_token, refresh_token, account_id))
    conn.commit()
    cur.close()

class KiroRelogin(KiroRegister):
    """
    Re-login flow for existing kiro accounts.
    Skips signup/OTP steps, uses stored password directly.
    """

    def step3_login_existing(self, email):
        """
        For existing accounts: submit email with no action_id.
        AWS should detect existing account and return password challenge.
        """
        self.log("Step 3-LOGIN: submit email to detect existing account...")
        fp_i = {"input_type": "FingerPrintRequestInput", "fingerPrint": self._gen_signin_fwcim()}
        usr_i = {"input_type": "UserRequestInput", "username": email}

        # 3a: init (stepId='')
        self.log("  3a: init...")
        if not self._exec("", inputs=[fp_i]): return None

        # 3b: start
        self.log("  3b: start...")
        if not self._exec("start", inputs=[fp_i]): return None

        # 3c: submit email with action_id=SUBMIT → AWS detects existing account → password challenge
        self.log("  3c: submit email (action_id=SUBMIT for existing user)...")
        r = self._exec("get-identity-user", inputs=[usr_i, fp_i], action_id="SUBMIT")
        if not r: return None
        self.log(f"  → stepId={r.get('stepId')} sid={self.sid}")
        return r

    def step4_login_password(self, email, password, step3_resp):
        """
        Submit JWE-encrypted password for existing account login.
        Works for both: 'get-credentials' (login) and 'get-new-password-for-password-creation' (signup).
        """
        self.log("Step 4-LOGIN: submit JWE password for existing account...")
        
        # Possible stepIds that lead to password entry
        step_id = step3_resp.get("stepId") or step3_resp.get("sid", "")
        enc_ctx = (step3_resp.get("workflowResponseData", {}) or {}).get("encryptionContextResponse", {})
        pub_key = enc_ctx.get("publicKey") if enc_ctx else None

        # Check redirect for login wsh
        redir = (step3_resp.get("redirect") or {}).get("url", "")
        if redir:
            import re
            m = re.search(r"workflowStateHandle=([^&#]+)", redir)
            if m: self.wsh = m.group(1)

        if not pub_key:
            self.log(f"  ⚠️  No publicKey in step3 response. stepId={step_id}")
            self.log(f"  Full resp: {json.dumps(step3_resp, ensure_ascii=False)[:500]}")
            return None, "no_pubkey"

        # JWE-encrypt password
        jwe_password = encrypt_password_jwe(password, pub_key)
        self.log(f"  ✅ JWE encrypted, len={len(jwe_password)}")

        fwcim = self._gen_signin_fwcim()
        fp_i2 = {"input_type": "FingerPrintRequestInput", "fingerPrint": fwcim}
        pwd_i = {
            "input_type": "PasswordRequestInput",
            "password": jwe_password,
            "successfullyEncrypted": "SUCCESSFUL",
            "errorLog": None
        }
        usr_i = {"input_type": "UserRequestInput", "username": email}
        evt_i = {
            "input_type": "UserEventRequestInput",
            "directoryId": DIR_ID,
            "userName": email,
            "userEvents": [{
                "input_type": "UserEvent",
                "eventType": "PAGE_SUBMIT",
                "pageName": "CREDENTIAL_COLLECTION",
                "timeSpentOnPage": random.randint(8000, 25000)
            }]
        }

        # Try login path first (no /signup prefix)
        # For login: POST to /platform/{DIR_ID}/api/execute (no /signup prefix)
        req_id = _uuid()
        body = {
            "stepId": "get-new-password-for-password-creation",
            "workflowStateHandle": self.wsh or "",
            "actionId": "SUBMIT",
            "inputs": [pwd_i, evt_i, usr_i, fp_i2],
            "visitorId": self._tes_visitor_id or "",
            "requestId": req_id
        }
        
        # For LOGIN: use normal /api/execute endpoint with stepId='get-password'
        url = f"{SIGNIN}/platform/{DIR_ID}/api/execute"
        body["stepId"] = step_id  # use the actual stepId from step3 response ('get-password')
        self.log(f"  POST {url}")
        self.log(f"  stepId={step_id} wsh={str(self.wsh)[:40]}...")
        
        import curl_cffi.requests as _cr_req
        h = {**UA,
             "accept": "application/json, text/plain, */*",
             "content-type": "application/json; charset=UTF-8",
             "origin": SIGNIN,
             "x-amzn-requestid": req_id,
             "x-amz-date": time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime()),
             "referer": f"{SIGNIN}/platform/{DIR_ID}/login",
             "sec-fetch-site": "same-origin",
             "sec-fetch-mode": "cors",
             "sec-fetch-dest": "empty",
             "sec-gpc": "1",
             "priority": "u=1, i"}

        r = self.s.post(url, headers=h, json=body)
        self.log(f"  Status: {r.status_code}")
        self._capture_cookies(r)
        if r.status_code != 200:
            self.log(f"  ❌ {r.status_code}: {r.text[:500]}")
            return None, f"http_{r.status_code}"
        try:
            d = r.json()
        except:
            self.log(f"  ❌ non-JSON: {r.text[:300]}")
            return None, "non_json"
        if d.get("workflowStateHandle"):
            self.wsh = d["workflowStateHandle"]
        self.log(f"  → stepId={d.get('stepId')} sid={self.sid}")
        self.log(f"  Resp: {json.dumps(d, ensure_ascii=False)[:400]}")
        return d, None

    def set_otp_refresh_token(self, rt: str | None):
        self._otp_rt = rt

    def _get_otp(self, tag: str = "", timeout: int = 120) -> str | None:
        """Read OTP from outlook inbox via Graph API."""
        rt = getattr(self, "_otp_rt", None)
        if not rt:
            self.log(f"  ⚠️ No outlook refresh_token — cannot read OTP")
            return None
        try:
            from kiro_register import wait_for_aws_otp
        except ImportError:
            import importlib.util, pathlib
            spec = importlib.util.spec_from_file_location(
                "kiro_register", "/data/Toolkit/artifacts/api-server/kiro_register.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            wait_for_aws_otp = mod.wait_for_aws_otp
        return wait_for_aws_otp(rt, timeout=timeout, tag=tag)

    def _submit_otp(self, otp: str, prev_resp: dict):
        """Submit OTP to AWS /api/execute endpoint."""
        wsh = prev_resp.get("workflowStateHandle", self.wsh)
        step_id = prev_resp.get("stepId", "")
        url = f"{SIGNIN}/platform/{DIR_ID}/api/execute"
        body = {
            "workflowStateHandle": wsh,
            "stepId": step_id,
            "actionId": "SUBMIT",
            "formFields": [{"id": "otpCode", "value": otp}],
        }
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        }
        r = self.s.post(url, headers=h, json=body)
        self.log(f"  OTP submit Status: {r.status_code}")
        self._capture_cookies(r)
        if r.status_code != 200:
            self.log(f"  ❌ {r.status_code}: {r.text[:300]}")
            return None, f"http_{r.status_code}"
        try:
            d = r.json()
        except Exception:
            return None, "non_json"
        if d.get("workflowStateHandle"):
            self.wsh = d["workflowStateHandle"]
        return d, None

    def relogin(self, email, password, tag="relogin"):
        """
        Full re-login flow for existing kiro account.
        Returns tokens dict or None.
        """
        self.tag = tag
        self.log(f"[{tag}] Re-login: {email}")

        # Step 1: InitiateLogin
        redir_url = self.step1_kiro_init()
        if not redir_url:
            return None, "step1_failed"

        # Step 2: Get wsh
        if not self.step2_get_wsh(redir_url):
            return None, "step2_failed"

        time.sleep(random.uniform(1.0, 2.0))

        # Step 5: TES token (needed for fingerprint/visitorId)
        self.step5_get_tes_token()

        time.sleep(random.uniform(0.5, 1.5))

        # Step 3-LOGIN: Submit email to detect existing account
        r3 = self.step3_login_existing(email)
        if r3 is None:
            return None, "step3_failed"

        # Check if step3 gave us the password challenge directly
        r3_step = r3.get("stepId", "")
        if r3_step == "end-of-workflow-success":
            self.log(f"  ★ Already logged in via step3! sid={r3_step}")
            # Try to get tokens from step3 result
        elif "password" in r3_step.lower() or "credential" in r3_step.lower():
            self.log(f"  ✅ Password challenge detected: stepId={r3_step}")
        elif r3.get("redirect"):
            redir = r3["redirect"].get("url", "")
            self.log(f"  Step3 redirect: {redir[:100]}")
            # May need to follow redirect chain to reach password page
        else:
            self.log(f"  ⚠️ Unexpected step3 response: stepId={r3_step}")
            self.log(f"  Full: {json.dumps(r3, ensure_ascii=False)[:500]}")
            return None, f"step3_unexpected_{r3_step}"

        time.sleep(random.uniform(1.0, 2.5))

        # Step 4-LOGIN: Submit JWE password
        r4, err = self.step4_login_password(email, password, r3)
        if r4 is None:
            return None, f"step4_failed_{err}"

        # Step 4b-OTP: Handle email OTP challenge if returned by step4
        r4_step = r4.get("stepId", "") if isinstance(r4, dict) else ""
        if "otp" in r4_step.lower() or "credential" in r4_step.lower():
            self.log(f"  OTP challenge: stepId={r4_step}")
            otp = self._get_otp(tag=tag, timeout=120)
            if not otp:
                return None, "step4b_otp_timeout"
            r4, err = self._submit_otp(otp, r4)
            if r4 is None:
                return None, f"step4b_otp_submit_failed_{err}"
            self.log(f"  OTP submitted → stepId={r4.get('stepId') if isinstance(r4,dict) else '?'}")

        # Step 11: Final login
        r11 = self.step11_final_login(email, r4)
        if r11 is None:
            return None, "step11_failed"

        # Step 12: Get tokens
        tokens = self.step12_get_tokens()
        if not tokens:
            return None, "step12_failed"

        return tokens, None


def run_relogin(account_id, email, password, dry_run=False, ol_rt=None):
    """Re-login a single account. Returns (success, result_dict)."""
    tag = f"{account_id}"
    kr = KiroRelogin(tag=tag)
    kr.set_otp_refresh_token(ol_rt)
    try:
        tokens, err = kr.relogin(email=email, password=password, tag=tag)
        if err or not tokens:
            return False, {"error": err or "no_tokens", "account_id": account_id}
        
        access_token = tokens.get("accessToken", "")
        refresh_token = tokens.get("refreshToken", "")
        expires_in = tokens.get("expiresIn", 28800)
        
        if not access_token:
            return False, {"error": "empty_access_token", "account_id": account_id}
        
        log(f"✅ [{account_id}] {email}: got fresh token (rt={'yes' if refresh_token else 'no'}, exp={expires_in}s)", "OK")
        return True, {
            "account_id": account_id,
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in
        }
    except Exception as e:
        tb = traceback.format_exc()
        log(f"❌ [{account_id}] {email}: exception: {e}", "ERR")
        log(f"  {tb[:500]}", "DBG")
        return False, {"error": str(e), "account_id": account_id}


def main():
    parser = argparse.ArgumentParser(description="Re-authenticate kiro accounts")
    parser.add_argument("--limit", type=int, default=5, help="Max accounts to process")
    parser.add_argument("--id", type=int, default=None, help="Specific account ID")
    parser.add_argument("--dry-run", action="store_true", help="Don't update DB")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay between accounts (s)")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    accounts = get_accounts(conn, limit=args.limit, account_id=args.id)
    log(f"Found {len(accounts)} accounts to re-login")

    ok = 0
    failed = 0
    for row in accounts:
        acc_id, email, password, sub_status, token, rt, ol_rt = row
        log(f"Processing [{acc_id}] {email} (status={sub_status})")

        if not password:
            log(f"  ⚠️ No password stored, skipping", "WARN")
            failed += 1
            continue

        success, result = run_relogin(acc_id, email, password, dry_run=args.dry_run, ol_rt=ol_rt)
        
        if success and not args.dry_run:
            exp = datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 28800))
            update_token(
                conn, acc_id,
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token", ""),
                sub_status="pending"  # keep pending so sub_retry picks it up with fresh token
            )
            log(f"  ✅ DB updated for [{acc_id}]", "OK")
            ok += 1
        elif not success:
            log(f"  ❌ [{acc_id}] failed: {result.get('error')}", "ERR")
            failed += 1
        
        time.sleep(args.delay + random.uniform(0, 2))

    log(f"Done: ✅ok={ok} ❌failed={failed}")
    conn.close()


if __name__ == "__main__":
    main()
