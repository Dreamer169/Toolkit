import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function isUrl(input: string): boolean {
  if (!input) return false;
  // If it starts with protocol, it's a URL
  if (/^https?:\/\//i.test(input)) return true;
  // If it has domain format (e.g., example.com, test.co.uk)
  if (/^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:\/.*)?$/.test(input)) return true;
  // Localhost
  if (/^(localhost|127\.0\.0\.1)(:\d+)?(?:\/.*)?$/.test(input)) return true;
  return false;
}

export function formatUrl(input: string): string {
  if (isUrl(input) && !/^https?:\/\//i.test(input)) {
    return `https://${input}`;
  }
  return input;
}

export function getSearchUrl(query: string, engine: string): string {
  const q = encodeURIComponent(query);
  switch (engine) {
    case "Google": return `https://www.google.com/search?q=${q}`;
    case "Bing": return `https://www.bing.com/search?q=${q}`;
    case "Brave Search": return `https://search.brave.com/search?q=${q}`;
    case "Wikipedia": return `https://en.wikipedia.org/wiki/Special:Search?search=${q}`;
    case "DuckDuckGo": return `https://duckduckgo.com/?q=${q}`;
    case "DuckDuckGo (HTML)":
    default: return `https://html.duckduckgo.com/html/?q=${q}`;
  }
}

export function getFaviconUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("browser://")) {
    return ""; // Internal pages handle their own icons
  }
  try {
    const { hostname } = new URL(url);
    if (!hostname) return "";
    return `https://www.google.com/s2/favicons?domain=${hostname}&sz=64`;
  } catch {
    return "";
  }
}

export function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(timestamp));
}

export function formatDate(timestamp: number): string {
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(new Date(timestamp));
}
