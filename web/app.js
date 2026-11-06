/* Bus · frontend (valencià) — busos d'EMT València en directe (abans EMTropello).
   El poller de la Raspa llig el geoportal cada minut i dona a cada bus el seu
   número; el client pregunta cada pocs segons si hi ha pasada nova
   (/api/ultimo) i, quan n'hi ha, reajusta la simulació: cada bus avança AL LLARG
   de la seua ruta entre una dada i la següent.
   Enllaços directes: bus.pr1.es/<parada> i bus.pr1.es/<número de bus> (si ja no
   circula, el seu recorregut: historia.js). Popups amb pestanyes. Xat: xat.js. */
const API = "";
const REFRESH_MS = 120000;   // refresc de seguretat; el normal el dispara vigilaServidor
const VIGILA_MS = 5000;      // cada quant es pregunta si hi ha pasada nova
const ANIM_MS = 500;         // pas d'animació (reorientació de fletxes en zoom/pan)
const MATCH_MAX_M = 3000;    // radi per reidentificar un bus entre pulls
const MOVE_MIN_MS = 0.7;     // velocitat mínima (m/s) per pintar-lo "en marxa"
const MAX_SPEED_MS = 25;     // per damunt d'això => emparellament dolent, el deixem parat

/* color estable i viu per línia (hash del codi -> to HSL) */
const colorCache = {};
function colorLinea(linea) {
  if (colorCache[linea]) return colorCache[linea];
  let h = 0;
  for (let i = 0; i < linea.length; i++) h = (h * 31 + linea.charCodeAt(i)) >>> 0;
  const c = `hsl(${h % 360} 72% 60%)`;
  colorCache[linea] = c;
  return c;
}

const map = L.map("map", { zoomControl: false, attributionControl: false })
  .setView([39.47, -0.376], 13);
