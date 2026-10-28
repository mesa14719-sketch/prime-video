async function decodePvimport(encoded) {
  const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const text = await new Response(stream).text();
  return JSON.parse(text);
}

function setStatus(text, isError) {
  const el = document.getElementById("status");
  if (el) {
    el.textContent = text;
    el.className = isError ? "err" : "ok";
  }
  const spin = document.getElementById("spin");
  if (spin && isError) spin.style.display = "none";
}

async function run() {
  const params = new URLSearchParams(location.search);
  const encoded = params.get("d");
  const dest = params.get("to") || "https://www.primevideo.com/";
  const tabId = params.get("tabId") ? Number(params.get("tabId")) : undefined;

  if (!encoded) {
    setStatus("Invalid login link — missing session data.", true);
    return;
  }

  try {
    const cookies = await decodePvimport(encoded);
    const response = await chrome.runtime.sendMessage({
      type: "import-cookies",
      cookies,
      tabId,
    });

    if (!response?.ok) {
      setStatus(response?.error || "Cookie import failed.", true);
      return;
    }

    setStatus(`Imported ${response.count} cookies. Opening Prime Video...`, false);
    setTimeout(() => window.location.replace(dest), 300);
  } catch (error) {
    setStatus(error.message || "Import failed.", true);
  }
}

run();
