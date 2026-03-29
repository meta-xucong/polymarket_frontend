const appState = {
  authRequired: false,
  authenticated: false,
  mustChangeCredentials: false,
  auth: {},
  instance: {},
};

async function readJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
  });
  const payload = await response.json().catch(() => ({}));

  if (response.status === 401) {
    if (appState.authRequired) {
      setAuthenticated(false);
      hideCredentialsGate();
      showAuthGate();
    }
    throw new Error(payload.error || "Login required");
  }

  if (response.status === 403) {
    if (payload.code === "AUTH_SETUP_REQUIRED") {
      appState.mustChangeCredentials = true;
      appState.auth = payload.auth || appState.auth;
      showCredentialsGate();
    }
    throw new Error(payload.error || "Credentials update required");
  }

  if (!response.ok) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }

  return payload;
}

function showToast(message, isError = false) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  node.style.background = isError ? "rgba(132, 37, 24, 0.96)" : "rgba(18, 32, 24, 0.95)";
  window.clearTimeout(showToast._timer);
  showToast._timer = window.setTimeout(() => node.classList.add("hidden"), 2800);
}

function fillForm(form, values) {
  Object.entries(values).forEach(([key, value]) => {
    const field = form.elements.namedItem(key);
    if (!field) return;
    field.value = value == null ? "" : String(value);
  });
}

function ratioToPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "";
  return String(num * 100);
}

function percentToRatio(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return num / 100;
}

function maskAccountFields(account) {
  return {
    ...account,
    POLY_KEY: account.POLY_KEY || "",
    POLY_API_SECRET: account.POLY_API_SECRET || "",
    POLY_API_PASSPHRASE: account.POLY_API_PASSPHRASE || "",
  };
}

function normalizeAddress(value) {
  const text = String(value || "").trim();
  return /^0x[a-fA-F0-9]{40}$/.test(text) ? text : "";
}

function updateLinkState(link, address, emptyText) {
  if (!address) {
    link.href = "#";
    link.textContent = emptyText;
    link.classList.add("disabled");
    link.setAttribute("aria-disabled", "true");
    return;
  }

  const url = `https://polymarket.com/profile/${address}`;
  link.href = url;
  link.textContent = url;
  link.classList.remove("disabled");
  link.setAttribute("aria-disabled", "false");
}

function updateProfileLink() {
  const form = document.getElementById("account-form");
  const funder = normalizeAddress(form.elements.namedItem("POLY_FUNDER")?.value);
  const dataAddress = normalizeAddress(form.elements.namedItem("POLY_DATA_ADDRESS")?.value);
  updateLinkState(document.getElementById("profile-link"), dataAddress || funder, "No valid address");
}

function updateV3ProfileLink() {
  const form = document.getElementById("v3-account-form");
  const address = normalizeAddress(form.elements.namedItem("my_address")?.value);
  updateLinkState(document.getElementById("v3-profile-link"), address, "No valid address");
}

function buildStats(items) {
  return items
    .map(
      ([label, value]) => `<div class="stat"><strong>${label}</strong><span>${value}</span></div>`,
    )
    .join("");
}

function buildV2RuntimeSummary(payload) {
  const services = payload.services?.services || {};
  return buildStats([
    ["Active Tokens", payload.active_token_count ?? 0],
    ["Copytrade State", services.copytrade?.raw || "unknown"],
    ["Copytrade PID", services.copytrade?.pid ?? "-"],
    ["Autorun State", services.autorun?.raw || "unknown"],
    ["Autorun PID", services.autorun?.pid ?? "-"],
    ["Updated At", payload.copytrade_updated_at || "-"],
  ]);
}

function buildV3RuntimeSummary(payload) {
  const service = payload.services?.services?.v3multi || {};
  return buildStats([
    ["Runtime State", service.raw || "unknown"],
    ["PID", service.pid ?? "-"],
    ["Active Accounts", payload.active_account_count ?? 0],
    ["Target Addresses", payload.target_address_count ?? 0],
    ["Log File", payload.log_file || "-"],
  ]);
}

function setSelectOptions(select, accounts) {
  const currentValue = select.value;
  select.innerHTML = "";
  accounts.forEach((account) => {
    const option = document.createElement("option");
    option.value = String(account.index);
    option.textContent = `${account.name}${account.enabled ? "" : " (disabled)"}`;
    select.appendChild(option);
  });
  if (accounts.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No accounts";
    select.appendChild(option);
  }
  if (accounts.some((item) => String(item.index) === currentValue)) {
    select.value = currentValue;
  } else if (accounts.length > 0) {
    select.value = String(accounts[0].index);
  }
}

