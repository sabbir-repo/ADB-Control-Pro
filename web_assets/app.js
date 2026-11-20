/* ADB Control Pro — PyWebView Frontend Controller */

const state = {
  apps: [],
  explorerEntries: [],
  explorerSelected: null,
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
    .replace(/"/g, "&quot;");
}

function nowTime() {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

function logConsole(title, body = "", isErr = false) {
  const el = document.getElementById("console-body");
  const div = document.createElement("div");
  div.className = "log-entry";
  div.innerHTML = `<div class="${isErr ? "log-err" : "log-head"}">[${nowTime()}] ${esc(title)}</div>${body ? `<div>${esc(body)}</div>` : ""}`;
  el.appendChild(div);
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

async function callApi(fnName, args = [], label = "", showToast = true) {
  try {
    const res = await api()[fnName](...args);
    if (!res || !res.ok) {
      const err = (res && res.error) || "Unknown error";
      logConsole(`❌ ${label || fnName}`, err, true);
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
    logConsole(`❌ ${label || fnName}`, e.message || String(e), true);
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
  if (pageId === "profiles") loadProfiles();
  if (pageId === "macro") loadMacros();
}

function switchComms(sub, btn) {
  document.querySelectorAll(".subtab-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("comms-notif").style.display = sub === "notif" ? "block" : "none";
  document.getElementById("comms-sms").style.display = sub === "sms" ? "flex" : "none";
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
async function refreshDevices() {
  const data = await callApi("list_devices", [], "", false);
  if (!data) return;
  const sel = document.getElementById("device-select");
  sel.innerHTML = "";
  if (!data.devices.length) {
    sel.innerHTML = `<option value="">No Devices Found</option>`;
  } else {
    for (const d of data.devices) {
      const opt = document.createElement("option");
      opt.value = d.serial;
      opt.textContent = `${d.serial} (${d.state})`;
      if (d.serial === data.selected) opt.selected = true;
      sel.appendChild(opt);
    }
  }
  await refreshTelemetry();
}

async function onSelectDevice() {
  const serial = document.getElementById("device-select").value;
  await callApi("select_device", [serial], "Select Device", true);
  await refreshTelemetry();
}

async function refreshTelemetry() {
  const data = await callApi("device_telemetry", [], "", false);
  const dot = document.getElementById("hud-dot");
  const modelEl = document.getElementById("hud-model");
  const batPill = document.getElementById("hud-bat-pill");
  const connPill = document.getElementById("hud-conn-pill");
  if (!data || !data.connected) {
    dot.classList.remove("online");
    modelEl.textContent = "Offline / No Device";
    batPill.style.display = "none";
    connPill.style.display = "none";
    return;
  }
  dot.classList.add("online");
  modelEl.textContent = `${data.model} • Android ${data.android}`;
  if (data.battery !== null && data.battery !== undefined) {
    batPill.style.display = "inline-flex";
    const tmp = data.temp !== null ? ` • ${data.temp}°C` : "";
    document.getElementById("hud-bat").textContent = `${data.battery}%${data.charging ? " ⚡" : ""}${tmp}`;
  }
  connPill.style.display = "inline-flex";
  document.getElementById("hud-conn").textContent = data.connection;
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
  await callApi("exec_shell", [cmd], `Shell: ${cmd}`, false);
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

/* ─── Apps Tab ─── */
async function loadApps() {
  const data = await callApi("list_launcher_apps", [], "Load Apps");
  if (!data) return;
  state.apps = data;
  renderApps();
}
function renderApps() {
  const q = (document.getElementById("app-filter").value || "").toLowerCase();
  const tbody = document.getElementById("apps-tbody");
  const filtered = state.apps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.package.toLowerCase().includes(q));
  document.getElementById("apps-count").textContent = `${filtered.length} apps`;
  tbody.innerHTML = filtered
    .map((a) => `<tr onclick="selectApp('${esc(a.package)}', this)"><td><strong>${esc(a.name)}</strong></td><td class="mono muted">${esc(a.package)}</td></tr>`)
    .join("");
}
function selectApp(pkg, tr) {
  document.querySelectorAll("#apps-tbody tr").forEach((r) => r.classList.remove("selected"));
  tr.classList.add("selected");
  document.getElementById("sel-pkg").value = pkg;
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
async function explorerGo(dirPath) {
  const data = await callApi("list_dir", [dirPath || "/sdcard"], "", false);
  if (!data) return;
  document.getElementById("exp-path").value = data.path;
  document.getElementById("exp-filter").value = "";
  state.explorerEntries = data.entries || [];
  state.explorerSelected = null;
  renderExplorer();
}
function explorerUp() {
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  if (!cur || cur === "/") return explorerGo("/");
  const idx = cur.lastIndexOf("/");
  explorerGo(idx <= 0 ? "/" : cur.slice(0, idx));
}
function renderExplorer() {
  const q = (document.getElementById("exp-filter").value || "").toLowerCase();
  const tbody = document.getElementById("exp-tbody");
  const list = state.explorerEntries.filter((e) => !q || e.name.toLowerCase().includes(q));
  document.getElementById("exp-count").textContent = `${list.length} / ${state.explorerEntries.length} items`;
  tbody.innerHTML = list
    .map((e, i) => {
      const ico = fileIcon(e.name, e.type);
      return `<tr data-idx="${i}" onclick="selectExp(${i}, this)" ondblclick="dblClickExp(${i})">
        <td>${ico} ${esc(e.name)}</td>
        <td class="mono muted">${esc(e.size)}</td>
        <td class="mono muted">${esc(e.modified)}</td>
      </tr>`;
    })
    .join("");
  state._expFiltered = list;
}
function selectExp(i, tr) {
  document.querySelectorAll("#exp-tbody tr").forEach((r) => r.classList.remove("selected"));
  tr.classList.add("selected");
  state.explorerSelected = (state._expFiltered || [])[i] || null;
}
function dblClickExp(i) {
  const item = (state._expFiltered || [])[i];
  if (!item) return;
  state.explorerSelected = item;
  if (item.type === "dir") explorerGo(item.path);
  else explorerDownload();
}
async function explorerDownload() {
  if (!state.explorerSelected) return toast("ডাউনলোডের জন্য একটি ফাইল/ফোল্ডার সিলেক্ট করুন।", true);
  await callApi("download_item_dialog", [state.explorerSelected.path, state.explorerSelected.type === "dir"], "Download");
}
async function explorerUpload() {
  const cur = document.getElementById("exp-path").value || "/sdcard";
  const res = await callApi("upload_here_dialog", [cur], "Upload");
  if (res) explorerGo(cur);
}
async function explorerNewFolder() {
  const name = prompt("নতুন ফোল্ডারের নাম লিখুন:");
  if (!name || !name.trim()) return;
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  const full = cur === "/" ? `/${name.trim()}` : `${cur}/${name.trim()}`;
  const res = await callApi("mkdir", [full], "Create Folder");
  if (res !== null) explorerGo(cur || "/");
}
async function explorerRename() {
  if (!state.explorerSelected) return toast("রিনেম করার জন্য আইটেম সিলেক্ট করুন।", true);
  const newName = prompt("নতুন নাম লিখুন:", state.explorerSelected.name);
  if (!newName || !newName.trim() || newName.trim() === state.explorerSelected.name) return;
  const cur = (document.getElementById("exp-path").value || "/sdcard").replace(/\/+$/, "");
  const dst = cur === "/" ? `/${newName.trim()}` : `${cur}/${newName.trim()}`;
  const res = await callApi("rename_path", [state.explorerSelected.path, dst], "Rename");
  if (res !== null) explorerGo(cur || "/");
}
async function explorerDelete() {
  if (!state.explorerSelected) return toast("ডিলিট করার জন্য আইটেম সিলেক্ট করুন।", true);
  if (!confirm(`আপনি কি নিশ্চিতভাবে ডিলিট করতে চান?\n${state.explorerSelected.path}`)) return;
  const cur = document.getElementById("exp-path").value || "/sdcard";
  const res = await callApi("delete_path", [state.explorerSelected.path], "Delete");
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
  const tbody = document.getElementById("prof-tbody");
  tbody.innerHTML = (data.profiles || [])
    .map(
      (p) => `<tr onclick='fillProfile(${JSON.stringify(p).replace(/'/g, "&#39;")})'>
      <td><strong>${esc(p.name)}</strong></td>
      <td>${esc(p.lock_type)}</td>
      <td class="muted">${esc(p.unlock_process)}</td>
      <td><button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteProfile('${esc(p.fingerprint)}')">🗑️</button></td>
    </tr>`
    )
    .join("");
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
  document.getElementById("notif-tbody").innerHTML = items
    .map(
      (n) => `<tr onclick="copyText('${esc(n.text || n.title)}')">
      <td class="mono muted">${esc(n.package)}</td>
      <td><strong>${esc(n.title)}</strong></td>
      <td>${esc(n.text)}</td>
    </tr>`
    )
    .join("");
}
async function loadSms() {
  const items = await callApi("list_sms", [], "SMS Inbox");
  if (!items) return;
  document.getElementById("sms-tbody").innerHTML = items
    .map(
      (s) => `<tr onclick="copyText('${esc(s.otp || s.body)}')">
      <td><strong>${esc(s.address)}</strong></td>
      <td>${s.otp ? `<span class="badge">${esc(s.otp)}</span>` : "-"}</td>
      <td>${esc(s.body)}</td>
      <td class="mono muted">${esc(s.date)}</td>
    </tr>`
    )
    .join("");
}
async function loadCalls() {
  const items = await callApi("list_call_logs", [], "Call Logs");
  if (!items) return;
  document.getElementById("calls-tbody").innerHTML = items
    .map(
      (c) => `<tr onclick="document.getElementById('dial-num').value='${esc(c.number)}'">
      <td class="mono"><strong>${esc(c.number)}</strong></td>
      <td>${esc(c.name)}</td>
      <td>${esc(c.type)}</td>
      <td class="mono muted">${esc(c.duration)}</td>
      <td class="mono muted">${esc(c.date)}</td>
    </tr>`
    )
    .join("");
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
  await callApi("sync_folder_dialog", [rem, dir], `Sync Folder (${dir})`);
}
async function toggleAutoSync() {
  const rem = document.getElementById("exp-path").value.trim() || "/sdcard/Download";
  const res = await callApi("toggle_auto_sync", [rem], "Auto-Sync Watch");
  if (!res) return;
  const btn = document.getElementById("btn-autosync");
  btn.textContent = res.active ? "⏹️ Stop Auto-Sync" : "👁️ Auto-Sync Watch";
  btn.className = res.active ? "btn btn-danger" : "btn btn-blue";
  if (res.active && !state.logcatTimer) startLogcatPollLoop();
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
    const res = await callApi("poll_logcat", [], "", false);
    if (!res) return;
    if (res.lines && res.lines.length) {
      logConsole("Live Stream", res.lines.join("\n"));
    }
  }, 500);
}
async function toggleLiveLogcat() {
  const btn = document.getElementById("btn-live-logcat");
  if (state.liveLogcat) {
    await callApi("stop_live_logcat", [], "Stop Live Logcat");
    state.liveLogcat = false;
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

/* ─── Startup Bootstrap ─── */
window.addEventListener("pywebviewready", async () => {
  const savedTheme = localStorage.getItem("adb_theme") || "emerald";
  const dot = document.querySelector(`.theme-dot[data-theme="${savedTheme}"]`);
  setTheme(savedTheme, dot);

  logConsole("⚡ ADB Control Pro", "Ultra-Modern PyWebView UI Ready!");
  await refreshDevices();
  await loadMacros();

  // Gentle live HUD telemetry refresh every 12s
  state.telemetryTimer = setInterval(() => {
    if (!state.recording) refreshTelemetry();
  }, 12000);
});
