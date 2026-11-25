/* ADB Control Pro — PyWebView Frontend Controller */

const state = {
  apps: [],
  _appsFiltered: [],
  explorerEntries: [],
  explorerSelected: null,
  notifications: [],
  sms: [],
  calls: [],
  sims: [],
  primaryPhone: "",
  lastSimSerial: "",
  profiles: [],
  macros: {},
  macroSteps: [],
  recording: false,
  paused: false,
  recPollTimer: null,
  telemetryTimer: null,
};

function api() {
  if (!window.pywebview || !window.pywebview.api) {
    throw new Error("PyWebView API not ready yet.");
  }
  return window.pywebview.api;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function nowTime() {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

const MAX_CONSOLE_ENTRIES = 400;

function logConsole(title, body = "", isErr = false) {
  const el = document.getElementById("console-body");
  if (!el) return;
  const div = document.createElement("div");
  div.className = "log-entry";
  div.innerHTML = `<div class="${isErr ? "log-err" : "log-head"}">[${nowTime()}] ${esc(title)}</div>${body ? `<div>${esc(body)}</div>` : ""}`;
  el.appendChild(div);
  while (el.childElementCount > MAX_CONSOLE_ENTRIES) {
    el.removeChild(el.firstElementChild);
  }
  el.scrollTop = el.scrollHeight;
}

function toast(msg, isErr = false) {
  const stack = document.getElementById("toast-stack");
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = (isErr ? "❌ " : "✔ ") + msg;
  stack.appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transform = "translateX(20px)";
    t.style.transition = "all 0.25s ease";
    setTimeout(() => t.remove(), 260);
  }, 3000);
}

async function callApi(fnName, args = [], label = "", showToast = true, logErrors = true) {
  try {
    const res = await api()[fnName](...args);
    if (!res || !res.ok) {
      const err = (res && res.error) || "Unknown error";
      if (logErrors) logConsole(`❌ ${label || fnName}`, err, true);
      if (showToast) toast(err, true);
      return null;
    }
    if (label || res.message) {
      const body = typeof res.data === "string" ? res.data : "";
      logConsole(`✔ ${label || res.message}`, body);
      if (showToast && res.message) toast(res.message);
    }
    return res.data;
  } catch (e) {
    if (logErrors) logConsole(`❌ ${label || fnName}`, e.message || String(e), true);
    if (showToast) toast(e.message || String(e), true);
    return null;
  }
}

/* ─── Navigation, Themes & Console ─── */
function switchPage(pageId, btn) {
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
  const page = document.getElementById("page-" + pageId);
  if (page) page.classList.add("active");
  if (btn) btn.classList.add("active");
  if (pageId === "dev") {
    loadProfiles();
    if (typeof renderBatteryGraph === "function") renderBatteryGraph();
  }
  if (pageId === "macro") loadMacros();
}

function switchComms(sub, btn) {
  const wrap = btn ? btn.closest(".subtabs") : null;
  if (wrap) wrap.querySelectorAll(".subtab-btn").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  const n = document.getElementById("comms-notif");
  const s = document.getElementById("comms-sms");
  const c = document.getElementById("comms-calls");
  if (n) n.style.display = sub === "notif" ? "block" : "none";
  if (s) s.style.display = sub === "sms" ? "flex" : "none";
  if (c) c.style.display = sub === "calls" ? "flex" : "none";
}

