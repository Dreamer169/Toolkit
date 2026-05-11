/**
 * geo-resolver.ts
 * Resolves exit-IP geo profile (timezone / language / lat-lng) by querying
 * ip-api.com *through* the session proxy.  Results cached 5 min per proxy URL.
 * Used by CdpSession.start() to make timezoneId / locale / geolocation self-consistent.
 */
import { logger } from "./logger.js";

export interface GeoProfile {
  timezone: string;
  language: string;
  locale: string;
  latitude: number;
  longitude: number;
  countryCode: string;
}

export const DEFAULT_GEO: GeoProfile = {
  timezone: "America/Los_Angeles",
  language: "en-US",
  locale: "en-US",
  latitude: 37.7749,
  longitude: -122.4194,
  countryCode: "US",
};

const TZ_LANG: [string, string][] = [
  ["America/", "en-US"],
  ["Europe/London", "en-GB"],
  ["Europe/Paris", "fr-FR"],
  ["Europe/Berlin", "de-DE"],
  ["Europe/Rome", "it-IT"],
  ["Europe/Madrid", "es-ES"],
  ["Europe/Lisbon", "pt-PT"],
  ["Europe/Moscow", "ru-RU"],
  ["Europe/Warsaw", "pl-PL"],
  ["Europe/Amsterdam", "nl-NL"],
  ["Asia/Tokyo", "ja-JP"],
  ["Asia/Shanghai", "zh-CN"],
  ["Asia/Seoul", "ko-KR"],
  ["Asia/Singapore", "en-SG"],
  ["Asia/Hong_Kong", "zh-HK"],
  ["Asia/Kolkata", "hi-IN"],
  ["Asia/Bangkok", "th-TH"],
  ["Australia/", "en-AU"],
  ["Pacific/Auckland", "en-NZ"],
];

function tzToLang(tz: string): string {
  for (const [prefix, lang] of TZ_LANG) {
    if (tz.startsWith(prefix)) return lang;
  }
  return "en-US";
}

interface IpApiJson {
  status: string;
  timezone?: string;
  lat?: number;
  lon?: number;
  countryCode?: string;
}

const IP_API_PATH = "/json?fields=status,timezone,lat,lon,countryCode";

function parseHttpBody(raw: string): string {
  const sep = raw.indexOf("\r\n\r\n");
  const body = sep === -1 ? raw.trim() : raw.slice(sep + 4).trim();
  // Strip chunked transfer encoding size lines
  if (/^[0-9a-f]+\r\n/i.test(body)) {
    return body.replace(/^[0-9a-f]+\r\n/gim, "").replace(/\r\n/g, "").trim();
  }
  return body;
}

function buildGeoProfile(j: IpApiJson): GeoProfile {
  const tz = j.timezone ?? "America/Los_Angeles";
  const lang = tzToLang(tz);
  return {
    timezone: tz,
    language: lang,
    locale: lang,
    latitude: j.lat ?? DEFAULT_GEO.latitude,
    longitude: j.lon ?? DEFAULT_GEO.longitude,
    countryCode: j.countryCode ?? "US",
  };
}

async function fetchViaSocks5(host: string, port: number): Promise<GeoProfile> {
  const { SocksClient } = await import("socks");
  const { socket } = await SocksClient.createConnection({
    proxy: { host, port, type: 5 },
    command: "connect",
    destination: { host: "ip-api.com", port: 80 },
    timeout: 6000,
  });
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { socket.destroy(); reject(new Error("geo socks5 timeout")); }, 9000);
    let buf = "";
    socket.setEncoding("utf8");
    socket.write(`GET ${IP_API_PATH} HTTP/1.1\r\nHost: ip-api.com\r\nConnection: close\r\n\r\n`);
    socket.on("data", (d: string) => { buf += d; });
    socket.on("end", () => {
      clearTimeout(t);
      try {
        const j = JSON.parse(parseHttpBody(buf)) as IpApiJson;
        if (j.status !== "success" || !j.timezone) { reject(new Error("ip-api bad response")); return; }
        resolve(buildGeoProfile(j));
      } catch (e) { reject(e); }
    });
    socket.on("error", (e: Error) => { clearTimeout(t); reject(e); });
  });
}

async function fetchViaHttpProxy(proxyUrl: URL): Promise<GeoProfile> {
  const { connect } = await import("node:net");
  const proxyHost = proxyUrl.hostname;
  const proxyPort = Number(proxyUrl.port) || 8080;
  const raw = proxyUrl.username && proxyUrl.password
    ? Buffer.from(`${decodeURIComponent(proxyUrl.username)}:${decodeURIComponent(proxyUrl.password)}`).toString("base64")
    : null;

  return new Promise((resolve, reject) => {
    const socket = connect(proxyPort, proxyHost);
    const t = setTimeout(() => { socket.destroy(); reject(new Error("geo http-proxy timeout")); }, 9000);
    let reqLines = `GET http://ip-api.com${IP_API_PATH} HTTP/1.1\r\nHost: ip-api.com\r\n`;
    if (raw) reqLines += `Proxy-Authorization: Basic ${raw}\r\n`;
    reqLines += "Connection: close\r\n\r\n";
    socket.on("connect", () => socket.write(reqLines));
    let buf = "";
    socket.setEncoding("utf8");
    socket.on("data", (d: string) => { buf += d; });
    socket.on("end", () => {
      clearTimeout(t);
      try {
        const j = JSON.parse(parseHttpBody(buf)) as IpApiJson;
        if (j.status !== "success" || !j.timezone) { reject(new Error("ip-api bad response")); return; }
        resolve(buildGeoProfile(j));
      } catch (e) { reject(e); }
    });
    socket.on("error", (e: Error) => { clearTimeout(t); reject(e); });
  });
}

const _cache = new Map<string, { geo: GeoProfile; at: number }>();
const TTL = 5 * 60_000;

export async function resolveGeoProfile(proxy: string): Promise<GeoProfile> {
  const hit = _cache.get(proxy);
  if (hit && Date.now() - hit.at < TTL) return hit.geo;
  try {
    const u = new URL(proxy.includes("://") ? proxy : `http://${proxy}`);
    const scheme = u.protocol.replace(":", "").toLowerCase();
    let geo: GeoProfile;
    if (scheme === "socks5" || scheme === "socks5h" || scheme === "socks4" || scheme === "socks") {
      geo = await fetchViaSocks5(u.hostname || "127.0.0.1", Number(u.port) || 1080);
    } else {
      geo = await fetchViaHttpProxy(u);
    }
    _cache.set(proxy, { geo, at: Date.now() });
    logger.info({ tz: geo.timezone, cc: geo.countryCode }, "[geo] resolved via proxy");
    return geo;
  } catch (e) {
    logger.warn({ err: String(e) }, "[geo] resolve failed → using default (LA)");
    return DEFAULT_GEO;
  }
}
