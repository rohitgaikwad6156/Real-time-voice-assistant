(() => {
  const BACKEND = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? location.origin
    : "https://real-time-voice-assistant-9bh1.onrender.com";

  const TOKEN_KEY = "voiceAssistantToken";
  const USER_KEY = "voiceAssistantUser";
  const CONVERSATION_KEY = "voiceAssistantConversationId";
  const SELECT_LATEST_ON_BOOT_KEY = "voiceAssistantSelectLatestOnBoot";
  const GOOGLE_SCRIPT_ID = "google-identity-services";

  let overlayEnhanced = false;
  let googleInitInProgress = false;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(`${BACKEND}${path}`, {
        ...options,
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
      });
    } catch (cause) {
      const error = new Error("Server is still waking up. Please try again in a moment.");
      error.status = 0;
      error.cause = cause;
      throw error;
    }

    let data = {};
    try {
      data = await response.json();
    } catch (_) {}

    if (!response.ok) {
      const error = new Error(data.detail || "Google sign-in failed.");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function storeAuthenticatedSession(result) {
    localStorage.setItem(TOKEN_KEY, result.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(result.user));
    localStorage.removeItem(CONVERSATION_KEY);
    localStorage.setItem(SELECT_LATEST_ON_BOOT_KEY, "1");
  }

  function loadGoogleIdentityScript() {
    if (window.google?.accounts?.id) return Promise.resolve();

    const existing = document.getElementById(GOOGLE_SCRIPT_ID);
    if (existing) {
      return new Promise((resolve, reject) => {
        if (window.google?.accounts?.id) {
          resolve();
          return;
        }
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", () => reject(new Error("Could not load Google Sign-In.")), { once: true });
      });
    }

    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.id = GOOGLE_SCRIPT_ID;
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Could not load Google Sign-In."));
      document.head.appendChild(script);
    });
  }

  async function initializeGoogleButton(section) {
    if (googleInitInProgress || section.dataset.googleReady === "true") return;
    googleInitInProgress = true;

    const mount = section.querySelector("#googleButtonMount");
    const status = section.querySelector("#googleAuthStatus");
    status.textContent = "Loading Google sign-in...";

    try {
      let config = null;

      // Render free instances can be asleep. Retry config while keeping the
      // ordinary email/password form usable immediately.
      for (let attempt = 0; attempt < 12; attempt++) {
        try {
          config = await api("/api/auth/google/config", { method: "GET" });
          break;
        } catch (error) {
          if (attempt === 11) throw error;
          status.textContent = "Waking server for Google sign-in...";
          await sleep(attempt < 3 ? 1500 : 2500);
        }
      }

      if (!config?.enabled || !config?.client_id) {
        status.textContent = "Google sign-in is not configured yet.";
        return;
      }

      await loadGoogleIdentityScript();
      if (!window.google?.accounts?.id) {
        throw new Error("Google Sign-In did not initialize.");
      }

      mount.innerHTML = "";
      window.google.accounts.id.initialize({
        client_id: config.client_id,
        auto_select: false,
        cancel_on_tap_outside: true,
        callback: async (response) => {
          const credential = response?.credential;
          if (!credential) {
            status.textContent = "Google did not return a sign-in credential.";
            return;
          }

          status.textContent = "Signing in with Google...";
          try {
            const result = await api("/api/auth/google", {
              method: "POST",
              body: JSON.stringify({ credential }),
            });
            storeAuthenticatedSession(result);
            status.textContent = "Signed in. Starting assistant...";
            location.reload();
          } catch (error) {
            console.error("[Google Auth]", error);
            status.textContent = error.message || "Google sign-in failed.";
          }
        },
      });

      window.google.accounts.id.renderButton(mount, {
        type: "standard",
        theme: "outline",
        size: "large",
        text: "signin_with",
        shape: "pill",
        logo_alignment: "left",
        width: Math.min(360, Math.max(260, section.clientWidth - 8)),
      });

      section.dataset.googleReady = "true";
      status.textContent = "";
    } catch (error) {
      console.error("[Google Auth] Initialization failed:", error);
      status.textContent = error.message || "Google sign-in is temporarily unavailable.";
    } finally {
      googleInitInProgress = false;
    }
  }

  function enhanceAuthOverlay() {
    if (overlayEnhanced) return true;

    const card = document.querySelector(".auth-card");
    if (!card) return false;
    if (card.querySelector("#googleAuthSection")) {
      overlayEnhanced = true;
      return true;
    }

    const section = document.createElement("div");
    section.id = "googleAuthSection";
    section.style.cssText = "margin-top:18px;text-align:center";
    section.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin:4px 0 14px;color:#64748b;font-size:12px">
        <span style="height:1px;background:#273449;flex:1"></span>
        <span>or continue with</span>
        <span style="height:1px;background:#273449;flex:1"></span>
      </div>
      <div id="googleButtonMount" style="display:flex;justify-content:center;min-height:44px"></div>
      <div id="googleAuthStatus" style="min-height:18px;margin-top:8px;color:#94a3b8;font-size:12px"></div>
    `;

    card.appendChild(section);
    overlayEnhanced = true;
    initializeGoogleButton(section);
    return true;
  }

  function start() {
    if (enhanceAuthOverlay()) return;

    const observer = new MutationObserver(() => {
      if (enhanceAuthOverlay()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    // Safety fallback in case the auth overlay is rendered after a delayed boot.
    const interval = setInterval(() => {
      if (enhanceAuthOverlay()) clearInterval(interval);
    }, 500);
    setTimeout(() => clearInterval(interval), 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