function switchDevTab(sub, btn) {
  const wrap = btn ? btn.closest(".subtabs") : null;
  if (wrap) wrap.querySelectorAll(".subtab-btn").forEach((b) => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  ["monitor", "shell", "profiles"].forEach((k) => {
    const el = document.getElementById("dev-" + k);
    if (el) el.style.display = k === sub ? "flex" : "none";
  });
  if (sub === "profiles") loadProfiles();
  if (sub === "monitor" && typeof renderBatteryGraph === "function") renderBatteryGraph();
}

function setTheme(name, dotEl) {
  document.body.setAttribute("data-theme", name);
  localStorage.setItem("adb_theme", name);
  document.querySelectorAll(".theme-dot").forEach((d) => d.classList.remove("active"));
  if (dotEl) dotEl.classList.add("active");
}

function toggleConsole() {
  document.getElementById("console-drawer").classList.toggle("collapsed");
}
function clearConsole() {
  document.getElementById("console-body").innerHTML = "";
}
function copyConsole() {
  navigator.clipboard.writeText(document.getElementById("console-body").innerText || "");
  toast("Console copied!");
}

/* ─── Devices & Live Telemetry HUD ─── */
function _formatDeviceOption(d) {
  const conn = d.connection || (String(d.serial || "").includes(":") ? "Wi-Fi" : "USB");
  const model = (d.model || "").trim();
  const stateTag = d.state === "device" ? conn : `${conn} • ${d.state}`;
  return model ? `📱 ${model} — ${d.serial} [${stateTag}]` : `${d.serial} [${stateTag}]`;
}

function clearDeviceScopedUI() {
  // Apps tab
  state.apps = [];
  const appsTbody = document.getElementById("apps-tbody");
  if (appsTbody) appsTbody.innerHTML = "";
  const appsCount = document.getElementById("apps-count");
  if (appsCount) appsCount.textContent = "0 apps";
  const selPkg = document.getElementById("sel-pkg");
  if (selPkg) selPkg.value = "";

  // File Explorer tab
  state.explorerEntries = [];
  state.explorerSelected = null;
  if (state.explorerSelectedSet) state.explorerSelectedSet.clear();
  state.explorerAnchorIdx = -1;
  state._expFiltered = [];
  const expTbody = document.getElementById("exp-tbody");
  if (expTbody) expTbody.innerHTML = "";
  const expCount = document.getElementById("exp-count");
  if (expCount) expCount.textContent = "0 / 0 items";
  const expPath = document.getElementById("exp-path");
  if (expPath) expPath.value = "/sdcard";

  // Comms & Monitor tabs
  state.notifications = [];
  state.sms = [];
  state.calls = [];
  state.sims = [];
  state.primaryPhone = "";
  state.lastSimSerial = "";
  ["notif-tbody", "sms-tbody", "calls-tbody"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  const hudPhonePill = document.getElementById("hud-phone-pill");
  if (hudPhonePill) hudPhonePill.style.display = "none";
  const hudRootPill = document.getElementById("hud-root-pill");
  if (hudRootPill) hudRootPill.style.display = "none";
  const expRoot = document.getElementById("exp-root-mode");
  if (expRoot) expRoot.checked = false;
  const expRootJumps = document.getElementById("exp-root-jumps");
  if (expRootJumps) expRootJumps.style.display = "none";
  const shellRoot = document.getElementById("shell-root");
  if (shellRoot) shellRoot.checked = false;
  const simCards = document.getElementById("sim-cards");
  if (simCards) simCards.innerHTML = `<div class="muted">🔄 SIM তথ্য লোড হচ্ছে...</div>`;
  const commsSim = document.getElementById("comms-sim-strip");
  if (commsSim) {
    commsSim.innerHTML = "";
    commsSim.style.display = "none";
  }
  const monOut = document.getElementById("monitor-out");
  if (monOut) monOut.textContent = "";
  state.batHistory = [];
  if (state.thumbCache) state.thumbCache.clear();
  if (typeof onExpHoverLeave === "function") onExpHoverLeave();
  if (typeof renderBatteryGraph === "function") renderBatteryGraph();
}

async function refreshDevices(skipTelemetry = false) {
  if (state.refreshingDevices) return;
  state.refreshingDevices = true;
  try {
    const data = await callApi("list_devices", [], "", false, false);
    if (!data) return;
    const sel = document.getElementById("device-select");
    const prevSelected = state.selectedSerial || sel.value || "";
    sel.innerHTML = "";
    if (!data.devices.length) {
      sel.innerHTML = `<option value="">No Devices Found</option>`;
    } else {
      for (const d of data.devices) {
        const opt = document.createElement("option");
        opt.value = d.serial;
        opt.textContent = _formatDeviceOption(d);
        if (d.serial === data.selected) opt.selected = true;
        sel.appendChild(opt);
      }
    }
    const nextSelected = data.selected || "";
    if (prevSelected && nextSelected && prevSelected !== nextSelected) {
      clearDeviceScopedUI();
    }
    state.selectedSerial = nextSelected;
    if (!skipTelemetry) {
      await refreshTelemetry();
    }
    if (nextSelected && state.lastSimSerial !== nextSelected) {
      loadPhoneNumbers(true);
    }
  } finally {
    state.refreshingDevices = false;
  }
}

async function onSelectDevice() {
  const serial = document.getElementById("device-select").value;
  const prev = state.selectedSerial || "";
  await callApi("select_device", [serial], "Select Device", true);
  if (serial !== prev) {
    clearDeviceScopedUI();
    state.selectedSerial = serial;
  }
  await refreshTelemetry();
  if (serial) loadPhoneNumbers(true);
}

async function refreshTelemetry() {
  const data = await callApi("device_telemetry", [], "", false, false);
  const dot = document.getElementById("hud-dot");
  const modelEl = document.getElementById("hud-model");
  const rootPill = document.getElementById("hud-root-pill");
  const batPill = document.getElementById("hud-bat-pill");
  const connPill = document.getElementById("hud-conn-pill");
  if (!data || !data.connected) {
    // On transient Wi-Fi timeout, keep existing HUD pill instead of flickering offline
    if (data && data.timeout) return;
    dot.classList.remove("online");
    modelEl.textContent = "Offline / No Device";
    if (rootPill) rootPill.style.display = "none";
    batPill.style.display = "none";
    connPill.style.display = "none";
    return;
  }
  dot.classList.add("online");
  modelEl.textContent = `${data.model} • Android ${data.android}`;
  if (rootPill) rootPill.style.display = data.rooted ? "inline-flex" : "none";
  if (data.battery !== null && data.battery !== undefined) {
    batPill.style.display = "inline-flex";
    const tmp = data.temp !== null ? ` • ${data.temp}°C` : "";
    document.getElementById("hud-bat").textContent = `${data.battery}%${data.charging ? " ⚡" : ""}${tmp}`;
  }
  connPill.style.display = "inline-flex";
  let connText = data.connection || "ADB";
  if (data.wifi_rssi !== null && data.wifi_rssi !== undefined) {
    const q = data.wifi_quality === "Weak" ? " ⚠️ Weak" : "";
    connText += ` (${data.wifi_rssi} dBm${q})`;
    connPill.style.borderColor = data.wifi_quality === "Weak" ? "#f59e0b" : "";
  } else {
    connPill.style.borderColor = "";
  }
  document.getElementById("hud-conn").textContent = connText;
}

/* ─── Device Tab Actions ─── */
async function connectWifi() {
  const target = document.getElementById("wifi-ip").value.trim();
  await callApi("connect_wifi", [target], "Connect Wi-Fi");
  refreshDevices();
}
async function disconnectDevice() {
  await callApi("disconnect_device", [], "Disconnect");
  refreshDevices();
}
async function restartAdb() {
  await callApi("restart_adb", [], "Restart ADB");
  refreshDevices();
}
async function rebootDevice() {
  if (!confirm("ডিভাইসটি রিবুট করতে চান?")) return;
  await callApi("reboot_device", [], "Reboot");
}
async function runShell() {
  const cmd = document.getElementById("shell-cmd").value;
  const asRoot = !!document.getElementById("shell-root")?.checked;
  await callApi("exec_shell", [cmd, asRoot], asRoot ? `👑 Root Shell: ${cmd}` : `Shell: ${cmd}`, false);
}
async function checkRootStatus() {
  const data = await callApi("check_root_status", [], "Root Status Check", true);
  if (!data) return;
  if (data.summary) logConsole("👑 Root Diagnostics", data.summary);
  const rootPill = document.getElementById("hud-root-pill");
  if (rootPill && data.status) {
    rootPill.style.display = data.status.rooted ? "inline-flex" : "none";
  }
}
async function runShellDirect(cmd) {
  await callApi("exec_shell", [cmd], cmd, false);
}
async function sendKey(code) { await callApi("keyevent", [code], code, false); }
async function sendText() {
  const txt = document.getElementById("inp-text").value;
  if (!txt) return;
  await callApi("send_text", [txt], "Send Text");
}
async function sendTap() {
  const x = parseInt(document.getElementById("tap-x").value || "0", 10);
  const y = parseInt(document.getElementById("tap-y").value || "0", 10);
  await callApi("tap", [x, y], `Tap (${x},${y})`, false);
}
async function wakeUnlock() { await callApi("wake_unlock", [], "Wake & Swipe"); }
async function openUrl() {
  const url = document.getElementById("inp-url").value.trim();
  if (url) await callApi("open_url", [url], "Open URL");
}
async function pullClipboard() {
  const txt = await callApi("pull_clipboard", [], "Phone Clipboard");
  if (txt) {
    await navigator.clipboard.writeText(txt);
    toast("ফোনের ক্লিপবোর্ড পিসিতে কপি হয়েছে!");
  }
}
async function pushClipboard() {
  try {
    const txt = await navigator.clipboard.readText();
    if (!txt) return toast("পিসির ক্লিপবোর্ড খালি!", true);
    await callApi("push_clipboard", [txt], "PC → Phone Clipboard");
  } catch (e) {
    toast("Clipboard read permission denied.", true);
  }
}

/* ─── Apps Tab (Launcher, User, System Bloatware & Freezer) ─── */
async function loadApps() {
  const mode = document.getElementById("app-mode")?.value || "launcher";
  const data = await callApi("list_launcher_apps", [mode], `Load Apps (${mode})`);
  if (!data) return;
  state.apps = data;
  renderApps();
}
function renderApps() {
  const q = (document.getElementById("app-filter").value || "").toLowerCase();
  const tbody = document.getElementById("apps-tbody");
  const filtered = state.apps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.package.toLowerCase().includes(q));
  state._appsFiltered = filtered;
  document.getElementById("apps-count").textContent = `${filtered.length} apps`;
  tbody.innerHTML = filtered
    .map((a, i) => {
      const statusBadge = a.disabled
        ? `<span class="badge badge-frozen">❄️ Frozen</span>`
        : a.is_system
          ? `<span class="badge badge-warn">System</span>`
          : `<span class="badge">Active</span>`;
      return `<tr onclick="selectAppIdx(${i}, this)"><td><strong>${esc(a.name)}</strong></td><td class="mono muted">${esc(a.package)}</td><td>${statusBadge}</td></tr>`;
    })
    .join("");
}
async function freezeApp(freeze) {
  const pkg = document.getElementById("sel-pkg").value.trim();
  if (!pkg) return toast("প্রথমে একটি অ্যাপ সিলেক্ট করুন।", true);
  if (freeze && !confirm(`'${pkg}' অ্যাপটি ফ্রিজ (Disable) করতে চান?\n(পরে যেকোনো সময় '🔥 Unfreeze' দিয়ে চালু করতে পারবেন)`)) return;
  const res = await callApi("freeze_app", [pkg, !!freeze], freeze ? `Freeze ${pkg}` : `Unfreeze ${pkg}`);
  if (res !== null) loadApps();
}
function selectAppIdx(i, tr) {
  const app = (state._appsFiltered || [])[i];
  if (!app) return;
  document.querySelectorAll("#apps-tbody tr").forEach((r) => r.classList.remove("selected"));
  tr.classList.add("selected");
  document.getElementById("sel-pkg").value = app.package;
}
async function installApk() { await callApi("install_apk_dialog", [], "Install APK"); }
async function appAction(kind) {
  const pkg = document.getElementById("sel-pkg").value.trim();
  if (!pkg) return toast("প্রথমে একটি অ্যাপ সিলেক্ট করুন।", true);
  if (kind === "uninstall" && !confirm(`'${pkg}' আনইনস্টল করবেন?`)) return;
  const map = { launch: "launch_app", stop: "force_stop", clear: "clear_data", uninstall: "uninstall_app" };
  await callApi(map[kind], [pkg], `${kind}: ${pkg}`);
}
async function permAction(mode) {
  const pkg = document.getElementById("sel-pkg").value.trim();
  const perm = document.getElementById("perm-select").value;
  if (!pkg) return toast("প্রথমে একটি অ্যাপ সিলেক্ট করুন।", true);
  await callApi("permission_action", [pkg, perm, mode], `Perm ${mode}`);
}

/* ─── Interactive File Explorer ─── */
function fileIcon(name, type) {
  if (type === "dir") return "📁";
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["jpg", "jpeg", "png", "webp", "gif", "heic"].includes(ext)) return "🖼️";
  if (["mp4", "mkv", "mov", "avi", "webm"].includes(ext)) return "🎬";
  if (["mp3", "wav", "m4a", "aac", "ogg"].includes(ext)) return "🎵";
  if (ext === "apk") return "📦";
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return "🗜️";
  return "📄";
}
state.explorerSelectedSet = new Set();
state.explorerAnchorIdx = -1;

function _getSelectedExpItems() {
  const list = state._expFiltered || [];
  return list.filter((e) => state.explorerSelectedSet.has(e.path));
}

function _syncExplorerSelectionUI() {
  const list = state._expFiltered || [];
  document.querySelectorAll("#exp-tbody tr").forEach((tr) => {
    const idx = parseInt(tr.getAttribute("data-idx") || "-1", 10);
    const item = list[idx];
    if (item && state.explorerSelectedSet.has(item.path)) {
      tr.classList.add("selected");
    } else {
      tr.classList.remove("selected");
    }
  });
  const selectedItems = _getSelectedExpItems();
  state.explorerSelected = selectedItems.length ? selectedItems[selectedItems.length - 1] : null;
  const selSuffix = selectedItems.length > 0 ? ` • ${selectedItems.length} selected` : "";
  const cntEl = document.getElementById("exp-count");
  if (cntEl) cntEl.textContent = `${list.length} / ${state.explorerEntries.length} items${selSuffix}`;
}

