/* Bus · xat (portat de MetroViu el 06/11/2026). Cada bus i cada parada té els
   seus missatges, a la pestanya «Xat» del popup, i s'hi escriu allí mateix. Un
   missatge nou apareix 5 s com a bombolla damunt del seu bus o parada (un de
   nou esborra l'anterior). Sense comptes: àlies lliure; el servidor posa el
   límit de llargària i de missatges per IP. Usa els globals d'app.js. */
(function () {
  const BOMBOLLA_MS = 5000;
  let alias = "";
  try { alias = sessionStorage.getItem("bus_alias") || ""; } catch (e) { /* sense storage */ }
  let ultimId = 0, primera = true, carregant = false;
  let bombolla = null;   // {tt, marker, fins}

  function htmlMissatge(m) {
    const qui = m.alias ? `<b>${esc(m.alias)}</b>` : "<span class='xat-muted'>algú</span>";
    return `<div class="xat-msg"><span class="xat-hora">${hm(m.ts)}</span> ${qui}` +
      `<div class="xat-txt">${esc(m.text)}</div></div>`;
  }

  // la pestanya «Xat» d'un popup: missatges del lloc + formulari
  window.xatOmpliPopup = async function (root) {
    const el = root && root.querySelector(".pop-xat");
    if (!el || el.dataset.ple) return;
    el.dataset.ple = "1";
    const { tipus, lloc } = el.dataset;
    el.innerHTML = `<div class="pop-xat-llista xat-muted">carregant…</div>` +
      `<form class="xat-form"><input class="xat-alias" maxlength="24" placeholder="àlies (opcional)" value="${esc(alias)}">` +
      `<input class="xat-text" maxlength="200" placeholder="${tipus === "bus" ? "Escriu des d'este bus…" : "Escriu des d'esta parada…"}" required>` +
      `<button type="submit">Envia</button><div class="xat-avis"></div></form>`;
    const llista = el.querySelector(".pop-xat-llista");
    const form = el.querySelector("form");
    const avis = (t, suau) => { const a = el.querySelector(".xat-avis"); a.textContent = t; a.classList.toggle("suau", !!suau); };
    async function carrega() {
      try {
        const j = await (await fetch(`api/xat?tipus=${tipus}&lloc=${lloc}&n=20&_=${Date.now()}`, { cache: "no-store" })).json();
        const ms = j.missatges || [];
        llista.innerHTML = ms.length ? ms.map(htmlMissatge).join("") : "<span class='xat-muted'>encara no hi ha missatges</span>";
        llista.scrollTop = llista.scrollHeight;
      } catch (e) { llista.textContent = "sense connexió"; }
    }
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const text = form.querySelector(".xat-text").value.trim();
      if (!text) return;
      alias = form.querySelector(".xat-alias").value.trim().slice(0, 24);
      try { sessionStorage.setItem("bus_alias", alias); } catch (e) { /* sense storage */ }
      const b = form.querySelector("button");
      b.disabled = true;
      try {
        const r = await fetch("api/xat", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tipus, lloc: Number(lloc), alias, text }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.status);
        form.querySelector(".xat-text").value = "";
        avis("Enviat.", true);
        await carrega();
        ultimId = Math.max(ultimId, j.id || 0);
      } catch (e) {
        avis("No s'ha enviat: " + e.message);
      } finally { b.disabled = false; }
    };
    await carrega();
  };

  function mostraBombolla(m) {
    const marker = m.tipus === "bus" ? window.marcadorBus(m.lloc) : window.marcadorParada(m.lloc);
    if (!marker) return;
    if (bombolla) map.removeLayer(bombolla.tt);
    const tt = L.tooltip({ permanent: true, direction: "top", offset: [0, -12], className: "xat-bombolla", interactive: false })
      .setLatLng(marker.getLatLng())
      .setContent(`${m.alias ? `<b>${esc(m.alias)}</b>: ` : ""}${esc(m.text)}`)
      .addTo(map);
    bombolla = { tt, marker, fins: Date.now() + BOMBOLLA_MS };
  }
  setInterval(() => {
    if (!bombolla) return;
    if (Date.now() > bombolla.fins) { map.removeLayer(bombolla.tt); bombolla = null; }
    else bombolla.tt.setLatLng(bombolla.marker.getLatLng());   // segueix el bus
  }, 250);

  // la crida vigilaServidor (app.js) amb l'últim id que coneix el servidor
  window.xatNovetats = async function (xatId) {
    if (primera) { ultimId = xatId; primera = false; return; }   // en obrir: sense bombolles velles
    if (xatId <= ultimId || carregant) return;
    carregant = true;
    try {
      const j = await (await fetch(`api/xat?des_de=${ultimId}&n=20&_=${Date.now()}`, { cache: "no-store" })).json();
      const ms = j.missatges || [];
      if (ms.length) mostraBombolla(ms[ms.length - 1]);
      ultimId = Math.max(ultimId, j.ultim_id || 0, ...ms.map((m) => m.id));
    } catch (e) { /* ja es reintentarà */ } finally { carregant = false; }
  };
  window.iniciaXat = function () { /* el feed arrenca amb el primer vigilaServidor */ };
})();
