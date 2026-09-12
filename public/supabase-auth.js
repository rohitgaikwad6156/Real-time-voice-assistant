(() => {
  const BACKEND = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? location.origin
    : "https://real-time-voice-assistant-9bh1.onrender.com";
  const SUPABASE_JS_URL = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.116.0/dist/umd/supabase.min.js";
  const SUPABASE_JS_INTEGRITY = "sha384-JBR+x8blGwjDRO63aHCGiZMD4VNiTR4ZUGA+N6ZKLf3zNt1fK8IBpcgPaMrxqWBp";
  const SUPABASE_SCRIPT_ID = "supabase-js";
  const TOKEN_KEY = "voiceAssistantToken";
  const USER_KEY = "voiceAssistantUser";
  const CONVERSATION_KEY = "voiceAssistantConversationId";
  const SELECT_LATEST_ON_BOOT_KEY = "voiceAssistantSelectLatestOnBoot";

  let clientPromise = null;
  let overlayEnhanced = false;
  let exchangeInProgress = false;

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(`${BACKEND}${path}`, {
        ...options,
        cache: "no-store",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      });
    } catch (cause) {
      const error = new Error("Server is still waking up. Please try again in a moment.");
      error.cause = cause;
      throw error;
    }

    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || "Supabase sign-in failed.");
    return data;
  }

  function loadSupabaseScript() {
    if (window.supabase?.createClient) return Promise.resolve();
    const existing = document.getElementById(SUPABASE_SCRIPT_ID);
    if (existing) {
      return new Promise((resolve, reject) => {
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", () => reject(new Error("Could not load Supabase Auth.")), { once: true });
      });
    }

    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.id = SUPABASE_SCRIPT_ID;
      script.src = SUPABASE_JS_URL;
      script.integrity = SUPABASE_JS_INTEGRITY;
      script.crossOrigin = "anonymous";
      script.async = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Could not load Supabase Auth."));
      document.head.appendChild(script);
    });
  }

  function getClient() {
    if (clientPromise) return clientPromise;
    clientPromise = (async () => {
      const config = await api("/api/auth/supabase/config", { method: "GET" });
      if (!config?.enabled || !config.url || !config.publishable_key) {
        throw new Error("Supabase Google sign-in is not configured yet.");
      }
      await loadSupabaseScript();
      const client = window.supabase.createClient(config.url, config.publishable_key, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      });
      window.VOICE_SUPABASE_SIGN_OUT = () => client.auth.signOut({ scope: "local" });
      return client;
    })();
    return clientPromise;
  }

  function storeApplicationSession(result) {
    localStorage.setItem(TOKEN_KEY, result.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(result.user));
    localStorage.removeItem(CONVERSATION_KEY);
    localStorage.setItem(SELECT_LATEST_ON_BOOT_KEY, "1");
  }

  async function exchangeSupabaseSession(session, status) {
    if (!session?.access_token || exchangeInProgress || localStorage.getItem(TOKEN_KEY)) return false;
    exchangeInProgress = true;
    if (status) status.textContent = "Completing secure Google sign-in...";
    try {
      const result = await api("/api/auth/supabase", {
        method: "POST",
        body: JSON.stringify({ access_token: session.access_token }),
      });
      storeApplicationSession(result);
      const cleanUrl = new URL(location.href);
      ["code", "error", "error_code", "error_description", "state"].forEach((key) => cleanUrl.searchParams.delete(key));
      cleanUrl.hash = "";
      history.replaceState(null, "", `${cleanUrl.pathname}${cleanUrl.search}`);
      location.reload();
      return true;
    } finally {
      exchangeInProgress = false;
    }
  }

  async function initializeGoogleButton(section) {
    const button = section.querySelector("#supabaseGoogleButton");
    const status = section.querySelector("#supabaseAuthStatus");
    try {
      status.textContent = "Loading secure Google sign-in...";
      const client = await getClient();
      const { data, error } = await client.auth.getSession();
      if (error) throw error;
      if (await exchangeSupabaseSession(data.session, status)) return;

      button.disabled = false;
      status.textContent = "";
      button.onclick = async () => {
        button.disabled = true;
        status.textContent = "Redirecting to Google...";
        const redirectTo = `${location.origin}${location.pathname}`;
        const { error: oauthError } = await client.auth.signInWithOAuth({
          provider: "google",
          options: { redirectTo },
        });
        if (oauthError) {
          button.disabled = false;
          status.textContent = oauthError.message || "Google sign-in failed.";
        }
      };
    } catch (error) {
      console.error("[Supabase Auth]", error);
      button.disabled = true;
      status.textContent = error.message || "Google sign-in is temporarily unavailable.";
    }
  }

  function enhanceAuthOverlay() {
    if (overlayEnhanced) return true;
    const card = document.querySelector(".auth-card");
    if (!card) return false;
    if (card.querySelector("#supabaseAuthSection")) return true;

    const section = document.createElement("div");
    section.id = "supabaseAuthSection";
    section.style.cssText = "margin-top:18px;text-align:center";
    section.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin:4px 0 14px;color:#64748b;font-size:12px">
        <span style="height:1px;background:#273449;flex:1"></span><span>or continue with</span><span style="height:1px;background:#273449;flex:1"></span>
      </div>
      <button id="supabaseGoogleButton" type="button" disabled style="width:100%;padding:12px;border-radius:999px;border:1px solid #cbd5e1;background:white;color:#1f2937;font-weight:700;cursor:pointer">Continue with Google</button>
      <div id="supabaseAuthStatus" style="min-height:18px;margin-top:8px;color:#94a3b8;font-size:12px"></div>`;
    card.appendChild(section);
    overlayEnhanced = true;
    initializeGoogleButton(section);
    return true;
  }

  async function start() {
    // Initialize early so an OAuth callback can be exchanged even before the overlay appears.
    try {
      const client = await getClient();
      const { data } = await client.auth.getSession();
      if (await exchangeSupabaseSession(data.session, null)) return;
    } catch (error) {
      console.warn("[Supabase Auth] Initialization delayed:", error);
    }

    if (enhanceAuthOverlay()) return;
    const observer = new MutationObserver(() => {
      if (enhanceAuthOverlay()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => observer.disconnect(), 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