function _isExplorerRoot() {
  return !!document.getElementById("exp-root-mode")?.checked;
}
function onToggleExplorerRoot() {
  const on = _isExplorerRoot();
  const jumps = document.getElementById("exp-root-jumps");
  if (jumps) jumps.style.display = on ? "flex" : "none";
  const cur = document.getElementById("exp-path")?.value || (on ? "/" : "/sdcard");
  explorerGo(cur);
}
async function explorerGo(dirPath) {
  const useRoot = _isExplorerRoot();
  const data = await callApi("list_dir", [dirPath || "/sdcard", useRoot], "", false);
  if (!data) return;
  document.getElementById("exp-path").value = data.path;
  document.getElementById("exp-filter").value = "";
  state.explorerEntries = data.entries || [];
  state.explorerSelected = null;
  state.explorerSelectedSet.clear();
  state.explorerAnchorIdx = -1;
  renderExplorer();
}
function explorerUp() {
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  if (!cur || cur === "/") return explorerGo("/");
  const idx = cur.lastIndexOf("/");
  explorerGo(idx <= 0 ? "/" : cur.slice(0, idx));
}
function renderExplorer() {
  onExpHoverLeave();
  const q = (document.getElementById("exp-filter").value || "").toLowerCase();
  const tbody = document.getElementById("exp-tbody");
  const list = state.explorerEntries.filter((e) => !q || e.name.toLowerCase().includes(q));
  state._expFiltered = list;
  // Prune any selections no longer visible after filtering
  const visiblePaths = new Set(list.map((e) => e.path));
  for (const p of Array.from(state.explorerSelectedSet)) {
    if (!visiblePaths.has(p)) state.explorerSelectedSet.delete(p);
  }
  tbody.innerHTML = list
    .map((e, i) => {
      const ico = fileIcon(e.name, e.type);
      const selCls = state.explorerSelectedSet.has(e.path) ? ' class="selected"' : "";
      const hoverAttrs =
        e.type !== "dir" && isImageName(e.name)
          ? ` onmouseenter="onExpHoverEnter(${i}, event)" onmousemove="onExpHoverMove(event)" onmouseleave="onExpHoverLeave()"`
          : "";
      return `<tr data-idx="${i}"${selCls} onclick="selectExp(${i}, this, event)" ondblclick="dblClickExp(${i})"${hoverAttrs}>
        <td>${ico} ${esc(e.name)}</td>
        <td class="mono muted">${esc(e.size)}</td>
        <td class="mono muted">${esc(e.modified)}</td>
      </tr>`;
    })
    .join("");
  _syncExplorerSelectionUI();
}
function selectExp(i, tr, e) {
  const list = state._expFiltered || [];
  const item = list[i];
  if (!item) return;
  const ctrl = !!(e && (e.ctrlKey || e.metaKey));
  const shift = !!(e && e.shiftKey);

  if (shift && state.explorerAnchorIdx >= 0) {
    const start = Math.min(state.explorerAnchorIdx, i);
    const end = Math.max(state.explorerAnchorIdx, i);
    if (!ctrl) state.explorerSelectedSet.clear();
    for (let k = start; k <= end; k++) {
      if (list[k]) state.explorerSelectedSet.add(list[k].path);
    }
  } else if (ctrl) {
    if (state.explorerSelectedSet.has(item.path)) {
      state.explorerSelectedSet.delete(item.path);
    } else {
      state.explorerSelectedSet.add(item.path);
    }
    state.explorerAnchorIdx = i;
  } else {
    state.explorerSelectedSet.clear();
    state.explorerSelectedSet.add(item.path);
    state.explorerAnchorIdx = i;
  }
  _syncExplorerSelectionUI();
}
function explorerSelectAll() {
  const list = state._expFiltered || [];
  if (!list.length) return;
  if (state.explorerSelectedSet.size === list.length) {
    state.explorerSelectedSet.clear();
  } else {
    list.forEach((item) => state.explorerSelectedSet.add(item.path));
  }
  _syncExplorerSelectionUI();
}
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "a") return;
  const page = document.getElementById("page-files");
  if (!page || !page.classList.contains("active")) return;
  const tag = (document.activeElement && document.activeElement.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  e.preventDefault();
  explorerSelectAll();
});
function isImageName(name) {
  const ext = (String(name || "").split(".").pop() || "").toLowerCase();
  return ["jpg", "jpeg", "png", "webp", "gif", "bmp"].includes(ext);
}

/* ─── Hover Image Preview with Instant Cancel & Lightweight Canvas Thumbnail Cache ─── */
state.thumbCache = new Map();
state.hoverTimer = null;
state.hoverPath = "";
state.hoverLoading = false;

function _makeThumbDataUrl(dataUrl, maxDim = 260) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const w = img.naturalWidth || maxDim;
        const h = img.naturalHeight || maxDim;
        const scale = Math.min(1, maxDim / Math.max(w, h));
        const cw = Math.max(1, Math.round(w * scale));
        const ch = Math.max(1, Math.round(h * scale));
        const canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, cw, ch);
        resolve(canvas.toDataURL("image/jpeg", 0.72));
      } catch (_) {
        resolve(dataUrl);
      }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

function _positionHoverCard(e) {
  const card = document.getElementById("hover-preview");
  if (!card || !e) return;
  const pad = 16;
  const cardW = card.offsetWidth || 264;
  const cardH = card.offsetHeight || 280;
  let x = e.clientX + pad;
  let y = e.clientY + pad;
  if (x + cardW > window.innerWidth - 10) x = Math.max(10, e.clientX - cardW - pad);
  if (y + cardH > window.innerHeight - 10) y = Math.max(10, window.innerHeight - cardH - 10);
  card.style.left = `${x}px`;
  card.style.top = `${y}px`;
}

function _showHoverCard(e, dataUrl, metaText) {
  const card = document.getElementById("hover-preview");
  const img = document.getElementById("hover-preview-img");
  const meta = document.getElementById("hover-preview-meta");
  if (!card || !img || !meta) return;
  if (dataUrl) {
    img.src = dataUrl;
    img.style.display = "block";
  } else {
    img.style.display = "none";
  }
  meta.textContent = metaText;
  card.classList.add("open");
  _positionHoverCard(e);
}

function onExpHoverEnter(i, e) {
  const item = (state._expFiltered || [])[i];
  if (!item || item.type === "dir" || !isImageName(item.name)) return;
  if (state.hoverTimer) {
    clearTimeout(state.hoverTimer);
    state.hoverTimer = null;
  }
  if (state.hoverLoading) {
    callApi("cancel_preview", [], "", false, false);
  }
  state.hoverPath = item.path;
  const lastEvent = { clientX: e.clientX, clientY: e.clientY };

  const cached = state.thumbCache.get(item.path);
  if (cached) {
    _showHoverCard(lastEvent, cached.data_url, `${item.name}${cached.size ? ` • ${cached.size}` : ""}`);
    return;
  }

  state.hoverTimer = setTimeout(async () => {
    if (state.hoverPath !== item.path) return;
    state.hoverLoading = true;
    _showHoverCard(lastEvent, "", `⏳ Loading ${item.name}...`);
    const data = await callApi("preview_remote_image", [item.path, 5], "", false, false);
    state.hoverLoading = false;
    if (state.hoverPath !== item.path || (data && data.cancelled)) return;
    if (!data || !data.data_url) {
      _showHoverCard(lastEvent, "", `ℹ️ ডাবল-ক্লিক করে ফুল প্রিভিউ দেখুন (${item.size || item.name})`);
      return;
    }
    const thumbUrl = await _makeThumbDataUrl(data.data_url, 260);
    if (state.hoverPath !== item.path) return;
    if (state.thumbCache.size >= 40) {
      const oldestKey = state.thumbCache.keys().next().value;
      state.thumbCache.delete(oldestKey);
    }
    const entry = { data_url: thumbUrl, size: data.size || item.size || "" };
    state.thumbCache.set(item.path, entry);
    _showHoverCard(lastEvent, entry.data_url, `${item.name}${entry.size ? ` • ${entry.size}` : ""}`);
  }, 1000);
}

function onExpHoverMove(e) {
  const card = document.getElementById("hover-preview");
  if (card && card.classList.contains("open")) {
    _positionHoverCard(e);
  }
}

function onExpHoverLeave() {
  if (state.hoverTimer) {
    clearTimeout(state.hoverTimer);
    state.hoverTimer = null;
  }
  if (state.hoverLoading) {
    state.hoverLoading = false;
    callApi("cancel_preview", [], "", false, false);
  }
  state.hoverPath = "";
  const card = document.getElementById("hover-preview");
  if (card) card.classList.remove("open");
}
function dblClickExp(i) {
  const item = (state._expFiltered || [])[i];
  if (!item) return;
  state.explorerSelected = item;
  if (item.type === "dir") explorerGo(item.path);
  else if (isImageName(item.name)) explorerPreview();
  else explorerDownload();
}