function switchPanel(panelId) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.panel === panelId);
  });
  document.querySelectorAll(".panel-view").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === panelId);
  });
}

function optionalNumberOrEmpty(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const num = Number(text);
  return Number.isFinite(num) ? num : "";
}

function setInstanceBadge(instance) {
  const badge = document.getElementById("instance-badge");
  const source = String(instance?.source_root || "-");
  const root = String(instance?.instance_root || "-");
  badge.textContent = `Instance: ${root}`;
  badge.title = `Source root: ${source}\nInstance root: ${root}`;
}

function setAuthenticated(flag) {
  appState.authenticated = !!flag;
  document.getElementById("logout-btn").classList.toggle("hidden", !appState.authenticated);
}

function showAuthGate() {
  document.getElementById("auth-gate").classList.remove("hidden");
}

function hideAuthGate() {
  document.getElementById("auth-gate").classList.add("hidden");
  clearAuthError();
}

function showAuthError(message) {
  const node = document.getElementById("auth-error");
  node.textContent = message;
  node.classList.remove("hidden");
}

function clearAuthError() {
  const node = document.getElementById("auth-error");
  node.textContent = "";
  node.classList.add("hidden");
}

function showCredentialsGate() {
  document.getElementById("credentials-username").value = String(appState.auth?.username || "admin");
  document.getElementById("credentials-password").value = "";
  document.getElementById("credentials-password-confirm").value = "";
  clearCredentialsError();
  document.getElementById("credentials-gate").classList.remove("hidden");
}

function hideCredentialsGate() {
  document.getElementById("credentials-gate").classList.add("hidden");
  clearCredentialsError();
}

function showCredentialsError(message) {
  const node = document.getElementById("credentials-error");
  node.textContent = message;
  node.classList.remove("hidden");
}

function clearCredentialsError() {
  const node = document.getElementById("credentials-error");
  node.textContent = "";
  node.classList.add("hidden");
}

async function refreshAuthSession() {
  const payload = await readJson("/api/auth/session");
  appState.authRequired = !!payload.required;
  appState.authenticated = !!payload.authenticated;
  appState.mustChangeCredentials = !!payload.must_change_credentials;
  appState.auth = payload.auth || {};
  appState.instance = payload.instance || {};
  setInstanceBadge(appState.instance);
  setAuthenticated(appState.authenticated);

  if (appState.authRequired && !appState.authenticated) {
    hideCredentialsGate();
    showAuthGate();
    return false;
  }

  hideAuthGate();
  if (appState.mustChangeCredentials) {
    showCredentialsGate();
    return false;
  }

  hideCredentialsGate();
  return true;
}

async function login(event) {
  event.preventDefault();
  clearAuthError();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const result = await readJson("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    appState.authRequired = !!result.required;
    appState.authenticated = !!result.authenticated;
    appState.mustChangeCredentials = !!result.must_change_credentials;
    appState.auth = result.auth || {};
    appState.instance = result.instance || {};
    setAuthenticated(appState.authenticated);
    setInstanceBadge(appState.instance);
    hideAuthGate();

    if (appState.mustChangeCredentials) {
      showCredentialsGate();
      showToast("Please replace the default admin/admin login.");
      return;
    }

    hideCredentialsGate();
    await refreshAll();
    showToast("Signed in successfully.");
  } catch (error) {
    showAuthError(error.message || "Unable to sign in.");
  }
}

async function updateCredentials(event) {
  event.preventDefault();
  clearCredentialsError();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const result = await readJson("/api/auth/credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    appState.authRequired = !!result.required;
    appState.authenticated = !!result.authenticated;
    appState.mustChangeCredentials = !!result.must_change_credentials;
    appState.auth = result.auth || {};
    appState.instance = result.instance || appState.instance;
    setAuthenticated(true);
    setInstanceBadge(appState.instance);
    hideCredentialsGate();
    await refreshAll();
    showToast("Credentials updated.");
  } catch (error) {
    showCredentialsError(error.message || "Unable to update credentials.");
  }
}

async function logout() {
  await readJson("/api/auth/logout", { method: "POST" });
  appState.authenticated = false;
  appState.mustChangeCredentials = false;
  setAuthenticated(false);
  hideCredentialsGate();
  if (appState.authRequired) {
    showAuthGate();
  }
  showToast("Signed out.");
}

async function refreshAccount() {
  const payload = await readJson("/api/account");
  fillForm(document.getElementById("account-form"), maskAccountFields(payload.account));
  updateProfileLink();
}

