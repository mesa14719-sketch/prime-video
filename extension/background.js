const PRIME_DOMAINS = ["primevideo.com", "amazon.com", "amazon.in", "amazon.co.uk"];
const HASH_TAG = "#pvimport=";
const QUERY_KEY = "pvimport";

function normalizeValue(value) {
  if (typeof value !== "string") return String(value ?? "");
  let v = value.trim();
  if (v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1);
  if (v.startsWith("'") && v.endsWith("'")) v = v.slice(1, -1);
  return v;
}

function cookieUrl(entry) {
  const domain = (entry.domain || "").toLowerCase();
  if (domain.includes("primevideo.com")) return "https://www.primevideo.com/";
  if (domain.includes("amazon.in")) return "https://www.amazon.in/";
  if (domain.includes("amazon.co.uk")) return "https://www.amazon.co.uk/";
  if (domain.includes("amazon.com")) return "https://www.amazon.com/";
  const host = domain.startsWith(".") ? `www${domain}` : domain;
  return `https://${host}/`;
}

async function decodePvimport(encoded) {
  const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const text = await new Response(stream).text();
  return JSON.parse(text);
}

async function resolveStoreId(tabId) {
  if (!tabId) return undefined;
  const stores = await chrome.cookies.getAllCookieStores();
  const store = stores.find((s) => s.tabIds.includes(tabId));
  return store?.id;
}

async function clearPrimeCookies(storeId) {
  for (const domain of PRIME_DOMAINS) {
    const query = storeId ? { domain, storeId } : { domain };
    const existing = await chrome.cookies.getAll(query);
    for (const cookie of existing) {
      const host = cookie.domain.replace(/^\./, "");
      const scheme = cookie.secure ? "https" : "http";
      const removeQuery = {
        url: `${scheme}://${host}${cookie.path}`,
        name: cookie.name,
      };
      if (storeId) removeQuery.storeId = storeId;
      await chrome.cookies.remove(removeQuery);
    }
  }
}

async function setCookie(entry, storeId) {
  const value = normalizeValue(entry.value);
  const details = {
    url: cookieUrl(entry),
    name: entry.name,
    value,
    path: entry.path || "/",
    secure: true,
    httpOnly: !!entry.httpOnly,
    sameSite: "no_restriction",
  };
  if (entry.domain) details.domain = entry.domain;
  if (entry.expirationDate && Number(entry.expirationDate) > 0) {
    details.expirationDate = Number(entry.expirationDate);
  }
  if (storeId) details.storeId = storeId;

  let result = await chrome.cookies.set(details);
  if (!result && details.httpOnly) {
    details.httpOnly = false;
    result = await chrome.cookies.set(details);
  }
  if (!result) {
    throw new Error(`Could not set cookie: ${entry.name}`);
  }
  return result;
}

async function verifySessionToken(storeId) {
  const query = { url: "https://www.primevideo.com/", name: "session-token" };
  if (storeId) query.storeId = storeId;
  const cookie = await chrome.cookies.get(query);
  return !!cookie?.value;
}

async function importCookies(cookies, storeId) {
  await clearPrimeCookies(storeId);

  const results = await Promise.allSettled(cookies.map((entry) => setCookie(entry, storeId)));
  const failed = results.filter((r) => r.status === "rejected");
  const ok = results.filter((r) => r.status === "fulfilled");

  const hasSession = cookies.some((c) => c.name === "session-token");
  if (hasSession) {
    const verified = await verifySessionToken(storeId);
    if (!verified) {
      throw new Error(
        "session-token not set. Enable 'Allow in incognito' on the extension and reload it."
      );
    }
  }

  if (ok.length === 0) {
    throw new Error(failed[0]?.reason?.message || "All cookies failed to import.");
  }

  return { ok: true, count: ok.length, failed: failed.length };
}

function extractPvimport(url) {
  const hashIdx = url.indexOf(HASH_TAG);
  if (hashIdx !== -1) {
    return {
      encoded: url.slice(hashIdx + HASH_TAG.length).split("&")[0],
      dest: url.slice(0, hashIdx) || "https://www.primevideo.com/",
    };
  }
  const match = url.match(/[?&]pvimport=([^&#]+)/);
  if (match) {
    const dest = url.replace(/([?&])pvimport=[^&#]+/, "").replace(/[?&]$/, "");
    return { encoded: match[1], dest: dest || "https://www.primevideo.com/" };
  }
  return null;
}

function redirectTabToImport(tabId, encoded, dest) {
  const importUrl =
    chrome.runtime.getURL("import.html") +
    `?d=${encodeURIComponent(encoded)}` +
    `&to=${encodeURIComponent(dest)}` +
    `&tabId=${tabId}`;
  return chrome.tabs.update(tabId, { url: importUrl });
}

chrome.webNavigation.onBeforeNavigate.addListener(
  (details) => {
    if (details.frameId !== 0) return;
    const parsed = extractPvimport(details.url);
    if (!parsed?.encoded) return;
    redirectTabToImport(details.tabId, parsed.encoded, parsed.dest).catch((error) => {
      console.error("[pvimport] redirect failed:", error);
    });
  },
  { url: [{ hostContains: "primevideo.com" }] }
);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "import-cookies" || !Array.isArray(message.cookies)) {
    sendResponse({ ok: false, error: "Invalid payload" });
    return;
  }

  (async () => {
    const tabId = message.tabId || _sender.tab?.id;
    const storeId = await resolveStoreId(tabId);
    const result = await importCookies(message.cookies, storeId);
    sendResponse(result);
  })().catch((error) => {
    sendResponse({ ok: false, error: String(error.message || error) });
  });

  return true;
});
