"use strict";

const DIRS = ["pz-krk", "krk-pz"];
const REFRESH_MS = 60_000;
const STALE_AFTER_S = 6 * 60;
const state = {
  active: "pz-krk",
  data: {},          // direction -> ostatnia odpowiedź /api/departures
  loading: false,
  push: { enabled: false, publicKey: null, endpoint: null, watched: new Set() },
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// --- pamięć wybranej zakładki (tylko wygoda, może nie działać) ---
function loadPref() {
  try {
    const p = new URLSearchParams(location.search).get("direction") || localStorage.getItem("direction");
    if (DIRS.includes(p)) state.active = p;
  } catch (_) { /* prywatny tryb itp. */ }
}
function savePref() {
  try { localStorage.setItem("direction", state.active); } catch (_) { /* ignoruj */ }
}

// --- formatowanie ---
const hhmm = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Warsaw" });
};

function delayClass(dep) {
  if (dep.cancelled) return "bad";
  if (dep.delayMinutes === null || dep.delayMinutes === undefined) return "unknown";
  if (dep.delayMinutes <= 0) return "ok";
  if (dep.delayMinutes <= 15) return "warn";
  return "bad";
}

function delayText(dep) {
  if (dep.cancelled) return "ODWOŁANY";
  if (dep.delayMinutes === null || dep.delayMinutes === undefined) return "plan";
  if (dep.delayMinutes <= 0) return dep.delayMinutes < 0 ? `${dep.delayMinutes}` : "0";
  return `+${dep.delayMinutes}`;
}

function untilText(iso, departed) {
  if (departed) return "odjechał";
  const min = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (min <= 0) return "teraz";
  if (min < 60) return `za ${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  return `za ${h} h ${String(m).padStart(2, "0")}`;
}

function ageText(seconds) {
  if (seconds === null) return "brak danych";
  if (seconds < 60) return "dane: teraz";
  const min = Math.floor(seconds / 60);
  if (min < 60) return `sprzed ${min} min`;
  const h = Math.floor(min / 60);
  return `sprzed ${h} h ${min % 60} min`;
}

// wiek liczymy u klienta z liveFetchedAt — działa też dla odpowiedzi z cache SW (offline)
function liveAge(data) {
  if (!data || !data.liveFetchedAt) return null;
  return Math.max(0, Math.round((Date.now() - new Date(data.liveFetchedAt).getTime()) / 1000));
}

// --- render ---
function renderRow(dep, direction) {
  const li = $("#row").content.firstElementChild.cloneNode(true);
  const cls = delayClass(dep);
  li.classList.add(cls);
  if (dep.cancelled) li.classList.add("cancelled");
  if (dep.departed) li.classList.add("departed");
  li.dataset.id = dep.id;

  const changed = dep.expectedDeparture && dep.plannedDeparture
    && hhmm(dep.expectedDeparture) !== hhmm(dep.plannedDeparture);
  $(".t-main", li).textContent = hhmm(changed && !dep.cancelled ? dep.expectedDeparture : dep.plannedDeparture);
  const sub = $(".t-sub", li);
  if (changed && !dep.cancelled) {
    sub.textContent = hhmm(dep.plannedDeparture);
    sub.classList.add("planned");
    sub.title = "Planowo";
  }
  $(".until", li).textContent = dep.cancelled ? "" : untilText(dep.expectedDeparture, dep.departed);

  $(".delay", li).textContent = delayText(dep);
  $(".delay", li).title = dep.live ? "Opóźnienie (min)" : "Brak danych na żywo — czas z rozkładu";
  $(".train", li).textContent = [dep.category, dep.number, dep.name].filter(Boolean).join(" ") || "Pociąg";
  $(".carrier", li).textContent = dep.carrierName || dep.carrierCode || "";
  const arr = dep.expectedArrival || dep.plannedArrival;
  $(".eta", li).textContent = arr ? `przyj. ${hhmm(arr)}` : "";

  const plat = $(".plat", li);
  $(".p-val", plat).textContent = dep.platform || "—";
  $(".tr", plat).textContent = dep.track ? `tor ${dep.track}` : "";
  if (!dep.platform) plat.classList.add("none");

  const bell = $(".bell", li);
  if (state.push.enabled && !dep.departed && !dep.cancelled) {
    bell.hidden = false;
    const on = state.push.watched.has(dep.id);
    bell.setAttribute("aria-pressed", String(on));
    bell.setAttribute("aria-label", on ? "Przestań obserwować" : "Obserwuj ten pociąg");
    bell.addEventListener("click", () => toggleWatch(dep, direction, bell));
  }
  return li;
}

function renderColumn(direction) {
  const col = $(`.col[data-dir="${direction}"]`);
  const list = $(".list", col);
  const data = state.data[direction];
  list.replaceChildren();
  if (!data) {
    list.append(Object.assign(document.createElement("li"), { className: "empty", textContent: "Ładowanie…" }));
    return;
  }
  if (!data.departures.length) {
    list.append(Object.assign(document.createElement("li"), {
      className: "empty",
      textContent: data.scheduleFetchedAt ? `Brak odjazdów w ciągu ${data.windowHours} h.` : "Brak danych rozkładu.",
    }));
    return;
  }
  for (const dep of data.departures) list.append(renderRow(dep, direction));
}

function renderStatus() {
  const data = state.data[state.active] || state.data[DIRS.find((d) => state.data[d])];
  const age = liveAge(data);
  const el = $("#age");
  el.textContent = state.loading ? "odświeżam…" : ageText(age);
  el.classList.toggle("stale", age === null || age > STALE_AFTER_S);
  el.classList.toggle("fresh", age !== null && age <= STALE_AFTER_S);
  $("#demo").hidden = !(data && data.demo);
  const notice = $("#notice");
  const offline = data && data._offline;
  const text = offline ? "Brak połączenia — pokazuję ostatnio pobrane dane." : data && data.notice;
  notice.hidden = !text;
  notice.textContent = text || "";
}

function renderTabs() {
  for (const t of $$(".tab")) t.setAttribute("aria-selected", String(t.dataset.dir === state.active));
  for (const c of $$(".col")) c.dataset.active = String(c.dataset.dir === state.active);
}

function renderAll() {
  renderTabs();
  DIRS.forEach(renderColumn);
  renderStatus();
}

// --- dane ---
async function fetchDirection(direction) {
  const r = await fetch(`/api/departures?direction=${direction}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  data._offline = r.headers.get("X-From-Cache") === "1";
  return data;
}

