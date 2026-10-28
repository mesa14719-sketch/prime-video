const LOGIN_PATH = /^\/login\/([A-Za-z0-9_-]+)/;

function showStatus(message, isError) {
  const el = document.getElementById("pv-status");
  if (el) {
    el.textContent = message;
    el.style.color = isError ? "#ff6b8a" : "#3dff9a";
  }
  const spin = document.querySelector(".spin");
  if (spin) spin.style.display = isError ? "none" : "";
}

async function runLoginImport() {
  const match = window.location.pathname.match(LOGIN_PATH);
  if (!match) return;

  const token = match[1];
  const target = new URLSearchParams(window.location.search).get("target") || "pc";

  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
    showStatus(
      "Extension not loaded. Install Prime Video Session Import and allow it in incognito.",
      true
    );
    return;
  }

  showStatus("Fetching session...", false);

  let data;
  try {
    const res = await fetch(`/api/session/${token}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || "Session not found or expired.");
  } catch (error) {
    showStatus(error.message, true);
    return;
  }

  showStatus(`Importing ${data.cookies?.length || 0} cookies...`, false);

  let response;
  try {
    response = await chrome.runtime.sendMessage({
      type: "import-cookies",
      cookies: data.cookies,
    });
  } catch (_error) {
    showStatus(
      "Extension not connected. chrome://extensions → Prime Video Session Import → Allow in incognito → Reload.",
      true
    );
    return;
  }

  if (!response) {
    showStatus("Extension gave no response. Click Reload on the extension and try again.", true);
    return;
  }

  if (!response.ok) {
    showStatus(response.error || "Cookie import failed.", true);
    return;
  }

  const redirects = data.redirects || {};
  const dest = redirects[target] || redirects.pc || "https://www.primevideo.com/";
  showStatus(`Imported ${response.count} cookies. Opening Prime Video...`, false);
  setTimeout(() => window.location.replace(dest), 400);
}

if (LOGIN_PATH.test(window.location.pathname)) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runLoginImport);
  } else {
    runLoginImport();
  }
}
