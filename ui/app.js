/* SnapSpeak 设置界面的前端逻辑
   与 Python 的通信用 WKWebView 的 messageHandler "bridge" */

const MOD = { cmd: 256, shift: 512, option: 2048, control: 4096 };

// 浏览器 event.code → macOS 虚拟键码
const KEYCODE = {
  KeyA: 0, KeyS: 1, KeyD: 2, KeyF: 3, KeyH: 4, KeyG: 5, KeyZ: 6, KeyX: 7, KeyC: 8, KeyV: 9,
  KeyB: 11, KeyQ: 12, KeyW: 13, KeyE: 14, KeyR: 15, KeyY: 16, KeyT: 17,
  Digit1: 18, Digit2: 19, Digit3: 20, Digit4: 21, Digit6: 22, Digit5: 23, Equal: 24,
  Digit9: 25, Digit7: 26, Minus: 27, Digit8: 28, Digit0: 29, BracketRight: 30,
  KeyO: 31, KeyU: 32, BracketLeft: 33, KeyI: 34, KeyP: 35, KeyL: 37, KeyJ: 38,
  Quote: 39, KeyK: 40, Semicolon: 41, Backslash: 42, Comma: 43, Slash: 44,
  KeyN: 45, KeyM: 46, Period: 47, Tab: 48, Space: 49, Backquote: 50, Backspace: 51,
  F1: 122, F2: 120, F3: 99, F4: 118, F5: 96, F6: 97, F7: 98, F8: 100,
  F9: 101, F10: 109, F11: 103, F12: 111,
  ArrowLeft: 123, ArrowRight: 124, ArrowDown: 125, ArrowUp: 126,
  Home: 115, End: 119, PageUp: 116, PageDown: 121, ForwardDelete: 117,
};

const KEYNAME = {
  Space: "Space", Tab: "⇥", Backspace: "⌫", ForwardDelete: "⌦", Escape: "⎋",
  Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Semicolon: ";",
  Quote: "'", Comma: ",", Period: ".", Slash: "/", Backslash: "\\", Backquote: "`",
  ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→",
};

function keyLabel(code) {
  if (KEYNAME[code]) return KEYNAME[code];
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  return code;
}

function modsToSymbols(m) {
  let s = "";
  if (m & MOD.control) s += "⌃";
  if (m & MOD.option) s += "⌥";
  if (m & MOD.shift) s += "⇧";
  if (m & MOD.cmd) s += "⌘";
  return s;
}

// ---------------------------------------------------------------- 通信
function send(cmd, extra = {}) {
  try {
    window.webkit.messageHandlers.bridge.postMessage({ cmd, ...extra });
  } catch (e) {
    console.error("bridge 不可用", e);
  }
}

// ---------------------------------------------------------------- 全局状态
let S = { settings: {}, voices_zh: [], voices_en: [], permission: true };
let recording = false;

window.ui = {
  setState(state) {
    S = state;
    render();
  },
  setStatus(text) {
    document.getElementById("status").textContent = text;
  },
  saved() {
    flashSaved();
  },
  keyResult(ok, msg) {
    const el = document.getElementById("keystate");
    el.textContent = msg || (ok ? "Key 可用" : "Key 有问题");
    el.className = "keystate " + (ok ? "ok" : "err");
    document.getElementById("status").textContent = msg || "";
  },
};

// ---------------------------------------------------------------- 渲染
function fillSelect(el, items, value) {
  el.innerHTML = "";
  for (const it of items) {
    const o = document.createElement("option");
    o.value = it.id;
    o.textContent = it.label;
    if (it.id === value) o.selected = true;
    el.appendChild(o);
  }
}

