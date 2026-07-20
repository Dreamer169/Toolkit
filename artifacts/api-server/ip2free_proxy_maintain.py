#!/usr/bin/env python3
"""
ip2free_proxy_maintain.py v1.0
================================
Proxy quality check, classification, expiry cleanup, and resi_pool maintenance.

Runs every hour via cron.  Does NOT call ip2free API — works entirely from
the local proxy data files written by ip2free_proxy_sync.py.

Pipeline
--------
1. Load  : read event + free proxy files from /data/Toolkit/proxy_data/
2. Expire : drop event proxies whose expire_ts < now
3. Probe  : concurrent curl probe → alive + latency_ms per proxy
4. Classify: ip-api.com batch ASN lookup → residential / datacenter / unknown
5. Score  : multi-factor quality score
6. Clean  : rewrite proxy files keeping only non-expired entries with quality metadata
7. Inject : top-scored alive proxies → resi_pool (event residential first, then free)
8. Replenish: if alive event pool < MIN_EVENT_POOL, log warning + optionally trigger sync

Quality score (0-200)
---------------------
  +100  alive
  + 50  residential (hosting=False from ip-api.com)
  + 20  event source (more reliable creds, known expiry)
  -  X  latency penalty: -5 per 500ms over 500ms
  - 20  expires in <7 days
  - 30  fails to return 204 (not alive) — shouldn't reach score phase

resi_pool injection
-------------------
  Writes dedicated /data/Toolkit/proxy_data/ip2free_resi_pool_entries.json
  to track which strings came from ip2free (so stale ones can be removed
  without touching proxyscrape entries).
  Then writes merged external file to /tmp/resi_pool_external.json.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Paths ─────────────────────────────────────────────────────────────────────
PROXY_DATA_DIR      = "/data/Toolkit/proxy_data"
FREE_PROXY_FILE     = f"{PROXY_DATA_DIR}/ip2free_free_proxies.json"
EVENT_PROXY_FILE    = f"{PROXY_DATA_DIR}/ip2free_event_proxies.json"
QUALITY_DB_FILE     = f"{PROXY_DATA_DIR}/ip2free_quality_db.json"
RESI_POOL_TRACK     = f"{PROXY_DATA_DIR}/ip2free_resi_pool_entries.json"
MAINTAIN_STATE_FILE = f"{PROXY_DATA_DIR}/ip2free_maintain_state.json"
RESI_POOL_EXT_FILE  = "/tmp/resi_pool_external.json"
LOCK_FILE           = "/tmp/ip2free_maintain.lock"
LOG_FILE            = "/var/log/ip2free_proxy_maintain.log"
SYNC_SCRIPT         = "/data/Toolkit/artifacts/api-server/ip2free_proxy_sync.py"
SYNC_LOCK_FILE      = "/tmp/ip2free_proxy_sync.lock"

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_QUALITY_SCORE   = 80    # minimum score to enter resi_pool
MAX_RESI_INJECT     = 100   # max ip2free entries in resi_pool
MIN_EVENT_POOL      = 5     # warn + trigger sync if alive event proxies < this
PROBE_WORKERS       = 30    # concurrent curl threads
PROBE_TIMEOUT       = 8     # curl max-time (s)
CLASSIFY_BATCH      = 100   # ip-api.com batch size (hard limit: 100)
CLASSIFY_RATE_SLEEP = 1.5   # sleep between batches (45 req/min limit)
PROBE_TARGET        = "http://www.gstatic.com/generate_204"

# ── IP classification cache (in-process, reloaded each run) ──────────────────
_ip_class_cache: Dict[str, Dict[str, Any]] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Dataclass
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProxyRecord:
    uid:          str
    ip:           str
    port:         int
    username:     str      = ""
    password:     str      = ""
    protocol:     str      = "socks5"
    source:       str      = "ip2free_free"   # ip2free_free | ip2free_event
    country_code: str      = ""
    city:         str      = ""
    expire_ts:    Optional[float] = None     # None = no expiry (free proxies)
    # filled by maintenance pipeline
    alive:        Optional[bool]  = None
    latency_ms:   Optional[int]   = None
    ip_type:      str      = "unknown"       # residential | datacenter | unknown
    ip_org:       str      = ""
    ip_country:   str      = ""
    quality:      int      = 0
    last_probe_ts: Optional[float] = None
    probe_fail_streak: int = 0
    source_account: str   = ""
    meta:         dict     = field(default_factory=dict)

    @property
    def url(self) -> str:
        if self.username and self.password:
            return f"{self.username}:{self.password}@{self.ip}:{self.port}"
        return f"{self.ip}:{self.port}"

    @property
    def socks5h_url(self) -> str:
        return f"socks5h://{self.url}"

    def is_expired(self) -> bool:
        return self.expire_ts is not None and time.time() > self.expire_ts

    def expires_in_days(self) -> Optional[float]:
        if self.expire_ts is None:
            return None
        return (self.expire_ts - time.time()) / 86400

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

def log(msg: str) -> None:
    ts   = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Load / save helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


def load_quality_db() -> Dict[str, dict]:
    """Load persistent quality metadata keyed by proxy uid."""
    return _load_json(QUALITY_DB_FILE, {})


def save_quality_db(db: Dict[str, dict]) -> None:
    _save_json(QUALITY_DB_FILE, db)


# ══════════════════════════════════════════════════════════════════════════════
# Load proxy records
# ══════════════════════════════════════════════════════════════════════════════

def _parse_free_url(url_str: str) -> Optional[ProxyRecord]:
    """Parse 'user:pass@host:port' or 'host:port' string."""
    try:
        if "@" in url_str:
            auth, hostport = url_str.rsplit("@", 1)
            user, pw = auth.split(":", 1)
        else:
            hostport = url_str
            user = pw = ""
        host, port_s = hostport.rsplit(":", 1)
        return ProxyRecord(
            uid=f"free:{host}:{port_s}",
            ip=host, port=int(port_s),
            username=user, password=pw,
            source="ip2free_free",
        )
    except Exception:
        return None


def load_free_proxies(qdb: Dict[str, dict]) -> List[ProxyRecord]:
    data = _load_json(FREE_PROXY_FILE, {})
    raw  = data.get("proxies", [])
    records: List[ProxyRecord] = []
    for item in raw:
        if isinstance(item, str):
            r = _parse_free_url(item)
        elif isinstance(item, dict):
            # legacy dict format
            ip   = item.get("ip","")
            port = int(item.get("port", 0) or 0)
            if not ip or not port:
                continue
            r = ProxyRecord(
                uid=f"free:{ip}:{port}",
                ip=ip, port=port,
                username=item.get("username",""),
                password=item.get("password",""),
                source="ip2free_free",
                country_code=item.get("country_code",""),
                city=item.get("city",""),
                source_account=item.get("source_account",""),
            )
        else:
            continue
        if r:
            # restore quality metadata from db
            q = qdb.get(r.uid, {})
            r.alive        = q.get("alive")
            r.latency_ms   = q.get("latency_ms")
            r.ip_type      = q.get("ip_type", "unknown")
            r.ip_org       = q.get("ip_org", "")
            r.ip_country   = q.get("ip_country", "")
            r.quality      = q.get("quality", 0)
            r.last_probe_ts = q.get("last_probe_ts")
            r.probe_fail_streak = q.get("probe_fail_streak", 0)
            records.append(r)
    return records


def load_event_proxies(qdb: Dict[str, dict]) -> List[ProxyRecord]:
    data = _load_json(EVENT_PROXY_FILE, {})
    raw  = data.get("proxies", [])
    records: List[ProxyRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ip   = item.get("ip","")
        port = int(item.get("port", 0) or 0)
        if not ip or not port:
            continue
        uid = f"event:{item.get('proxy_uid', ip + ':' + str(port))}"
        r = ProxyRecord(
            uid=uid,
            ip=ip, port=port,
            username=item.get("username",""),
            password=item.get("password",""),
            source="ip2free_event",
            country_code=item.get("country_code",""),
            city=item.get("city",""),
            expire_ts=item.get("expire_ts"),
            source_account=item.get("source_account",""),
            meta=item,
        )
        # restore quality metadata from db
        q = qdb.get(uid, {})
        r.alive             = q.get("alive")
        r.latency_ms        = q.get("latency_ms")
        r.ip_type           = q.get("ip_type", "unknown")
        r.ip_org            = q.get("ip_org", "")
        r.ip_country        = q.get("ip_country", "")
        r.quality           = q.get("quality", 0)
        r.last_probe_ts     = q.get("last_probe_ts")
        r.probe_fail_streak = q.get("probe_fail_streak", 0)
        records.append(r)
    return records


# ══════════════════════════════════════════════════════════════════════════════
# Probe
# ══════════════════════════════════════════════════════════════════════════════

PROBE_CACHE_TTL = 1800   # re-probe only if last probe > 30 min ago


def _probe_one(record: ProxyRecord) -> Tuple[str, bool, int]:
    """Return (uid, alive, latency_ms). Runs in thread."""
    cmd = [
        "curl", "-s",
        "--max-time", str(PROBE_TIMEOUT),
        "--proxy", record.socks5h_url,
        "-o", "/dev/null",
        "-w", "%{time_total}|%{http_code}",
        PROBE_TARGET,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT + 3
        )
        out = r.stdout.strip()
        if "|" in out:
            t_str, code = out.split("|", 1)
            alive = code not in ("", "000")
            return record.uid, alive, round(float(t_str) * 1000)
    except Exception:
        pass
    return record.uid, False, 9999


def probe_all(
    records: List[ProxyRecord],
    force: bool = False,
    workers: int = PROBE_WORKERS,
) -> Dict[str, Tuple[bool, int]]:
    """
    Probe all records concurrently.
    Skips records probed recently (< PROBE_CACHE_TTL) unless force=True.
    Returns {uid: (alive, latency_ms)}.
    """
    now = time.time()
    to_probe = [
        r for r in records
        if force
        or r.last_probe_ts is None
        or (now - r.last_probe_ts) > PROBE_CACHE_TTL
    ]
    skipped = len(records) - len(to_probe)
    log(f"[probe] {len(to_probe)} to probe ({skipped} cached), workers={workers}")

    results: Dict[str, Tuple[bool, int]] = {
        r.uid: (r.alive or False, r.latency_ms or 9999)
        for r in records
        if r.uid not in {rec.uid for rec in to_probe}
    }

    if not to_probe:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_one, rec): rec for rec in to_probe}
        done = 0
        for fut in concurrent.futures.as_completed(futs, timeout=120):
            try:
                uid, alive, latency = fut.result()
                results[uid] = (alive, latency)
                done += 1
                if done % 20 == 0:
                    alive_count = sum(1 for a, _ in results.values() if a)
                    log(f"  probed {done}/{len(to_probe)}, alive so far={alive_count}")
            except Exception as e:
                rec = futs[fut]
                results[rec.uid] = (False, 9999)
                log(f"  probe exc {rec.uid}: {e}")

    alive_count = sum(1 for a, _ in results.values() if a)
    log(f"[probe] done: {alive_count}/{len(records)} alive")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# IP Classification  (ip-api.com batch, rate-limited)
# ══════════════════════════════════════════════════════════════════════════════

def classify_ips(ips: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Classify IPs via ip-api.com/batch.
    Returns {ip: {type: residential|datacenter|unknown, org, country}}.
    Rate limit: 45 req/min (free tier) → sleep 1.5s between batches.
    Caches results in _ip_class_cache to avoid re-classifying same IPs.
    """
    global _ip_class_cache
    result: Dict[str, Dict[str, Any]] = {}
    to_fetch = [ip for ip in ips if ip not in _ip_class_cache]

    for i in range(0, len(to_fetch), CLASSIFY_BATCH):
        batch = to_fetch[i: i + CLASSIFY_BATCH]
        payload = json.dumps([
            {"query": ip, "fields": "query,org,isp,hosting,country,countryCode"}
            for ip in batch
        ]).encode()
        try:
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=query,org,isp,hosting,country,countryCode",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                items = json.load(resp)
            for item in items:
                ip = item.get("query", "")
                if not ip:
                    continue
                hosting = item.get("hosting", False)
                org     = item.get("org", "") or item.get("isp", "")
                country = item.get("country", "")
                isp     = (item.get("isp") or "").lower()
                # Extra heuristic: some hosting=False IPs are still datacenter
                # if org contains well-known DC keywords
                DC_KEYWORDS = ("servermania","b2 net","ipxo","coloc","datacenter",
                               "data center","hosting","vps","cloud","server",
                               "dedicated","hetzner","ovh","linode","digital ocean",
                               "vultr","amazonaws","google cloud","microsoft azure",
                               "internet data","net solutions","leaseweb","cogent",
                               "level 3","telia","zayo","bandwidth")
                org_lower = org.lower()
                if hosting or any(k in org_lower for k in DC_KEYWORDS):
                    ip_type = "datacenter"
                else:
                    ip_type = "residential"
                _ip_class_cache[ip] = {
                    "type": ip_type, "org": org, "country": country,
                }
        except Exception as e:
            log(f"[classify] batch error: {e}")
            for ip in batch:
                _ip_class_cache.setdefault(ip, {"type": "unknown", "org": "", "country": ""})
        if i + CLASSIFY_BATCH < len(to_fetch):
            time.sleep(CLASSIFY_RATE_SLEEP)

    for ip in ips:
        result[ip] = _ip_class_cache.get(ip, {"type": "unknown", "org": "", "country": ""})
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Quality scoring
# ══════════════════════════════════════════════════════════════════════════════

