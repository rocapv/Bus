/* Bus · recorregut d'un bus (bus.pr1.es/1234), encara que ja no circule.
   Reprodueix amb el rellotge accelerat les posicions GPS que va tindre a cada
   pasada del poller (/api/historia_bus): el traç es va dibuixant, el bus es mou
   entre lectura i lectura i, al final, una marca diu on es va deixar de veure.
   Un bus conserva el número tot el torn: el recorregut són tots els seus viatges.
   Portat de MetroViu (06/11/2026); ací les posicions són GPS reals. */
(function () {
  const SALT_MAX = 600;     // s: entre dues lectures més separades no s'interpola (forat de dades)
  let h = null;

  const $ = (id) => document.getElementById(id);
  const hora = (ts, seg) => new Date(ts * 1000).toLocaleTimeString("ca-ES", seg
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit" });

  function panes() {
    if (!map.getPane("historia")) map.createPane("historia").style.zIndex = 640;
  }

  function posA(T) {
    const P = h.punts;
    if (T <= P[0].ts) return { p: P[0], ll: [P[0].lat, P[0].lng], i: 0 };
    let i = P[h.iPos].ts <= T ? h.iPos : 0;
    while (i + 1 < P.length && P[i + 1].ts <= T) i++;
    h.iPos = i;
    const a = P[i], b = P[i + 1];
    if (!b || b.ts - a.ts > SALT_MAX) return { p: a, ll: [a.lat, a.lng], i };
    const f = (T - a.ts) / (b.ts - a.ts);
    return { p: a, ll: [a.lat + f * (b.lat - a.lat), a.lng + f * (b.lng - a.lng)], i };
  }

  function construeix() {
    panes();
    const j = h.j, P = h.punts;
    h.capa = L.layerGroup().addTo(map);
    const tot = P.map((p) => [p.lat, p.lng]);
    L.polyline(tot, { color: colorLinea(j.linea), weight: 2, opacity: 0.25, dashArray: "4 6", pane: "historia",
      interactive: false }).addTo(h.capa);
    h.traca = L.polyline([], { color: colorLinea(j.linea), weight: 4, opacity: 0.9, pane: "historia",
      interactive: false }).addTo(h.capa);
    h.marca = L.marker(tot[0], {
      icon: L.divIcon({ className: "hist-icona", html: `<span class="hist-bus" style="background:${colorLinea(j.linea)}"></span>`,
        iconSize: [16, 16], iconAnchor: [8, 8] }),
      pane: "historia", interactive: false, keyboard: false,
    }).addTo(h.capa);
    const estret = window.innerWidth < 600;
    map.fitBounds(L.latLngBounds(tot), {
      paddingTopLeft: [40, estret ? 60 : 200], paddingBottomRight: [40, estret ? 240 : 40], maxZoom: 16, animate: false,
    });
  }

  function marcaFi(si) {
    if (!si) { if (h.marcaFi) { h.capa.removeLayer(h.marcaFi); h.marcaFi = null; } return; }
    if (h.marcaFi) return;
    const j = h.j, u = h.punts[h.punts.length - 1];
    const viu = j.estat === "viu";
    const txt = viu ? "Continua en directe"
      : `<b>Es deixa de veure</b> a les ${hora(j.fi)}` + (j.prop ? `, prop de ${esc(j.prop.nom)}` : "");
    h.marcaFi = L.marker([u.lat, u.lng], {
      icon: L.divIcon({ className: "", html: `<div class="hist-fi ${viu ? "viu" : "fi"}" aria-hidden="true">${viu ? "●" : "■"}</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12] }),
      pane: "historia", interactive: false, keyboard: false,
    }).bindTooltip(txt, { permanent: true, direction: "top", offset: [0, -14], className: "hist-tt" }).addTo(h.capa);
  }

  function pinta() {
    const T = h.T;
    const { p, ll, i } = posA(T);
    h.marca.setLatLng(ll);
    const pts = h.punts.slice(0, i + 1).map((q) => [q.lat, q.lng]);
    pts.push(ll);
    h.traca.setLatLngs(pts);
    marcaFi(T >= h.fi);
    $("hist-hora").textContent = hora(T, true);
    $("hist-pos").value = Math.round(1000 * (T - h.ini) / Math.max(1, h.fi - h.ini));
    $("hist-ara").innerHTML = `${chipLinia(p.linea)}<span class="hist-mut">${esc(p.trayecto || "")}</span>`;
  }

  function tic() {
    if (!h) return;
    const ara = performance.now();
    const dt = h.ultim ? (ara - h.ultim) / 1000 : 0;
    h.ultim = ara;
    if (h.enMarxa) {
      h.T = Math.min(h.fi, h.T + dt * h.vel);
      if (h.T >= h.fi) posaMarxa(false);
    }
    pinta();
  }

  function posaMarxa(si) {
    if (si && h.T >= h.fi) h.T = h.ini;
    h.enMarxa = si;
    const b = $("hist-play");
    b.textContent = si ? "❚❚" : "▶";
    b.setAttribute("aria-label", si ? "Pausa" : "Reprodueix");
  }

  function panell() {
    const j = h.j;
    const viu = j.estat === "viu";
    const estat = viu
      ? `<span class="hist-estat viu">Encara circula</span>` +
        (window.busViu && window.busViu(j.id) ? ` <button type="button" class="hist-seguix" data-viu="${j.id}">En directe →</button>` : "")
      : `<span class="hist-estat fi">Es deixa de veure a les ${hora(j.fi)}${j.prop ? `, prop de ${esc(j.prop.nom)}` : ""}</span>`;
    const vels = [30, 60, 120, 300, 600];
    const passos = (j.passos || []).slice().reverse();
    const div = document.createElement("section");
    div.id = "hist-panell";
    div.setAttribute("aria-label", `Recorregut del bus ${j.id}`);
    div.innerHTML =
      `<div class="hist-cap">${capCompartir(`${chipLinia(j.linea)}<span class="pop-tray">${esc(j.trayecto || "")}</span> ` +
        `<span class="pop-id">#${j.id}</span>`, String(j.id), `el bus ${j.linea} #${j.id}`, "pop-bus-nom")}` +
      `<button type="button" class="hist-tanca" aria-label="Tanca el recorregut">×</button></div>` +
      `<div class="hist-sub">${estat} <span class="hist-mut">${hora(h.ini)}–${hora(h.fi)}` +
      ` · ${j.viatges} ${j.viatges === 1 ? "viatge" : "viatges"}${j.linies.length > 1 ? " · línies " + j.linies.join(", ") : ""}</span></div>` +
      `<div class="hist-ctrl"><button type="button" id="hist-play" aria-label="Pausa">❚❚</button>` +
      `<span id="hist-hora"></span>` +
      `<input id="hist-pos" type="range" min="0" max="1000" value="0" aria-label="Moment del recorregut">` +
      `<select id="hist-vel" aria-label="Velocitat">` +
      vels.map((v) => `<option value="${v}"${v === h.vel ? " selected" : ""}>×${v}</option>`).join("") +
      `</select></div>` +
      `<div id="hist-ara" class="hist-ret"></div>` +
      `<details class="hist-llista"><summary>Parades per on ha passat (${passos.length})</summary>` +
      `<div class="pop-reg-llista">${passos.length ? passos.map((f) =>
        `<div class="reg-row"><span class="reg-h">${hm(f.ts)}</span>${chipLinia(f.linea)}<span class="reg-nom">${esc(f.nom)}</span></div>`).join("")
        : "<div class='pop-buida'>cap parada registrada</div>"}</div></details>`;
    document.body.appendChild(div);
    lligaCompartir(div);
    div.querySelector(".hist-tanca").onclick = tanca;
    const seg = div.querySelector(".hist-seguix");
    if (seg) seg.onclick = () => { tanca(); window.obrirEnllac(seg.dataset.viu); };
    $("hist-play").onclick = () => posaMarxa(!h.enMarxa);
    $("hist-vel").onchange = (e) => { h.vel = Number(e.target.value); };
    $("hist-pos").oninput = (e) => {
      h.T = h.ini + (h.fi - h.ini) * Number(e.target.value) / 1000;
      pinta();
    };
  }

  function tanca() {
    if (!h) return;
    clearInterval(h.timer);
    map.removeLayer(h.capa);
    const p = $("hist-panell");
    if (p) p.remove();
    document.body.classList.remove("mode-historia");
    const id = String(h.j.id);
    h = null;
    llevaEnllac(id);
  }

  window.historiaActiva = () => !!h;
  window.historiaBus = async function (id) {
    tanca();
    posaEnllac(String(id));
    let j;
    try {
      const r = await fetch(`api/historia_bus?id=${id}&_=${Date.now()}`, { cache: "no-store" });
      if (r.status === 404) {
        avisCurt(`No hi ha registre del bus #${id}: es guarden els últims 7 dies`, 6000);
        llevaEnllac(String(id));
        return;
      }
      j = await r.json();
    } catch (e) {
      avisCurt("No s'ha pogut carregar el recorregut del bus", 5000);
      return;
    }
    if (!j || !j.punts || !j.punts.length) { llevaEnllac(String(id)); return; }
    h = { j, punts: j.punts, ini: j.punts[0].ts, fi: j.punts[j.punts.length - 1].ts, iPos: 0 };
    const dur = h.fi - h.ini;
    h.vel = dur > 3 * 3600 ? 300 : dur > 3600 ? 120 : 60;
    h.T = h.ini;
    h.enMarxa = true;
    map.closePopup();
    document.body.classList.add("mode-historia");
    construeix();
    panell();
    pinta();
    h.timer = setInterval(tic, 100);
  };
  window.tancaHistoria = tanca;
})();
