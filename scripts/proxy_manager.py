#!/usr/bin/env python3
"""
proxy_manager.py v2.0 -- Unified Proxy Manager

Sources:
  ip2free    -- residential SOCKS5 w/ auth, NOT for ip2free registration
  local_xray -- local xray SOCKS5 ports 10820-10889 (auto-discover alive), no restriction
  proxyscrape-- anonymous free SOCKS5, no restriction
  webshare   -- Webshare HTTP datacenter proxies, NOT for webshare registration
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

LOCAL_XRAY_PORTS: List[int] = list(range(10850, 10860))  # legacy; auto-discover uses LOCAL_XRAY_PORT_RANGE

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
# ---------------------------------------------------------------------------
# Webshare
# ---------------------------------------------------------------------------

WEBSHARE_API_KEY  = "lx7r5124cubob5mfmofbdtjvdti5bqy2lxdg06ho"
WEBSHARE_API_BASE = "https://proxy.webshare.io/api/v2"

# ---------------------------------------------------------------------------
# SQLite integration (api-server DB)
# ---------------------------------------------------------------------------

SQLITE_DB = Path("/data/Toolkit/artifacts/api-server/data.db")

# ---------------------------------------------------------------------------
# Local xray port discovery range (probed at runtime)
# ---------------------------------------------------------------------------

LOCAL_XRAY_PORT_RANGE: List[int] = list(range(10820, 10890))

# ---------------------------------------------------------------------------
# Per-platform proxy selection policies
# ---------------------------------------------------------------------------
# Each entry: preferred_sources (tried in order), preferred_types (tried in order).
# The not_for mechanism on ProxyEntry already handles source-level exclusions
# (e.g. ip2free proxies have not_for=["ip2free"]).
# pick_for(platform) uses both this policy AND per-entry not_for checks.

PLATFORM_POLICIES: Dict[str, dict] = {
    "ip2free": {
        "preferred_sources": ["local_xray", "proxyscrape", "webshare"],
        "preferred_types":   ["residential", "unknown"],
        "notes": "Can't use ip2free-sourced proxies (self-referential)",
    },
    "webshare": {
        "preferred_sources": ["local_xray", "ip2free", "proxyscrape"],
        "preferred_types":   ["residential", "unknown"],
        "notes": "Can't use webshare-sourced proxies (self-referential)",
    },
    "outlook": {
        "preferred_sources": ["local_xray", "ip2free"],
        "preferred_types":   ["residential", "unknown"],
        "notes": "Outlook blocks most datacenter IPs; must use residential; webshare datacenter excluded",
    },
    "obvious": {
        "preferred_sources": ["local_xray", "ip2free", "webshare", "proxyscrape"],
        "preferred_types":   ["residential", "unknown", "datacenter"],
        "notes": "AI coding sandbox — any proxy OK",
    },
    "airforce": {
        "preferred_sources": ["local_xray", "ip2free", "webshare", "proxyscrape"],
        "preferred_types":   ["residential", "unknown", "datacenter"],
        "notes": "AI proxy — any proxy OK",
    },
    "talordata": {
        "preferred_sources": ["local_xray", "ip2free"],
        "preferred_types":   ["residential", "unknown"],
        "notes": "E-commerce — must be residential; webshare datacenter excluded",
    },
    "unitool": {
        "preferred_sources": ["local_xray", "ip2free", "webshare", "proxyscrape"],
        "preferred_types":   ["residential", "unknown", "datacenter"],
        "notes": "AI tool — any proxy OK",
    },
    "replit": {
        "preferred_sources": ["local_xray", "ip2free"],
        "preferred_types":   ["residential", "unknown"],
        "notes": "Developer platform — must be residential; webshare datacenter excluded",
    },
    "generic": {
        "preferred_sources": ["local_xray", "ip2free", "webshare", "proxyscrape"],
        "preferred_types":   ["residential", "unknown", "datacenter"],
        "notes": "Generic fallback — any proxy OK",
    },
}

# ---------------------------------------------------------------------------
# Proxy limit rules
# ---------------------------------------------------------------------------

@dataclass
class ProxyLimitRule:
    """Describes operational limits for a proxy source."""
    source:           str
    max_ttl_hours:    Optional[float] = None   # hours from claim before proxy expires
    max_traffic_mb:   Optional[float] = None   # MB bandwidth cap (None=unlimited)
    max_concurrent:   int = 0                  # 0=unlimited
    notes:            str = ""

PROXY_LIMIT_RULES: Dict[str, "ProxyLimitRule"] = {
    "ip2free": ProxyLimitRule(
        source="ip2free",
        max_ttl_hours=24.0,
        max_traffic_mb=None,
        notes="Claimed from ip2free daily tasks; valid ~24h from claim time",
    ),
    "proxyscrape": ProxyLimitRule(
        source="proxyscrape",
        max_ttl_hours=48.0,
        max_traffic_mb=None,
        notes="Free public SOCKS5; typically last 24-72h; re-fetched every 2h via cron",
    ),
    "webshare": ProxyLimitRule(
        source="webshare",
        max_ttl_hours=None,
        max_traffic_mb=None,
        notes="Free tier: 10 proxies, datacenter HTTP, NOT for residential-required platforms",
    ),
    "local_xray": ProxyLimitRule(
        source="local_xray",
        max_ttl_hours=None,
        max_traffic_mb=None,
        notes="Local xray SOCKS5; no expiry as long as xray service runs",
    ),
    "manual": ProxyLimitRule(
        source="manual",
        max_ttl_hours=None,
        max_traffic_mb=None,
        notes="Manually added; check meta for custom limits",
    ),
}


# Source -> list of platforms this source's proxies CANNOT be used for
EXCLUSION_RULES: Dict[str, List[str]] = {
    "ip2free":    ["ip2free"],
    "local_xray": [],
    "proxyscrape":[],
    "webshare":   ["webshare"],
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
    use_count:       int  = 0
    last_used_ts:    Optional[float] = None
    last_used_for:   str  = ""   # last platform this proxy was used for

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
                    if expire_ts is None:  # ip2free often omits expire_time; default 24h TTL
                        expire_ts = time.time() + 86400
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
                            expire_ts=_parse_expire(p.get("expire_time")) or time.time() + 86400,
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
                expire_ts=(_parse_expire(p.get("expire_time") or p.get("expires_at"))
                          or (time.time() + 86400 if source == "ip2free" else None)),
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
        results["webshare"]    = self.refresh_webshare(log_fn=log)
        # Auto-load temp files if present
        for f in ["/tmp/ip2free_proxies_all.json",
                  "/tmp/ip2free_proxies_live.json",
                  "/tmp/ip2free_proxies.json"]:
            if Path(f).exists():
                n = self.load_from_json_file(f, source="ip2free", log_fn=log)
                results[f"file:{Path(f).name}"] = n
        log(f"[proxy_manager] Refresh complete: {results}")
        # Auto-sync to databases after full refresh
        try:
            self.sync_sqlite()
            self.sync_postgres()
        except Exception as _se:
            log(f"[proxy_manager] DB sync warn: {_se}")
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
    # Source: Webshare  (HTTP datacenter proxies, 10 free slots)
    # ------------------------------------------------------------------

    def refresh_webshare(self, log_fn=None) -> int:
        """Fetch all Webshare proxies via API and upsert into proxy DB."""
        log = log_fn or (lambda m: print(f"[webshare] {m}"))
        import urllib.request as _ul
        try:
            req = _ul.Request(
                f"{WEBSHARE_API_BASE}/proxy/list/?mode=direct&page=1&page_size=100",
                headers={"Authorization": f"Token {WEBSHARE_API_KEY}",
                         "User-Agent": "proxy_manager/2.0"})
            with _ul.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
        except Exception as e:
            log(f"API error: {e}"); return 0

        results = data.get("results", [])
        added = updated = 0
        for p in results:
            ws_id  = p.get("id", "")
            uid    = f"webshare:{ws_id}"
            host   = p.get("proxy_address", "")
            port   = int(p.get("port", 0))
            user   = p.get("username", "")
            passwd = p.get("password", "")
            cc     = p.get("country_code", "")
            city   = p.get("city_name", "")
            valid  = p.get("valid", False)
            if not host or not port:
                continue
            formatted = f"http://{user}:{passwd}@{host}:{port}"
            existing = self.db.get(uid)
            entry = ProxyEntry(
                uid=uid, proto="http",
                host=host, port=port, user=user, passwd=passwd,
                source="webshare", source_account="nnhginhn",
                country=cc, city=city,
                proxy_type="datacenter",
                not_for=list(EXCLUSION_RULES["webshare"]),
                alive=valid or None,
                meta={"ws_id": ws_id, "asn_name": p.get("asn_name", ""),
                      "formatted": formatted,
                      "last_verification": p.get("last_verification", ""),
                      "created_at": p.get("created_at", ""),
                      "bandwidth_exhausted": False},
            )
            if existing:
                # Preserve usage stats
                entry.use_count      = existing.use_count
                entry.last_used_ts   = existing.last_used_ts
                entry.last_used_for  = existing.last_used_for
                entry.success_count  = existing.success_count
                entry.fail_count     = existing.fail_count
                updated += 1
            else:
                added += 1
            self.db.put(entry, save=False)
        self.db._save()
        log(f"Webshare: {len(results)} proxies fetched  +{added} new  {updated} updated")
        # Quick bandwidth self-check: try one proxy; mark all dead on 402
        self._webshare_check_bandwidth(log_fn=log)
        return added

    def _webshare_check_bandwidth(self, log_fn=None):
        """Probe one webshare proxy; if 402 bandwidthlimit → blacklist all until next month."""
        log = log_fn or (lambda m: print(f"[webshare_bw] {m}"))
        import subprocess as _sp, datetime as _dt, calendar as _cal
        ws = [e for e in self.db.all() if e.source == "webshare" and e.user]
        if not ws:
            return
        e = ws[0]
        try:
            r = _sp.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", "8",
                 "--proxy", f"http://{e.user}:{e.passwd}@{e.host}:{e.port}",
                 "https://api.ipify.org"],
                capture_output=True, text=True, timeout=10)
            code = r.stdout.strip()
        except Exception:
            code = "error"
        if code == "200":
            log("Bandwidth OK — proxies are usable")
            for en in self.db.all():
                if en.source == "webshare":
                    en.alive = True
                    en.blacklist_until = None
                    en.fail_count = 0
                    en.meta["bandwidth_exhausted"] = False
                    self.db.put(en, save=False)
            self.db._save()
        elif code in ("402", "407"):
            now = _dt.datetime.utcnow()
            if now.month == 12:
                reset = _dt.datetime(now.year + 1, 1, 1)
            else:
                reset = _dt.datetime(now.year, now.month + 1, 1)
            reset_ts = reset.timestamp()
            log(f"Bandwidth EXHAUSTED (HTTP {code}), blacklisting until {reset.strftime('%Y-%m-%d')}")
            for en in self.db.all():
                if en.source == "webshare":
                    en.alive = False
                    en.blacklist_until = reset_ts
                    en.meta["bandwidth_exhausted"] = True
                    en.meta["bandwidth_reset_at"] = reset.strftime("%Y-%m-%d")
                    self.db.put(en, save=False)
            self.db._save()
        else:
            log(f"Bandwidth check inconclusive (code={code})")


    # ------------------------------------------------------------------
    # Source: local_xray auto-discover  (replaces static port list)
    # ------------------------------------------------------------------

    def refresh_local_xray_discover(self, log_fn=None) -> int:
        """Probe LOCAL_XRAY_PORT_RANGE and register all alive ports."""
        log = log_fn or (lambda m: print(f"[local_xray_discover] {m}"))
        log(f"Probing {len(LOCAL_XRAY_PORT_RANGE)} ports ({LOCAL_XRAY_PORT_RANGE[0]}-{LOCAL_XRAY_PORT_RANGE[-1]})...")

        def _probe(port):
            try:
                p = subprocess.Popen(
                    ["curl", "-s", "--max-time", "5", "--connect-timeout", "4",
                     "--socks5", f"127.0.0.1:{port}",
                     "https://api.ipify.org"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, _ = p.communicate(timeout=7)
                ip = out.decode().strip()
                return (port, ip if ip and "." in ip else None)
            except Exception:
                return (port, None)

        alive_ports = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            for port, ip in ex.map(_probe, LOCAL_XRAY_PORT_RANGE):
                if ip:
                    alive_ports[port] = ip

        unique_ips = set(alive_ports.values())
        log(f"Found {len(alive_ports)} alive ports  {len(unique_ips)} unique IPs")

        added = 0
        for port, exit_ip in alive_ports.items():
            uid = f"local_xray:{port}"
            existing = self.db.get(uid)
            entry = ProxyEntry(
                uid=uid, proto="socks5",
                host="127.0.0.1", port=port,
                source="local_xray", proxy_type="residential",
                not_for=list(EXCLUSION_RULES["local_xray"]),
                alive=True, last_probe_ts=time.time(),
                meta={"exit_ip": exit_ip},
            )
            if existing:
                existing.alive = True
                existing.last_probe_ts = time.time()
                existing.meta["exit_ip"] = exit_ip
                self.db.put(existing, save=False)
            else:
                self.db.put(entry, save=False)
                added += 1

        # Mark previously registered ports that are now dead
        for e in self.db.all():
            if e.source == "local_xray" and e.port not in alive_ports:
                e.alive = False
                self.db.put(e, save=False)

        self.db._save()
        log(f"local_xray: +{added} new ports registered  {len(alive_ports)} total alive")
        return added

    # ------------------------------------------------------------------
    # Platform-aware proxy selection
    # ------------------------------------------------------------------

    def pick_for(self, platform: str,
                 country: Optional[str] = None,
                 probe_if_unknown: bool = True) -> Optional["ProxyEntry"]:
        """
        Pick the best proxy for a given platform.
        Uses PLATFORM_POLICIES to try preferred sources/types in order.
        Respects per-entry not_for restrictions.

        Args:
            platform: e.g. "ip2free", "outlook", "obvious", "generic"
            country:  ISO-2 country filter (optional)
            probe_if_unknown: launch background probe for unknown-alive proxies

        Returns:
            Best available ProxyEntry, or None if nothing matches.
        """
        policy = PLATFORM_POLICIES.get(platform.lower(),
                                       PLATFORM_POLICIES["generic"])
        preferred_srcs  = policy["preferred_sources"]
        preferred_types = policy["preferred_types"]

        # Try preferred sources in policy order, each source in preferred type order
        for src in preferred_srcs:
            for ptype in preferred_types:
                e = self.pick(not_for=platform, source=src,
                              proxy_type=ptype, country=country,
                              probe_if_unknown=probe_if_unknown)
                if e:
                    return e
            # Try source without type restriction (broader fallback)
            e = self.pick(not_for=platform, source=src, country=country,
                          probe_if_unknown=probe_if_unknown)
            if e:
                return e

        # Final fallback: any proxy not excluded for this platform
        return self.pick(not_for=platform, country=country,
                         probe_if_unknown=probe_if_unknown)

    def pick_for_url(self, platform: str, **kwargs) -> Optional[str]:
        """Return proxy URL for platform or None."""
        e = self.pick_for(platform, **kwargs)
        if e is None:
            return None
        if e.is_local():
            return f"socks5h://127.0.0.1:{e.port}"
        if e.proto == "http":
            return e.url  # http:// for http proxies
        return e.socks5h_url

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def report_use(self, uid: str, platform: str = "",
                   outcome: str = "success"):
        """Track proxy usage. outcome: 'success' | 'fail' | 'attempt'"""
        e = self.db.get(uid)
        if not e:
            return
        e.use_count    += 1
        e.last_used_ts  = time.time()
        e.last_used_for = platform
        if outcome == "success":
            e.success_count += 1
            e.fail_count    = 0
            e.alive         = True
            e.blacklist_until = None
        elif outcome == "fail":
            e.fail_count += 1
            e.alive = False if e.fail_count >= FAIL_THRESHOLD else e.alive
            if e.fail_count >= FAIL_THRESHOLD:
                e.blacklist_until = time.time() + BLACKLIST_TTL
        self.db.put(e)

    # ------------------------------------------------------------------
    # SQLite sync
    # ------------------------------------------------------------------

    def sync_sqlite(self, db_path: Path = SQLITE_DB,
                    log_fn=None) -> int:
        """
        Sync all proxy entries to SQLite proxies table.
        Creates the table if it doesn't exist.
        Returns number of rows upserted.
        """
        log = log_fn or (lambda m: print(f"[sync_sqlite] {m}"))
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(db_path))
            cur  = conn.cursor()

            # Create table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid          TEXT    UNIQUE NOT NULL,
                    source       TEXT    NOT NULL,
                    proto        TEXT    DEFAULT 'socks5',
                    host         TEXT    NOT NULL,
                    port         INTEGER NOT NULL,
                    username     TEXT    DEFAULT '',
                    password     TEXT    DEFAULT '',
                    formatted    TEXT,
                    country      TEXT    DEFAULT '',
                    city         TEXT    DEFAULT '',
                    proxy_type   TEXT    DEFAULT 'unknown',
                    alive        INTEGER DEFAULT NULL,
                    not_for      TEXT    DEFAULT '[]',
                    expire_ts    REAL    DEFAULT NULL,
                    fail_count   INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    use_count    INTEGER DEFAULT 0,
                    last_used    TEXT    DEFAULT NULL,
                    last_used_for TEXT   DEFAULT '',
                    last_probe_ts REAL   DEFAULT NULL,
                    status       TEXT    DEFAULT 'active',
                    raw          TEXT    DEFAULT '{}',
                    created_at   TEXT    DEFAULT (datetime('now')),
                    updated_at   TEXT    DEFAULT (datetime('now'))
                )
            """)

            upserted = 0
            for e in self.db.all():
                # Build formatted URL
                if e.is_local():
                    fmt = f"socks5h://127.0.0.1:{e.port}"
                elif e.proto == "http" and e.user:
                    fmt = f"http://{e.user}:{e.passwd}@{e.host}:{e.port}"
                elif e.user:
                    fmt = f"socks5h://{e.user}:{e.passwd}@{e.host}:{e.port}"
                else:
                    fmt = f"socks5h://{e.host}:{e.port}"

                alive_int = (1 if e.alive is True
                             else (0 if e.alive is False else None))
                status = ("expired" if e.is_expired()
                          else ("blacklisted" if e.is_blacklisted()
                                else "active"))
                last_used = None
                if e.last_used_ts:
                    import datetime as _dt
                    last_used = _dt.datetime.fromtimestamp(
                        e.last_used_ts).strftime("%Y-%m-%d %H:%M:%S")

                cur.execute("""
                    INSERT INTO proxies
                        (uid, source, proto, host, port, username, password,
                         formatted, country, city, proxy_type, alive, not_for,
                         expire_ts, fail_count, success_count, use_count,
                         last_used, last_used_for, last_probe_ts, status, raw,
                         updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                    ON CONFLICT(uid) DO UPDATE SET
                        source=excluded.source, proto=excluded.proto,
                        host=excluded.host, port=excluded.port,
                        username=excluded.username, password=excluded.password,
                        formatted=excluded.formatted,
                        country=excluded.country, city=excluded.city,
                        proxy_type=excluded.proxy_type,
                        alive=excluded.alive, not_for=excluded.not_for,
                        expire_ts=excluded.expire_ts,
                        fail_count=excluded.fail_count,
                        success_count=excluded.success_count,
                        use_count=excluded.use_count,
                        last_used=excluded.last_used,
                        last_used_for=excluded.last_used_for,
                        last_probe_ts=excluded.last_probe_ts,
                        status=excluded.status, raw=excluded.raw,
                        updated_at=datetime('now')
                """, (
                    e.uid, e.source, e.proto,
                    e.host, e.port, e.user, e.passwd,
                    fmt, e.country, e.city, e.proxy_type,
                    alive_int, json.dumps(e.not_for),
                    e.expire_ts, e.fail_count, e.success_count,
                    e.use_count, last_used, e.last_used_for,
                    e.last_probe_ts, status,
                    json.dumps(e.to_dict()),
                ))
                upserted += 1

            conn.commit()
            conn.close()
            log(f"Synced {upserted} proxies → SQLite {db_path}")
            return upserted
        except Exception as ex:
            log(f"SQLite sync error: {ex}")
            return 0



    def check_limits(self, entry: "ProxyEntry") -> dict:
        """
        Check if a proxy is still within its operational limits.
        Returns dict with keys: ok(bool), reason(str), ttl_remaining_h(float|None)
        """
        import time as _t
        rule = PROXY_LIMIT_RULES.get(entry.source)
        if rule is None:
            return {"ok": True, "reason": "no_rule", "ttl_remaining_h": None}

        # TTL check
        if rule.max_ttl_hours and entry.last_probe_ts:
            age_h = (_t.time() - entry.last_probe_ts) / 3600
            if age_h >= rule.max_ttl_hours:
                return {
                    "ok": False,
                    "reason": f"ttl_expired ({age_h:.1f}h > {rule.max_ttl_hours}h)",
                    "ttl_remaining_h": 0,
                }
            return {
                "ok": True,
                "reason": "within_ttl",
                "ttl_remaining_h": rule.max_ttl_hours - age_h,
            }

        # Explicit expire_ts
        if entry.expire_ts and _t.time() > entry.expire_ts:
            return {"ok": False, "reason": "expire_ts_passed", "ttl_remaining_h": 0}

        return {"ok": True, "reason": "no_ttl_limit", "ttl_remaining_h": None}

    def limits_report(self, log_fn=None):
        """Print a table of all proxies with their limit status."""
        log = log_fn or print
        import time as _t
        log("\n=== PROXY LIMITS REPORT ===")
        log(f"  {'Source':<12} {'UID':<35} {'Alive':>5} {'Status':<20} {'TTL Remaining':>14}")
        log("  " + "-"*90)
        for e in sorted(self.db.all(), key=lambda x: (x.source, x.uid)):
            lim = self.check_limits(e)
            alive_s = "yes" if e.alive is True else ("no" if e.alive is False else "?")
            ttl_s   = (f"{lim['ttl_remaining_h']:.1f}h" if lim['ttl_remaining_h'] else
                       "unlimited" if lim["reason"] == "no_ttl_limit" else "EXPIRED")
            stat_s  = lim["reason"][:20]
            log(f"  {e.source:<12} {e.uid:<35} {alive_s:>5} {stat_s:<20} {ttl_s:>14}")

    def sync_postgres(self,
                      db_url: str = "postgresql://postgres:postgres@localhost/toolkit",
                      log_fn=None) -> int:
        """
        Sync proxy entries into the PostgreSQL toolkit.proxies table.
        Schema: formatted(UNIQUE), host, port, username, password,
                status, used_count, last_used, raw.
        Inserts new proxies and updates existing ones.
        Skips local_xray ports not in range 10820-10860 (not useful to API server).
        """
        log = log_fn or (lambda m: print(f"[sync_postgres] {m}"))
        try:
            import psycopg2 as _pg
        except ImportError:
            try:
                import subprocess as _sp
                _sp.run(["pip3","install","psycopg2-binary","-q"], check=False)
                import psycopg2 as _pg
            except Exception as e:
                log(f"psycopg2 not available: {e}"); return 0
        try:
            conn = _pg.connect(db_url)
            cur  = conn.cursor()
            upserted = 0
            for e in self.db.all():
                # Build formatted URL matching API server conventions
                if e.is_local():
                    # Only sync xray ports in the range API server uses
                    if not (10820 <= e.port <= 10889):
                        continue
                    fmt = f"socks5://127.0.0.1:{e.port}"
                elif e.proto == "http" and e.user:
                    fmt = f"http://{e.user}:{e.passwd}@{e.host}:{e.port}"
                elif e.user:
                    fmt = f"socks5h://{e.user}:{e.passwd}@{e.host}:{e.port}"
                else:
                    fmt = f"socks5h://{e.host}:{e.port}"

                # Map alive status → postgres status
                # 'active' = alive (usable), 'idle' = unprobed, 'banned' = dead
                status = ("active" if e.alive is True
                          else ("banned" if e.alive is False
                                else "active" if e.alive is None and e.source in ("local_xray", "webshare")
                                else "idle"))
                if e.is_blacklisted():
                    status = "banned"

                import json as _j, datetime as _dt
                last_used = None
                if e.last_used_ts:
                    last_used = _dt.datetime.fromtimestamp(e.last_used_ts)

                raw = _j.dumps({
                    "uid":         e.uid,
                    "source":      e.source,
                    "proxy_type":  e.proxy_type,
                    "country":     e.country,
                    "city":        e.city,
                    "not_for":     e.not_for,
                    "expire_ts":   e.expire_ts,
                    "fail_count":  e.fail_count,
                    "last_used_for": e.last_used_for,
                    **(e.meta or {}),
                })

                cur.execute("""
                    INSERT INTO proxies
                        (formatted, host, port, username, password, status,
                         used_count, last_used, raw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (formatted) DO UPDATE SET
                        host       = EXCLUDED.host,
                        port       = EXCLUDED.port,
                        username   = EXCLUDED.username,
                        password   = EXCLUDED.password,
                        status     = CASE
                            WHEN proxies.status = 'banned' AND EXCLUDED.status != 'active'
                                THEN 'banned'
                            ELSE EXCLUDED.status
                        END,
                        used_count = GREATEST(proxies.used_count, EXCLUDED.used_count),
                        last_used  = COALESCE(EXCLUDED.last_used, proxies.last_used),
                        raw        = EXCLUDED.raw
                """, (fmt, e.host, e.port,
                        e.user or "", e.passwd or "",
                        status, e.use_count, last_used, raw))
                upserted += 1

            conn.commit()
            cur.close(); conn.close()
            log(f"Synced {upserted} proxies → PostgreSQL {db_url.split('@')[-1]}")
            return upserted
        except Exception as ex:
            log(f"PostgreSQL sync error: {ex}")
            return 0

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
        description="proxy_manager.py v2.0 — Unified Proxy Manager")
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


    # webshare-sync
    sub.add_parser("webshare-sync", help="Fetch Webshare proxies from API")

    # local-discover
    sub.add_parser("local-discover",
                   help="Auto-probe xray ports 10820-10889 and register alive ones")

    # pick-for
    p_pf = sub.add_parser("pick-for",
                           help="Pick best proxy for a given platform (uses PLATFORM_POLICIES)")
    p_pf.add_argument("platform", help="e.g. ip2free / webshare / outlook / obvious / generic")
    p_pf.add_argument("--country", default=None)

    # sync-db
    sub.add_parser("sync-db", help="Sync proxy_db.json to SQLite proxies table")

    # platform-rules
    sub.add_parser("platform-rules", help="Show PLATFORM_POLICIES table")

    # report-use
    p_ru = sub.add_parser("report-use", help="Record proxy usage outcome")
    p_ru.add_argument("uid",      help="Proxy UID")
    p_ru.add_argument("platform", help="Platform name")
    p_ru.add_argument("outcome",  choices=["success","fail","attempt"])


    sub.add_parser("limits", help="Show limit rules for all proxy sources")
    sub.add_parser("limits-report", help="Show per-proxy TTL / expiry status")

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


    if args.cmd == "webshare-sync":
        n = pm.refresh_webshare(log_fn=print)
        print(f"Webshare sync: +{n} new proxies")
        pm.print_status(); return 0

    if args.cmd == "local-discover":
        n = pm.refresh_local_xray_discover(log_fn=print)
        print(f"local_xray discover: +{n} new ports registered")
        pm.print_status(); return 0

    if args.cmd == "pick-for":
        e = pm.pick_for(args.platform, country=getattr(args,"country",None),
                        probe_if_unknown=False)
        if e:
            url = pm.pick_for_url(args.platform, country=getattr(args,"country",None),
                                  probe_if_unknown=False)
            # Re-pick since pick_for_url calls pick_for again — just print what we have
            u2 = (f"socks5h://127.0.0.1:{e.port}" if e.is_local()
                  else (e.url if e.proto=="http" else e.socks5h_url))
            print(u2)
            print(f"  uid={e.uid}", file=sys.stderr)
            print(f"  source={e.source}  type={e.proxy_type}  "
                  f"country={e.country}  alive={e.alive}", file=sys.stderr)
            return 0
        print(f"None — no proxy available for platform={args.platform!r}"); return 1

    if args.cmd == "sync-db":
        n  = pm.sync_sqlite(log_fn=print)
        n2 = pm.sync_postgres(log_fn=print)
        print(f"Synced {n} proxies to SQLite, {n2} proxies to PostgreSQL"); return 0

    if args.cmd == "platform-rules":
        print(f"\n{'Platform':<12} {'Preferred Sources':<46} {'Types':<40}")
        print("-"*100)
        for pf, pol in PLATFORM_POLICIES.items():
            srcs  = " > ".join(pol["preferred_sources"])
            types = " > ".join(pol["preferred_types"])
            print(f"{pf:<12} {srcs:<46} {types}")
        print()
        return 0

    if args.cmd == "report-use":
        pm.report_use(args.uid, args.platform, args.outcome)
        print(f"Recorded: {args.uid}  platform={args.platform}  outcome={args.outcome}")
        return 0


    if args.cmd == "limits":
        print("\n=== PROXY SOURCE LIMIT RULES ===")
        print(f"  {'Source':<14} {'TTL(h)':>8} {'Traffic':>10}  Notes")
        print("  " + "-"*70)
        for src, rule in PROXY_LIMIT_RULES.items():
            ttl   = f"{rule.max_ttl_hours}h" if rule.max_ttl_hours else "unlimited"
            traff = f"{rule.max_traffic_mb}MB" if rule.max_traffic_mb else "unlimited"
            print(f"  {src:<14} {ttl:>8} {traff:>10}  {rule.notes}")
        print()
        return 0

    if args.cmd == "limits-report":
        pm.limits_report()
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
