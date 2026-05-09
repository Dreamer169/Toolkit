#!/usr/bin/env python3
"""
proxy_manager.py v1.2 -- Unified Proxy Manager

Sources:
  ip2free    -- residential SOCKS5 w/ auth (user:pass), NOT usable for ip2free registration
  local_xray -- local xray SOCKS5 ports 10850-10859, no restriction
  proxyscrape-- anonymous free SOCKS5, no restriction
  manual     -- manually added, no restriction

Platform exclusion rule:
  pick(not_for="ip2free")  --> exclude all proxies with source="ip2free"

Persistent DB:  /data/proxy_db.json
Account config: /data/proxy_accounts.json  (optional override of built-in list)

Library usage:
    from proxy_manager import ProxyManager
    pm = ProxyManager()
    proxy = pm.pick(not_for="ip2free")
    if proxy:
        use(proxy.socks5h_url)
        pm.report_success(proxy.uid)
    else:
        pm.report_failure(proxy.uid)

CLI:
    python3 proxy_manager.py status
    python3 proxy_manager.py refresh
    python3 proxy_manager.py refresh-source ip2free
    python3 proxy_manager.py probe [--force] [--workers 20]
    python3 proxy_manager.py pick [--not-for ip2free] [--country US] [--type residential]
    python3 proxy_manager.py load-file /tmp/ip2free_proxies_all.json [--source ip2free]
    python3 proxy_manager.py list [--not-for ip2free] [--alive-only] [--source ip2free]
    python3 proxy_manager.py inject-resi-pool [--not-for ip2free] [--max 40]
    python3 proxy_manager.py add socks5://user:pass@host:port [--not-for ip2free]
    python3 proxy_manager.py daemon [--interval 1800] [--probe-interval 600]
"""
from __future__ import annotations
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_FILE         = Path("/data/proxy_db.json")
ACCOUNTS_FILE   = Path("/data/proxy_accounts.json")  # optional override

PROBE_TARGET    = "http://www.gstatic.com/generate_204"
PROBE_TIMEOUT   = 5
PROBE_CACHE_TTL = 300    # seconds before re-probe
BLACKLIST_TTL   = 300    # seconds proxy stays blacklisted after FAIL_THRESHOLD
IP2FREE_STALE_DAYS   = 1.5   # ip2free rotates creds ~3x/day; >1.5d without re-verify = stale
FAIL_THRESHOLD  = 3

LOCAL_XRAY_PORTS: List[int] = list(range(10850, 10860))

IP2FREE_API     = "https://api.ip2free.com"
IP2FREE_HEADERS = {
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36",
    "Content-Type": "text/plain;charset=UTF-8",
    "Origin":       "https://www.ip2free.com",
    "Referer":      "https://www.ip2free.com/",
    "lang":         "cn",
    "domain":       "www.ip2free.com",
    "webname":      "IP2FREE",
    "affid":        "",
    "invitecode":   "",
    "serviceid":    "",
}

IP2FREE_ACCOUNTS_DEFAULT: List[Dict] = [
    # --- Verified working accounts (9 active as of 2026-05-09) ---
    # emily_gomez98: password changed/lost, login fails — kept commented for reference
    # {"email": "emily_gomez98@outlook.com",        "password": "inAyy$X87Uj^"},
    {"email": "sophiagray574@outlook.com",        "password": "8nQDovHvbR@%mWL$"},
    {"email": "e.lewis904@outlook.com",           "password": "Aa123456"},
    {"email": "rylan_rivera98@outlook.com",       "password": "AWgpis7xb0"},
    # --- 2026-05 batch (guerrillamailblock.com) ---
    {"email": "reg2026a1@guerrillamailblock.com", "password": "Reg2026@reg202X"},
    {"email": "reg2026b2@guerrillamailblock.com", "password": "Reg2026@reg202X"},
    {"email": "reg2026c3@guerrillamailblock.com", "password": "Reg2026@reg202X"},
    # --- Invite-registered accounts (wshu.net) ---
    {"email": "ip2r_ysrlrfeu@wshu.net",          "password": "Reg2026@Secure!"},
    {"email": "ip2r_7vgq5rxn@wshu.net",          "password": "Reg2026@Secure!"},
    {"email": "ip2r_lhs9p54x@wshu.net",          "password": "Reg2026@Secure!"},
    # --- DEAD: registration never completed (用户名不存在) ---
    # {"email": "5pygn9r8bhlie7@wshu.net",  "password": "JA%o#hCmBtA4$t"},
    # {"email": "fd46qce8g3fm5m@wshu.net",  "password": "AzPOjqy!htMXS8"},
    # {"email": "bjd6c2ayft0zr1@wshu.net",  "password": "Y23AbP%eR7Tey0"},
    # {"email": "caseyjon2860@cuvox.de",    "password": "F7C438aa1776R_Nb5Q"},
    # {"email": "jamesdav8027@dayrep.com",  "password": "1630C443a668anLwVw"},
    # {"email": "emilywan9588@teleworm.us", "password": "F31ACCf3a191Rcocug"},
]