async function refreshSettings() {
  const payload = await readJson("/api/settings");
  const settings = payload.settings;
  fillForm(document.getElementById("settings-form"), {
    target_addresses: (settings.copytrade.target_addresses || []).join("\n"),
    poll_interval_sec: settings.copytrade.poll_interval_sec,
    min_size: settings.copytrade.min_size,
    max_concurrent_tasks: settings.scheduler.max_concurrent_tasks,
    copytrade_poll_seconds: settings.scheduler.copytrade_poll_seconds,
    command_poll_seconds: settings.scheduler.command_poll_seconds,
    strategy_mode: settings.scheduler.strategy_mode,
    burst_slots: settings.scheduler.burst_slots,
    order_size: settings.strategy.order_size,
    max_position_per_market: settings.strategy.max_position_per_market,
    drop_pct: ratioToPercent(settings.strategy.drop_pct),
    profit_pct: ratioToPercent(settings.strategy.profit_pct),
    sell_mode: settings.strategy.sell_mode,
    shock_guard_enabled: String(settings.strategy.shock_guard_enabled),
    shock_window_sec: settings.strategy.shock_window_sec,
    shock_drop_pct: ratioToPercent(settings.strategy.shock_drop_pct),
  });
}

async function refreshRuntime(preferredLog = null) {
  const payload = await readJson("/api/runtime");
  document.getElementById("runtime-summary").innerHTML = buildV2RuntimeSummary(payload);
  document.getElementById("status-pill").textContent =
    payload.services?.mode === "local-process" ? "local-process" : "systemd";
  if (payload.instance) {
    appState.instance = payload.instance;
    setInstanceBadge(payload.instance);
  }
  const viewer = document.getElementById("log-viewer");
  viewer.textContent =
    preferredLog === "copytrade"
      ? payload.copytrade_log_tail || "No logs"
      : preferredLog === "autorun"
        ? payload.autorun_log_tail || "No logs"
        : payload.autorun_log_tail || payload.copytrade_log_tail || "No logs";
}

async function refreshV3Settings() {
  const payload = await readJson("/api/v3/settings");
  const settings = payload.settings;
  const startupMode =
    settings.global.boot_sync_mode === "baseline_only" ? "baseline_only" : "baseline_replay";
  const replayHours = Math.max(
    1,
    Math.round(Number(settings.global.actions_replay_window_sec || 86400) / 3600),
  );

  fillForm(document.getElementById("v3-settings-form"), {
    target_addresses: (settings.global.target_addresses || []).join("\n"),
    poll_interval_sec: settings.global.poll_interval_sec,
    poll_interval_sec_exiting: settings.global.poll_interval_sec_exiting,
    startup_mode: startupMode,
    replay_window_hours: replayHours,
    follow_new_topics_only: String(settings.global.follow_new_topics_only),
    min_order_usd: settings.global.min_order_usd,
    max_order_usd: settings.global.max_order_usd,
    max_notional_per_token: settings.global.max_notional_per_token,
    max_notional_total: settings.global.max_notional_total,
    taker_enabled: String(settings.global.taker_enabled),
    taker_spread_threshold: settings.global.taker_spread_threshold,
    taker_order_type: settings.global.taker_order_type,
    maker_max_wait_sec: settings.global.maker_max_wait_sec,
    maker_to_taker_enabled: String(settings.global.maker_to_taker_enabled),
    lowp_guard_enabled: String(settings.global.lowp_guard_enabled),
    lowp_price_threshold: settings.global.lowp_price_threshold,
    lowp_follow_ratio_mult: settings.global.lowp_follow_ratio_mult,
    lowp_min_order_usd: settings.global.lowp_min_order_usd,
    lowp_max_order_usd: settings.global.lowp_max_order_usd,
  });

  const select = document.getElementById("v3-account-select");
  setSelectOptions(select, settings.accounts || []);
  const preferredIndex =
    settings.selected_account?.index != null
      ? String(settings.selected_account.index)
      : select.value;

  if (preferredIndex) {
    select.value = preferredIndex;
    await refreshV3Account(preferredIndex);
    return;
  }

  fillForm(document.getElementById("v3-account-form"), {
    index: "",
    name: "",
    my_address: "",
    private_key: "",
    follow_ratio: "",
    enabled: "false",
    max_notional_per_token: "",
    max_notional_total: "",
  });
  updateV3ProfileLink();
}