/* ─── Live Transfer Progress Bar & Cancel ─── */
function startTransferPoll() {
  const box = document.getElementById("xfer-box");
  if (box) box.classList.add("active");
  if (state.xferTimer) return;
  state.xferTimer = setInterval(async () => {
    const st = await callApi("poll_transfer_progress", [], "", false, false);
    if (!st) return;
    const lbl = document.getElementById("xfer-label");
    const meta = document.getElementById("xfer-meta");
    const fill = document.getElementById("xfer-fill");
    if (lbl) lbl.textContent = `⏳ ${st.label || "Transferring..."}`;
    if (meta) meta.textContent = `${st.pct || 0}% • ${st.done || "0 B"} / ${st.total || "?"} • ${st.speed || "0 B/s"}`;
    if (fill) fill.style.width = `${Math.min(100, Math.max(0, st.pct || 0))}%`;

    if (!st.active) {
      clearInterval(state.xferTimer);
      state.xferTimer = null;
      if (st.error) {
        logConsole("❌ Transfer", st.error, true);
        toast(st.error, true);
      } else if (st.finished_msg) {
        logConsole("✔ Transfer", st.finished_msg);
        toast(st.finished_msg);
        if (st.refresh_dir) explorerGo(st.refresh_dir);
      }
      setTimeout(() => {
        if (!state.xferTimer && box) box.classList.remove("active");
      }, 1500);
    }
  }, 260);
}
async function cancelTransfer() {
  await callApi("cancel_transfer", [], "Cancel Transfer");
}
async function explorerDownload() {
  const selected = _getSelectedExpItems();
  if (!selected.length) return toast("ডাউনলোডের জন্য একটি বা একাধিক ফাইল/ফোল্ডার সিলেক্ট করুন।", true);
  if (selected.length === 1) {
    const it = selected[0];
    const res = await callApi("download_item_dialog", [it.path, it.type === "dir"], "Download");
    if (res && res.started) startTransferPoll();
    return;
  }
  const payload = selected.map((it) => ({ path: it.path, is_dir: it.type === "dir", name: it.name }));
  const res = await callApi("download_items_dialog", [payload, _isExplorerRoot()], `Download (${selected.length} items)`);
  if (res && res.started) startTransferPoll();
}
async function explorerUpload() {
  const cur = document.getElementById("exp-path").value || "/sdcard";
  const res = await callApi("upload_here_dialog", [cur, _isExplorerRoot()], "Upload");
  if (res && res.started) startTransferPoll();
}
async function explorerPreview() {
  if (!state.explorerSelected || state.explorerSelected.type === "dir") {
    return toast("প্রিভিউ করার জন্য একটি ইমেজ ফাইল সিলেক্ট করুন।", true);
  }
  const data = await callApi("preview_remote_image", [state.explorerSelected.path], "Image Preview", false);
  if (!data || !data.data_url) return;
  document.getElementById("shot-title").textContent = `🖼️ ${data.filename}${data.size ? ` (${data.size})` : ""}`;
  document.getElementById("shot-img").src = data.data_url;
  document.getElementById("shot-modal").classList.add("open");
}

function initDropZone() {
  const dz = document.getElementById("drop-zone");
  if (!dz) return;
  ["dragenter", "dragover"].forEach((ev) => {
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove("dragover");
    });
  });
  dz.addEventListener("drop", async (e) => {
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    if (!files.length) return;
    const f = files[0];
    if (f.size > 150 * 1024 * 1024) {
      return toast("Drag & Drop ফাইল ১৫০ MB-এর বেশি — 'Upload Here' বাটন ব্যবহার করুন।", true);
    }
    const cur = document.getElementById("exp-path").value || "/sdcard/Download";
    toast(`📥 Reading ${f.name}...`);
    const reader = new FileReader();
    reader.onload = async () => {
      const res = await callApi("upload_dropped_file", [cur, f.name, String(reader.result || "")], `Drop: ${f.name}`);
      if (res && res.started) startTransferPoll();
    };
    reader.onerror = () => toast("ফাইল পড়া যায়নি।", true);
    reader.readAsDataURL(f);
  });
}
async function explorerNewFolder() {
  const name = prompt("নতুন ফোল্ডারের নাম লিখুন:");
  if (!name || !name.trim()) return;
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  const full = cur === "/" ? `/${name.trim()}` : `${cur}/${name.trim()}`;
  const res = await callApi("mkdir", [full, _isExplorerRoot()], "Create Folder");
  if (res !== null) explorerGo(cur || "/");
}
async function explorerRename() {
  const selected = _getSelectedExpItems();
  if (selected.length !== 1) return toast("রিনেম করার জন্য ঠিক ১টি আইটেম সিলেক্ট করুন।", true);
  const item = selected[0];
  const newName = prompt("নতুন নাম লিখুন:", item.name);
  if (!newName || !newName.trim() || newName.trim() === item.name) return;
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  const dst = cur === "/" ? `/${newName.trim()}` : `${cur}/${newName.trim()}`;
  const res = await callApi("rename_path", [item.path, dst, _isExplorerRoot()], "Rename");
  if (res !== null) explorerGo(cur || "/");
}
async function explorerDelete() {
  const selected = _getSelectedExpItems();
  if (!selected.length) return toast("ডিলিট করার জন্য আইটেম সিলেক্ট করুন।", true);
  const cur = document.getElementById("exp-path").value || "/sdcard";
  const useRoot = _isExplorerRoot();
  if (selected.length === 1) {
    if (!confirm(`আপনি কি নিশ্চিতভাবে ডিলিট করতে চান?\n${selected[0].path}`)) return;
    const res = await callApi("delete_path", [selected[0].path, useRoot], "Delete");
    if (res !== null) explorerGo(cur);
    return;
  }
  const preview = selected.slice(0, 5).map((it) => `• ${it.name}`).join("\n");
  const extra = selected.length > 5 ? `\n... এবং আরও ${selected.length - 5}টি` : "";
  if (!confirm(`আপনি কি নিশ্চিতভাবে মোট ${selected.length}টি আইটেম ডিলিট করতে চান?\n\n${preview}${extra}`)) return;
  const res = await callApi("delete_paths", [selected.map((it) => it.path), useRoot], `Delete (${selected.length} items)`);
  if (res !== null) explorerGo(cur);
}
async function takeScreenshot() {
  const data = await callApi("take_screenshot", [], "Screenshot");
  if (!data || !data.data_url) return;
  document.getElementById("shot-title").textContent = `📸 ${data.filename}`;
  document.getElementById("shot-img").src = data.data_url;
  document.getElementById("shot-modal").classList.add("open");
}

/* ─── Monitor, Screen Share & Controls ─── */
async function runMonitor(kind) {
  const data = await callApi("monitor_query", [kind], `Monitor: ${kind}`, false);
  if (data !== null) document.getElementById("monitor-out").textContent = data || "(No output)";
}
function applyScrcpyPreset() {
  const val = document.getElementById("scrcpy-preset").value;
  if (val) {
    document.getElementById("scrcpy-custom").value = val;
  }
}
function _getScreenOffOpts() {
  return {
    res: document.getElementById("soff-res")?.value || "720",
    fps: document.getElementById("soff-fps")?.value || "30",
    bitrate: document.getElementById("soff-bitrate")?.value || "2M",
    audio: document.getElementById("soff-audio")?.value || "off",
    codec: document.getElementById("soff-codec")?.value || "h264",
    stay_awake: !!document.getElementById("soff-stay")?.checked,
    power_off_close: !!document.getElementById("soff-lock")?.checked,
    always_top: !!document.getElementById("soff-top")?.checked,
    read_only: !!document.getElementById("soff-ro")?.checked,
  };
}
function updateScreenOffPreview() {
  const o = _getScreenOffOpts();
  const parts = ["-S"];
  if (o.res !== "orig") parts.push(`-m ${o.res}`);
  parts.push(`--max-fps ${o.fps}`, `-b ${o.bitrate}`);
  if (o.audio === "off") parts.push("--no-audio");
  else parts.push(`--audio-bit-rate=${o.audio}`);
  if (o.codec === "h265") parts.push("--video-codec=h265");
  if (o.stay_awake) parts.push("-w");
  if (o.power_off_close) parts.push("--power-off-on-close");
  if (o.always_top) parts.push("--always-on-top");
  if (o.read_only) parts.push("-n");
  const el = document.getElementById("soff-cmd-preview");
  if (el) el.textContent = parts.join(" ");
}
async function launchScreenOffMirror() {
  const opts = _getScreenOffOpts();
  await callApi("start_screen_off_scrcpy", [opts], "Screen-Off Mirror");
}
async function startScrcpy(mode) {
  const custom = document.getElementById("scrcpy-custom").value || "";
  await callApi("start_scrcpy", [mode, custom], `scrcpy (${mode})`);
}
async function stopScrcpy() { await callApi("stop_scrcpy", [], "Stop scrcpy"); }
async function launchVirtualDisplay() {
  const pkg = document.getElementById("vd-pkg").value.trim();
  const size = document.getElementById("vd-size").value;
  await callApi("launch_virtual_display", [pkg, size], "Virtual Display");
}
async function sysToggle(kind, val) { await callApi("system_toggle", [kind, val], `Control: ${kind}`); }

