const ALLOWED_RAW_PATH = "/w/index.php";
const ALLOWED_API_PATH = "/w/api.php";
const ALLOWED_REST_PREFIX = "/w/rest.php/v1/";
const TARGET_ORIGIN = "https://wikirby.com";
const CDN_ORIGIN = "https://cdn.wikirby.com";
const CACHE_SECONDS = 300;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function isAllowedPath(pathname, searchParams) {
  if (searchParams.get("asset") === "image") {
    return /^\/(?:thumb\/)?[0-9a-f]\/[0-9a-f]{2}\/[^/]+(?:\/[^/]+)?$/i.test(pathname);
  }
  if (pathname === ALLOWED_API_PATH) return true;
  if (pathname.startsWith(`${ALLOWED_REST_PREFIX}page/`)) return true;
  if (pathname === "/w/rest.php/v1/search/page") return true;
  return pathname === ALLOWED_RAW_PATH && searchParams.get("action") === "raw";
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET") {
      return json({ error: "method_not_allowed" }, 405);
    }

    const expectedToken = env.WIKIRBY_PROXY_TOKEN;
    const suppliedToken = request.headers.get("Authorization") || "";
    if (!expectedToken || suppliedToken !== `Bearer ${expectedToken}`) {
      return json({ error: "unauthorized" }, 401);
    }

    const incoming = new URL(request.url);
    const pathname = incoming.searchParams.get("path") || "";
    if (!pathname.startsWith("/") || pathname.includes("..") || !isAllowedPath(pathname, incoming.searchParams)) {
      return json({ error: "path_not_allowed" }, 400);
    }

    const isImage = incoming.searchParams.get("asset") === "image";
    const target = new URL(isImage ? CDN_ORIGIN : TARGET_ORIGIN);
    target.pathname = pathname;
    for (const [key, value] of incoming.searchParams) {
      if (key !== "path" && key !== "asset") target.searchParams.append(key, value);
    }

    const cacheKey = new Request(target.toString(), { method: "GET" });
    const cached = await caches.default.match(cacheKey);
    if (cached) return cached;

    const upstream = await fetch(target.toString(), {
      headers: {
        Accept: isImage ? "image/*" : request.headers.get("Accept") || "application/json",
        "Accept-Language": request.headers.get("Accept-Language") || "en-US,en;q=0.8",
        "User-Agent": "kirby-catalog-cloudflare-worker/1.0",
      },
    });

    const headers = new Headers(upstream.headers);
    headers.delete("set-cookie");
    headers.set("Cache-Control", `public, max-age=${CACHE_SECONDS}`);
    headers.set("X-WiKirby-Proxy", "cloudflare-worker");
    const response = new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });

    if (upstream.ok) ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
    return response;
  },
};