L.control.zoom({ position: "bottomleft" }).addTo(map);
// Fons fosc d'Esri (sense clau). CARTO (basemaps.cartocdn.com) des de nov. 2026 demana
// API key i torna una tessel·la "API KEY REQUIRED".
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
L.tileLayer(ESRI + "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 20, maxNativeZoom: 16 }).addTo(map);
L.tileLayer(ESRI + "World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 20, maxNativeZoom: 16, opacity: 0.7 }).addTo(map);

const busLayer = L.layerGroup().addTo(map);
const buses = [];                  // estat viu de cada bus
const routeLayers = {};            // code -> L.layerGroup (trazado)
const lineasVisibles = new Set();
const lineasVistas = new Set();
const stopsLayer = L.layerGroup();  // parades (visibles en acostar el zoom)
const STOP_ZOOM = 14;               // a partir d'ací es mostren les parades
// graf de la ruta de cada línia: polilínies amb longitud acumulada + nodes de
// connexió entre trams. Serveix per SIMULAR el bus avançant AL LLARG de la via.
const RG = {};                      // code -> {paths:[{pts,cum,len}], grid:Map, nodes:Map}
const CELL = 0.0006;                // ~55 m (cel·la per a l'enganxe)
const cellK = (p) => Math.round(p[0] / CELL) + "|" + Math.round(p[1] / CELL);
const nodeK = (p) => p[0].toFixed(5) + "," + p[1].toFixed(5);   // ~1 m (extrems de tram)
let ultimoTs = 0, ultimoPts = 0, edadSeg = 0;
const marcadorsParada = new Map();  // id de parada -> circleMarker
const parades = new Map();          // id de parada -> parada

// Enllaços directes. La barra d'adreces segueix el popup obert.
const URL_PUBLICA = "https://bus.pr1.es/";
const BASE = location.pathname.replace(/[^/]*$/, "");          // "/" o "/bus/" (LAN)
let ENLLAC_INICIAL = "";
try { ENLLAC_INICIAL = decodeURIComponent(location.pathname.slice(BASE.length)).toLowerCase(); } catch (e) { /* camí mal format */ }
let enllacActiu = "";
function posaEnllac(cami) {
  if (window.historiaActiva && window.historiaActiva() && !/^\d+$/.test(cami)) return;
  enllacActiu = cami;
  try { history.replaceState(null, "", BASE + cami + location.search + location.hash); } catch (e) { /* file:// */ }
}
function llevaEnllac(cami) { if (enllacActiu === cami) posaEnllac(""); }
window.posaEnllac = posaEnllac;
window.llevaEnllac = llevaEnllac;

function indexarRuta(code, rawPaths) {
  const paths = [], grid = new Map(), nodes = new Map();
  for (const raw of rawPaths) {
    if (!raw || raw.length < 2) continue;
    const cum = [0];
    for (let i = 1; i < raw.length; i++) cum.push(cum[i - 1] + distM(raw[i - 1], raw[i]));
    const pi = paths.length;
    paths.push({ pts: raw, cum, len: cum[cum.length - 1] });
    for (let si = 0; si + 1 < raw.length; si++) {
      const a = raw[si], b = raw[si + 1], key = pi * 100000 + si;
      const steps = Math.max(1, Math.ceil(distM(a, b) / 40));  // mostreja cel·les cada ~40 m
      let lastK = null;
      for (let s = 0; s <= steps; s++) {
        const f = s / steps;
        const k = cellK([a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])]);
        if (k === lastK) continue;
        lastK = k;
        let arr = grid.get(k); if (!arr) grid.set(k, arr = []);
        arr.push(key);
      }
    }
    for (const end of [0, 1]) {
      const k = nodeK(end ? raw[raw.length - 1] : raw[0]);
      let a = nodes.get(k); if (!a) nodes.set(k, a = []);
      a.push({ pi, end });
    }
  }
  RG[code] = { paths, grid, nodes };
}
// projecta p sobre a-b -> {point, dist(m), t}
function projSeg(p, a, b) {
  const cf = Math.cos(p[0] * Math.PI / 180);
  const ax = a[1] * cf, ay = a[0], bx = b[1] * cf, by = b[0], px = p[1] * cf, py = p[0];
  const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
  let t = L2 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx, qy = ay + t * dy;
  return { point: [qy, qx / cf], dist: Math.hypot(px - qx, py - qy) * 111000, t };
}
// enganxa ll a la via -> {pi, arc(m des de l'inici de la path), dist, tang}
function snapToLine(code, ll) {
  const idx = RG[code]; if (!idx) return null;
  const cla = Math.round(ll[0] / CELL), cln = Math.round(ll[1] / CELL);
  let best = null; const seen = new Set();
  for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) {
    const arr = idx.grid.get((cla + i) + "|" + (cln + j));
    if (!arr) continue;
    for (const key of arr) {
      if (seen.has(key)) continue; seen.add(key);
      const pi = Math.floor(key / 100000), si = key % 100000;
      const P = idx.paths[pi], a = P.pts[si], b = P.pts[si + 1];
      const pr = projSeg(ll, a, b);
      if (!best || pr.dist < best.dist)
        best = { pi, arc: P.cum[si] + pr.t * (P.cum[si + 1] - P.cum[si]),
                 dist: pr.dist, tang: [b[0] - a[0], b[1] - a[1]] };
    }
  }
  return best;
}
// punt i tangent a `arc` metres d'una path
function pointAt(P, arc) {
  const cum = P.cum, pts = P.pts;
  arc = Math.max(0, Math.min(P.len, arc));
  let lo = 0, hi = cum.length - 1;
  while (lo < hi) { const m = (lo + hi) >> 1; if (cum[m] < arc) lo = m + 1; else hi = m; }
  const i = Math.max(1, lo), seg = cum[i] - cum[i - 1];
  const t = seg ? (arc - cum[i - 1]) / seg : 0, a = pts[i - 1], b = pts[i];
  return { pt: [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])],
           tang: [b[0] - a[0], b[1] - a[1]] };
}
// en arribar a un extrem, tria la path que millor continua el rumb (menys gir)
function siguientePath(idx, pi, dir) {
  const P = idx.paths[pi];
  const far = dir > 0 ? P.pts[P.pts.length - 1] : P.pts[0];
  const near = dir > 0 ? P.pts[P.pts.length - 2] : P.pts[1];
  const hx = far[0] - near[0], hy = far[1] - near[1], hn = Math.hypot(hx, hy) || 1;
  const cands = idx.nodes.get(nodeK(far)); if (!cands) return null;
  let best = null, bd = -2;
  for (const c of cands) {
    if (c.pi === pi) continue;
    const Q = idx.paths[c.pi];
    const s0 = c.end ? Q.pts[Q.pts.length - 1] : Q.pts[0];
    const s1 = c.end ? Q.pts[Q.pts.length - 2] : Q.pts[1];
    const ox = s1[0] - s0[0], oy = s1[1] - s0[1], on = Math.hypot(ox, oy) || 1;
    const dot = (hx * ox + hy * oy) / (hn * on);
    if (dot > bd) { bd = dot; best = { pi: c.pi, dir: c.end ? -1 : 1 }; }
  }
  return bd > -0.4 ? best : null;   // gir massa brusc: para (el reajust de 5 min ho corregix)
}
// avança la simulació del bus `metros` al llarg de la via
function avanzarSim(e, metros) {
  const idx = RG[e.linea]; if (!idx || !e.sim) return;
  let { pi, arc, dir } = e.sim, remain = metros, guard = 0;
  while (remain > 0.01 && guard++ < 30) {
    const P = idx.paths[pi];
    const toEnd = dir > 0 ? P.len - arc : arc;
    if (remain <= toEnd) { arc += dir * remain; break; }
    remain -= toEnd;
    const nxt = siguientePath(idx, pi, dir);
    if (!nxt) { arc = dir > 0 ? P.len : 0; break; }
    pi = nxt.pi; dir = nxt.dir; arc = dir > 0 ? 0 : idx.paths[pi].len;
  }
  e.sim.pi = pi; e.sim.arc = arc; e.sim.dir = dir;
}
function bearingScreen(from, to) {
  const a = map.latLngToContainerPoint(from), b = map.latLngToContainerPoint(to);
  return Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
}