/* ─── Profiles ─── */
async function loadProfiles() {
  const data = await callApi("list_profiles", [], "", false);
  if (!data) return;
  const lk = document.getElementById("prof-lock");
  const pr = document.getElementById("prof-proc");
  if (!lk.options.length) {
    lk.innerHTML = data.lock_types.map((t) => `<option>${esc(t)}</option>`).join("");
    pr.innerHTML = data.unlock_processes.map((t) => `<option>${esc(t)}</option>`).join("");
  }
  state.profiles = data.profiles || [];
  const tbody = document.getElementById("prof-tbody");
  tbody.innerHTML = state.profiles
    .map(
      (p, i) => `<tr onclick="fillProfileIdx(${i})">
      <td><strong>${esc(p.name)}</strong></td>
      <td>${esc(p.lock_type)}</td>
      <td class="muted">${esc(p.unlock_process)}</td>
      <td><button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteProfileIdx(${i})">🗑️</button></td>
    </tr>`
    )
    .join("");
}
function fillProfileIdx(i) {
  const p = (state.profiles || [])[i];
  if (p) fillProfile(p);
}
function fillProfile(p) {
  document.getElementById("prof-fp").value = p.fingerprint || "";
  document.getElementById("prof-name").value = p.name || "";
  document.getElementById("prof-lock").value = p.lock_type || "PIN";
  document.getElementById("prof-proc").value = p.unlock_process || "";
  document.getElementById("prof-val").value = p.unlock_value || "";
  document.getElementById("prof-coords").value = p.swipe_coords || "auto";
}
async function detectProfile() {
  const data = await callApi("detect_profile", [], "Detect Device Profile");
  if (!data) return;
  if (data.profile) fillProfile(data.profile);
  else {
    document.getElementById("prof-fp").value = data.fingerprint || "";
    document.getElementById("prof-name").value = data.model || "";
  }
}
async function saveProfile() {
  const prof = {
    fingerprint: document.getElementById("prof-fp").value.trim(),
    name: document.getElementById("prof-name").value.trim(),
    lock_type: document.getElementById("prof-lock").value,
    unlock_process: document.getElementById("prof-proc").value,
    unlock_value: document.getElementById("prof-val").value,
    swipe_coords: document.getElementById("prof-coords").value.trim() || "auto",
  };
  await callApi("save_profile", [prof], "Save Profile");
  loadProfiles();
}
async function deleteProfileIdx(i) {
  const p = (state.profiles || [])[i];
  if (!p) return;
  await deleteProfile(p.fingerprint);
}
async function deleteProfile(fp) {
  if (!confirm("প্রোফাইলটি ডিলিট করবেন?")) return;
  await callApi("delete_profile", [fp], "Delete Profile");
  loadProfiles();
}
async function oneClickUnlock() { await callApi("one_click_unlock", [], "1-Click Unlock"); }

/* ─── Comms (Notifications, SMS/OTP & Calls) ─── */
async function loadNotifications() {
  const items = await callApi("list_notifications", [], "Notifications");
  if (!items) return;
  state.notifications = items;
  document.getElementById("notif-tbody").innerHTML = items
    .map(
      (n, i) => `<tr onclick="copyNotifIdx(${i})">
      <td class="mono muted">${esc(n.package)}</td>
      <td><strong>${esc(n.title)}</strong></td>
      <td>${esc(n.text)}</td>
    </tr>`
    )
    .join("");
}
function copyNotifIdx(i) {
  const n = (state.notifications || [])[i];
  if (n) copyText(n.text || n.title || "");
}
async function loadSms() {
  const items = await callApi("list_sms", [], "SMS Inbox");
  if (!items) return;
  state.sms = items;
  document.getElementById("sms-tbody").innerHTML = items
    .map(
      (s, i) => `<tr onclick="copySmsIdx(${i})">
      <td><strong>${esc(s.address)}</strong></td>
      <td>${s.otp ? `<span class="badge">${esc(s.otp)}</span>` : "-"}</td>
      <td>${esc(s.body)}</td>
      <td class="mono muted">${esc(s.date)}</td>
    </tr>`
    )
    .join("");
}
function copySmsIdx(i) {
  const s = (state.sms || [])[i];
  if (s) copyText(s.otp || s.body || "");
}
async function loadCalls() {
  const items = await callApi("list_call_logs", [], "Call Logs");
  if (!items) return;
  state.calls = items;
  document.getElementById("calls-tbody").innerHTML = items
    .map(
      (c, i) => `<tr onclick="pickCallIdx(${i})">
      <td class="mono"><strong>${esc(c.number)}</strong></td>
      <td>${esc(c.name)}</td>
      <td>${esc(c.type)}</td>
      <td class="mono muted">${esc(c.duration)}</td>
      <td class="mono muted">${esc(c.date)}</td>
    </tr>`
    )
    .join("");
}
function pickCallIdx(i) {
  const c = (state.calls || [])[i];
  if (c) document.getElementById("dial-num").value = c.number || "";
}
async function dialNumber() {
  const n = document.getElementById("dial-num").value.trim();
  if (n) await callApi("dial_number", [n], `Dial ${n}`);
}
async function callAction(act) { await callApi("call_action", [act], `Call ${act}`); }
function copyText(txt) {
  navigator.clipboard.writeText(txt);
  toast(`কপি হয়েছে: ${txt.slice(0, 36)}`);
}

/* ─── Device SIM & Phone Number(s) ─── */
function renderSimCards(sims) {
  state.sims = Array.isArray(sims) ? sims : [];
  const primary = state.sims.find((s) => s.number && String(s.number).trim())?.number || "";
  state.primaryPhone = primary;

  // 1. Update Top HUD quick-copy pill
  const hudPill = document.getElementById("hud-phone-pill");
  const hudVal = document.getElementById("hud-phone");
  if (hudPill && hudVal) {
    if (primary) {
      hudVal.textContent = primary;
      hudPill.style.display = "inline-flex";
    } else {
      hudPill.style.display = "none";
    }
  }

  // 2. Render Remote & Controls SIM card grid
  const wrap = document.getElementById("sim-cards");
  if (wrap) {
    if (!state.sims.length) {
      wrap.innerHTML = `<div class="muted">কোনো Active SIM পাওয়া যায়নি। চাইলে ডানপাশের "➕ Add Number" দিয়ে এই ফোনের নম্বর সেভ করে রাখতে পারেন।</div>`;
    } else {
      wrap.innerHTML = state.sims
        .map((s, i) => {
          const num = (s.number || "").trim();
          const srcLabel = s.source ? `<span class="badge">${esc(s.source)}</span>` : "";
          const hint = s.redacted
            ? "Android সিকিউরিটি নম্বর হাইড করেছে — USSD দিয়ে দেখে একবার সেভ করুন"
            : "SIM কার্ডে নম্বর সেভ নেই — USSD দিয়ে দেখে একবার সেভ করুন";
          const ussdBtn = s.ussd
            ? `<button class="btn btn-sm" onclick="dialUssdIdx(${i})" title="ফোনে ${esc(s.ussd)} ডায়াল করে নম্বর দেখুন">📞 ${esc(s.ussd)}</button>`
            : "";
          return `<div class="sim-card">
            <div class="sim-info">
              <div class="sim-meta">
                <span class="badge">SIM ${esc(s.slot)}</span>
                <strong>${esc(s.carrier || ("SIM " + s.slot))}</strong>
                ${srcLabel}
              </div>
              ${
                num
                  ? `<div class="sim-num mono">${esc(num)}</div>`
                  : `<div class="sim-num empty">${esc(hint)}</div>`
              }
            </div>
            <div class="sim-actions">
              ${num ? `<button class="btn btn-accent btn-sm" onclick="copySimIdx(${i})">📋 Copy</button>` : ussdBtn}
              <button class="btn btn-sm" onclick="editSimIdx(${i})" title="নম্বর সেভ/এডিট করুন">✏️ ${num ? "Edit" : "Save"}</button>
            </div>
          </div>`;
        })
        .join("");
    }
  }

  // 3. Render compact strip in Comms → Calls & Dialer
  const strip = document.getElementById("comms-sim-strip");
  if (strip) {
    const withNum = state.sims
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => (s.number || "").trim());
    if (withNum.length) {
      strip.style.display = "flex";
      strip.innerHTML =
        `<span class="muted">My Number:</span>` +
        withNum
          .map(
            ({ s, i }) =>
              `<button class="btn btn-sm" onclick="copySimIdx(${i})" title="Click to copy">📱 SIM ${esc(s.slot)} (${esc(s.carrier)}): <span class="mono">${esc(s.number)}</span> 📋</button>`
          )
          .join("");
    } else {
      strip.innerHTML = "";
      strip.style.display = "none";
    }
  }
}

async function loadPhoneNumbers(silent = false) {
  const expectedSerial = state.selectedSerial || "";
  const data = await callApi("get_phone_numbers", [], silent ? "" : "Detect SIM & Phone Number", !silent, !silent);
  if (!data) return;
  if (expectedSerial && state.selectedSerial && state.selectedSerial !== expectedSerial) return;
  state.lastSimSerial = data.serial || expectedSerial;
  renderSimCards(data.sims || []);
}

function copyPrimaryPhone() {
  if (state.primaryPhone) copyText(state.primaryPhone);
}

function copySimIdx(i) {
  const s = (state.sims || [])[i];
  if (s && s.number) copyText(s.number);
}

async function dialUssdIdx(i) {
  const s = (state.sims || [])[i];
  if (s && s.ussd) {
    await callApi("dial_number", [s.ussd], `USSD ${s.ussd}`);
  }
}

async function editSimIdx(i) {
  const s = (state.sims || [])[i];
  if (!s) return;
  await editSimNumber(s.slot, s.number || "", s.carrier || "");
}