function render() {
  const s = S.settings || {};

  // 快捷键
  document.getElementById("keylabel").textContent =
    (s.hotkey && s.hotkey.label) || "未设置";

  // 音色
  fillSelect(document.getElementById("voice_zh"), S.voices_zh || [], s.voice_zh);
  fillSelect(document.getElementById("voice_en"), S.voices_en || [], s.voice_en);

  // 自定义替换
  const trEl = document.getElementById("text_replacements");
  if (trEl && document.activeElement !== trEl) {
    trEl.value = (s.text_replacements || [])
      .map((p) => (p && p.length >= 2 ? `${p[0]}=>${p[1]}` : ""))
      .filter(Boolean).join("\n");
  }

  // 自定义替换
const trBox = document.getElementById("text_replacements");
if (trBox) {
  trBox.onchange = () => {
    const pairs = trBox.value.split("\n").map((l) => l.trim()).filter(Boolean)
      .map((l) => {
        const i = l.indexOf("=>");
        return i > 0 ? [l.slice(0, i).trim(), l.slice(i + 2).trim()] : null;
      }).filter(Boolean);
    trBox.value = pairs.map((p) => `${p[0]}=>${p[1]}`).join("\n");
    save({ text_replacements: pairs });
  };
}

// 译文字号
  const fs = document.getElementById("hud_font_size");
  if (fs) {
    fs.value = s.hud_font_size ?? 12.5;
    document.getElementById("fontout").textContent = Number(fs.value).toFixed(1);
  }

  // 语速
  const sp = document.getElementById("speed");
  sp.value = s.speed ?? 1;
  document.getElementById("speedout").textContent =
    Number(sp.value).toFixed(2) + "×";

  // 引擎 / 截图方式
  setSegmented("engine", s.engine || "kokoro");
  setSegmented("capture_mode", s.capture_mode || "region");

  // API Key 状态(Key 本身从不回传到 JS)
  const keyInput = document.getElementById("apikey");
  keyInput.value = "";
  keyInput.placeholder = S.has_key ? "已保存 · 输入新的可替换" : "sk-...";
  const ks = document.getElementById("keystate");
  if (!ks.textContent) {
    ks.textContent = S.has_key ? "已保存" : "未设置";
    ks.className = "keystate " + (S.has_key ? "ok" : "");
  }

  // 开关
  document.getElementById("translate_en").checked = s.translate_en !== false;
  const fg = document.getElementById("fix_greek");
  if (fg) fg.checked = s.fix_greek !== false;
  document.getElementById("copy_to_clipboard").checked = !!s.copy_to_clipboard;
  document.getElementById("auto_play").checked = !!s.auto_play;
  document.getElementById("notify").checked = !!s.notify;

  // 权限
  const perm = document.getElementById("perm");
  if (S.permission === false) {
    perm.textContent = "⚠ 缺少屏幕录制权限 · 点击设置";
    perm.className = "perm warn";
    perm.onclick = () => send("open_permission");
  } else {
    perm.textContent = "屏幕录制已授权";
    perm.className = "perm ok";
    perm.onclick = null;
  }

  renderHistory();

  // 强制一次样式计算:否则 :checked 伪元素的样式可能晚一帧才绘制,
  // 用 takeSnapshot 之类的手段抓图时会抓到旧样子。
  void document.body.offsetHeight;
}

function setSegmented(id, val) {
  for (const b of document.querySelectorAll(`#${id} button`)) {
    b.classList.toggle("on", b.dataset.val === val);
  }
}

function renderHistory() {
  const box = document.getElementById("history");
  const hist = (S.settings && S.settings.history) || [];
  box.innerHTML = "";
  if (!hist.length) {
    box.innerHTML = '<p class="empty">还没有记录</p>';
    return;
  }
  for (const h of hist) {
    const d = document.createElement("div");
    d.className = "item";
    const when = new Date(h.ts * 1000);
    const hh = String(when.getHours()).padStart(2, "0");
    const mm = String(when.getMinutes()).padStart(2, "0");
    const tr = h.translation || "";
    const badge = tr ? "译 · " : "";
    d.innerHTML = `<span class="txt"></span><span class="meta">${badge}${hh}:${mm}</span>`;
    d.querySelector(".txt").textContent = h.text.replace(/\s+/g, " ").slice(0, 80);
    d.onclick = () => send("respeak", { text: h.text, translation: tr });
    d.title = tr ? `译文:${tr}\n\n点击重新朗读` : "点击重新朗读";
    box.appendChild(d);
  }
}

// ---------------------------------------------------------------- 交互
let savedTimer = null;
function flashSaved() {
  const el = document.getElementById("status");
  const old = el.textContent;
  el.textContent = "已保存";
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { el.textContent = old; }, 900);
}

function save(patch) {
  Object.assign(S.settings, patch);
  send("save", { settings: patch });
}

// 快捷键录制
const keyrec = document.getElementById("keyrec");
const keyhint = document.getElementById("keyhint");