const nowSec = () => Date.now() / 1000;
const distM = (a, b) =>
  Math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * Math.cos(a[0] * Math.PI / 180) * 111000);

/* ---------- trazado de línies (estàtic) ---------- */
async function cargarRed() {
  let net;
  try { net = await (await fetch(API + "api/network")).json(); }
  catch (e) { return; }
  const routes = net.routes || {};
  for (const [code, info] of Object.entries(routes)) {
    const paths = info.paths || [];
    if (!paths.length) continue;
    const grp = L.layerGroup();
    for (const path of paths) {
      L.polyline(path, { color: colorLinea(code), weight: 2, opacity: 0.45,
        smoothFactor: 1.5, interactive: false }).addTo(grp);
    }
    routeLayers[code] = grp;
    lineasVistas.add(code); lineasVisibles.add(code);
    indexarRuta(code, paths);
    grp.addTo(map);
  }
  dibujarParadas(net.stops || []);
  construirLeyenda();
}

/* ---------- parades ---------- */
function dibujarParadas(stops) {
  for (const s of stops) {
    const m = L.circleMarker([s.lat, s.lng], {
      radius: 3, color: "#0d0d12", weight: 1,
      fillColor: "#e8e8ef", fillOpacity: 0.9,
    });
    m.parada = s;
    m.on("click", () => obrirPopupParada(s));
    m.addTo(stopsLayer);
    marcadorsParada.set(s.id, m);
    parades.set(s.id, s);
  }
  actualizarParadas();
}
function actualizarParadas() {
  const wanted = document.getElementById("paradas-chk").checked && map.getZoom() >= STOP_ZOOM;
  if (wanted && !map.hasLayer(stopsLayer)) stopsLayer.addTo(map);
  else if (!wanted && map.hasLayer(stopsLayer)) map.removeLayer(stopsLayer);
  const hint = document.getElementById("paradas-hint");
  if (hint) hint.style.display =
    (document.getElementById("paradas-chk").checked && map.getZoom() < STOP_ZOOM) ? "block" : "none";
}