async function editSimNumber(slot = 1, currentVal = "", carrier = "") {
  const label = carrier ? `SIM ${slot} (${carrier})` : `SIM ${slot}`;
  const val = prompt(
    `${label}-এর মোবাইল নম্বর লিখুন (এই ডিভাইসের জন্য স্থায়ীভাবে সেভ থাকবে; খালি রেখে OK দিলে মুছে যাবে):`,
    currentVal || ""
  );
  if (val === null) return;
  const data = await callApi("save_phone_number", [Number(slot) || 1, val.trim()], `Save ${label} Number`);
  if (data && data.sims) {
    renderSimCards(data.sims);
  }
}

/* ─── Macros & Physical Action Recorder ─── */
async function loadMacros() {
  const data = await callApi("list_macros", [], "", false);
  if (!data) return;
  state.macros = data.macros || {};
  const sel = document.getElementById("macro-select");
  const prev = sel.value;
  sel.innerHTML = (data.names || []).map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  if (prev && state.macros[prev]) sel.value = prev;
  if (!state.recording && sel.value && !state.macroSteps.length) {
    onSelectMacro();
  }
}
function onSelectMacro() {
  const name = document.getElementById("macro-select").value;
  document.getElementById("macro-name").value = name;
  state.macroSteps = JSON.parse(JSON.stringify(state.macros[name] || []));
  renderMacroSteps();
}
function renderMacroSteps() {
  document.getElementById("macro-step-count").textContent = `${state.macroSteps.length} steps`;
  document.getElementById("macro-tbody").innerHTML = state.macroSteps
    .map(
      (s, i) => `<tr>
      <td class="mono muted">${i + 1}</td>
      <td><span class="badge">${esc(s.action)}</span></td>
      <td class="mono">${esc(s.arg || "")}</td>
      <td class="muted">${esc(s.note || "")}</td>
      <td>
        <button class="btn btn-sm" onclick="moveStep(${i},-1)">⬆</button>
        <button class="btn btn-sm" onclick="moveStep(${i},1)">⬇</button>
        <button class="btn btn-danger btn-sm" onclick="removeStep(${i})">✕</button>
      </td>
    </tr>`
    )
    .join("");
}
function addMacroStep() {
  const action = document.getElementById("step-act").value;
  const arg = document.getElementById("step-arg").value.trim();
  state.macroSteps.push({ action, arg, note: "" });
  document.getElementById("step-arg").value = "";
  renderMacroSteps();
}
function removeStep(i) { state.macroSteps.splice(i, 1); renderMacroSteps(); }
function moveStep(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= state.macroSteps.length) return;
  [state.macroSteps[i], state.macroSteps[j]] = [state.macroSteps[j], state.macroSteps[i]];
  renderMacroSteps();
}
function clearMacroSteps() { state.macroSteps = []; renderMacroSteps(); }

async function saveMacro() {
  const name = (document.getElementById("macro-name").value || "").trim();
  if (!name) return toast("ম্যাক্রোর নাম দিন।", true);
  await callApi("save_macro", [name, state.macroSteps], "Save Macro");
  await loadMacros();
  document.getElementById("macro-select").value = name;
}
async function deleteMacro() {
  const name = (document.getElementById("macro-name").value || "").trim();
  if (!name || !confirm(`'${name}' ডিলিট করবেন?`)) return;
  await callApi("delete_macro", [name], "Delete Macro");
  state.macroSteps = [];
  await loadMacros();
  renderMacroSteps();
}
async function runMacro() {
  const loop = parseInt(document.getElementById("macro-loop").value || "1", 10);
  await callApi("run_macro", [state.macroSteps, loop], `Run Macro (x${loop})`);
}
async function stopMacro() { await callApi("stop_macro", [], "Stop Macro"); }

async function toggleRecording() {
  const btn = document.getElementById("btn-rec");
  const pauseBtn = document.getElementById("btn-rec-pause");
  const badge = document.getElementById("rec-badge");
  const statusEl = document.getElementById("rec-status");

  if (state.recording) {
    await callApi("stop_recording", [], "Stop Recording");
    state.recording = false;
    state.paused = false;
    if (state.recPollTimer) clearInterval(state.recPollTimer);
    btn.textContent = "🔴 Start Recording";
    pauseBtn.disabled = true;
    pauseBtn.textContent = "⏸️ Pause";
    badge.style.display = "none";
    statusEl.textContent = `⏹️ Stopped — মোট ${state.macroSteps.length}টি স্টেপ রেডি।`;
    return;
  }

  const mode = document.getElementById("rec-mode").value;
  const waits = document.getElementById("rec-waits").checked;
  const apps = document.getElementById("rec-apps").checked;
  const res = await callApi("start_recording", [mode, waits, apps], "Start Recording");
  if (!res) return;

  state.recording = true;
  state.paused = false;
  btn.textContent = "⏹️ Stop Recording";
  pauseBtn.disabled = false;
  badge.style.display = "inline-flex";
  document.getElementById("rec-badge-txt").textContent = "REC";
  statusEl.textContent = `🔴 Recording (${mode}) | ${res.screen}`;

  state.recPollTimer = setInterval(async () => {
    const p = await callApi("poll_recorder_events", [], "", false);
    if (!p || !p.events || !p.events.length) return;
    let changed = false;
    for (const ev of p.events) {
      if (ev.kind === "step") {
        state.macroSteps.push(ev.step);
        logConsole("Recorded", ev.log);
        changed = true;
      } else if (ev.kind === "replace") {
        while (state.macroSteps.length && state.macroSteps[state.macroSteps.length - 1].action === "wait") {
          state.macroSteps.pop();
        }
        if (state.macroSteps.length && ["tap", "tap_text"].includes(state.macroSteps[state.macroSteps.length - 1].action)) {
          state.macroSteps[state.macroSteps.length - 1] = ev.step;
        } else {
          state.macroSteps.push(ev.step);
        }
        logConsole("Recorded", ev.log);
        changed = true;
      } else if (ev.kind === "status") {
        statusEl.textContent = ev.message;
        logConsole("Recorder", ev.message);
      }
    }
    if (changed) {
      renderMacroSteps();
      const box = document.querySelector("#page-macro .table-box");
      if (box) box.scrollTop = box.scrollHeight;
    }
  }, 260);
}

async function togglePauseRecording() {
  const res = await callApi("toggle_pause_recording", [], "Recorder Pause");
  if (!res) return;
  state.paused = !!res.paused;
  document.getElementById("btn-rec-pause").textContent = state.paused ? "▶️ Resume" : "⏸️ Pause";
  document.getElementById("rec-badge-txt").textContent = state.paused ? "PAUSED" : "REC";
}

/* ─── Full Parity Handlers (Logs, Apps, Files, Monitor, scrcpy, WiFi & Live) ─── */
async function saveConsole() {
  const txt = document.getElementById("console-body").innerText || "";
  await callApi("save_console_dialog", [txt], "Save Console");
}
async function checkConnection() { await callApi("check_connection", [], "Check Connection"); }
async function waitDevice() {
  toast("ডিভাইসের জন্য অপেক্ষা করা হচ্ছে...");
  await callApi("wait_device", [], "Wait Device");
  refreshDevices();
}
async function fetchLogs() {
  const lines = parseInt(document.getElementById("log-lines").value || "150", 10);
  const mode = document.getElementById("log-mode").value;
  const kw = document.getElementById("log-kw").value;
  await callApi("fetch_logs", [lines, mode, kw], `Logcat (${mode})`, false);
  toast("Logcat কনসোলে লোড হয়েছে!");
}

async function batchInstallApk() { await callApi("batch_install_dialog", [], "Batch Install APKs"); }
async function currentActivity() { await callApi("current_activity", [], "Current Activity"); }
async function packageInfo() {
  const pkg = document.getElementById("sel-pkg").value.trim();
  if (!pkg) return toast("প্রথমে একটি অ্যাপ সিলেক্ট করুন।", true);
  await callApi("package_info", [pkg], `Info: ${pkg}`, false);
  toast("Package Info কনসোলে লোড হয়েছে!");
}
async function backupApk() {
  const pkg = document.getElementById("sel-pkg").value.trim();
  if (!pkg) return toast("প্রথমে একটি অ্যাপ সিলেক্ট করুন।", true);
  await callApi("backup_apk_dialog", [pkg], "Backup APK");
}

async function burstScreenshots() { await callApi("burst_screenshots", [3], "Burst Screenshots"); }
async function recordScreen() {
  const sec = parseInt(document.getElementById("rec-sec").value || "10", 10);
  toast(`🎥 ${sec}s স্ক্রিন রেকর্ডিং শুরু হচ্ছে...`);
  await callApi("record_screen", [sec], "Record Screen");
}
async function syncFolder(dir) {
  const rem = document.getElementById("exp-path").value.trim() || "/sdcard/Download";
  const res = await callApi("sync_folder_dialog", [rem, dir], `Sync Folder (${dir})`);
  if (res && res.started) startTransferPoll();
}
function startAutoSyncPollLoop() {
  if (state.autoSyncTimer) return;
  state.autoSyncTimer = setInterval(async () => {
    const res = await callApi("poll_auto_sync", [], "", false, false);
    if (!res) return;
    if (res.events && res.events.length) {
      logConsole("Auto-Sync", res.events.join("\n"));
    }
    if (!res.active) stopAutoSyncPollLoop();
  }, 1200);
}
function stopAutoSyncPollLoop() {
  if (state.autoSyncTimer) {
    clearInterval(state.autoSyncTimer);
    state.autoSyncTimer = null;
  }
}
async function toggleAutoSync() {
  const rem = document.getElementById("exp-path").value.trim() || "/sdcard/Download";
  const res = await callApi("toggle_auto_sync", [rem], "Auto-Sync Watch");
  if (!res) return;
  const btn = document.getElementById("btn-autosync");
  btn.textContent = res.active ? "⏹️ Stop Auto-Sync" : "👁️ Auto-Sync Watch";
  btn.className = res.active ? "btn btn-danger" : "btn btn-blue";
  if (res.active) startAutoSyncPollLoop();
  else stopAutoSyncPollLoop();
}