PROXYSCRAPE_URLS = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
]

# Source -> list of platforms this source's proxies CANNOT be used for
EXCLUSION_RULES: Dict[str, List[str]] = {
    "ip2free":    ["ip2free"],
    "local_xray": [],
    "proxyscrape":[],
    "manual":     [],
}


# ---------------------------------------------------------------------------
# ProxyEntry
# ---------------------------------------------------------------------------

@dataclass
class ProxyEntry:
    uid:             str
    proto:           str            # socks5 / http
    host:            str
    port:            int
    user:            str  = ""      # "" = no auth
    passwd:          str  = ""
    source:          str  = "manual"
    source_account:  str  = ""      # which account provided this proxy
    country:         str  = ""      # ISO-2
    city:            str  = ""
    proxy_type:      str  = "unknown"   # residential / datacenter / unknown
    not_for:         List[str] = field(default_factory=list)
    added_ts:        float = field(default_factory=time.time)
    expire_ts:       Optional[float] = None   # None = no expiry
    last_probe_ts:   Optional[float] = None
    alive:           Optional[bool]  = None
    fail_count:      int = 0
    success_count:   int = 0
    blacklist_until: Optional[float] = None
    meta:            dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        if self.user and self.passwd:
            return f"{self.proto}://{self.user}:{self.passwd}@{self.host}:{self.port}"
        return f"{self.proto}://{self.host}:{self.port}"

    @property
    def socks5h_url(self) -> str:
        """socks5h:// URL — resolves hostnames through proxy (important for .onion / remote DNS)."""
        raw = self.url
        if raw.startswith("socks5://"):
            return "socks5h://" + raw[len("socks5://"):]
        if not raw.startswith("socks5h://"):
            return "socks5h://" + raw.split("://", 1)[-1]
        return raw

    def is_local(self) -> bool:
        return self.host in ("127.0.0.1", "localhost")

    def is_expired(self) -> bool:
        return self.expire_ts is not None and time.time() > self.expire_ts

    def is_blacklisted(self) -> bool:
        return self.blacklist_until is not None and time.time() < self.blacklist_until

    @property
    def stale_days(self) -> float:
        """Days since ip2free last verified this proxy (meta.last_checked_at, UTC+8)."""
        lc = self.meta.get("last_checked_at", "")
        if not lc:
            return 0.0
        try:
            dt = datetime.datetime.strptime(str(lc), "%Y-%m-%d %H:%M:%S")
            dt_utc = dt - datetime.timedelta(hours=8)   # ip2free API is UTC+8
            return (datetime.datetime.utcnow() - dt_utc).total_seconds() / 86400
        except Exception:
            return 0.0

    def is_stale(self) -> bool:
        """True when ip2free hasn't re-verified this proxy recently (creds likely rotated)."""
        if self.source != "ip2free":
            return False
        lc = self.meta.get("last_checked_at", "")
        return bool(lc) and self.stale_days > IP2FREE_STALE_DAYS

    def needs_probe(self) -> bool:
        if self.last_probe_ts is None:
            return True
        return (time.time() - self.last_probe_ts) > PROBE_CACHE_TTL

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyEntry":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def one_line(self) -> str:
        alive_s = "OK " if self.alive else ("ERR" if self.alive is False else "?  ")
        url = f"socks5h://127.0.0.1:{self.port}" if self.is_local() else self.socks5h_url
        not_for_s = ",".join(self.not_for) if self.not_for else "-"
        return (f"[{alive_s}] [{self.source:<12}] {self.country:<3} "
                f"{self.proxy_type:<12} not_for={not_for_s:<10}  {url}")


# ---------------------------------------------------------------------------
# ProxyDB  (thread-safe JSON store)
# ---------------------------------------------------------------------------

class ProxyDB:
    def __init__(self, path: Path = DB_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, ProxyEntry] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            for uid, d in raw.get("proxies", {}).items():
                try:
                    self._data[uid] = ProxyEntry.from_dict(d)
                except Exception:
                    pass
        except Exception as e:
            print(f"[proxy_db] load error: {e}", file=sys.stderr)

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version":  1,
                "saved_at": time.time(),
                "total":    len(self._data),
                "proxies":  {uid: e.to_dict() for uid, e in self._data.items()},
            }
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            tmp.replace(self.path)
        except Exception as e:
            print(f"[proxy_db] save error: {e}", file=sys.stderr)

    def all(self) -> List[ProxyEntry]:
        with self._lock:
            return list(self._data.values())

    def get(self, uid: str) -> Optional[ProxyEntry]:
        with self._lock:
            return self._data.get(uid)

    def put(self, entry: ProxyEntry, save: bool = True):
        with self._lock:
            self._data[entry.uid] = entry
            if save:
                self._save()

    def put_many(self, entries: List[ProxyEntry]):
        with self._lock:
            for e in entries:
                self._data[e.uid] = e
            self._save()

    def delete(self, uid: str):
        with self._lock:
            self._data.pop(uid, None)
            self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._data)


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def _probe_url(proxy_url: str, timeout: int = PROBE_TIMEOUT) -> bool:
    """Return True if proxy can reach PROBE_TARGET."""
    try:
        p = subprocess.Popen(
            ["curl", "-s", "--max-time", str(timeout),
             "--proxy", proxy_url,
             "-o", "/dev/null", "-w", "%{http_code}", PROBE_TARGET],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = p.communicate(timeout=timeout + 2)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); return False
        return out.decode().strip() not in ("", "000")
    except Exception:
        return False


