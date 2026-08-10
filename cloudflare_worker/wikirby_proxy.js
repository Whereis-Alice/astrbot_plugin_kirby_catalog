const ALLOWED_RAW_PATH = "/w/index.php";
const ALLOWED_API_PATH = "/w/api.php";
const ALLOWED_REST_PREFIX = "/w/rest.php/v1/";
const WIKIRBY_ORIGIN = "https://wikirby.com";
const WIKIRBY_CDN_ORIGIN = "https://cdn.wikirby.com";
const FANDOM_ORIGIN = "https://kirby.fandom.com";
const SHINKAKU_ORIGIN = "https://seesaawiki.jp";
const SHINKAKU_PAGE_PREFIX = "/kirby_shinkaku/d/";
const SHINKAKU_SEARCH_PATH = "/kirby_shinkaku/search";
const FANDOM_IMAGE_HOSTS = new Set([
  "static.wikia.nocookie.net",
  "vignette.wikia.nocookie.net",
  "kirby.fandom.com",
]);
const CONTROL_QUERY_KEYS = new Set([
  "site",
  "path",
  "asset",
  "image_host",
  "raw_query",
]);
const CACHE_SECONDS = 300;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function isSafePath(pathname) {
  let decoded = pathname;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    // EUC-JP page paths intentionally contain percent bytes that are not
    // UTF-8. The original value is still checked below.
  }
  return (
    pathname.startsWith("/") &&
    !pathname.includes("..") &&
    !pathname.includes("\\") &&
    !pathname.includes("\0") &&
    !decoded.includes("..") &&
    !decoded.includes("\\") &&
    !decoded.includes("\0")
  );
}

function isAllowedWikirbyPath(pathname, isImage, searchParams) {
  if (isImage) {
    return /^\/(?:thumb\/)?[0-9a-f]\/[0-9a-f]{2}\/[^/]+(?:\/[^/]+)?$/i.test(pathname);
  }
  if (pathname === ALLOWED_API_PATH) return true;
  if (pathname.startsWith(`${ALLOWED_REST_PREFIX}page/`)) return true;
  if (pathname === "/w/rest.php/v1/search/page") return true;
  return pathname === ALLOWED_RAW_PATH && searchParams.get("action") === "raw";
}

function isAllowedFandomImagePath(hostname, pathname) {
  if (hostname === "kirby.fandom.com") {
    return pathname.startsWith("/images/");
  }
  return pathname.startsWith("/kirby/images/");
}

function isAllowedShinkakuPath(pathname, isImage, imageHost) {
  if (isImage) {
    return (
      /^image0[1-9]\.seesaawiki\.jp$/i.test(imageHost) &&
      pathname.startsWith("/k/u/kirby_shinkaku/")
    );
  }
  return pathname.startsWith(SHINKAKU_PAGE_PREFIX) || pathname === SHINKAKU_SEARCH_PATH;
}

function resolveRoute(incoming) {
  const site = (incoming.searchParams.get("site") || "wikirby").toLowerCase();
  const asset = incoming.searchParams.get("asset");
  const isImage = asset === "image";
  const pathname = incoming.searchParams.get("path") || "";

  if (!isSafePath(pathname) || (asset !== null && !isImage)) return null;

  if (site === "wikirby") {
    if (!isAllowedWikirbyPath(pathname, isImage, incoming.searchParams)) return null;
    return {
      site,
      isImage,
      origin: isImage ? WIKIRBY_CDN_ORIGIN : WIKIRBY_ORIGIN,
      pathname,
      referer: "https://wikirby.com/",
    };
  }

  if (site === "fandom") {
    if (!isImage) {
      if (pathname !== "/api.php") return null;
      return {
        site,
        isImage,
        origin: FANDOM_ORIGIN,
        pathname,
        referer: `${FANDOM_ORIGIN}/`,
      };
    }

    const imageHost = (incoming.searchParams.get("image_host") || "").toLowerCase();
    if (
      !FANDOM_IMAGE_HOSTS.has(imageHost) ||
      !isAllowedFandomImagePath(imageHost, pathname)
    ) {
      return null;
    }
    return {
      site,
      isImage,
      origin: `https://${imageHost}`,
      pathname,
      referer: `${FANDOM_ORIGIN}/`,
    };
  }

  if (site === "shinkaku") {
    const imageHost = (incoming.searchParams.get("image_host") || "").toLowerCase();
    if (!isAllowedShinkakuPath(pathname, isImage, imageHost)) return null;
    return {
      site,
      isImage,
      origin: isImage ? `https://${imageHost}` : SHINKAKU_ORIGIN,
      pathname,
      referer: `${SHINKAKU_ORIGIN}/kirby_shinkaku/`,
      acceptLanguage: "ja,en-US;q=0.9,en;q=0.8",
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
        "AppleWebKit/537.36 (KHTML, like Gecko) " +
        "Chrome/128.0.0.0 Safari/537.36",
    };
  }

  return null;
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
    const route = resolveRoute(incoming);
    if (!route) {
      return json({ error: "path_not_allowed" }, 400);
    }

    const target = new URL(route.origin);
    target.pathname = route.pathname;
    const rawQuery =
      route.site === "shinkaku" ? incoming.searchParams.get("raw_query") : null;
    if (rawQuery !== null) {
      // Seesaa search uses EUC-JP percent bytes. URLSearchParams would decode
      // those bytes as UTF-8 replacement characters, so restore the exact
      // upstream query string supplied by the authenticated plugin instead.
      target.search = rawQuery ? `?${rawQuery}` : "";
    } else {
      for (const [key, value] of incoming.searchParams) {
        if (!CONTROL_QUERY_KEYS.has(key)) target.searchParams.append(key, value);
      }
    }

    const cacheKey = new Request(target.toString(), { method: "GET" });
    const cached = await caches.default.match(cacheKey);
    if (cached) return cached;

    const upstream = await fetch(target.toString(), {
      headers: {
        Accept: route.isImage
          ? "image/*"
          : request.headers.get("Accept") || "application/json",
        "Accept-Language":
          route.acceptLanguage || request.headers.get("Accept-Language") || "en-US,en;q=0.8",
        Referer: route.referer,
        "User-Agent": route.userAgent || "kirby-catalog-cloudflare-worker/1.2",
      },
    });

    const headers = new Headers(upstream.headers);
    headers.delete("set-cookie");
    headers.set("Cache-Control", `public, max-age=${CACHE_SECONDS}`);
    headers.set("X-Kirby-Catalog-Proxy", route.site);
    if (route.site === "wikirby") {
      headers.set("X-WiKirby-Proxy", "cloudflare-worker");
    } else if (route.site === "fandom") {
      headers.set("X-Fandom-Proxy", "cloudflare-worker");
    } else {
      headers.set("X-Shinkaku-Proxy", "cloudflare-worker");
    }
    const response = new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });

    if (upstream.ok) ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
    return response;
  },
};