async function appsWithSize() {
  const data = await callApi("apps_with_size", [], "Apps & APK Paths", false);
  if (data !== null) document.getElementById("monitor-out").textContent = data;
}
function toggleLivePerf() {
  const btn = document.getElementById("btn-live-perf");
  if (state.livePerfTimer) {
    clearInterval(state.livePerfTimer);
    state.livePerfTimer = null;
    btn.textContent = "▶️ Start Live Monitor";
    btn.className = "btn btn-accent btn-sm";
    toast("Live Performance Monitor বন্ধ হয়েছে।");
    return;
  }
  btn.textContent = "⏹️ Stop Live Monitor";
  btn.className = "btn btn-danger btn-sm";
  const tick = async () => {
    const snap = await callApi("perf_snapshot", [], "", false);
    if (snap) document.getElementById("monitor-out").textContent = snap;
  };
  tick();
  state.livePerfTimer = setInterval(tick, 3000);
  toast("Live Performance Monitor চালু হয়েছে!");
}

async function multiDeviceScrcpy() { await callApi("multi_device_scrcpy", [], "Multi-Device scrcpy"); }
async function scrcpyRecord() { await callApi("scrcpy_record_dialog", [], "scrcpy Record"); }
async function scrcpyRecordMic() { await callApi("scrcpy_record_mic", [], "Record Phone Mic"); }
async function scrcpySessions() { await callApi("scrcpy_sessions", [], "scrcpy Sessions"); }
async function checkScrcpy() { await callApi("check_scrcpy", [], "Check scrcpy"); }

/* ─── WiFi & Live Tab ─── */
async function pairWifiManual() {
  const ip = document.getElementById("pair-ip").value.trim();
  const code = document.getElementById("pair-code").value.trim();
  await callApi("pair_wifi_manual", [ip, code], "Pair Wi-Fi");
  refreshDevices();
}
async function startQrPairing() {
  const res = await callApi("start_qr_pairing", [], "QR Pairing");
  if (!res) return;
  document.getElementById("qr-img").src = res.data_url;
  document.getElementById("qr-status").textContent = res.status;
  document.getElementById("qr-box").style.display = "flex";
  if (state.qrTimer) clearInterval(state.qrTimer);
  state.qrTimer = setInterval(async () => {
    const st = await callApi("poll_qr_status", [], "", false);
    if (!st) return;
    document.getElementById("qr-status").textContent = st.status;
    if (st.connected) {
      clearInterval(state.qrTimer);
      state.qrTimer = null;
      toast(st.status);
      refreshDevices();
    }
  }, 1000);
}
async function stopQrPairing() {
  if (state.qrTimer) { clearInterval(state.qrTimer); state.qrTimer = null; }
  document.getElementById("qr-box").style.display = "none";
  await callApi("stop_qr_pairing", [], "", false);
}
async function enableTcpip() {
  const port = parseInt(document.getElementById("tcpip-port").value || "5555", 10);
  await callApi("enable_tcpip", [port], `TCP/IP ${port}`);
}
async function transferAllTcpip() {
  const port = parseInt(document.getElementById("tcpip-port").value || "5555", 10);
  await callApi("transfer_all_tcpip", [port], "Transfer All USB → Wi-Fi");
  refreshDevices();
}
async function wifiStatus() { await callApi("wifi_status", [], "Wi-Fi Diagnostics"); }

function startLogcatPollLoop() {
  if (state.logcatTimer) return;
  state.logcatTimer = setInterval(async () => {
    const res = await callApi("poll_logcat", [], "", false, false);
    if (!res) return;
    if (res.lines && res.lines.length) {
      logConsole("Live Logcat", res.lines.join("\n"));
    }
  }, 500);
}
function stopLogcatPollLoop() {
  if (state.logcatTimer) {
    clearInterval(state.logcatTimer);
    state.logcatTimer = null;
  }
}
async function toggleLiveLogcat() {
  const btn = document.getElementById("btn-live-logcat");
  if (state.liveLogcat) {
    await callApi("stop_live_logcat", [], "Stop Live Logcat");
    state.liveLogcat = false;
    stopLogcatPollLoop();
    btn.textContent = "▶️ Start Live Logcat";
    btn.className = "btn btn-accent";
    return;
  }
  const kw = document.getElementById("live-log-kw").value.trim();
  const res = await callApi("start_live_logcat", [kw], "Start Live Logcat");
  if (!res) return;
  state.liveLogcat = true;
  btn.textContent = "⏹️ Stop Live Logcat";
  btn.className = "btn btn-danger";
  startLogcatPollLoop();
}
async function saveTimeout() {
  const sec = parseInt(document.getElementById("adb-timeout").value || "30", 10);
  await callApi("set_timeout", [sec], "Command Timeout");
}

/* ─── Auto-Reconnect Watchdog & Fast 5555 Scan ─── */
async function toggleWatchdog() {
  const keepAlive = document.getElementById("wd-keepalive").checked;
  const res = await callApi("toggle_watchdog", [keepAlive], "Auto-Reconnect Watchdog");
  if (!res) return;
  const btn = document.getElementById("btn-watchdog");
  const hudPill = document.getElementById("hud-wd-pill");
  const badge = document.getElementById("wd-status");
  if (res.running) {
    btn.textContent = "⏹️ Stop Watchdog";
    btn.className = "btn btn-danger";
    hudPill.style.display = "inline-flex";
    badge.textContent = res.status || "Watching";
    startWatchdogPoll();
  } else {
    btn.textContent = "🛡️ Enable Auto-Reconnect";
    btn.className = "btn btn-accent";
    hudPill.style.display = "none";
    badge.textContent = "Idle";
    if (state.wdTimer) { clearInterval(state.wdTimer); state.wdTimer = null; }
  }
}

function startWatchdogPoll() {
  if (state.wdTimer) return;
  state.wdTimer = setInterval(async () => {
    const res = await callApi("poll_watchdog", [], "", false);
    if (!res) return;
    document.getElementById("wd-status").textContent = res.status || "Idle";
    for (const ev of (res.events || [])) {
      if (ev.kind === "reconnected") {
        toast(`🟢 Auto-Reconnected: ${ev.target}`);
        refreshDevices();
      } else if (ev.kind === "event" && ev.log) {
        logConsole("Watchdog", ev.log);
      }
    }
  }, 2000);
}

async function scanConnect5555() {
  const port = parseInt(document.getElementById("tcpip-port").value || "5555", 10);
  toast(`🔍 লোকাল নেটওয়ার্কে পোর্ট ${port} স্ক্যান হচ্ছে...`);
  const res = await callApi("scan_connect_5555", [port], `Scan Port ${port}`);
  if (res) refreshDevices();
}

async function installCompanionApk() {
  toast("📲 Companion APK ইনস্টল ও সেটআপ হচ্ছে...");
  await callApi("install_companion_apk", [], "Install Companion APK");
}

async function setupCompanionPermissions() {
  await callApi("setup_companion_permissions", [], "Optimize Companion");
}

/* ─── Live Battery Health, Wattage & Pure-SVG Graph (Display Only) ─── */
state.batHistory = [];
async function refreshBatteryDetail() {
  const d = await callApi("battery_telemetry_detail", [], "", false, false);
  if (!d) return;
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt("bat-status-badge", `${d.status_text || "OK"}${d.charging ? " ⚡" : ""} • ${d.ts || ""}`);
  setTxt("bat-v-lvl", d.level !== null && d.level !== undefined ? `${d.level}%` : "--%");
  setTxt("bat-v-watt", d.wattage_w !== null && d.wattage_w !== undefined ? `${d.wattage_w} W` : "-- W");
  setTxt("bat-v-ma", d.current_ma !== null && d.current_ma !== undefined ? `${d.current_ma} mA` : "-- mA");
  setTxt("bat-v-volt", d.voltage_v !== null && d.voltage_v !== undefined ? `${d.voltage_v} V` : "-- V");
  setTxt("bat-v-temp", d.temp_c !== null && d.temp_c !== undefined ? `${d.temp_c} °C` : "-- °C");
  setTxt("bat-v-health", d.health || "--");
  const capParts = [];
  if (d.charge_mah) capParts.push(`${d.charge_mah} mAh`);
  if (d.full_mah) capParts.push(`/ ${d.full_mah} mAh`);
  if (d.cycle_count !== null && d.cycle_count !== undefined) capParts.push(`• Cycles: ${d.cycle_count}`);
  if (d.technology) capParts.push(`• ${d.technology}`);
  setTxt("bat-cap-info", capParts.join(" "));

  state.batHistory.push({
    level: d.level ?? 0,
    watt: d.wattage_w ?? 0,
    temp: d.temp_c ?? 0,
  });
  if (state.batHistory.length > 30) state.batHistory.shift();
  renderBatteryGraph();
}