keyrec.addEventListener("click", () => {
  recording = !recording;
  keyrec.classList.toggle("recording", recording);
  document.getElementById("keylabel").textContent = recording ? "按下组合键…" : (S.settings.hotkey?.label || "未设置");
  keyhint.classList.remove("err");
  keyhint.textContent = recording
    ? "正在录制,按 Esc 取消"
    : "点这里,然后按下你想要的组合键(需包含 ⌘ ⌃ ⌥ ⇧ 中的至少一个)";
});

document.addEventListener("keydown", (e) => {
  if (!recording) return;
  e.preventDefault();
  e.stopPropagation();

  if (e.code === "Escape") {
    keyrec.click();
    return;
  }

  let mods = 0;
  if (e.metaKey) mods |= MOD.cmd;
  if (e.ctrlKey) mods |= MOD.control;
  if (e.altKey) mods |= MOD.option;
  if (e.shiftKey) mods |= MOD.shift;

  const code = e.code;
  if (!(code in KEYCODE)) return;                 // 修饰键本身,忽略
  if (mods === 0) {
    keyhint.classList.add("err");
    keyhint.textContent = "至少要带一个修饰键(⌘ ⌃ ⌥ ⇧)";
    return;
  }

  const label = modsToSymbols(mods) + keyLabel(code);
  const hotkey = { keycode: KEYCODE[code], modifiers: mods, label };
  save({ hotkey });
  document.getElementById("keylabel").textContent = label;
  keyrec.classList.remove("recording");
  recording = false;
  keyhint.classList.remove("err");
  keyhint.textContent = "点这里,然后按下你想要的组合键(需包含 ⌘ ⌃ ⌥ ⇧ 中的至少一个)";
}, true);

document.getElementById("keyclear").onclick = () => {
  save({ hotkey: null });
  S.settings.hotkey = null;
  document.getElementById("keylabel").textContent = "未设置";
};

// 音色
document.getElementById("voice_zh").onchange = (e) => save({ voice_zh: e.target.value });
document.getElementById("voice_en").onchange = (e) => save({ voice_en: e.target.value });

for (const b of document.querySelectorAll("[data-audition]")) {
  b.onclick = () => {
    const lang = b.dataset.audition;
    const sel = document.getElementById("voice_" + lang);
    b.textContent = "合成中";
    b.disabled = true;
    send("audition", { lang, voice: sel.value });
    setTimeout(() => { b.textContent = "试听"; b.disabled = false; }, 2500);
  };
}

// 译文字号
const fsize = document.getElementById("hud_font_size");
if (fsize) {
  fsize.oninput = () => {
    document.getElementById("fontout").textContent = Number(fsize.value).toFixed(1);
  };
  fsize.onchange = () => save({ hud_font_size: Number(fsize.value) });
}

// 语速
const speed = document.getElementById("speed");
speed.oninput = () => {
  document.getElementById("speedout").textContent = Number(speed.value).toFixed(2) + "×";
};
speed.onchange = () => save({ speed: Number(speed.value) });

// 分段控件
for (const id of ["engine", "capture_mode"]) {
  const box = document.getElementById(id);
  box.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    setSegmented(id, b.dataset.val);
    save({ [id]: b.dataset.val });
  };
}

// 开关
for (const id of ["copy_to_clipboard", "auto_play", "notify", "translate_en", "fix_greek"]) {
  document.getElementById(id).onchange = (e) => save({ [id]: e.target.checked });
}

// 按钮
document.getElementById("runnow").onclick = () => {
  document.getElementById("status").textContent = "取词中…";
  send("run_now");
};
document.getElementById("openconfig").onclick = () => send("open_config");

document.getElementById("savekey").onclick = () => {
  const btn = document.getElementById("savekey");
  const val = document.getElementById("apikey").value.trim();
  btn.textContent = "测试中";
  btn.disabled = true;
  document.getElementById("status").textContent = "正在验证 Key…";
  send("save_key", { key: val });
  setTimeout(() => { btn.textContent = "保存并测试"; btn.disabled = false; }, 6000);
};
document.getElementById("clearhist").onclick = () => {
  S.settings.history = [];
  save({ history: [] });
  renderHistory();
};

// 启动
let started = false;
function boot() {
  if (started) return;
  started = true;
  send("ready");
}
window.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();