def probe_entry(entry: ProxyEntry) -> bool:
    if entry.is_local():
        return _probe_url(f"socks5h://127.0.0.1:{entry.port}")
    return _probe_url(entry.socks5h_url)


def _parse_expire(raw) -> Optional[float]:
    """Parse expiry string into Unix timestamp, or None if no expiry."""
    if not raw:
        return None
    try:
        import datetime
        s = str(raw).strip()
        if s in ("", "0", "null", "None"):
            return None
        s = s.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ProxyManager
# ---------------------------------------------------------------------------

class ProxyManager:
    """
    Unified proxy manager.

    Quick start:
        pm = ProxyManager()
        pm.refresh_all()                    # pull from all sources
        proxy = pm.pick(not_for="ip2free")  # exclude ip2free-sourced proxies
        if proxy:
            requests.get(url, proxies={"https": proxy.socks5h_url})
            pm.report_success(proxy.uid)
    """

    def __init__(self, db_path: Path = DB_FILE):
        self.db = ProxyDB(db_path)
        self._rr_idx  = 0
        self._rr_lock = threading.Lock()
        self._ip2free_accounts = self._load_ip2free_accounts()

    def _load_ip2free_accounts(self) -> List[dict]:
        if ACCOUNTS_FILE.exists():
            try:
                data = json.loads(ACCOUNTS_FILE.read_text())
                accts = data.get("ip2free", [])
                if accts:
                    return accts
            except Exception:
                pass
        return IP2FREE_ACCOUNTS_DEFAULT

    # ------------------------------------------------------------------
    # Source: local xray ports
    # ------------------------------------------------------------------

    def refresh_local_xray(self, log_fn=None) -> int:
        log = log_fn or (lambda m: print(f"[local_xray] {m}"))
        added = 0
        for port in LOCAL_XRAY_PORTS:
            uid = f"local_xray:{port}"
            if self.db.get(uid) is None:
                self.db.put(ProxyEntry(
                    uid=uid, proto="socks5",
                    host="127.0.0.1", port=port,
                    source="local_xray", proxy_type="residential",
                    not_for=list(EXCLUSION_RULES["local_xray"]),
                ), save=False)
                added += 1
        if added:
            self.db._save()
        log(f"{len(LOCAL_XRAY_PORTS)} ports registered (+{added} new)")
        return added

    # ------------------------------------------------------------------
    # Source: ip2free  (13 accounts, residential SOCKS5 w/ auth)
    # ------------------------------------------------------------------

    def refresh_ip2free(self, log_fn=None) -> int:
        log = log_fn or (lambda m: print(f"[ip2free] {m}"))
        try:
            import requests
            import urllib3; urllib3.disable_warnings()
            return self._refresh_ip2free_requests(requests, log)
        except ImportError:
            return self._refresh_ip2free_urllib(log)

    def _refresh_ip2free_requests(self, requests, log) -> int:
        added = updated = 0
        seen: set = set()
        for acct in self._ip2free_accounts:
            email = acct["email"]
            try:
                s = requests.Session(); s.verify = False
                s.headers.update(IP2FREE_HEADERS)
                r = s.post(IP2FREE_API + "/api/account/login?",
                           data=json.dumps({"email": email, "password": acct["password"]}),
                           timeout=15)
                tok = r.json().get("data", {}).get("token")
                if not tok:
                    log(f"  login fail {email}: {r.json().get('msg','?')}"); continue
                s.headers["x-token"] = tok

                items: List[dict] = []
                for ep in ["/api/ip/freeList", "/api/ip/activeList", "/api/activity/list"]:
                    try:
                        d2 = s.post(IP2FREE_API + ep + "?",
                                    data=json.dumps({"size": 200}), timeout=15).json()
                        lst = d2.get("data") or {}
                        if isinstance(lst, dict):
                            items = (lst.get("free_ip_list") or lst.get("list") or
                                     lst.get("data") or [])
                        elif isinstance(lst, list):
                            items = lst
                        if items:
                            break
                    except Exception:
                        pass

                log(f"  {email}: {len(items)} proxies")
                for p in items:
                    raw_uid = (p.get("proxy_uid") or p.get("id")
                               or f"{p.get('ip')}:{p.get('port')}")
                    uid = f"ip2free:{raw_uid}"
                    if uid in seen:
                        continue
                    seen.add(uid)
                    expire_ts = _parse_expire(p.get("expire_time") or p.get("expires_at"))
                    ex = self.db.get(uid)
                    lca = p.get("last_checked_at", "")
                    if ex is None:
                        self.db.put(ProxyEntry(
                            uid=uid,
                            proto=p.get("protocol", "socks5"),
                            host=p.get("ip", ""), port=int(p.get("port", 0)),
                            user=p.get("username", ""), passwd=p.get("password", ""),
                            source="ip2free", source_account=email,
                            country=p.get("country_code", ""), city=p.get("city", ""),
                            proxy_type="residential",
                            not_for=list(EXCLUSION_RULES["ip2free"]),
                            expire_ts=expire_ts,
                            meta={"proxy_uid": str(raw_uid), "is_new": p.get("is_new", 0),
                                  "status": p.get("status", 1), "last_checked_at": lca},
                        ), save=False); added += 1
                    else:
                        changed = False
                        new_u = p.get("username", ""); new_p = p.get("password", "")
                        old_lca = ex.meta.get("last_checked_at", "")
                        if new_u and (ex.user != new_u or ex.passwd != new_p):
                            ex.user = new_u; ex.passwd = new_p; changed = True
                        if lca and lca != old_lca:
                            ex.meta["last_checked_at"] = lca
                            if ex.alive is False:
                                ex.alive = None; ex.fail_count = 0; ex.blacklist_until = None
                                log(f"  rotation: {uid} lca {old_lca!r} -> {lca!r}")
                            changed = True
                        if expire_ts != ex.expire_ts:
                            ex.expire_ts = expire_ts; changed = True
                        if changed:
                            self.db.put(ex, save=False); updated += 1
            except Exception as e:
                log(f"  {email} error: {type(e).__name__}: {str(e)[:80]}")

        self.db._save()
        log(f"Done: +{added} new, {updated} updated")
        return added

    def _refresh_ip2free_urllib(self, log) -> int:
        added = 0
        for acct in self._ip2free_accounts:
            email = acct["email"]
            try:
                req = urllib.request.Request(
                    IP2FREE_API + "/api/account/login?",
                    data=json.dumps({"email": email, "password": acct["password"]}).encode(),
                    headers={**IP2FREE_HEADERS, "Content-Type": "text/plain;charset=UTF-8"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    tok = json.loads(resp.read()).get("data", {}).get("token")
                if not tok:
                    log(f"  login fail {email}"); continue
                req2 = urllib.request.Request(
                    IP2FREE_API + "/api/ip/freeList?",
                    data=json.dumps({"size": 200}).encode(),
                    headers={**IP2FREE_HEADERS, "x-token": tok})
                with urllib.request.urlopen(req2, timeout=15) as resp:
                    items = json.loads(resp.read()).get("data", {}).get("free_ip_list", [])
                log(f"  {email}: {len(items)} proxies")
                for p in items:
                    raw_uid = p.get("proxy_uid") or f"{p.get('ip')}:{p.get('port')}"
                    uid = f"ip2free:{raw_uid}"
                    if not self.db.get(uid):
                        self.db.put(ProxyEntry(
                            uid=uid, proto=p.get("protocol","socks5"),
                            host=p.get("ip",""), port=int(p.get("port",0)),
                            user=p.get("username",""), passwd=p.get("password",""),
                            source="ip2free", source_account=email,
                            country=p.get("country_code",""), city=p.get("city",""),
                            proxy_type="residential",
                            not_for=list(EXCLUSION_RULES["ip2free"]),
                            expire_ts=_parse_expire(p.get("expire_time")),
                            meta={"proxy_uid": str(raw_uid)},
                        ), save=False); added += 1
            except Exception as e:
                log(f"  {email} error: {e}")
        self.db._save()
        log(f"Done (urllib): +{added} new")
        return added

    # ------------------------------------------------------------------
    # Source: proxyscrape  (anonymous free SOCKS5)
    # ------------------------------------------------------------------

    def refresh_proxyscrape(self, max_inject: int = 30, log_fn=None) -> int:
        log = log_fn or (lambda m: print(f"[proxyscrape] {m}"))
        raw: set = set()
        for url in PROXYSCRAPE_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    for line in r.read().decode("utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if ":" in line and not line.startswith("#"):
                            raw.add(line)
            except Exception as e:
                log(f"  fetch err: {e}")

        import random
        candidates = list(raw); random.shuffle(candidates)
        candidates = candidates[:min(max_inject * 5, 200)]
        log(f"Fetched {len(raw)} candidates, probing {len(candidates)} (want {max_inject})")

        added = 0

        def _probe(ps: str) -> Optional[str]:
            return ps if _probe_url(f"socks5h://{ps}", timeout=3) else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
            futs = {ex.submit(_probe, p): p for p in candidates}
            try:
                for fut in concurrent.futures.as_completed(futs, timeout=60):
                    result = fut.result()
                    if result:
                        h, pt = result.rsplit(":", 1)
                        uid = f"proxyscrape:{result}"
                        if not self.db.get(uid):
                            self.db.put(ProxyEntry(
                                uid=uid, proto="socks5",
                                host=h, port=int(pt),
                                source="proxyscrape", proxy_type="unknown",
                                not_for=list(EXCLUSION_RULES["proxyscrape"]),
                                alive=True, last_probe_ts=time.time(),
                            ), save=False); added += 1
                        if added >= max_inject:
                            break
            except concurrent.futures.TimeoutError:
                log("probe timeout 60s")
            finally:
                for f in futs: f.cancel()
                ex.shutdown(wait=False)

        self.db._save()
        log(f"Done: +{added} new")
        return added

    # ------------------------------------------------------------------
    # Import from JSON file
    # ------------------------------------------------------------------

    def load_from_json_file(self, path: str, source: str = "ip2free",
                            log_fn=None) -> int:
        """
        Import proxies from a saved JSON file.
        Supports /tmp/ip2free_proxies_all.json,  /tmp/ip2free_proxies_live.json, etc.
        """
        log = log_fn or (lambda m: print(f"[load_json] {m}"))
        try:
            data = json.loads(Path(path).read_text())
        except Exception as e:
            log(f"Error reading {path}: {e}"); return 0

        items = data.get("proxies", [])
        proxy_type = "residential" if source == "ip2free" else "unknown"
        not_for    = list(EXCLUSION_RULES.get(source, []))
        added = 0
        for p in items:
            raw_uid = (p.get("proxy_uid") or p.get("id")
                       or f"{p.get('ip')}:{p.get('port')}")
            uid = f"{source}:{raw_uid}"
            if self.db.get(uid):
                continue
            h = p.get("ip",""); pt = int(p.get("port",0) or 0)
            if not h or not pt:
                continue
            self.db.put(ProxyEntry(
                uid=uid, proto=p.get("protocol","socks5"),
                host=h, port=pt,
                user=p.get("username",""), passwd=p.get("password",""),
                source=source, source_account=p.get("source_account",""),
                country=p.get("country_code",""), city=p.get("city",""),
                proxy_type=proxy_type, not_for=not_for,
                expire_ts=_parse_expire(p.get("expire_time") or p.get("expires_at")),
                meta={"proxy_uid": str(raw_uid)},
            ), save=False); added += 1
        self.db._save()
        log(f"+{added} from {path}  (source={source})")
        return added

    # ------------------------------------------------------------------
    # Refresh all
    # ------------------------------------------------------------------

    def refresh_all(self, log_fn=None) -> dict:
        log = log_fn or print
        log("=" * 55)
        log("[proxy_manager] Refreshing all proxy sources...")
        results = {}
        results["local_xray"]  = self.refresh_local_xray(log_fn=log)
        results["ip2free"]     = self.refresh_ip2free(log_fn=log)
        results["proxyscrape"] = self.refresh_proxyscrape(log_fn=log)
        # Auto-load temp files if present
        for f in ["/tmp/ip2free_proxies_all.json",
                  "/tmp/ip2free_proxies_live.json",
                  "/tmp/ip2free_proxies.json"]:
            if Path(f).exists():
                n = self.load_from_json_file(f, source="ip2free", log_fn=log)
                results[f"file:{Path(f).name}"] = n
        log(f"[proxy_manager] Refresh complete: {results}")
        return results

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self, uid: str, force: bool = False) -> Optional[bool]:
        e = self.db.get(uid)
        if not e:
            return None
        if not force and not e.needs_probe():
            return e.alive
        alive = probe_entry(e)
        e.alive = alive; e.last_probe_ts = time.time()
        if not alive:
            e.fail_count += 1
            if e.fail_count >= FAIL_THRESHOLD:
                e.blacklist_until = time.time() + BLACKLIST_TTL
        else:
            e.fail_count = 0; e.blacklist_until = None
        self.db.put(e)
        return alive

    def probe_all(self, max_workers: int = 20, force: bool = False,
                  log_fn=None) -> dict:
        log = log_fn or (lambda m: print(f"[probe_all] {m}"))
        _all = [e for e in self.db.all()
                if not e.is_expired() and (force or e.needs_probe())]
        stale = [e for e in _all if e.is_stale()]
        entries = [e for e in _all if not e.is_stale()]
        if stale:
            log(f"Skipping {len(stale)} stale ip2free proxies (await credential rotation)")
            for e in stale:
                e.alive = False; e.fail_count = max(e.fail_count, 1)
                self.db.put(e, save=False)
        log(f"Probing {len(entries)} entries (workers={max_workers})...")
        results = {"probed": 0, "alive": 0, "dead": 0}

        def _do(e: ProxyEntry):
            alive = probe_entry(e)
            e.alive = alive; e.last_probe_ts = time.time()
            if not alive:
                e.fail_count += 1
                if e.fail_count >= FAIL_THRESHOLD:
                    e.blacklist_until = time.time() + BLACKLIST_TTL
            else:
                e.fail_count = 0; e.blacklist_until = None
            self.db.put(e, save=False)
            return alive

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_do, e): e for e in entries}
            try:
                for fut in concurrent.futures.as_completed(futs, timeout=120):
                    ok = fut.result()
                    results["probed"] += 1
                    results["alive" if ok else "dead"] += 1
            except concurrent.futures.TimeoutError:
                log("probe round timed out at 120s")

        self.db._save()
        log(f"Done: {results}")
        return results

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _filter(self, not_for: Optional[str] = None,
                country: Optional[str] = None,
                proxy_type: Optional[str] = None,
                source: Optional[str] = None) -> List[ProxyEntry]:
        result = []
        for e in self.db.all():
            if e.is_expired():      continue
            if e.is_blacklisted():  continue
            if not e.host or not e.port: continue
            # Platform exclusion: skip if caller's platform is in proxy's not_for
            if not_for and not_for in e.not_for: continue
            if country    and e.country.upper() != country.upper(): continue
            if proxy_type and e.proxy_type != proxy_type:           continue
            if source     and e.source != source:                   continue
            result.append(e)
        return result

    def pick(self,
             not_for:         Optional[str]  = None,
             country:         Optional[str]  = None,
             proxy_type:      Optional[str]  = None,
             source:          Optional[str]  = None,
             probe_if_unknown: bool = True) -> Optional[ProxyEntry]:
        """
        Pick best available proxy.

        Args:
            not_for:          Platform name to exclude by source
                              (e.g. "ip2free" excludes ip2free-sourced proxies)
            country:          ISO-2 country filter (e.g. "US")
            proxy_type:       "residential" / "datacenter" / "unknown"
            source:           Exact source filter
            probe_if_unknown: Background-probe entry if alive status not yet known

        Priority:  alive=True > alive=None > alive=False
                   residential > unknown > datacenter
                   Round-robin within each tier
        """
        candidates = self._filter(not_for=not_for, country=country,
                                   proxy_type=proxy_type, source=source)
        if not candidates:
            return None

        def _key(e: ProxyEntry) -> tuple:
            ap = 0 if e.alive is True else (1 if e.alive is None else 2)
            tp = 0 if e.proxy_type == "residential" else (1 if e.proxy_type == "unknown" else 2)
            return (ap, tp, e.fail_count)

        candidates.sort(key=_key)

        with self._rr_lock:
            self._rr_idx += 1
            alive_ok = [e for e in candidates if e.alive is True]
            if alive_ok:
                return alive_ok[self._rr_idx % len(alive_ok)]
            unk_ok = [e for e in candidates if e.alive is None]
            if unk_ok:
                e = unk_ok[self._rr_idx % len(unk_ok)]
                if probe_if_unknown:
                    threading.Thread(target=self.probe, args=(e.uid,), daemon=True).start()
                return e
            return candidates[0]  # all dead — return anyway (may have recovered)

    def pick_url(self, not_for: Optional[str] = None, **kwargs) -> Optional[str]:
        """Return proxy socks5h:// URL or None."""
        e = self.pick(not_for=not_for, **kwargs)
        if e is None:
            return None
        return f"socks5h://127.0.0.1:{e.port}" if e.is_local() else e.socks5h_url

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def report_success(self, uid: str):
        e = self.db.get(uid)
        if not e: return
        e.success_count += 1; e.fail_count = 0
        e.alive = True; e.blacklist_until = None
        self.db.put(e)

    def report_failure(self, uid: str):
        e = self.db.get(uid)
        if not e: return
        e.fail_count += 1
        e.alive = False if e.fail_count >= FAIL_THRESHOLD else e.alive
        if e.fail_count >= FAIL_THRESHOLD:
            e.blacklist_until = time.time() + BLACKLIST_TTL
        self.db.put(e)

    # ------------------------------------------------------------------
    # resi_pool bridge
    # ------------------------------------------------------------------

    def inject_resi_pool(self, not_for: Optional[str] = None,
                         max_inject: int = 40, log_fn=None) -> int:
        """
        Inject live external proxies into resi_pool.
        Writes /tmp/resi_pool_external.json AND calls resi_pool.add_external().
        Authenticated proxies are stored as "user:pass@host:port" strings —
        resi_pool.proxy_url() already handles that format correctly.
        """
        log = log_fn or (lambda m: print(f"[inject_resi_pool] {m}"))
        candidates = [e for e in self._filter(not_for=not_for)
                      if not e.is_local() and e.alive is not False]

        ext_list = []
        for e in candidates[:max_inject]:
            if e.user and e.passwd:
                ext_list.append(f"{e.user}:{e.passwd}@{e.host}:{e.port}")
            else:
                ext_list.append(f"{e.host}:{e.port}")

        # Write resi_pool external file
        ext_file = Path("/tmp/resi_pool_external.json")
        try:
            ext_file.write_text(json.dumps(
                {"proxies": ext_list, "ts": time.time(),
                 "source": "proxy_manager"},
                indent=2))
        except Exception as ex:
            log(f"write ext file error: {ex}")

        # Live-inject into resi_pool if available
        try:
            _scripts = str(Path(__file__).parent)
            if _scripts not in sys.path:
                sys.path.insert(0, _scripts)
            import resi_pool as rp
            injected = 0
            for entry in ext_list[:max_inject]:
                # Use add_external_full to preserve user:pass credentials
                if hasattr(rp, "add_external_full"):
                    rp.add_external_full(entry, probe=False)
                else:
                    # Fallback: parse host:port (loses auth but keeps compat)
                    hp = entry.rsplit("@", 1)[-1]
                    h, p = hp.rsplit(":", 1)
                    rp.add_external(h, int(p), probe=False)
                injected += 1
            log(f"Live-injected {injected} into resi_pool; ext_file={len(ext_list)}")
        except Exception as ex:
            log(f"resi_pool live-inject warn: {ex} (ext_file still written)")

        return len(ext_list)

    # ------------------------------------------------------------------
    # Status & display
    # ------------------------------------------------------------------

    def status(self) -> dict:
        all_e = self.db.all()
        by_source: Dict[str, dict] = {}
        for e in all_e:
            s = e.source
            if s not in by_source:
                by_source[s] = {"total":0,"alive":0,"dead":0,
                                "unknown":0,"expired":0,"blacklisted":0,"stale":0,
                                "countries": []}
            d = by_source[s]; d["total"] += 1
            if e.country and e.country not in d["countries"]:
                d["countries"].append(e.country)
            if e.is_expired():       d["expired"] += 1
            elif e.is_stale():       d["stale"] += 1
            elif e.is_blacklisted(): d["blacklisted"] += 1
            elif e.alive is True:    d["alive"] += 1
            elif e.alive is False:   d["dead"] += 1
            else:                    d["unknown"] += 1
        return {"total": len(all_e), "by_source": by_source,
                "db_path": str(self.db.path)}

    def print_status(self):
        st = self.status()
        print(f"\n{'='*70}")
        print(f" proxy_manager  DB: {st['db_path']}  total={st['total']}")
        print(f"{'='*70}")
        print(f" {'Source':<14} {'Total':>5} {'Alive':>5} {'Dead':>5} "
              f"{'Stale':>5} {'Unk':>5} {'Exp':>5} {'BL':>4}  Countries")
        print(f" {'-'*76}")
        for src, d in sorted(st["by_source"].items()):
            cc = ",".join(sorted(d["countries"])[:8])
            print(f" {src:<14} {d['total']:>5} {d['alive']:>5} {d['dead']:>5} "
                  f"{d.get('stale',0):>5} {d['unknown']:>5} {d['expired']:>5} "
                  f"{d['blacklisted']:>4}  {cc}")
        print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    if argv is None:
        argv = sys.argv[1:]

    ap = argparse.ArgumentParser(
        description="proxy_manager.py v1.0 — Unified Proxy Manager")
    ap.add_argument("--db", default=str(DB_FILE))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status",  help="Print status table")
    sub.add_parser("refresh", help="Refresh all sources")

    p_rs = sub.add_parser("refresh-source", help="Refresh one source")
    p_rs.add_argument("source",
                      choices=["ip2free","proxyscrape","local_xray"])

    p_probe = sub.add_parser("probe", help="Probe all proxies")
    p_probe.add_argument("--force",   action="store_true")
    p_probe.add_argument("--workers", type=int, default=20)

    p_pick = sub.add_parser("pick", help="Pick a proxy URL")
    p_pick.add_argument("--not-for",  default=None,
                        help="Exclude proxies from this platform (e.g. ip2free)")
    p_pick.add_argument("--country",  default=None, help="ISO-2 country code")
    p_pick.add_argument("--type",     default=None, dest="proxy_type",
                        choices=["residential","datacenter","unknown"])
    p_pick.add_argument("--source",   default=None)

    p_lf = sub.add_parser("load-file", help="Load proxies from JSON file")
    p_lf.add_argument("file")
    p_lf.add_argument("--source", default="ip2free")

    p_list = sub.add_parser("list", help="List filtered proxies")
    p_list.add_argument("--not-for",    default=None)
    p_list.add_argument("--source",     default=None)
    p_list.add_argument("--alive-only", action="store_true")
    p_list.add_argument("--json",       action="store_true")

    p_inj = sub.add_parser("inject-resi-pool",
                            help="Inject proxies into resi_pool external file")
    p_inj.add_argument("--not-for", default=None)
    p_inj.add_argument("--max",     type=int, default=40)

    p_add = sub.add_parser("add", help="Manually add a proxy")
    p_add.add_argument("url",      help="socks5://[user:pass@]host:port")
    p_add.add_argument("--not-for",   default="",
                       help="Comma-separated platform names to exclude")
    p_add.add_argument("--country",   default="")
    p_add.add_argument("--type",      default="unknown", dest="proxy_type")

    p_daemon = sub.add_parser("daemon",
                               help="Run refresh+probe daemon")
    p_daemon.add_argument("--interval",       type=int, default=1800,
                          help="Refresh interval seconds (default=1800=30min)")
    p_daemon.add_argument("--probe-interval", type=int, default=600,
                          help="Probe interval seconds (default=600=10min)")

    args = ap.parse_args(argv)
    pm = ProxyManager(Path(args.db))

    if args.cmd == "status":
        pm.print_status(); return 0

    if args.cmd == "refresh":
        pm.refresh_all(log_fn=print); pm.print_status(); return 0

    if args.cmd == "refresh-source":
        fn = getattr(pm, f"refresh_{args.source}", None)
        if fn:
            fn(log_fn=print)
        pm.print_status(); return 0

    if args.cmd == "probe":
        r = pm.probe_all(max_workers=args.workers, force=args.force, log_fn=print)
        print(json.dumps(r, indent=2)); pm.print_status(); return 0

    if args.cmd == "pick":
        e = pm.pick(not_for=args.not_for, country=args.country,
                    proxy_type=args.proxy_type, source=args.source,
                    probe_if_unknown=False)
        if e:
            url = f"socks5h://127.0.0.1:{e.port}" if e.is_local() else e.socks5h_url
            print(url)
            print(f"  uid={e.uid}", file=sys.stderr)
            print(f"  source={e.source}  not_for={e.not_for}", file=sys.stderr)
            print(f"  country={e.country}  type={e.proxy_type}  alive={e.alive}",
                  file=sys.stderr)
            return 0
        print("None — no matching proxy available"); return 1

    if args.cmd == "load-file":
        pm.load_from_json_file(args.file, source=args.source, log_fn=print)
        pm.print_status(); return 0

    if args.cmd == "list":
        entries = pm._filter(not_for=args.not_for, source=args.source)
        if args.alive_only:
            entries = [e for e in entries if e.alive is True]
        if args.json:
            print(json.dumps([e.to_dict() for e in entries],
                              indent=2, ensure_ascii=False))
        else:
            print(f"  {'Source':<12} {'CC':<3} {'Type':<12} {'Alive':<5} "
                  f"{'Fails':>5}  {'not_for':<12}  URL")
            print("  " + "-"*90)
            for e in entries:
                url = (f"socks5h://127.0.0.1:{e.port}" if e.is_local()
                       else e.socks5h_url)
                alive_s = "OK " if e.alive else ("ERR" if e.alive is False else "?  ")
                nf = ",".join(e.not_for) if e.not_for else "-"
                print(f"  {e.source:<12} {e.country:<3} {e.proxy_type:<12} "
                      f"{alive_s:<5} {e.fail_count:>5}  {nf:<12}  {url}")
        return 0

    if args.cmd == "inject-resi-pool":
        n = pm.inject_resi_pool(not_for=args.not_for, max_inject=args.max,
                                log_fn=print)
        print(f"Injected {n} proxies into resi_pool"); return 0

    if args.cmd == "add":
        import re
        m = re.match(r"(\w+)://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)", args.url)
        if not m:
            print(f"Cannot parse URL: {args.url}"); return 1
        proto, user, passwd, host, port = m.groups()
        uid = f"manual:{host}:{port}"
        nfl = [x.strip() for x in args.not_for.split(",") if x.strip()]
        pm.db.put(ProxyEntry(
            uid=uid, proto=proto or "socks5",
            host=host, port=int(port),
            user=user or "", passwd=passwd or "",
            source="manual", proxy_type=args.proxy_type,
            country=args.country, not_for=nfl,
        ))
        print(f"Added {uid}"); return 0

    if args.cmd == "daemon":
        _run_daemon(pm, args.interval, args.probe_interval); return 0

    return 1


def _run_daemon(pm: ProxyManager, refresh_interval: int,
                probe_interval: int):
    import datetime
    def ts():
        return datetime.datetime.now().strftime("[%H:%M:%S]")

    print(f"{ts()} [daemon] Starting  refresh={refresh_interval}s  "
          f"probe={probe_interval}s")
    last_refresh = 0.0
    last_probe   = 0.0

    while True:
        now = time.time()
        if now - last_refresh >= refresh_interval:
            print(f"{ts()} [daemon] Refreshing sources...")
            try:
                pm.refresh_all(log_fn=lambda m: print(f"{ts()} {m}"))
            except Exception as e:
                print(f"{ts()} [daemon] refresh error: {e}")
            last_refresh = time.time()

        if now - last_probe >= probe_interval:
            print(f"{ts()} [daemon] Probing all proxies...")
            try:
                r = pm.probe_all(max_workers=25,
                                 log_fn=lambda m: print(f"{ts()} {m}"))
                print(f"{ts()} [daemon] probe result: {r}")
                pm.inject_resi_pool(log_fn=lambda m: print(f"{ts()} {m}"))
            except Exception as e:
                print(f"{ts()} [daemon] probe error: {e}")
            last_probe = time.time()
            pm.print_status()

        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