function renderBatteryGraph() {
  const svg = document.getElementById("bat-svg");
  if (!svg) return;
  const hist = state.batHistory || [];
  const W = 600, H = 110, pad = 10;
  if (!hist.length) {
    svg.innerHTML = `<text x="300" y="60" fill="#64748b" font-size="12" text-anchor="middle">Click 'Read Now' or 'Live Graph' to plot live Battery, Power & Temp</text>`;
    return;
  }
  const maxW = Math.max(15, ...hist.map((h) => h.watt || 0));
  const toPts = (getter, maxVal) =>
    hist
      .map((h, i) => {
        const x = hist.length === 1 ? W / 2 : pad + (i / (hist.length - 1)) * (W - pad * 2);
        const y = H - pad - (Math.min(maxVal, Math.max(0, getter(h))) / maxVal) * (H - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");

  const grid = [0.25, 0.5, 0.75]
    .map((r) => `<line x1="${pad}" y1="${(H * r).toFixed(1)}" x2="${W - pad}" y2="${(H * r).toFixed(1)}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="4 4"/>`)
    .join("");
  svg.innerHTML = `
    ${grid}
    <polyline fill="none" stroke="#10b981" stroke-width="2.2" points="${toPts((h) => h.level, 100)}" />
    <polyline fill="none" stroke="#22d3ee" stroke-width="2.2" points="${toPts((h) => h.watt, maxW)}" />
    <polyline fill="none" stroke="#f59e0b" stroke-width="2.0" points="${toPts((h) => h.temp, 60)}" />
  `;
}

function toggleBatteryGraph() {
  const btn = document.getElementById("btn-bat-graph");
  if (state.batGraphTimer) {
    clearInterval(state.batGraphTimer);
    state.batGraphTimer = null;
    btn.textContent = "▶️ Live Graph";
    btn.className = "btn btn-accent btn-sm";
    return;
  }
  btn.textContent = "⏹️ Stop Graph";
  btn.className = "btn btn-danger btn-sm";
  refreshBatteryDetail();
  state.batGraphTimer = setInterval(refreshBatteryDetail, 3000);
}

/* ─── Visual Live UI Inspector & Click-to-Macro Picker ─── */
state.inspector = { width: 1080, height: 2400, nodes: [], hover: null, selected: null };

async function openUiInspector() {
  document.getElementById("insp-modal").classList.add("open");
  await refreshUiInspector();
}
function closeUiInspector() {
  document.getElementById("insp-modal").classList.remove("open");
}
async function refreshUiInspector() {
  document.getElementById("insp-info").textContent = "⏳ স্ক্রিনশট ও UI এলিমেন্ট লোড হচ্ছে...";
  const data = await callApi("capture_ui_inspector", [], "UI Inspector", false);
  if (!data) {
    document.getElementById("insp-info").textContent = "❌ UI Inspector লোড করা যায়নি।";
    return;
  }
  state.inspector = {
    width: data.width || 1080,
    height: data.height || 2400,
    nodes: data.nodes || [],
    hover: null,
    selected: null,
  };
  const img = document.getElementById("insp-img");
  img.onload = () => drawInspectorOverlay();
  img.src = data.data_url;
  document.getElementById("insp-title").textContent = `🎯 Visual UI Inspector (${data.width}x${data.height} • ${state.inspector.nodes.length} elements)`;
  document.getElementById("insp-info").textContent = "স্ক্রিনের যেকোনো বাটনের ওপর মাউস নিন অথবা ক্লিক করুন...";
}

function _inspectorHitTest(clientX, clientY) {
  const canvas = document.getElementById("insp-canvas");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const px = Math.round(((clientX - rect.left) / rect.width) * state.inspector.width);
  const py = Math.round(((clientY - rect.top) / rect.height) * state.inspector.height);
  const hit = state.inspector.nodes.find((n) => px >= n.x1 && px <= n.x2 && py >= n.y1 && py <= n.y2) || null;
  return { px, py, node: hit };
}

function drawInspectorOverlay() {
  const img = document.getElementById("insp-img");
  const canvas = document.getElementById("insp-canvas");
  if (!img || !canvas) return;
  const w = img.clientWidth || 300;
  const h = img.clientHeight || 600;
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, w, h);

  const sx = w / Math.max(1, state.inspector.width);
  const sy = h / Math.max(1, state.inspector.height);

  const drawBox = (n, stroke, fill) => {
    if (!n) return;
    const x = n.x1 * sx, y = n.y1 * sy, bw = (n.x2 - n.x1) * sx, bh = (n.y2 - n.y1) * sy;
    ctx.fillStyle = fill;
    ctx.fillRect(x, y, bw, bh);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, bw, bh);
  };
  drawBox(state.inspector.hover, "#22d3ee", "rgba(34,211,238,0.16)");
  drawBox(state.inspector.selected, "#10b981", "rgba(16,185,129,0.24)");
}

function _renderInspectorInfo(hit) {
  const el = document.getElementById("insp-info");
  if (!el || !hit) return;
  const n = hit.node;
  if (!n) {
    el.innerHTML = `<strong>Point:</strong> (${hit.px}, ${hit.py})<br/><span class="muted">No XML node at this point</span>`;
    return;
  }
  const label = n.text || n.desc || n.resource_id || "(no label)";
  el.innerHTML = `
    <div><strong>Element:</strong> ${esc(n.class)} ${n.clickable ? '<span class="badge">Clickable</span>' : ""}</div>
    <div><strong>Text:</strong> ${esc(n.text || "-")}</div>
    <div><strong>Desc:</strong> ${esc(n.desc || "-")}</div>
    <div><strong>ID:</strong> ${esc(n.resource_id || "-")}</div>
    <div><strong>Center:</strong> (${n.cx}, ${n.cy}) &nbsp;|&nbsp; <strong>Bounds:</strong> [${n.x1},${n.y1}][${n.x2},${n.y2}]</div>
    <div class="muted" style="margin-top:4px;">Smart Query: ${esc(label)}</div>
  `;
}

function initUiInspectorCanvas() {
  const canvas = document.getElementById("insp-canvas");
  if (!canvas) return;
  canvas.addEventListener("mousemove", (e) => {
    const hit = _inspectorHitTest(e.clientX, e.clientY);
    if (!hit) return;
    state.inspector.hover = hit.node;
    drawInspectorOverlay();
    if (!state.inspector.selected) _renderInspectorInfo(hit);
  });
  canvas.addEventListener("click", (e) => {
    const hit = _inspectorHitTest(e.clientX, e.clientY);
    if (!hit) return;
    state.inspector.selected = hit.node || { cx: hit.px, cy: hit.py, x1: hit.px - 10, y1: hit.py - 10, x2: hit.px + 10, y2: hit.py + 10, text: "", desc: "", resource_id: "", class: "Point", clickable: false };
    drawInspectorOverlay();
    _renderInspectorInfo(hit);
  });
}

async function inspectorTapPhone() {
  const sel = state.inspector.selected;
  if (!sel) return toast("প্রথমে স্ক্রিনে একটি জায়গায় ক্লিক করুন।", true);
  await callApi("tap", [sel.cx, sel.cy], `Inspector Tap (${sel.cx},${sel.cy})`);
  setTimeout(refreshUiInspector, 650);
}

function inspectorAddMacro(kind) {
  const sel = state.inspector.selected;
  if (!sel) return toast("প্রথমে স্ক্রিনে একটি এলিমেন্ট সিলেক্ট করুন।", true);
  const label = (sel.text || sel.desc || sel.resource_id || "").trim();
  let step;
  if (kind === "smart" && label) {
    step = { action: "tap_text", arg: label, note: `UI Inspector: ${sel.class} (at ${sel.cx},${sel.cy})` };
  } else {
    step = { action: "tap", arg: `${sel.cx},${sel.cy}`, note: `UI Inspector: ${label || sel.class}` };
  }
  state.macroSteps.push(step);
  renderMacroSteps();
  toast(`➕ Macro-তে যোগ হয়েছে: ${step.action} → ${step.arg}`);
}

/* ─── Startup Bootstrap ─── */
window.addEventListener("pywebviewready", async () => {
  const savedTheme = localStorage.getItem("adb_theme") || "emerald";
  const dot = document.querySelector(`.theme-dot[data-theme="${savedTheme}"]`);
  setTheme(savedTheme, dot);

  initDropZone();
  initUiInspectorCanvas();

  logConsole("⚡ ADB Control Pro", "Ultra-Modern PyWebView UI Ready!");
  await refreshDevices();
  await loadMacros();
  // Auto-Start Auto-Reconnect Watchdog on launch
  await toggleWatchdog();

  // Gentle live device list + HUD telemetry refresh every 12s
  state.telemetryTimer = setInterval(async () => {
    if (!state.recording) {
      await refreshDevices(false);
    }
  }, 12000);
});