async function refresh() {
  if (state.loading) return;
  state.loading = true;
  renderStatus();
  try {
    const results = await Promise.allSettled(DIRS.map(fetchDirection));
    results.forEach((res, i) => { if (res.status === "fulfilled") state.data[DIRS[i]] = res.value; });
    if (results.every((r) => r.status === "rejected")) toast("Nie udało się pobrać danych.");
  } finally {
    state.loading = false;
    renderAll();
  }
}

// --- zakładki ---
for (const t of $$(".tab")) {
  t.addEventListener("click", () => {
    state.active = t.dataset.dir;
    savePref();
    renderTabs();
    renderStatus();
    window.scrollTo({ top: 0 });
  });
}
$("#age").addEventListener("click", refresh);

// --- pull to refresh (PWA na ekranie głównym iOS nie ma natywnego) ---
(function pullToRefresh() {
  const ptr = $("#ptr");
  const THRESHOLD = 70;
  let startY = null, dy = 0;
  window.addEventListener("touchstart", (e) => {
    if (window.scrollY <= 0 && !state.loading) { startY = e.touches[0].clientY; dy = 0; }
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (startY === null) return;
    dy = Math.max(0, e.touches[0].clientY - startY);
    if (dy <= 0) return;
    const pull = Math.min(dy * 0.5, 90);
    ptr.classList.add("pulling");
    ptr.classList.toggle("ready", dy > THRESHOLD);
    ptr.style.transform = `translateY(${pull - 40}px)`;
    $(".ptr-spinner", ptr).style.transform = `rotate(${dy * 2}deg)`;
    $(".ptr-label", ptr).textContent = dy > THRESHOLD ? "Puść, aby odświeżyć" : "Pociągnij, aby odświeżyć";
  }, { passive: true });
  window.addEventListener("touchend", async () => {
    if (startY === null) return;
    const go = dy > THRESHOLD;
    startY = null;
    ptr.classList.remove("pulling", "ready");
    ptr.style.transform = "";
    if (go) {
      ptr.classList.add("loading");
      $(".ptr-label", ptr).textContent = "Odświeżam…";
      await refresh();
      ptr.classList.remove("loading");
    }
  });
})();

// --- toast ---
let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

// --- faza 2: powiadomienia push ---
const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function b64ToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* ignoruj */ }
    throw new Error(msg);
  }
  return r.json();
}

async function initPush() {
  try {
    const cfg = await (await fetch("/api/push/config")).json();
    if (!cfg.enabled || !pushSupported()) return;
    state.push.publicKey = cfg.publicKey;
    state.push.enabled = true;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      state.push.endpoint = sub.endpoint;
      await postJSON("/api/push/subscribe", sub.toJSON()); // odśwież na serwerze (np. po restarcie z pustą bazą)
      const { trainIds } = await postJSON("/api/watches/list", { endpoint: sub.endpoint });
      state.push.watched = new Set(trainIds);
    }
    if (/iPhone|iPad|iPod/.test(navigator.userAgent) && !isStandalone()) {
      const hint = $("#push-hint");
      hint.hidden = false;
      hint.textContent = "Powiadomienia działają po dodaniu aplikacji do ekranu głównego (Udostępnij → Do ekranu początkowego).";
    }
    renderAll();
  } catch (e) {
    console.warn("push init", e);
  }
}

async function ensureSubscription() {
  if (state.push.endpoint) return state.push.endpoint;
  if (/iPhone|iPad|iPod/.test(navigator.userAgent) && !isStandalone()) {
    throw new Error("Na iPhonie/iPadzie dodaj najpierw aplikację do ekranu głównego.");
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Brak zgody na powiadomienia.");
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(state.push.publicKey) });
  await postJSON("/api/push/subscribe", sub.toJSON());
  state.push.endpoint = sub.endpoint;
  return sub.endpoint;
}

async function toggleWatch(dep, direction, bell) {
  bell.disabled = true;
  try {
    const endpoint = await ensureSubscription();
    const body = { endpoint, trainId: dep.id, direction };
    if (state.push.watched.has(dep.id)) {
      await postJSON("/api/watches/remove", body);
      state.push.watched.delete(dep.id);
      toast("Nie obserwujesz już tego pociągu.");
    } else {
      await postJSON("/api/watches", body);
      state.push.watched.add(dep.id);
      toast("Obserwuję: dam znać o zmianie opóźnienia lub peronu.");
    }
    renderAll();
  } catch (e) {
    toast(e.message || "Nie udało się włączyć powiadomień.");
  } finally {
    bell.disabled = false;
  }
}

// --- start ---
loadPref();
renderAll();
refresh();
setInterval(refresh, REFRESH_MS);
setInterval(() => { renderAll(); }, 15_000); // "za X min" i wiek danych bez zapytań
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then(initPush).catch((e) => console.warn("SW", e));
}
