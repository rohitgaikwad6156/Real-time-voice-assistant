(() => {
  const BACKEND = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? location.origin
    : "https://real-time-voice-assistant-9bh1.onrender.com";

  const TOKEN_KEY = "voiceAssistantToken";
  const USER_KEY = "voiceAssistantUser";
  const CONVERSATION_KEY = "voiceAssistantConversationId";
  const SELECT_LATEST_ON_BOOT_KEY = "voiceAssistantSelectLatestOnBoot";
  const AUTH_RETRY_DELAY_MS = 2500;
  const CONVERSATION_CACHE_PREFIX = "voiceAssistantConversations:";
  const MESSAGE_CACHE_PREFIX = "voiceAssistantMessages:";
  const MAX_CACHED_MESSAGES = 120;

  let resolveAuthReady;
  let authResultPublished = false;
  let conversationSwitchSequence = 0;
  let activeConversationId = null;
  let knownConversations = [];
  let clearButtonBound = false;

  window.VOICE_AUTH_STATE = {
    ready: false,
    authenticated: false,
    user: null,
    conversationId: null,
  };

  window.VOICE_AUTH_READY = new Promise((resolve) => {
    resolveAuthReady = resolve;
  });

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function updateStartupStatus(text) {
    const connectionText = document.getElementById("connectionText");
    const heroPrompt = document.getElementById("heroPrompt");
    const recordStatus = document.getElementById("recordStatus");
    if (connectionText) connectionText.textContent = text;
    if (heroPrompt) heroPrompt.textContent = `"${text}"`;
    if (recordStatus) recordStatus.textContent = text;
  }

  function clearStoredSession() {
    clearUserCaches(readCachedUser());
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(CONVERSATION_KEY);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    activeConversationId = null;
    knownConversations = [];
    delete window.APP_CONFIG;
  }

  function configureAuthenticatedWebSocket(token, conversationId) {
    window.APP_CONFIG = {
      API_URL: BACKEND,
      WS_URL: `${BACKEND.replace(/^http/, "ws")}/ws/voice`,
      AUTH_TOKEN: token,
      CONVERSATION_ID: conversationId,
    };
  }

  function publishAuthResult(authenticated, user = null, conversationId = null) {
    if (authResultPublished) return;
    authResultPublished = true;

    window.VOICE_AUTH_STATE = {
      ready: true,
      authenticated,
      user,
      conversationId,
    };

    resolveAuthReady(window.VOICE_AUTH_STATE);
  }

  function authHeaders(tokenOverride = null) {
    const token = tokenOverride || localStorage.getItem(TOKEN_KEY) || "";
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  }

  async function api(path, options = {}) {
    let response;

    try {
      response = await fetch(`${BACKEND}${path}`, {
        ...options,
        cache: options.cache || "no-store",
        headers: {
          ...(options.headers || {}),
          ...(options.auth === false
            ? { "Content-Type": "application/json" }
            : authHeaders(options.tokenOverride || null)),
        },
      });
    } catch (cause) {
      const error = new Error("Unable to reach the server.");
      error.status = 0;
      error.cause = cause;
      throw error;
    }

    let data = {};
    try {
      data = await response.json();
    } catch (_) {}

    if (!response.ok) {
      const error = new Error(data.detail || "Request failed.");
      error.status = response.status;
      throw error;
    }

    return data;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    }[c]));
  }

  function cacheUserId(user = null) {
    const selected = user || readCachedUser();
    return selected?.id ? String(selected.id) : "";
  }

  function conversationCacheKey(user = null) {
    const userId = cacheUserId(user);
    return userId ? `${CONVERSATION_CACHE_PREFIX}${userId}` : "";
  }

  function messageCacheKey(conversationId, user = null) {
    const userId = cacheUserId(user);
    return userId && conversationId
      ? `${MESSAGE_CACHE_PREFIX}${userId}:${conversationId}`
      : "";
  }

  function readJsonCache(key) {
    if (!key) return null;
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function writeJsonCache(key, value) {
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) {
      // Storage can be unavailable/full on some mobile browsers. The app should
      // continue normally and simply fall back to server-loaded history.
    }
  }

  function cacheConversations(user, conversations) {
    if (!Array.isArray(conversations)) return;
    writeJsonCache(conversationCacheKey(user), conversations);
  }

  function getCachedConversations(user = null) {
    const value = readJsonCache(conversationCacheKey(user));
    return Array.isArray(value) ? value : [];
  }

  function cacheMessages(conversationId, messages) {
    if (!Array.isArray(messages)) return;
    writeJsonCache(messageCacheKey(conversationId), messages.slice(-MAX_CACHED_MESSAGES));
  }

  function getCachedMessages(conversationId) {
    const value = readJsonCache(messageCacheKey(conversationId));
    return Array.isArray(value) ? value : null;
  }

  function clearUserCaches(user = null) {
    const userId = cacheUserId(user);
    if (!userId) return;

    localStorage.removeItem(`${CONVERSATION_CACHE_PREFIX}${userId}`);
    const messagePrefix = `${MESSAGE_CACHE_PREFIX}${userId}:`;
    const keys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && key.startsWith(messagePrefix)) keys.push(key);
    }
    keys.forEach((key) => localStorage.removeItem(key));
  }

  function injectStyles() {
    if (document.getElementById("voice-auth-styles")) return;

    const style = document.createElement("style");
    style.id = "voice-auth-styles";
    style.textContent = `
      .auth-overlay{position:fixed;inset:0;z-index:9999;background:#07101f;display:flex;align-items:center;justify-content:center;padding:24px;font-family:'Plus Jakarta Sans',sans-serif}
      .auth-card{width:min(430px,100%);background:#0f172a;border:1px solid #24324a;border-radius:22px;padding:30px;box-shadow:0 30px 80px rgba(0,0,0,.45)}
      .auth-logo{font-size:12px;font-weight:800;letter-spacing:.16em;color:#38bdf8;margin-bottom:8px}.auth-title{color:#f8fafc;font-size:28px;margin:0 0 8px}.auth-sub{color:#94a3b8;font-size:14px;margin-bottom:24px}
      .auth-tabs{display:flex;background:#0b1220;border-radius:12px;padding:4px;margin-bottom:20px}.auth-tab{flex:1;border:0;border-radius:9px;padding:10px;background:transparent;color:#94a3b8;font-weight:700;cursor:pointer}.auth-tab.active{background:#1d4ed8;color:white}
      .auth-field{display:block;margin:12px 0}.auth-field span{display:block;color:#cbd5e1;font-size:13px;margin-bottom:7px}.auth-field input{width:100%;box-sizing:border-box;padding:12px 13px;border-radius:11px;border:1px solid #334155;background:#0b1220;color:white;outline:none}.auth-field input:focus{border-color:#38bdf8}
      .auth-submit{width:100%;margin-top:10px;padding:13px;border:0;border-radius:11px;background:#2563eb;color:white;font-weight:800;cursor:pointer}.auth-submit:disabled{opacity:.65;cursor:wait}.auth-error{min-height:18px;color:#fb7185;font-size:13px;margin-top:10px}
      .history-sidebar{position:fixed;left:0;top:0;bottom:0;width:245px;background:#0a1220;border-right:1px solid #1e293b;z-index:40;padding:18px 14px;box-sizing:border-box;overflow:auto;font-family:'Plus Jakarta Sans',sans-serif}
      .history-brand{color:white;font-weight:800;font-size:14px;margin-bottom:16px}.history-new,.history-reminders{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #334155;background:#111c31;color:#e2e8f0;text-align:left;cursor:pointer;margin-bottom:8px;font-weight:700}
      .history-label{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:18px 6px 8px}
      .history-row{display:flex;align-items:center;gap:4px;border-radius:9px;margin-bottom:2px}
      .history-item{flex:1;min-width:0;padding:10px;border-radius:9px;color:#cbd5e1;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .history-item:hover,.history-item.active{background:#17233a;color:white}.history-item.switching{opacity:.7;cursor:wait}
      .history-delete{flex:0 0 34px;width:34px;height:34px;border:0;border-radius:8px;background:transparent;color:#64748b;cursor:pointer;font-size:15px;display:flex;align-items:center;justify-content:center}
      .history-delete:hover,.history-delete:focus{background:#3b1218;color:#fca5a5;outline:none}.history-delete:disabled{opacity:.45;cursor:wait}
      .history-user{margin-top:18px;padding-top:14px;border-top:1px solid #1e293b;color:#cbd5e1;font-size:12px}.history-user strong{display:block;color:white;margin-bottom:3px}.history-logout{margin-top:9px;border:0;background:transparent;color:#f87171;padding:0;cursor:pointer}
      body.has-history-sidebar .app-layout{
        margin-left:245px;
        margin-right:0;
        width:calc(100vw - 245px);
        max-width:none;
      }
      .reminder-modal{position:fixed;inset:0;z-index:9998;background:rgba(2,6,23,.72);display:flex;align-items:center;justify-content:center;padding:20px}.reminder-card{width:min(520px,100%);max-height:70vh;overflow:auto;background:#0f172a;border:1px solid #334155;border-radius:18px;padding:22px;color:white}.reminder-row{padding:12px 0;border-bottom:1px solid #1e293b}.reminder-row small{display:block;color:#94a3b8;margin-top:4px}.reminder-close{float:right;border:0;background:#1e293b;color:white;border-radius:8px;padding:7px 10px;cursor:pointer}
      .history-mobile-toggle,.history-mobile-backdrop,.history-mobile-close{display:none}
      @media(max-width:900px){
        body.has-history-sidebar .app-layout{margin-left:0;margin-right:0;width:100%;max-width:none}
        .history-sidebar{
          display:block;left:0;top:0;bottom:0;width:min(86vw,320px);z-index:1002;
          transform:translateX(-105%);transition:transform .22s ease;
          box-shadow:18px 0 45px rgba(0,0,0,.45);padding-top:18px
        }
        .history-sidebar.mobile-open{transform:translateX(0)}
        .history-mobile-toggle{
          display:flex;position:fixed;left:12px;top:12px;z-index:1000;
          width:44px;height:44px;align-items:center;justify-content:center;
          border:1px solid #334155;border-radius:12px;background:#0f172a;
          color:#f8fafc;font-size:22px;line-height:1;box-shadow:0 8px 24px rgba(0,0,0,.28);
          cursor:pointer
        }
        .history-mobile-backdrop{
          position:fixed;inset:0;z-index:1001;background:rgba(2,6,23,.68);
          backdrop-filter:blur(2px)
        }
        .history-mobile-backdrop.visible{display:block}
        .history-mobile-close{
          display:flex;position:absolute;right:12px;top:12px;width:36px;height:36px;
          align-items:center;justify-content:center;border:1px solid #334155;border-radius:10px;
          background:#111c31;color:#f8fafc;font-size:20px;cursor:pointer
        }
        .history-brand{padding-right:46px}
        .history-user{position:sticky;bottom:0;background:#0a1220;padding-bottom:12px}
        .history-logout{
          width:100%;margin-top:10px;padding:10px 12px;border:1px solid #7f1d1d;
          border-radius:10px;background:#2a1015;color:#fca5a5;font-weight:700;text-align:center
        }
      }
    `;
    document.head.appendChild(style);
  }

  async function finishAuthentication(result) {
    if (!result?.access_token || !result?.user) {
      throw new Error("Authentication response is incomplete.");
    }

    localStorage.setItem(TOKEN_KEY, result.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(result.user));

    const overlay = document.querySelector(".auth-overlay");
    if (overlay) overlay.remove();
    updateStartupStatus("Opening your account...");

    // If the main app was already authenticated and later expired, a reload is
    // the safest way to rebuild its WebSocket state.
    if (authResultPublished) {
      location.reload();
      return;
    }

    const active = result.conversation_id || null;
    const conversations = Array.isArray(result.conversations) ? result.conversations : [];

    // New auth endpoints return the selected conversation in the same response.
    // Enter the app immediately instead of making another blocking API request.
    if (active) {
      localStorage.setItem(CONVERSATION_KEY, active);
      localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
      activeConversationId = active;
      knownConversations = conversations;
      cacheConversations(result.user, conversations);
      configureAuthenticatedWebSocket(result.access_token, active);
      renderAuthenticatedSidebar(result.user, conversations, active);
      bindPersistentClearButton();
      updateStartupStatus("Connecting assistant...");
      publishAuthResult(true, result.user, active);
      renderHistoryMessages(active).catch((err) => {
        console.warn("Conversation history load delayed:", err);
      });
      return;
    }

    // Compatibility fallback for an older backend during a rolling deployment.
    localStorage.removeItem(CONVERSATION_KEY);
    localStorage.setItem(SELECT_LATEST_ON_BOOT_KEY, "1");
    await resolveAuthenticatedSession(result.user);
  }

  window.VOICE_FINISH_AUTHENTICATION = finishAuthentication;

  function showAuth(message = "") {
    let overlay = document.querySelector(".auth-overlay");
    if (overlay) {
      const error = overlay.querySelector("#authError");
      if (error && message) error.textContent = message;
      return;
    }

    overlay = document.createElement("div");
    overlay.className = "auth-overlay";
    overlay.innerHTML = `
      <div class="auth-card">
        <div class="auth-logo">REAL-TIME VOICE ASSISTANT</div>
        <h2 class="auth-title">Welcome</h2>
        <div class="auth-sub">Login to keep your conversations and reminders private to your account.</div>
        <div class="auth-tabs"><button class="auth-tab active" data-mode="login">Login</button><button class="auth-tab" data-mode="register">Register</button></div>
        <form id="voiceAuthForm">
          <label class="auth-field" id="nameField" style="display:none"><span>Name</span><input id="authName" autocomplete="name"></label>
          <label class="auth-field"><span>Email</span><input id="authEmail" type="email" autocomplete="email" required></label>
          <label class="auth-field"><span>Password</span><input id="authPassword" type="password" autocomplete="current-password" required minlength="6"></label>
          <button class="auth-submit" type="submit">Login</button>
          <div class="auth-error" id="authError">${escapeHtml(message)}</div>
        </form>
        <div id="googleAuthSection" style="margin-top:18px;text-align:center">
          <div style="display:flex;align-items:center;gap:10px;margin:4px 0 14px;color:#64748b;font-size:12px">
            <span style="height:1px;background:#273449;flex:1"></span>
            <span>or continue with</span>
            <span style="height:1px;background:#273449;flex:1"></span>
          </div>
          <div id="googleButtonMount" style="display:flex;justify-content:center;min-height:44px">
            <button id="googleSetupButton" type="button" disabled
              style="width:min(360px,100%);min-height:44px;border-radius:999px;border:1px solid #cbd5e1;background:white;color:#1f2937;font-weight:700;cursor:not-allowed;opacity:.8">
              Continue with Google
            </button>
          </div>
          <div id="googleAuthStatus" style="min-height:18px;margin-top:8px;color:#94a3b8;font-size:12px">Loading Google sign-in...</div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    let mode = "login";

    overlay.querySelectorAll(".auth-tab").forEach((btn) => {
      btn.onclick = () => {
        mode = btn.dataset.mode;
        overlay.querySelectorAll(".auth-tab").forEach((b) => b.classList.toggle("active", b === btn));
        overlay.querySelector("#nameField").style.display = mode === "register" ? "block" : "none";
        overlay.querySelector(".auth-submit").textContent = mode === "register" ? "Create account" : "Login";
        overlay.querySelector("#authPassword").autocomplete = mode === "register" ? "new-password" : "current-password";
        overlay.querySelector("#authError").textContent = "";
      };
    });

    overlay.querySelector("#voiceAuthForm").onsubmit = async (event) => {
      event.preventDefault();
      const error = overlay.querySelector("#authError");
      const submit = overlay.querySelector(".auth-submit");
      error.textContent = "";
      submit.disabled = true;
      submit.textContent = mode === "register" ? "Creating..." : "Logging in...";

      try {
        const body = {
          email: overlay.querySelector("#authEmail").value.trim(),
          password: overlay.querySelector("#authPassword").value,
        };
        if (mode === "register") body.name = overlay.querySelector("#authName").value.trim();

        const result = await api(`/api/auth/${mode}`, {
          method: "POST",
          body: JSON.stringify(body),
          auth: false,
        });

        await finishAuthentication(result);
      } catch (err) {
        error.textContent = err.message;
        submit.disabled = false;
        submit.textContent = mode === "register" ? "Create account" : "Login";
      }
    };
  }

  window.VOICE_HANDLE_AUTH_FAILURE = (message = "Your session expired. Please log in again.") => {
    clearStoredSession();
    showAuth(message);
    updateStartupStatus("Sign in to start assistant");
  };

  async function showReminders() {
    try {
      const data = await api("/api/reminders");
      const modal = document.createElement("div");
      modal.className = "reminder-modal";
      const rows = data.reminders?.length
        ? data.reminders.map((r) => `<div class="reminder-row"><strong>${escapeHtml(r.title)}</strong><small>${escapeHtml(r.remind_at)} · ${escapeHtml(r.status || "pending")}</small></div>`).join("")
        : `<p style="color:#94a3b8">No reminders yet. Try saying “Remind me to study DSA tomorrow at 7 PM.”</p>`;
      modal.innerHTML = `<div class="reminder-card"><button class="reminder-close">Close</button><h3>Your reminders</h3>${rows}</div>`;
      modal.querySelector(".reminder-close").onclick = () => modal.remove();
      modal.onclick = (event) => { if (event.target === modal) modal.remove(); };
      document.body.appendChild(modal);
    } catch (err) {
      alert(err.message);
    }
  }

  function renderEmptyConversation(list) {
    list.innerHTML = `
      <div class="empty-hint" id="emptyHint">
        <div class="empty-icon">🎙️</div>
        <p class="empty-title">Start this conversation</p>
        <p class="empty-desc">Ask by voice or text. Messages stay inside this conversation.</p>
      </div>`;
  }

  function paintHistoryMessages(messages, expectedSwitchSequence = null) {
    if (expectedSwitchSequence !== null && expectedSwitchSequence !== conversationSwitchSequence) {
      return { stale: true, turns: 0 };
    }

    const list = document.getElementById("conversationList");
    if (!list) return { stale: false, turns: 0 };

    const safeMessages = Array.isArray(messages) ? messages : [];
    list.innerHTML = "";
    let turns = 0;

    if (safeMessages.length === 0) {
      renderEmptyConversation(list);
    } else {
      for (const msg of safeMessages) {
        const div = document.createElement("div");
        div.className = `turn ${msg.role === "user" ? "user-turn" : "assistant-turn"}`;
        div.innerHTML = `<div class="turn-header-row"><span class="turn-role-tag">${msg.role === "user" ? "USER" : "ASSISTANT"}</span></div><div class="turn-bubble"></div>`;
        div.querySelector(".turn-bubble").textContent = msg.text;
        list.appendChild(div);
        if (msg.role === "user") turns += 1;
      }
      list.scrollTop = list.scrollHeight;
    }

    const counter = document.getElementById("turnCounter");
    if (counter) counter.textContent = `${turns} ${turns === 1 ? "turn" : "turns"}`;
    if (typeof window.VOICE_APP_SYNC_TURN_COUNT === "function") {
      window.VOICE_APP_SYNC_TURN_COUNT(turns);
    }

    return { stale: false, turns };
  }

  function renderCachedHistoryMessages(conversationId, expectedSwitchSequence = null) {
    const cachedMessages = getCachedMessages(conversationId);
    if (!cachedMessages) return null;
    return paintHistoryMessages(cachedMessages, expectedSwitchSequence);
  }

  function renderHistoryLoading() {
    const list = document.getElementById("conversationList");
    if (!list) return;
    list.innerHTML = `
      <div class="empty-hint">
        <div class="empty-icon">💬</div>
        <p class="empty-title">Loading conversation...</p>
        <p class="empty-desc">You can continue using the assistant while history refreshes.</p>
      </div>`;
  }

  async function renderHistoryMessages(conversationId, expectedSwitchSequence = null) {
    const data = await api(`/api/conversations/${conversationId}/messages`);

    if (expectedSwitchSequence !== null && expectedSwitchSequence !== conversationSwitchSequence) {
      return { stale: true, turns: 0 };
    }

    const messages = Array.isArray(data.messages) ? data.messages : [];
    cacheMessages(conversationId, messages);
    return paintHistoryMessages(messages, expectedSwitchSequence);
  }

  function bindPersistentClearButton() {
    if (clearButtonBound) return;

    const button = document.getElementById("clearConversationBtn");
    if (!button) return;

    clearButtonBound = true;
    button.addEventListener("click", async (event) => {
      // Capture this click before app.js's old UI-only handler can run.
      event.preventDefault();
      event.stopImmediatePropagation();

      const conversationId = activeConversationId || localStorage.getItem(CONVERSATION_KEY);
      if (!conversationId) return;

      const confirmed = window.confirm(
        "Delete all messages in this conversation? This cannot be undone."
      );
      if (!confirmed) return;

      const clearSequence = ++conversationSwitchSequence;
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = "Clearing...";

      if (typeof window.VOICE_APP_BEGIN_CONVERSATION_SWITCH === "function") {
        window.VOICE_APP_BEGIN_CONVERSATION_SWITCH();
      }
      updateStartupStatus("Clearing conversation...");

      try {
        await api(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
          method: "DELETE",
        });

        // A newer sidebar switch owns the UI now; do not overwrite it.
        if (clearSequence !== conversationSwitchSequence || activeConversationId !== conversationId) {
          return;
        }

        const list = document.getElementById("conversationList");
        if (list) renderEmptyConversation(list);

        const counter = document.getElementById("turnCounter");
        if (counter) counter.textContent = "0 turns";
        if (typeof window.VOICE_APP_SYNC_TURN_COUNT === "function") {
          window.VOICE_APP_SYNC_TURN_COUNT(0);
        }

        if (typeof window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH === "function") {
          window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH({ turnCount: 0 });
        }

        updateStartupStatus("Conversation cleared");
      } catch (err) {
        if (clearSequence === conversationSwitchSequence) {
          if (typeof window.VOICE_APP_CANCEL_CONVERSATION_SWITCH === "function") {
            window.VOICE_APP_CANCEL_CONVERSATION_SWITCH();
          }
          updateStartupStatus("Could not clear conversation");
          window.alert(err.message || "Could not clear this conversation.");
        }
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    }, true);
  }

  function highlightActiveConversation(conversationId, switching = false) {
    document.querySelectorAll(".history-item[data-conversation-id]").forEach((item) => {
      const isActive = item.dataset.conversationId === conversationId;
      item.classList.toggle("active", isActive);
      item.classList.toggle("switching", switching && isActive);
    });
  }

  async function switchConversation(conversationId) {
    if (!conversationId || conversationId === activeConversationId) {
      highlightActiveConversation(activeConversationId, false);
      return;
    }

    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) return;

    const switchSequence = ++conversationSwitchSequence;

    localStorage.setItem(CONVERSATION_KEY, conversationId);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    activeConversationId = conversationId;
    if (window.VOICE_AUTH_STATE?.ready) {
      window.VOICE_AUTH_STATE.conversationId = conversationId;
    }
    highlightActiveConversation(conversationId, true);

    if (typeof window.VOICE_APP_BEGIN_CONVERSATION_SWITCH === "function") {
      window.VOICE_APP_BEGIN_CONVERSATION_SWITCH();
    }

    updateStartupStatus("Opening conversation...");

    const cached = renderCachedHistoryMessages(conversationId, switchSequence);
    if (!cached) renderHistoryLoading();

    // Do not make the voice connection wait for MongoDB history. The selected
    // conversation can connect immediately while history refreshes in parallel.
    configureAuthenticatedWebSocket(token, conversationId);
    highlightActiveConversation(conversationId, false);

    if (typeof window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH === "function") {
      window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH({ turnCount: cached?.turns || 0 });
    }

    updateStartupStatus("Connecting conversation...");

    renderHistoryMessages(conversationId, switchSequence).catch((err) => {
      if (switchSequence !== conversationSwitchSequence) return;
      console.warn("Conversation history refresh delayed:", err);
      if (!cached) {
        const list = document.getElementById("conversationList");
        if (list) {
          list.innerHTML = `
            <div class="empty-hint">
              <div class="empty-icon">☁️</div>
              <p class="empty-title">History is still loading</p>
              <p class="empty-desc">The assistant can reconnect while the server catches up.</p>
            </div>`;
        }
      }
    });
  }

  async function deleteConversation(conversationId, user) {
    if (!conversationId) return;

    const conversation = knownConversations.find((item) => item.id === conversationId);
    const title = conversation?.title || "this conversation";
    const confirmed = window.confirm(
      `Delete "${title}"? All messages in this conversation will be permanently deleted.`
    );
    if (!confirmed) return;

    const deleteButton = document.querySelector(
      `.history-delete[data-conversation-id="${conversationId}"]`
    );
    if (deleteButton) deleteButton.disabled = true;

    const deletingActive = conversationId === activeConversationId;
    const keepDrawerOpen = document.querySelector(".history-sidebar")?.classList.contains("mobile-open");

    try {
      const result = await api(`/api/conversations/${encodeURIComponent(conversationId)}`, {
        method: "DELETE",
      });

      const conversations = Array.isArray(result.conversations) ? result.conversations : [];
      const nextConversationId = result.conversation_id || conversations[0]?.id;

      if (!nextConversationId) {
        throw new Error("Could not select a conversation after deletion.");
      }

      knownConversations = conversations;

      if (deletingActive) {
        localStorage.setItem(CONVERSATION_KEY, nextConversationId);
        localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
        closeMobileHistoryDrawer();
        location.reload();
        return;
      }

      renderAuthenticatedSidebar(user, conversations, activeConversationId);
      if (keepDrawerOpen) openMobileHistoryDrawer();
    } catch (err) {
      console.error("Could not delete conversation", err);
      window.alert(err.message || "Could not delete this conversation.");
      if (deleteButton) deleteButton.disabled = false;
    }
  }

  async function refreshConversationSidebar() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token || !activeConversationId) return;

    const rawUser = localStorage.getItem(USER_KEY);
    if (!rawUser) return;

    let user;
    try {
      user = JSON.parse(rawUser);
    } catch (_) {
      return;
    }

    const drawerWasOpen = document.querySelector(".history-sidebar")?.classList.contains("mobile-open");

    try {
      const data = await api("/api/conversations");
      const conversations = Array.isArray(data.conversations) ? data.conversations : [];
      knownConversations = conversations;
      cacheConversations(user, conversations);
      renderAuthenticatedSidebar(user, conversations, activeConversationId);
      if (drawerWasOpen) openMobileHistoryDrawer();
    } catch (error) {
      console.warn("Could not refresh AI conversation title:", error);
    }
  }

  window.VOICE_REFRESH_CONVERSATIONS = refreshConversationSidebar;

  function closeMobileHistoryDrawer() {
    const sidebar = document.querySelector(".history-sidebar");
    const backdrop = document.querySelector(".history-mobile-backdrop");
    const toggle = document.querySelector(".history-mobile-toggle");
    if (sidebar) sidebar.classList.remove("mobile-open");
    if (backdrop) backdrop.classList.remove("visible");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("history-drawer-open");
  }

  function openMobileHistoryDrawer() {
    const sidebar = document.querySelector(".history-sidebar");
    const backdrop = document.querySelector(".history-mobile-backdrop");
    const toggle = document.querySelector(".history-mobile-toggle");
    if (sidebar) sidebar.classList.add("mobile-open");
    if (backdrop) backdrop.classList.add("visible");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("history-drawer-open");
  }

  function renderAuthenticatedSidebar(user, conversations, active) {
    document.body.classList.add("has-history-sidebar");
    knownConversations = conversations;
    activeConversationId = active;
  
    const existing = document.querySelector(".history-sidebar");
    if (existing) existing.remove();
    document.querySelector(".history-mobile-toggle")?.remove();
    document.querySelector(".history-mobile-backdrop")?.remove();
  
    const toggle = document.createElement("button");
    toggle.className = "history-mobile-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "Open conversation history");
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "☰";
    toggle.onclick = openMobileHistoryDrawer;
    document.body.appendChild(toggle);
  
    const backdrop = document.createElement("div");
    backdrop.className = "history-mobile-backdrop";
    backdrop.setAttribute("aria-hidden", "true");
    backdrop.onclick = closeMobileHistoryDrawer;
    document.body.appendChild(backdrop);
  
    const sidebar = document.createElement("aside");
    sidebar.className = "history-sidebar";
    sidebar.setAttribute("aria-label", "Conversation history and account");
    sidebar.innerHTML = `
      <button class="history-mobile-close" type="button" aria-label="Close conversation history">×</button>
      <div class="history-brand">🎙️ Voice Assistant</div>
      <button class="history-new">＋ New chat</button>
      <button class="history-reminders">⏰ Reminders</button>
      <div class="history-label">Conversations</div>
      <div class="history-list"></div>
      <div class="history-user"><strong>${escapeHtml(user.name)}</strong>${escapeHtml(user.email)}<br><button class="history-logout">Log out</button></div>`;
    document.body.appendChild(sidebar);
  
    sidebar.querySelector(".history-mobile-close").onclick = closeMobileHistoryDrawer;
  
    const list = sidebar.querySelector(".history-list");
    if (!conversations.length) {
      const loading = document.createElement("div");
      loading.className = "history-item";
      loading.textContent = "Loading conversations...";
      loading.style.opacity = ".65";
      list.appendChild(loading);
    }
  
    conversations.forEach((conversation) => {
      const row = document.createElement("div");
      row.className = "history-row";

      const item = document.createElement("div");
      item.className = `history-item ${conversation.id === active ? "active" : ""}`;
      item.dataset.conversationId = conversation.id;
      item.textContent = conversation.title || "New conversation";
      item.title = conversation.title || "New conversation";
      item.onclick = () => {
        closeMobileHistoryDrawer();
        switchConversation(conversation.id);
      };

      const deleteButton = document.createElement("button");
      deleteButton.className = "history-delete";
      deleteButton.type = "button";
      deleteButton.dataset.conversationId = conversation.id;
      deleteButton.setAttribute("aria-label", `Delete ${conversation.title || "conversation"}`);
      deleteButton.title = "Delete conversation";
      deleteButton.textContent = "🗑";
      deleteButton.onclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        deleteConversation(conversation.id, user);
      };

      row.appendChild(item);
      row.appendChild(deleteButton);
      list.appendChild(row);
    });
  
    sidebar.querySelector(".history-new").onclick = async () => {
      closeMobileHistoryDrawer();
      const created = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ title: "New conversation" }),
      });
      localStorage.setItem(CONVERSATION_KEY, created.conversation.id);
      localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
      location.reload();
    };
  
    sidebar.querySelector(".history-reminders").onclick = () => {
      closeMobileHistoryDrawer();
      showReminders();
    };
  
    sidebar.querySelector(".history-logout").onclick = () => {
      closeMobileHistoryDrawer();
      clearStoredSession();
      location.reload();
    };
  }
  async function selectConversationForSession(conversations) {
    if (conversations.length > 0) {
      const forceLatest = localStorage.getItem(SELECT_LATEST_ON_BOOT_KEY) === "1";
      const storedId = localStorage.getItem(CONVERSATION_KEY);
      const storedExists = storedId && conversations.some((conversation) => conversation.id === storedId);

      const selected = forceLatest || !storedExists
        ? conversations[0]
        : conversations.find((conversation) => conversation.id === storedId);

      localStorage.setItem(CONVERSATION_KEY, selected.id);
      localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
      return { conversation: selected, conversations };
    }

    const created = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "New conversation" }),
    });

    localStorage.setItem(CONVERSATION_KEY, created.conversation.id);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    return { conversation: created.conversation, conversations: [created.conversation] };
  }

  function readCachedUser() {
    try {
      const raw = localStorage.getItem(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  async function refreshOptimisticSession(token, cachedUser, cachedConversationId) {
    try {
      // One authenticated request is enough to validate the saved token and
      // refresh the sidebar. Avoid an extra /auth/me round-trip on every load.
      const data = await api("/api/conversations");

      const conversations = Array.isArray(data.conversations) ? data.conversations : [];
      const activeStillExists = conversations.some((item) => item.id === cachedConversationId);

      if (!activeStillExists) {
        const selection = await selectConversationForSession(conversations);
        cacheConversations(cachedUser, selection.conversations);
        localStorage.setItem(CONVERSATION_KEY, selection.conversation.id);
        location.reload();
        return;
      }

      knownConversations = conversations;
      cacheConversations(cachedUser, conversations);
      renderAuthenticatedSidebar(cachedUser, conversations, cachedConversationId);
      renderHistoryMessages(cachedConversationId).catch((err) => {
        console.warn("Conversation history refresh delayed:", err);
      });
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        console.warn("Cached session is no longer valid.");
        clearStoredSession();
        showAuth("Your session expired. Please log in again.");
        updateStartupStatus("Sign in to start assistant");
        return;
      }
      console.warn("Background session refresh delayed:", err);
    }
  }

  async function resolveAuthenticatedSession(knownUser = null) {
    const token = localStorage.getItem(TOKEN_KEY);

    if (!token) {
      clearStoredSession();
      showAuth();
      updateStartupStatus("Sign in to start assistant");
      // Keep VOICE_AUTH_READY pending. It resolves after a successful login.
      return;
    }

    const cachedUser = knownUser || readCachedUser();
    const cachedConversationId = localStorage.getItem(CONVERSATION_KEY);
    const forceLatest = localStorage.getItem(SELECT_LATEST_ON_BOOT_KEY) === "1";

    // Returning users should see the app immediately on mobile. Verify the
    // token/conversation with the server in the background while the WebSocket
    // is already waking/connecting.
    if (cachedUser && cachedConversationId && !forceLatest && !authResultPublished) {
      activeConversationId = cachedConversationId;
      knownConversations = getCachedConversations(cachedUser);
      configureAuthenticatedWebSocket(token, cachedConversationId);
      renderAuthenticatedSidebar(cachedUser, knownConversations, cachedConversationId);
      bindPersistentClearButton();
      renderCachedHistoryMessages(cachedConversationId);
      updateStartupStatus("Connecting assistant...");
      publishAuthResult(true, cachedUser, cachedConversationId);
      refreshOptimisticSession(token, cachedUser, cachedConversationId);
      return;
    }

    updateStartupStatus("Loading your account...");

    while (true) {
      try {
        const [me, data] = await Promise.all([
          knownUser ? Promise.resolve({ user: knownUser }) : api("/api/auth/me"),
          api("/api/conversations"),
        ]);
        knownUser = null;
        localStorage.setItem(USER_KEY, JSON.stringify(me.user));

        const serverConversations = Array.isArray(data.conversations) ? data.conversations : [];
        const selection = await selectConversationForSession(serverConversations);
        const active = selection.conversation.id;
        const conversations = selection.conversations;

        activeConversationId = active;
        knownConversations = conversations;
        cacheConversations(me.user, conversations);
        configureAuthenticatedWebSocket(token, active);
        renderAuthenticatedSidebar(me.user, conversations, active);
        bindPersistentClearButton();

        updateStartupStatus("Starting assistant...");
        publishAuthResult(true, me.user, active);

        renderHistoryMessages(active).catch((err) => {
          console.warn("Conversation history load delayed:", err);
        });
        return;
      } catch (err) {
        if (err.status === 401 || err.status === 403) {
          console.warn("Stored session is no longer valid.");
          clearStoredSession();
          showAuth("Your session expired. Please log in again.");
          updateStartupStatus("Sign in to start assistant");
          return;
        }

        console.warn("Authentication initialization delayed:", err);
        updateStartupStatus(navigator.onLine
          ? "Restoring your session — still retrying..."
          : "Waiting for network connection...");
        await sleep(AUTH_RETRY_DELAY_MS);
      }
    }
  }

  function bootAuth() {
    injectStyles();
    resolveAuthenticatedSession();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootAuth, { once: true });
  } else {
    bootAuth();
  }
})();