def compute_quality(record: ProxyRecord) -> int:
    score = 0
    if not record.alive:
        return 0
    score += 100  # alive
    if record.ip_type == "residential":
        score += 50
    elif record.ip_type == "unknown":
        score += 10
    if record.source == "ip2free_event":
        score += 20  # event proxies have known expiry and verified creds
    # latency penalty: -5 per 500ms over 500ms
    lat = record.latency_ms or 9999
    if lat > 500:
        penalty = int((lat - 500) / 500) * 5
        score -= min(penalty, 40)
    # expiry penalty
    days_left = record.expires_in_days()
    if days_left is not None and days_left < 7:
        score -= 20
    return max(score, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Update records with probe + classify results
# ══════════════════════════════════════════════════════════════════════════════

def update_records(
    records: List[ProxyRecord],
    probe_results: Dict[str, Tuple[bool, int]],
    classify_results: Dict[str, Dict[str, Any]],
    qdb: Dict[str, dict],
) -> None:
    now = time.time()
    for r in records:
        if r.uid in probe_results:
            alive, latency = probe_results[r.uid]
            r.alive      = alive
            r.latency_ms = latency
            r.last_probe_ts = now
            if alive:
                r.probe_fail_streak = 0
            else:
                r.probe_fail_streak = r.probe_fail_streak + 1

        cls = classify_results.get(r.ip, {})
        if cls:
            r.ip_type   = cls.get("type", r.ip_type)
            r.ip_org    = cls.get("org", r.ip_org)
            r.ip_country = cls.get("country", r.ip_country)

        r.quality = compute_quality(r)

        # persist to quality db
        qdb[r.uid] = {
            "alive":        r.alive,
            "latency_ms":   r.latency_ms,
            "ip_type":      r.ip_type,
            "ip_org":       r.ip_org,
            "ip_country":   r.ip_country,
            "quality":      r.quality,
            "last_probe_ts": r.last_probe_ts,
            "probe_fail_streak": r.probe_fail_streak,
            "source":       r.source,
            "updated_ts":   now,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Save cleaned proxy files
# ══════════════════════════════════════════════════════════════════════════════

def save_free_proxies(records: List[ProxyRecord]) -> None:
    """Save as list of 'user:pass@host:port' strings (original format)."""
    urls = []
    for r in records:
        if r.is_expired():
            continue  # free proxies have no expiry, so this shouldn't fire
        if r.probe_fail_streak >= 5:
            continue  # drop chronically dead entries
        url = r.url
        if url and url not in urls:
            urls.append(url)
    _save_json(FREE_PROXY_FILE, {"proxies": urls, "ts": time.time()})


def save_event_proxies(records: List[ProxyRecord]) -> None:
    """Save event proxies preserving original dict format + quality metadata."""
    items = []
    for r in records:
        if r.is_expired():
            continue
        if r.probe_fail_streak >= 5:
            continue
        base = r.meta.copy() if r.meta else {
            "proxy_uid": r.uid.replace("event:", ""),
            "ip":        r.ip,
            "port":      r.port,
            "username":  r.username,
            "password":  r.password,
            "protocol":  r.protocol,
            "country_code": r.country_code,
            "city":      r.city,
            "expire_ts": r.expire_ts,
            "source_account": r.source_account,
            "source":    "ip2free_event",
            "url":       r.url,
        }
        # inject quality metadata
        base["_quality"]    = r.quality
        base["_ip_type"]    = r.ip_type
        base["_ip_org"]     = r.ip_org
        base["_ip_country"] = r.ip_country
        base["_alive"]      = r.alive
        base["_latency_ms"] = r.latency_ms
        items.append(base)
    _save_json(EVENT_PROXY_FILE, {"proxies": items, "ts": time.time()})


# ══════════════════════════════════════════════════════════════════════════════
# resi_pool injection
# ══════════════════════════════════════════════════════════════════════════════

MAX_PER_IP = 3   # max proxies per unique physical IP in resi_pool (avoid redundant routes)


def inject_resi_pool(
    event_records: List[ProxyRecord],
    free_records:  List[ProxyRecord],
    max_inject: int = MAX_RESI_INJECT,
) -> int:
    """
    Build a fresh ip2free entry list for resi_pool:
      1. Score-sort event (residential) proxies → deduplicate by IP (max MAX_PER_IP each)
      2. Fill remaining slots with alive free proxies (max MAX_PER_IP per IP)

    KEY FIX: do NOT call add_external_full() in a loop — each call writes _save_externals_file()
    from the in-memory _externals (which starts from the old file contents), overwriting
    our merged file with a stale shorter list.  Instead we write the merged file directly
    and call reload_externals() once to live-update the in-memory pool.
    """
    # Qualified event proxies: include unprobed (alive is not False) + not expired
    # EVENT proxies are pre-vetted by ip2free (paid subscription) — no quality gate needed
    # alive=True → probed alive; alive=None → not yet probed (still include); alive=False → dead (exclude)
    qual_event = sorted(
        [r for r in event_records
         if r.alive is not False and not r.is_expired()],
        key=lambda r: (-r.quality, r.latency_ms or 9999),
    )
    # Deduplicate by IP: keep max MAX_PER_IP per physical IP for event proxies
    ip_seen: Dict[str, int] = {}
    deduped_event: List[ProxyRecord] = []
    for r in qual_event:
        if ip_seen.get(r.ip, 0) < MAX_PER_IP:
            deduped_event.append(r)
            ip_seen[r.ip] = ip_seen.get(r.ip, 0) + 1

    # Qualified free proxies: alive, quality >= threshold
    FREE_THRESHOLD = 80
    qual_free = sorted(
        [r for r in free_records if r.alive and r.quality >= FREE_THRESHOLD],
        key=lambda r: (-r.quality, r.latency_ms or 9999),
    )
    ip_seen2: Dict[str, int] = {}
    deduped_free: List[ProxyRecord] = []
    for r in qual_free:
        if ip_seen2.get(r.ip, 0) < MAX_PER_IP:
            deduped_free.append(r)
            ip_seen2[r.ip] = ip_seen2.get(r.ip, 0) + 1

    chosen: List[ProxyRecord] = []
    chosen.extend(deduped_event[:max_inject])
    if len(chosen) < max_inject:
        remaining = max_inject - len(chosen)
        chosen.extend(deduped_free[:remaining])

    ip2free_strings = list(dict.fromkeys(r.url for r in chosen))  # dedup strings too

    _save_json(RESI_POOL_TRACK, {
        "proxies": ip2free_strings,
        "ts": time.time(),
        "count": len(ip2free_strings),
        "event_count": sum(1 for r in chosen if r.source == "ip2free_event"),
        "free_count":  sum(1 for r in chosen if r.source == "ip2free_free"),
        "unique_ips":  len(set(r.ip for r in chosen)),
    })

    # Load existing resi_pool external file; strip previous ip2free entries
    old_track       = _load_json(RESI_POOL_TRACK + ".prev", {}).get("proxies", [])
    old_ext         = _load_json(RESI_POOL_EXT_FILE, {}).get("proxies", [])
    old_ip2free_set = set(old_track)
    # Keep non-ip2free entries (proxyscrape, manual, etc.)
    non_ip2free = [p for p in old_ext if p not in old_ip2free_set]

    # Merge: ip2free entries first (higher quality), then others
    ip2free_set = set(ip2free_strings)
    merged = ip2free_strings + [p for p in non_ip2free if p not in ip2free_set]

    # Write the merged external file ONCE — do not call add_external_full() afterwards
    # (add_external_full calls _save_externals_file() which overwrites our merged file
    #  with whatever is currently in the resi_pool in-memory _externals list)
    _save_json(RESI_POOL_EXT_FILE, {
        "proxies": merged,
        "ts": time.time(),
        "source": "ip2free_maintain",
        "ip2free_count": len(ip2free_strings),
    })
    log(f"[resi_pool] wrote {len(merged)} total ({len(ip2free_strings)} ip2free + {len(non_ip2free)} other) → {RESI_POOL_EXT_FILE}")

    # Save current track as prev for next run's cleanup
    import shutil
    try:
        shutil.copy2(RESI_POOL_TRACK, RESI_POOL_TRACK + ".prev")
    except Exception:
        pass

    # Live-update in-memory resi_pool by calling reload_externals ONCE
    # (reads the file we just wrote; merges new entries into _externals without overwriting file)
    try:
        scripts_dir = "/data/Toolkit/scripts"
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import resi_pool as rp
        if hasattr(rp, "reload_externals"):
            new_count = rp.reload_externals()
            log(f"[resi_pool] live reload_externals: +{new_count} entries into in-memory pool")
    except Exception as e:
        log(f"[resi_pool] live reload warning: {e} (file already written correctly)")

    return len(ip2free_strings)


# ══════════════════════════════════════════════════════════════════════════════
# Replenish trigger
# ══════════════════════════════════════════════════════════════════════════════

def maybe_trigger_sync(
    alive_event: int,
    env_file: str = "/data/Toolkit/.ip2free_proxy.env",
) -> None:
    if alive_event >= MIN_EVENT_POOL:
        return
    log(f"[replenish] alive event proxies ({alive_event}) < MIN ({MIN_EVENT_POOL}), triggering sync…")
    # Check if sync is already running
    try:
        fd = open(SYNC_LOCK_FILE, "r")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except (OSError, IOError):
            fd.close()
            log("[replenish] sync already running, skip trigger")
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    try:
        r = subprocess.run(
            [sys.executable, SYNC_SCRIPT, "--env-file", env_file],
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(SYNC_SCRIPT),
        )
        if r.returncode == 0:
            log("[replenish] sync triggered successfully")
        else:
            log(f"[replenish] sync failed rc={r.returncode}: {r.stderr[-300:]}")
    except Exception as e:
        log(f"[replenish] sync trigger error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Main maintenance run
# ══════════════════════════════════════════════════════════════════════════════

def run_maintenance(
    force_probe: bool = False,
    force_classify: bool = False,
    dry_run: bool = False,
    max_inject: int = MAX_RESI_INJECT,
) -> dict:
    t_start = time.time()
    qdb = load_quality_db()

    # ── 1. Load ──────────────────────────────────────────────────────────────
    free_records  = load_free_proxies(qdb)
    event_records = load_event_proxies(qdb)
    all_records   = free_records + event_records
    log(f"[maintain] loaded: {len(free_records)} free + {len(event_records)} event = {len(all_records)} total")

    # ── 2. Expire check ──────────────────────────────────────────────────────
    expired_count = sum(1 for r in event_records if r.is_expired())
    if expired_count:
        log(f"[expire] removing {expired_count} expired event proxies")
    event_records = [r for r in event_records if not r.is_expired()]

    # ── 2b. QDB fallback: if event file sparse (e.g. mid-sync), supplement from quality_db ──
    if len(event_records) < 5:
        alive_in_qdb = [
            v for k, v in qdb.items()
            if k.startswith("event:") and v.get("alive") and v.get("quality", 0) >= 80
        ]
        if alive_in_qdb:
            log(f"[event-fallback] file sparse ({len(event_records)} entries); supplementing {len(alive_in_qdb)} alive entries from quality_db")
            import dataclasses as _dc
            for ev in alive_in_qdb[:MAX_RESI_INJECT]:
                uid = next((k for k, v in qdb.items() if v is ev), None)
                if uid is None:
                    continue
                # Reconstruct minimal ProxyRecord from quality_db entry
                if any(r.uid == uid for r in event_records):
                    continue
                url = ev.get("url", "")
                if not url:
                    continue
                r_fb = ProxyRecord(
                    uid=uid, ip=ev.get("ip", ""), port=int(ev.get("port", 0) or 0),
                    username=ev.get("username", ""), password=ev.get("password", ""),
                    source="ip2free_event", expire_ts=ev.get("expire_ts"),
                )
                r_fb.alive        = ev.get("alive")
                r_fb.latency_ms   = ev.get("latency_ms")
                r_fb.ip_type      = ev.get("ip_type", "unknown")
                r_fb.ip_org       = ev.get("ip_org", "")
                r_fb.quality      = ev.get("quality", 0)
                r_fb.last_probe_ts= ev.get("last_probe_ts")
                # set url from quality_db (stored during last full probe)
                r_fb.meta         = {"url": url}
                event_records.append(r_fb)
            log(f"[event-fallback] event_records now: {len(event_records)}")

    # ── 3. Probe ─────────────────────────────────────────────────────────────
    all_active = free_records + event_records
    probe_results = probe_all(all_active, force=force_probe)

    # ── 4. Classify (only alive IPs, skip cached) ─────────────────────────
    alive_ips = list({r.ip for r in all_active if probe_results.get(r.uid, (False,))[0]
                     and (force_classify or r.ip_type == "unknown")})
    classify_results: Dict[str, Dict[str, Any]] = {}
    if alive_ips:
        log(f"[classify] {len(alive_ips)} IPs to classify via ip-api.com…")
        classify_results = classify_ips(alive_ips)
        type_counts: Dict[str, int] = {}
        for v in classify_results.values():
            t = v.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        log(f"[classify] results: {type_counts}")
    else:
        log("[classify] no new IPs to classify (all cached or all dead)")

    # ── 5. Update records (applies probe+classify → quality) ─────────────
    update_records(free_records, probe_results, classify_results, qdb)
    update_records(event_records, probe_results, classify_results, qdb)

    # ── 6. Stats ─────────────────────────────────────────────────────────────
    def _stats(recs: List[ProxyRecord]) -> dict:
        alive  = [r for r in recs if r.alive]
        dead   = [r for r in recs if not r.alive]
        resi   = [r for r in alive if r.ip_type == "residential"]
        dc     = [r for r in alive if r.ip_type == "datacenter"]
        unk    = [r for r in alive if r.ip_type == "unknown"]
        q_good = [r for r in alive if r.quality >= MIN_QUALITY_SCORE]
        lats   = [r.latency_ms for r in alive if r.latency_ms and r.latency_ms < 9000]
        avg_lat = round(sum(lats) / len(lats)) if lats else None
        return {
            "total": len(recs), "alive": len(alive), "dead": len(dead),
            "residential": len(resi), "datacenter": len(dc), "unknown": len(unk),
            "quality_ok": len(q_good), "avg_latency_ms": avg_lat,
        }

    free_stats  = _stats(free_records)
    event_stats = _stats(event_records)
    log(f"[free]  {free_stats}")
    log(f"[event] {event_stats}")

    # Print per-proxy table
    log("\n── Event Proxy Quality Table ──────────────────────────────────────────")
    log(f"{'UID':30} {'IP':18} {'Type':12} {'Alive':5} {'Lat':6} {'Q':4} {'Exp(d)':7} {'Org':35}")
    for r in sorted(event_records, key=lambda x: (-x.quality, x.latency_ms or 9999)):
        exp = f"{r.expires_in_days():.1f}" if r.expires_in_days() is not None else "none"
        lat = f"{r.latency_ms}" if r.latency_ms else "?"
        log(f"{str(r.uid)[:30]:30} {r.ip:18} {r.ip_type:12} {str(r.alive):5} {lat:6} {r.quality:4} {exp:7} {r.ip_org[:35]}")

    log("\n── Free Proxy Quality Table ───────────────────────────────────────────")
    for r in sorted(free_records, key=lambda x: (-x.quality, x.latency_ms or 9999)):
        lat = f"{r.latency_ms}" if r.latency_ms else "?"
        log(f"{str(r.uid)[:30]:30} {r.ip:18} {r.ip_type:12} {str(r.alive):5} {lat:6} {r.quality:4} {'none':7} {r.ip_org[:35]}")

    # ── 7. Save cleaned files ─────────────────────────────────────────────
    if not dry_run:
        dead_free_removed  = sum(1 for r in free_records  if r.probe_fail_streak >= 5)
        dead_event_removed = sum(1 for r in event_records if r.probe_fail_streak >= 5)
        save_free_proxies(free_records)
        save_event_proxies(event_records)
        save_quality_db(qdb)
        if dead_free_removed or dead_event_removed:
            log(f"[clean] removed {dead_free_removed} dead free + {dead_event_removed} dead event proxies (fail_streak>=5)")

    # ── 8. resi_pool injection ────────────────────────────────────────────
    injected = 0
    if not dry_run:
        injected = inject_resi_pool(event_records, free_records, max_inject=max_inject)
        log(f"[inject] {injected} ip2free proxies → resi_pool")

    # ── 9. Replenish check ────────────────────────────────────────────────
    alive_event = event_stats["alive"]
    if not dry_run:
        maybe_trigger_sync(alive_event)

    elapsed = round(time.time() - t_start, 1)
    summary = {
        "ts": time.time(),
        "elapsed_s": elapsed,
        "expired_removed": expired_count,
        "free":  free_stats,
        "event": event_stats,
        "injected_resi_pool": injected,
        "triggered_sync": alive_event < MIN_EVENT_POOL,
    }
    _save_json(MAINTAIN_STATE_FILE, summary)
    log(f"\n[done] elapsed={elapsed}s injected={injected} alive_event={alive_event}")
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Lock + main
# ══════════════════════════════════════════════════════════════════════════════

def _release_stale_lock(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        fd = open(path, "r")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN); fd.close()
            os.unlink(path)
            log(f"[lock] removed stale: {path}")
        except (OSError, IOError):
            fd.close()
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="ip2free proxy quality maintenance v1.0")
    parser.add_argument("--force-probe",    action="store_true",
                        help="Re-probe all proxies regardless of cache TTL")
    parser.add_argument("--force-classify", action="store_true",
                        help="Re-classify all IPs regardless of cache")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Check only, do not modify files or resi_pool")
    parser.add_argument("--max-inject",     type=int, default=MAX_RESI_INJECT,
                        help=f"Max ip2free proxies to inject into resi_pool (default {MAX_RESI_INJECT})")
    parser.add_argument("--status",         action="store_true",
                        help="Print last maintenance state and exit")
    args = parser.parse_args()

    if args.status:
        state = _load_json(MAINTAIN_STATE_FILE, {})
        if state:
            print(json.dumps(state, indent=2))
        else:
            print("No maintenance state yet.")
        return

    _release_stale_lock(LOCK_FILE)
    try:
        lf = open(LOCK_FILE, "w")
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        print("[main] another instance running, exit", file=sys.stderr)
        sys.exit(0)

    import atexit
    atexit.register(lambda: (fcntl.flock(lf, fcntl.LOCK_UN), lf.close()))

    try:
        summary = run_maintenance(
            force_probe=args.force_probe,
            force_classify=args.force_classify,
            dry_run=args.dry_run,
            max_inject=args.max_inject,
        )
        print(json.dumps(summary, ensure_ascii=False))
    except Exception as e:
        log(f"[FATAL] {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