/* ---------- icona: punt (parat) o fletxa (en marxa) ---------- */
function iconoBus(color) {
  return L.divIcon({
    className: "",
    html:
      `<div class="bus-wrap">` +
      `<span class="bus-dot" style="background:${color}"></span>` +
      `<svg class="bus-arrow" viewBox="0 0 24 24" width="22" height="22">` +
      `<path d="M21 12 L6 4 L11 12 L6 20 Z" fill="${color}" stroke="#fff" ` +
      `stroke-width="1.7" stroke-linejoin="round"/></svg>` +
      `</div>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
}
const chipLinia = (l) => `<span class="pop-linea" style="background:${colorLinea(l)}">${l}</span>`;
// popup d'un bus: un element per marcador, amb pestanyes (es refresca sense refer-lo)
function contBus(e) {
  if (e.cont && e.cont.dataset.id === String(e.id)) return e.cont;
  const cont = document.createElement("div");
  cont.className = "pop-bus";
  cont.dataset.id = String(e.id);
  cont.innerHTML = `<div class="pop-bus-cap"></div>`;
  cont.appendChild(ferPestanyes([
    { clau: "ara", titol: "Ara", html: `<div class="pop-bus-ara"></div>` },
    { clau: "reg", titol: "Registre", html: `<div class="pop-reg-llista" data-clau="bus:${e.id}">carregant…</div>` },
    { clau: "xat", titol: "Xat", html: `<div class="pop-xat" data-tipus="bus" data-lloc="${e.id}"></div>` },
  ], (clau, panell) => {
    if (clau === "reg") omplirRegistre(panell.querySelector(".pop-reg-llista"));
    if (clau === "xat") ompleXat(panell);
    if (e.cont === cont && e.marker.isPopupOpen()) mantinVisible(cont);
  }));
  e.cont = cont;
  actualitzaContBus(e);
  return cont;
}
function actualitzaContBus(e) {
  const cont = e.cont;
  if (!cont) return;
  const cap = cont.querySelector(".pop-bus-cap");
  cap.innerHTML = capCompartir(`${chipLinia(e.linea)}<span class="pop-tray">${e.trayecto || ""}</span>` +
    ` <span class="pop-id">#${e.id}</span>`, String(e.id), `el bus ${e.linea} #${e.id}`, "pop-bus-nom");
  lligaCompartir(cap);
  const vel = e.speed >= MOVE_MIN_MS ? `${Math.round(e.speed * 3.6)} km/h` : "parat";
  cont.querySelector(".pop-bus-ara").innerHTML =
    `<div>${vel}${e.fecha ? ` · <span class="pop-edat">GPS fa <span class="c-edat">${fmtEdat(e.fecha)}</span></span>` : ""}</div>` +
    `<button type="button" class="pop-boto" data-hist="${e.id}">Recorregut d'avui</button>`;
  const hb = cont.querySelector("[data-hist]");
  if (hb) hb.onclick = () => { map.closePopup(); if (window.historiaBus) window.historiaBus(e.id); };
  const x = cont.querySelector(".pop-xat");
  if (x) x.dataset.nom = `${e.linea} · ${e.trayecto || ""}`;
  if (pestanyaActiva(cont) === "reg") omplirRegistre(cont.querySelector(".pop-reg-llista"));
}
// el `fecha` del geoportal no és un epoch UTC fiable (ve ~1-2 h avançat): l'edat es
// mesura respecte al GPS més fresc de la pasada, que es pren com «ara» del servidor
let maxFechaMs = 0, maxFechaRebut = 0;
function fmtEdat(fechaMs) {
  const s = Math.max(0, Math.round((maxFechaMs - fechaMs) / 1000 + (Date.now() - maxFechaRebut) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/* ---------- refresc: assigna àncora + velocitat a cada bus ---------- */
function actualizarBuses(data, edad) {
  const busos = data.busos || [];
  const dt = (data.ts && data.pts) ? Math.max(1, data.ts - data.pts) : 0;
  // antiguitat del GPS de cada bus, RELATIVA al bus més fresc (maxFecha). Així és
  // immune al rellotge, a la zona horària i al DST (el `fecha` del geoportal ve
  // ~2h avançat). El bus més fresc -> gps_age 0; els altres, positiu.
  const maxFecha = busos.reduce((m, x) => Math.max(m, x.fecha || 0), 0);
  maxFechaMs = maxFecha; maxFechaRebut = Date.now() - edad * 1000;

  for (const e of buses) e.usado = false;
  const nuevos = [];

  // registra línies noves (per si un bus circula d'una línia sense trazado)
  let cambio = false;
  for (const b of busos) if (b.linea && !lineasVistas.has(b.linea)) {
    lineasVistas.add(b.linea); lineasVisibles.add(b.linea); cambio = true;
  }

  for (const b of busos) {
    const anchor = [b.lat, b.lng];
    // vel en recta (graus/s) només per desempatar el sentit sobre la via
    let vel = [0, 0];
    if (b.plat != null && dt > 0) vel = [(b.lat - b.plat) / dt, (b.lng - b.plng) / dt];

    // REAJUSTA la simulació: enganxa la posició actual (i l'anterior) a la via i
    // calcula velocitat i sentit AL LLARG de la ruta (no en recta)
    const snC = snapToLine(b.linea, anchor);
    const snP = b.plat != null ? snapToLine(b.linea, [b.plat, b.plng]) : null;
    let sim = null, speed = 0;
    if (snC) {
      let dir = 1;
      if (snP && snP.pi === snC.pi && dt > 0) {
        speed = Math.abs(snC.arc - snP.arc) / dt;         // exacte al llarg de la via
        dir = snC.arc >= snP.arc ? 1 : -1;
      } else if (b.plat != null && dt > 0) {
        speed = 1.2 * distM([b.plat, b.plng], anchor) / dt; // recta + factor de curvatura
        if (vel[0] * snC.tang[0] + vel[1] * snC.tang[1] < 0) dir = -1;
      }
      if (speed > MAX_SPEED_MS) speed = 0;                  // emparellament dolent
      sim = { pi: snC.pi, arc: snC.arc, dir };
      // COMPENSA EL RETARD: es dibuixa on el bus està ARA, no on estava. Retard =
      // edad (antiguitat del snapshot, la mesura el servidor) + gps_age (com de vell
      // és el GPS d'este bus respecte al més fresc). Amb topalls (75 s / 400 m) per no
      // sobrepassar en busos ràpids que freguen o giren; la següent dada ho recorregix.
      if (speed >= MOVE_MIN_MS && b.fecha) {
        const gpsAge = (maxFecha - b.fecha) / 1000;
        const lag = Math.max(0, Math.min(75, edad + gpsAge));
        if (lag > 1) avanzarSim({ linea: b.linea, sim }, Math.min(speed * lag, 400));
      }
    } else if (b.plat != null && dt > 0) {
      speed = distM([b.plat, b.plng], anchor) / dt;         // línia sense ruta (nocturnes)
      if (speed > MAX_SPEED_MS) speed = 0;
    }

    // el mateix bus: pel seu número (el dona el servidor); si no, el més pròxim
    let best = buses.find((e) => !e.usado && e.id === b.id) || null;
    if (!best) {
      let bestD = MATCH_MAX_M;
      for (const e of buses) {
        if (e.usado || e.linea !== b.linea || e.trayecto !== b.trayecto) continue;
        const d = distM(e.anchor, anchor);
        if (d < bestD) { bestD = d; best = e; }
      }
    }
    if (best) {
      best.usado = true;
      const altre = best.id !== b.id;
      if (best.linea !== b.linea) best.marker.setIcon(iconoBus(colorLinea(b.linea)));
      Object.assign(best, { id: b.id, linea: b.linea, anchor, vel, speed, sim, fecha: b.fecha, trayecto: b.trayecto });
      if (best.marker.isPopupOpen()) {
        if (altre) { best.cont = null; best.marker.getPopup().update(); posaEnllac(String(b.id)); }
        else actualitzaContBus(best);
      }
    } else {
      const e = { id: b.id, linea: b.linea, trayecto: b.trayecto, anchor, vel,
        speed, sim, fecha: b.fecha, usado: true, cont: null };
      e.marker = L.marker(sim ? pointAt(RG[b.linea].paths[sim.pi], sim.arc).pt : anchor,
        { icon: iconoBus(colorLinea(b.linea)) })
        .bindPopup(() => contBus(e), { minWidth: 260, maxWidth: 280 });
      e.marker.on("popupopen", () => { actualitzaContBus(e); seleccionat(e.marker, true); posaEnllac(String(e.id)); });
      e.marker.on("popupclose", () => { seleccionat(e.marker, false); llevaEnllac(String(e.id)); });
      e.marker.addTo(busLayer);
      nuevos.push(e);
    }
  }
  // esborra òrfens
  for (let i = buses.length - 1; i >= 0; i--)
    if (!buses[i].usado) { busLayer.removeLayer(buses[i].marker); buses.splice(i, 1); }
  for (const e of nuevos) buses.push(e);
  if (cambio) construirLeyenda();
}

/* ---------- animació: simula el bus avançant AL LLARG de la via ---------- */
let lastAnim = 0;
function animar() {
  const now = nowSec();
  const dtF = lastAnim ? Math.min(2, now - lastAnim) : 0;   // segons des de l'últim pas
  lastAnim = now;
  for (const e of buses) {
    const vis = lineasVisibles.has(e.linea);
    const elm = e.marker.getElement();
    if (elm) elm.style.display = vis ? "" : "none";
    if (!vis) continue;
    let ll, tang = null;
    if (e.sim) {
      if (e.speed >= MOVE_MIN_MS && dtF > 0) avanzarSim(e, e.speed * dtF);
      const P = RG[e.linea].paths[e.sim.pi];
      const pa = pointAt(P, e.sim.arc);
      ll = pa.pt;
      tang = e.sim.dir > 0 ? pa.tang : [-pa.tang[0], -pa.tang[1]];
    } else {
      ll = e.anchor;               // línia sense ruta: última posició real
      tang = [e.vel[0], e.vel[1]];
    }
    e.marker.setLatLng(ll);
    if (!elm) continue;
    const dot = elm.querySelector(".bus-dot"), arr = elm.querySelector(".bus-arrow");
    if (!dot || !arr) continue;
    if (e.speed >= MOVE_MIN_MS && tang) {
      arr.style.transform = `rotate(${bearingScreen(ll, [ll[0] + tang[0], ll[1] + tang[1]])}deg)`;
      arr.style.display = "block"; dot.style.display = "none";
    } else {
      arr.style.display = "none"; dot.style.display = "block";
    }
  }
}

/* ---------- llegenda (toggle línia: trazado + busos) ---------- */
function construirLeyenda() {
  const ley = document.getElementById("leyenda");
  ley.innerHTML = "";
  const lineas = [...lineasVistas].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  for (const l of lineas) {
    const chip = document.createElement("span");
    chip.className = "chip" + (lineasVisibles.has(l) ? "" : " off");
    chip.textContent = l;
    chip.style.background = colorLinea(l);
    chip.onclick = () => {
      if (lineasVisibles.has(l)) {
        lineasVisibles.delete(l); chip.classList.add("off");
        if (routeLayers[l]) map.removeLayer(routeLayers[l]);
      } else {
        lineasVisibles.add(l); chip.classList.remove("off");
        if (routeLayers[l]) routeLayers[l].addTo(map);
      }
      animar();
    };
    ley.appendChild(chip);
  }
  document.getElementById("ley-count").textContent = lineas.length;
}

/* ---------- refresc de dades ---------- */
let refrescant = false;
async function refrescar() {
  if (refrescant) return;
  refrescant = true;
  try {
    const data = await (await fetch(API + "api/buses?_=" + Date.now(), { cache: "no-store" })).json();
    edadSeg = data.edad_seg == null ? 0 : data.edad_seg;
    // NOMÉS reajusta la simulació quan arriba una pasada NOVA (ts distint). Si no,
    // el client demana cada 1 min però la dada canvia cada 5 min: re-enganxar en
    // cada fetch faria saltar el bus arrere a la posició de fa minuts.
    if (data.ts && data.ts !== ultimoTs) {
      ultimoTs = data.ts;
      actualizarBuses(data, edadSeg);
    }
    animar();
    const edad = data.edad_seg == null ? "—" : (data.edad_seg < 90 ? data.edad_seg + " s"
      : Math.round(data.edad_seg / 60) + " min");
    const vis = [...lineasVisibles].length, tot = lineasVistas.size;
    const filtro = vis < tot ? `<br><span style="color:#9a9aac">${vis}/${tot} línies visibles</span>` : "";
    document.getElementById("stats").innerHTML =
      `<b>${(data.busos || []).length}</b> busos en directe<br>dades fa <b>${edad}</b>${filtro}`;
  } catch (e) {
    document.getElementById("stats").textContent = "sense connexió amb el servidor";
  } finally {
    refrescant = false;
  }
}

// pasada nova al servidor? (i missatges nous del xat)
async function vigilaServidor() {
  try {
    const u = await (await fetch(API + "api/ultimo?_=" + Date.now(), { cache: "no-store" })).json();
    if (u.ts && u.ts !== ultimoTs) await refrescar();
    if (window.xatNovetats) window.xatNovetats(u.xat || 0);
  } catch (e) { /* sense xarxa: ja es reintentarà */ }
}

function tickSegon() {
  document.getElementById("reloj").textContent = new Date().toLocaleTimeString("ca-ES");
  for (const e of buses) {
    if (!e.cont || !e.marker.isPopupOpen()) continue;
    const el = e.cont.querySelector(".c-edat");
    if (el && e.fecha) el.textContent = fmtEdat(e.fecha);
  }
}

// parades: mostra/amaga segons zoom i checkbox
map.on("zoomend", actualizarParadas);
document.getElementById("paradas-chk").addEventListener("change", actualizarParadas);

// panell colapsable
(function () {
  const hud = document.getElementById("hud"), btn = document.getElementById("hud-toggle");
  if (btn && hud) btn.addEventListener("click", () => {
    const col = hud.classList.toggle("collapsed");
    btn.textContent = col ? "»" : "«";
  });
})();

(async function main() {
  await cargarRed();
  await refrescar();
  if (window.iniciaXat) window.iniciaXat();
  obrirEnllac(ENLLAC_INICIAL);
  setInterval(refrescar, REFRESH_MS);
  setInterval(vigilaServidor, VIGILA_MS);
  setInterval(animar, ANIM_MS);
  setInterval(tickSegon, 1000);
  tickSegon();
})();


/* ---------- selecció, enllaços i pestanyes (portat de MetroViu) ---------- */
// El que té el popup obert deixa passar els clics: un segon clic al mateix lloc
// arriba al que hi ha darrere (p. ex. la parada tapada per un bus).
function seleccionat(layer, si) {
  const el = layer && layer.getElement ? layer.getElement() : null;
  if (el) el.style.pointerEvents = si ? "none" : "";
}
const ICONA_COMPARTIR = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>' +
  '<line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/></svg>';
// capçalera d'un popup: el nom i la icona de compartir; tots dos copien l'enllaç
function capCompartir(contingut, cami, etiqueta, classe = "") {
  return `<div class="pop-cap"><button type="button" class="pop-nom ${classe}" data-enllac="${cami}" ` +
    `title="Copia l'enllaç">${contingut}</button>` +
    `<button type="button" class="pop-share" data-enllac="${cami}" title="Copia l'enllaç" ` +
    `aria-label="Copia l'enllaç a ${etiqueta}">${ICONA_COMPARTIR}</button></div>`;
}
function lligaCompartir(el) {
  el.querySelectorAll("[data-enllac]").forEach((b) => { b.onclick = () => copiaEnllac(b.dataset.enllac); });
}
async function copiaEnllac(cami) {
  const url = URL_PUBLICA + cami;
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(url); ok = true; }
  } catch (e) { /* sense permís: l'altra via */ }
  if (!ok) {   // per http (LAN) el porta-retalls modern no hi és
    const ta = document.createElement("textarea");
    ta.value = url; ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
  }
  avisCurt(ok ? `Enllaç copiat · ${url.replace(/^https?:\/\//, "")}` : `Copia l'enllaç: ${url}`, ok ? 2600 : 8000);
}
function avisCurt(text, ms = 2600) {
  let el = document.getElementById("avis-curt");
  if (!el) {
    el = document.createElement("div");
    el.id = "avis-curt"; el.setAttribute("role", "status"); el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.add("on");
  clearTimeout(avisCurt.t);
  avisCurt.t = setTimeout(() => el.classList.remove("on"), ms);
}
window.avisCurt = avisCurt;
window.capCompartir = capCompartir;
window.lligaCompartir = lligaCompartir;
// Pestanyes d'un popup: [{clau, titol, html}]; `obri(clau, panell)` en triar-ne una
function ferPestanyes(pestanyes, obri) {
  const w = document.createElement("div");
  w.className = "pop-tabs";
  w.innerHTML = `<div class="pop-tab-barra" role="tablist">` +
    pestanyes.map((p) => `<button type="button" role="tab" class="pop-tab" data-clau="${p.clau}">${p.titol}</button>`).join("") +
    `</div>` +
    pestanyes.map((p) => `<div class="pop-tab-panell" role="tabpanel" data-clau="${p.clau}" hidden>${p.html}</div>`).join("");
  w.tria = (clau) => {
    w.dataset.activa = clau;
    w.querySelectorAll(".pop-tab").forEach((b) => {
      const on = b.dataset.clau === clau;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    w.querySelectorAll(".pop-tab-panell").forEach((p) => { p.hidden = p.dataset.clau !== clau; });
    obri(clau, w.querySelector(`.pop-tab-panell[data-clau="${clau}"]`));
  };
  w.querySelectorAll(".pop-tab").forEach((b) => { b.onclick = () => w.tria(b.dataset.clau); });
  w.tria(pestanyes[0].clau);
  return w;
}
const pestanyaActiva = (cont) => (cont && cont.querySelector(".pop-tabs")?.dataset.activa) || null;
function ompleXat(panell) {
  const x = panell && panell.querySelector(".pop-xat");
  if (!x || !window.xatOmpliPopup) return;
  delete x.dataset.ple;
  Promise.resolve(window.xatOmpliPopup(panell)).then(() => mantinVisible(panell));
}
// El popup creix quan canvia de pestanya o arriba el contingut; Leaflet només el
// reajusta en obrir-lo. Si ara ix de la pantalla, es mou el mapa.
function mantinVisible(dins) {
  const el = dins && dins.closest ? dins.closest(".leaflet-popup") : null;
  if (!el) return;
  const r = el.getBoundingClientRect(), m = map.getContainer().getBoundingClientRect();
  const MARGE = 14;
  let dx = 0, dy = 0;
  if (r.top < m.top + MARGE) dy = r.top - (m.top + MARGE);
  else if (r.bottom > m.bottom - MARGE) dy = Math.min(r.bottom - (m.bottom - MARGE), r.top - (m.top + MARGE));
  if (r.left < m.left + MARGE) dx = r.left - (m.left + MARGE);
  else if (r.right > m.right - MARGE) dx = r.right - (m.right - MARGE);
  if (dx || dy) map.panBy([dx, dy], { animate: true, duration: 0.25 });
}

/* ---------- popup de parada ---------- */
let popupParada = null;
function obrirPopupParada(s) {
  const m = marcadorsParada.get(s.id);
  const cont = document.createElement("div");
  cont.className = "pop-parada-cont";
  cont.innerHTML = capCompartir(esc(s.nom || "Parada"), s.enllac, esc(s.nom || "la parada"), "pop-par-nom") +
    `<div class="pop-parada-lin">${(s.lineas || []).map(chipLinia).join("")}` +
    `<span class="pop-mut">parada ${s.id}</span></div>`;
  lligaCompartir(cont);
  let obert = null;
  cont.appendChild(ferPestanyes([
    { clau: "reg", titol: "Registre", html: `<div class="pop-reg-llista" data-clau="parada:${s.id}">carregant…</div>` },
    { clau: "xat", titol: "Xat", html: `<div class="pop-xat" data-tipus="parada" data-lloc="${s.id}" data-nom="${esc(s.nom)}"></div>` },
  ], (clau, panell) => {
    if (clau === "reg") omplirRegistre(panell.querySelector(".pop-reg-llista"));
    if (clau === "xat") ompleXat(panell);
    if (obert) mantinVisible(cont);
  }));
  const popup = L.popup({ maxWidth: 290, minWidth: 270 }).setLatLng([s.lat, s.lng]).setContent(cont).openOn(map);
  obert = { id: s.id, cont, popup };
  popupParada = obert;
  posaEnllac(s.enllac);
  seleccionat(m, true);
  popup.on("remove", () => {
    if (popupParada === obert) popupParada = null;
    seleccionat(m, false);
    llevaEnllac(s.enllac);
  });
}

/* ---------- registres del dia (parada i bus) ---------- */
const hm = (ts) => new Date(ts * 1000).toLocaleTimeString("ca-ES", { hour: "2-digit", minute: "2-digit" });
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
window.esc = esc;
window.hm = hm;
window.chipLinia = chipLinia;
async function omplirRegistre(llista) {
  if (!llista) return;
  const [tipus, id] = llista.dataset.clau.split(":");
  let files;
  try {
    const url = tipus === "parada" ? `api/registre_parada?id=${id}` : `api/registre_bus?id=${id}`;
    files = (await (await fetch(API + url + "&_=" + Date.now(), { cache: "no-store" })).json()).passos || [];
  } catch (e) { llista.textContent = "sense connexió"; return; }
  if (!files.length) {
    llista.innerHTML = `<div class="pop-buida">${tipus === "parada" ? "cap bus registrat hui" : "encara no ha passat per cap parada"}</div>`;
  } else if (tipus === "parada") {
    llista.innerHTML = files.slice(0, 120).map((f) =>
      `<div class="reg-row"><span class="reg-h">${hm(f.ts)}</span>${chipLinia(f.linea)}` +
      `<span class="reg-nom">${esc(f.trayecto)}</span>` +
      `<a class="reg-id" href="${BASE}${f.id}" data-bus="${f.id}">#${f.id}</a></div>`).join("");
    llista.querySelectorAll("[data-bus]").forEach((a) => a.onclick = (ev) => {
      ev.preventDefault(); map.closePopup(); obrirEnllac(a.dataset.bus);
    });
  } else {
    llista.innerHTML = files.slice().reverse().map((f) =>
      `<div class="reg-row"><span class="reg-h">${hm(f.ts)}</span>` +
      `<span class="reg-nom">${esc(f.nom)}</span></div>`).join("");
  }
  llista.dataset.pintat = "1";
  mantinVisible(llista);
}

// bus.pr1.es/<número> (bus; si ja no circula, el seu recorregut) o /<parada>
function obrirEnllac(x) {
  if (!x || !/^[a-z0-9]+$/.test(x)) return;
  if (/^\d+$/.test(x)) {
    const id = Number(x);
    const e = buses.find((e) => e.id === id);
    if (e) {
      map.setView(e.marker.getLatLng(), Math.max(map.getZoom(), 16), { animate: false });
      e.marker.openPopup();
    } else if (window.historiaBus) window.historiaBus(id);
    return;
  }
  for (const s of parades.values()) {
    if (s.enllac !== x) continue;
    map.setView([s.lat, s.lng], Math.max(map.getZoom(), 17), { animate: false });
    actualizarParadas();
    obrirPopupParada(s);
    return;
  }
}
window.obrirEnllac = obrirEnllac;
// per al xat i el recorregut
window.marcadorBus = (id) => { const e = buses.find((e) => e.id === id); return e ? e.marker : null; };
window.marcadorParada = (id) => marcadorsParada.get(Number(id)) || null;
window.busViu = (id) => buses.some((e) => e.id === id);
window.busosVius = () => buses.map((e) => ({ id: e.id, linea: e.linea, trayecto: e.trayecto, ll: e.marker.getLatLng() }));