async function refreshV3Account(index = null) {
  const select = document.getElementById("v3-account-select");
  const accountIndex = index ?? select.value;
  if (accountIndex === "") {
    return;
  }
  const payload = await readJson(`/api/v3/account?index=${encodeURIComponent(accountIndex)}`);
  fillForm(document.getElementById("v3-account-form"), {
    ...payload.account,
    enabled: String(payload.account.enabled),
    max_notional_per_token:
      payload.account.max_notional_per_token == null ? "" : payload.account.max_notional_per_token,
    max_notional_total:
      payload.account.max_notional_total == null ? "" : payload.account.max_notional_total,
  });
  updateV3ProfileLink();
}

async function refreshV3Runtime() {
  const payload = await readJson("/api/v3/runtime");
  document.getElementById("v3-runtime-summary").innerHTML = buildV3RuntimeSummary(payload);
  document.getElementById("v3-status-pill").textContent =
    payload.services?.mode === "local-process" ? "local-process" : "systemd";
  document.getElementById("v3-log-viewer").textContent = payload.copytrade_log_tail || "No logs";
}

async function saveAccount(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  await readJson("/api/account", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  updateProfileLink();
  showToast("Account settings saved.");
}

async function saveSettings(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  const payload = {
    copytrade: {
      target_addresses: String(data.target_addresses || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean),
      poll_interval_sec: Number(data.poll_interval_sec || 0),
      min_size: Number(data.min_size || 0),
    },
    scheduler: {
      max_concurrent_tasks: Number(data.max_concurrent_tasks || 0),
      copytrade_poll_seconds: Number(data.copytrade_poll_seconds || 0),
      command_poll_seconds: Number(data.command_poll_seconds || 0),
      strategy_mode: data.strategy_mode,
      burst_slots: Number(data.burst_slots || 0),
    },
    strategy: {
      order_size: Number(data.order_size || 0),
      max_position_per_market: Number(data.max_position_per_market || 0),
      drop_pct: percentToRatio(data.drop_pct),
      profit_pct: percentToRatio(data.profit_pct),
      sell_mode: data.sell_mode,
      shock_guard_enabled: data.shock_guard_enabled === "true",
      shock_window_sec: Number(data.shock_window_sec || 0),
      shock_drop_pct: percentToRatio(data.shock_drop_pct),
    },
  };
  await readJson("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  showToast("v2 settings saved.");
}

async function saveV3Settings(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  const startupMode = String(data.startup_mode || "baseline_only");
  const replayWindowHours = Math.max(1, Number(data.replay_window_hours || 24));
  const payload = {
    global: {
      target_addresses: String(data.target_addresses || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean),
      poll_interval_sec: Number(data.poll_interval_sec || 0),
      poll_interval_sec_exiting: Number(data.poll_interval_sec_exiting || 0),
      boot_sync_mode: startupMode,
      actions_replay_window_sec: Math.round(replayWindowHours * 3600),
      follow_new_topics_only: data.follow_new_topics_only === "true",
      min_order_usd: Number(data.min_order_usd || 0),
      max_order_usd: Number(data.max_order_usd || 0),
      max_notional_per_token: Number(data.max_notional_per_token || 0),
      max_notional_total: Number(data.max_notional_total || 0),
      taker_enabled: data.taker_enabled === "true",
      taker_spread_threshold: Number(data.taker_spread_threshold || 0),
      taker_order_type: data.taker_order_type,
      maker_max_wait_sec: Number(data.maker_max_wait_sec || 0),
      maker_to_taker_enabled: data.maker_to_taker_enabled === "true",
      lowp_guard_enabled: data.lowp_guard_enabled === "true",
      lowp_price_threshold: Number(data.lowp_price_threshold || 0),
      lowp_follow_ratio_mult: Number(data.lowp_follow_ratio_mult || 0),
      lowp_min_order_usd: Number(data.lowp_min_order_usd || 0),
      lowp_max_order_usd: Number(data.lowp_max_order_usd || 0),
    },
  };
  await readJson("/api/v3/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  showToast("v3 global settings saved.");
  await refreshV3Settings();
}

async function saveV3Account(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  const index = data.index;
  const payload = {
    name: data.name,
    my_address: data.my_address,
    private_key: data.private_key,
    follow_ratio: Number(data.follow_ratio || 0),
    enabled: data.enabled === "true",
    max_notional_per_token: optionalNumberOrEmpty(data.max_notional_per_token),
    max_notional_total: optionalNumberOrEmpty(data.max_notional_total),
  };
  await readJson(`/api/v3/account?index=${encodeURIComponent(index)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  showToast("Selected account saved.");
  await refreshV3Settings();
  document.getElementById("v3-account-select").value = String(index);
  await refreshV3Account(index);
}

async function deleteV3Account() {
  const select = document.getElementById("v3-account-select");
  const index = select.value;
  if (!index) {
    throw new Error("No account selected.");
  }
  if (!window.confirm("Delete the selected account?")) {
    return;
  }
  const payload = await readJson(`/api/v3/account/delete?index=${encodeURIComponent(index)}`, {
    method: "POST",
  });
  showToast("Selected account deleted.");
  const accounts = payload.settings?.accounts || [];
  setSelectOptions(select, accounts);
  if (accounts.length > 0) {
    await refreshV3Account(select.value);
  } else {
    fillForm(document.getElementById("v3-account-form"), {
      index: "",
      name: "",
      my_address: "",
      private_key: "",
      follow_ratio: "",
      enabled: "false",
      max_notional_per_token: "",
      max_notional_total: "",
    });
    updateV3ProfileLink();
  }
}

async function handleServiceAction(event) {
  const button = event.target.closest("button[data-service]");
  if (!button) return;

  const { service, action } = button.dataset;
  const originalText = button.textContent;
  button.disabled = true;
  button.classList.add("button-busy");
  button.textContent =
    action === "stop" ? "Stopping..." : action === "start" ? "Starting..." : "Restarting...";
  try {
    const payload = await readJson(`/api/service?name=${service}&action=${action}`, {
      method: "POST",
    });
    showToast(payload.ok ? `${service} ${action} complete.` : payload.message, !payload.ok);
    await Promise.all([refreshRuntime(service), refreshV3Runtime()]);
  } finally {
    button.disabled = false;
    button.classList.remove("button-busy");
    button.textContent = originalText;
  }
}

async function refreshAll() {
  if (appState.authRequired && !appState.authenticated) {
    return;
  }
  if (appState.mustChangeCredentials) {
    return;
  }
  await Promise.all([
    refreshAccount(),
    refreshSettings(),
    refreshRuntime(),
    refreshV3Settings(),
    refreshV3Runtime(),
  ]);
}

async function pingPanel() {
  await readJson("/api/ping");
}

async function boot() {
  document.getElementById("auth-form").addEventListener("submit", (event) => {
    login(event).catch((error) => showAuthError(error.message));
  });
  document.getElementById("credentials-form").addEventListener("submit", (event) => {
    updateCredentials(event).catch((error) => showCredentialsError(error.message));
  });
  document.getElementById("logout-btn").addEventListener("click", () => {
    logout().catch((error) => showToast(error.message, true));
  });

  document.getElementById("account-form").addEventListener("submit", (event) => {
    saveAccount(event).catch((error) => showToast(error.message, true));
  });
  document.getElementById("account-form").addEventListener("input", updateProfileLink);

  document.getElementById("settings-form").addEventListener("submit", (event) => {
    saveSettings(event).catch((error) => showToast(error.message, true));
  });
  document.getElementById("v3-settings-form").addEventListener("submit", (event) => {
    saveV3Settings(event).catch((error) => showToast(error.message, true));
  });
  document.getElementById("v3-account-form").addEventListener("submit", (event) => {
    saveV3Account(event).catch((error) => showToast(error.message, true));
  });
  document.getElementById("delete-v3-account").addEventListener("click", () => {
    deleteV3Account().catch((error) => showToast(error.message, true));
  });
  document.getElementById("v3-account-form").addEventListener("input", updateV3ProfileLink);
  document.getElementById("v3-account-select").addEventListener("change", (event) => {
    refreshV3Account(event.target.value).catch((error) => showToast(error.message, true));
  });

  document.querySelectorAll(".actions").forEach((group) => {
    group.addEventListener("click", (event) => {
      handleServiceAction(event).catch((error) => showToast(error.message, true));
    });
  });

  document.getElementById("refresh-all").addEventListener("click", () => {
    refreshAll().catch((error) => showToast(error.message, true));
  });
  document.getElementById("show-copytrade-log").addEventListener("click", () => {
    refreshRuntime("copytrade").catch((error) => showToast(error.message, true));
  });
  document.getElementById("show-autorun-log").addEventListener("click", () => {
    refreshRuntime("autorun").catch((error) => showToast(error.message, true));
  });
  document.getElementById("show-v3-log").addEventListener("click", () => {
    refreshV3Runtime().catch((error) => showToast(error.message, true));
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchPanel(tab.dataset.panel));
  });

  const ready = await refreshAuthSession();
  if (ready) {
    await refreshAll();
  }

  window.setInterval(() => {
    pingPanel().catch(() => {});
  }, 15000);
}

boot().catch((error) => showToast(error.message, true));
