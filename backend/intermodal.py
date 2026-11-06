"""
Ruta combinada metro + bus entre dos punts del mapa (MetroViu + Bus).

Graf: parades d'EMT (data/stops.json) + estacions de Metrovalència (de l'API de
MetroViu, /api/network) + l'origen i el destí. Arestes:

  - a peu: origen/destí <-> parades i estacions a menys de WALK_MAX m; transbords
    a peu entre parades/estacions a menys de TRANSBORD_MAX m (distància en línia
    recta x DETOUR_PEU, a PEU_MS m/s);
  - en vehicle: de qualsevol parada d'una línia a qualsevol altra de la mateixa
    línia. Metro/tramvia: distància sobre la via (la `d` de cada estació) i
    l'espera REAL dels panells de FGV (/api/arrivals: el primer tren de la línia en
    el sentit bo que passa després d'arribar a l'estació); si no n'hi ha, una
    espera estimada. Bus: no hi ha previsió d'arribades, així que espera estimada;
    la distància entre parades és la del recorregut
    (precalculada per línia, data/intermodal_dist.json) i el tram triat es dibuixa
    pel traçat.

Dijkstra per hora d'arribada (els temps són segons des d'ara), amb una penalització
per cada pujada perquè no tri transbords absurds. Es donen fins a tres
alternatives: combinada, només metro i només bus (les repetides es lleven).
"""
from __future__ import annotations

import heapq
import json
import math
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
STOPS_FILE = ROOT / "data" / "stops.json"

# MetroViu: a la Raspa, l'uvicorn local; si no respon (Pecera), la web pública
METRO_APIS = ("http://127.0.0.1:8770", "https://metro.pr1.es")

PEU_MS = 1.2            # velocitat a peu (m/s)
DETOUR_PEU = 1.3        # carrers vs línia recta
DETOUR_BUS = 1.35       # recorregut del bus vs línia recta (només per a la recerca)
WALK_MAX = 1200         # m a peu fins a la primera parada / des de l'última
TRANSBORD_MAX = 400     # m a peu entre dues parades/estacions en un transbord
BUS_MS = 3.6            # ~13 km/h comercial (parades i semàfors inclosos)
METRO_MS = 8.0          # ~29 km/h comercial
TRAM_MS = 5.0           # ~18 km/h
ESPERA_BUS = 6 * 60     # espera estimada (sense previsió d'arribades)
ESPERA_METRO = 6 * 60   # quan els panells no diuen res
ESPERA_TRAM = 8 * 60
PENAL_PUJADA = 120      # s per pujada: evita transbords que guanyen segons
TRAMS = {4, 6, 8}       # tramvies (sense temps real); la L10 té panells
NET_TTL = 24 * 3600
ARR_TTL = 60

_lock = threading.Lock()
_metro_net: dict | None = None
_metro_net_ts = 0.0
_arr: dict | None = None
_arr_ts = 0.0
_bus_net: dict | None = None
_geo: dict = {}          # línia de bus -> graf del traçat (per a refer el tram)


# ------------------------------------------------------------------ utilitats
def dist_m(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def color_bus(linea: str) -> str:
    """El mateix to que colorLinea() de web/app.js (hash del codi -> HSL)."""
    h = 0
    for ch in linea:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"hsl({h % 360} 72% 60%)"


def _get_json(path: str, timeout: float = 8):
    err = None
    for base in METRO_APIS:
        try:
            req = urllib.request.Request(base + path, headers={"User-Agent": "bus-intermodal"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"MetroViu no respon: {err}")


def _metro():
    global _metro_net, _metro_net_ts
    if _metro_net is None or time.time() - _metro_net_ts > NET_TTL:
        _metro_net = _get_json("/api/network", timeout=15)
        _metro_net_ts = time.time()
    return _metro_net


def _arribades() -> dict:
    """Panells de FGV en directe; si fallen, buit (s'usen esperes estimades)."""
    global _arr, _arr_ts
    if _arr is None or time.time() - _arr_ts > ARR_TTL:
        try:
            _arr = _get_json("/api/arrivals")
        except Exception:  # noqa: BLE001
            _arr = _arr or {"estaciones": {}, "ts": None}
        _arr_ts = time.time()
    return _arr


def _bus():
    global _bus_net
    if _bus_net is None:
        stops = json.loads(STOPS_FILE.read_text(encoding="utf-8"))
        routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        per_linia: dict[str, list[int]] = {}
        for i, s in enumerate(stops):
            for l in s.get("lineas") or []:
                if l in routes:                       # nocturns sense traçat: fora
                    per_linia.setdefault(l, []).append(i)
        _bus_net = {"stops": stops, "routes": routes, "per_linia": per_linia}
    return _bus_net


# ------------------------------------------------------------------ distàncies pel traçat
CACHE_FILE = ROOT / "data" / "intermodal_dist.json"
_dist_bus: dict | None = None          # línia -> {"i,j": metres} (índexs de stops.json)


def _calcula_dist_bus() -> dict:
    """Per a cada línia, la distància pel seu traçat entre cada parell de parades
    (graf no dirigit: on cada sentit va per un carrer, és una aproximació per baix).
    Es guarda a disc fins que canvien routes.json o stops.json."""
    bus = _bus()
    out = {}
    for linea, idxs in bus["per_linia"].items():
        pos, adj = _graf_bus(linea)
        if not pos:
            continue
        claus = list(pos)
        nodes = {}
        for i in idxs:
            s = bus["stops"][i]
            k = min(claus, key=lambda kk: (pos[kk][0] - s["lat"]) ** 2 + ((pos[kk][1] - s["lng"]) * 0.77) ** 2)
            if dist_m(pos[k], (s["lat"], s["lng"])) < 120:
                nodes[i] = k
        per_node = {}
        for k in set(nodes.values()):
            dist, heap = {k: 0.0}, [(0.0, k)]
            while heap:
                d, x = heapq.heappop(heap)
                if d > dist[x]:
                    continue
                for y, w in adj.get(x, {}).items():
                    nd = d + w
                    if nd < dist.get(y, math.inf):
                        dist[y] = nd
                        heapq.heappush(heap, (nd, y))
            per_node[k] = dist
        dl = {}
        for i, ki in nodes.items():
            for j, kj in nodes.items():
                if i < j and kj in per_node[ki]:
                    dl[f"{i},{j}"] = round(per_node[ki][kj])
        out[linea] = dl
    return out


def _firma_dades() -> str:
    return f"{ROUTES_FILE.stat().st_mtime_ns}-{STOPS_FILE.stat().st_mtime_ns}"


def _carrega_dist_bus():
    global _dist_bus
    try:
        c = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if c.get("firma") == _firma_dades():
            _dist_bus = c["dist"]
            return
    except Exception:  # noqa: BLE001
        pass
    dist = _calcula_dist_bus()
    try:
        CACHE_FILE.write_text(json.dumps({"firma": _firma_dades(), "dist": dist}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    _dist_bus = dist


def prepara():
    """Distàncies pel traçat (mig segon a Pecera, uns segons a la Raspa; després, de disc)."""
    if _dist_bus is None:
        _carrega_dist_bus()


def _metres_bus(linea: str, i: int, j: int, a, b) -> float:
    if _dist_bus is not None:
        d = _dist_bus.get(linea, {}).get(f"{min(i, j)},{max(i, j)}")
        if d is not None:
            return d
    return dist_m(a, b) * DETOUR_BUS


# ------------------------------------------------------------------ el graf
class _Graf:
    """Nodes: 0 = origen, 1 = destí, després parades de bus i estacions de metro."""

    def __init__(self):
        bus, net = _bus(), _metro()
        self.nodes = [None, None]           # {tipus, lat, lng, nom, id}
        self.bus_idx = {}
        for i, s in enumerate(bus["stops"]):
            self.bus_idx[i] = len(self.nodes)
            self.nodes.append({"tipus": "bus", "lat": s["lat"], "lng": s["lng"],
                               "nom": s["nom"], "id": s["id"], "i": i})
        self.est_idx = {}
        self.est_nom = {}
        for e in net["estaciones"]:
            self.est_idx[e["id"]] = len(self.nodes)
            self.est_nom[e["nombre"]] = e["id"]
            self.nodes.append({"tipus": "metro", "lat": e["lat"], "lng": e["lng"],
                               "nom": e["nombre"], "id": e["id"]})
        # línies: per node, les línies que hi paren; per línia, els seus nodes
        self.linies: dict[str, dict] = {}
        self.de_node: dict[int, list[str]] = {}
        for l, idxs in bus["per_linia"].items():
            k = "b:" + l
            self.linies[k] = {"mode": "bus", "nom": l, "color": color_bus(l),
                              "nodes": [self.bus_idx[i] for i in idxs]}
        colors = net.get("colores_comercial") or net.get("colores") or {}
        for lid, lc in (net.get("comerciales") or {}).items():
            disp = int(lc.get("variante_de") or lid)
            if disp < 1 or disp > 10:
                continue
            ests = [e for e in lc.get("estaciones") or [] if e["id"] in self.est_idx]
            if len(ests) < 2:
                continue
            k = "m:" + str(lid)
            self.linies[k] = {"mode": "tram" if disp in TRAMS else "metro", "nom": f"L{disp}",
                              "lid": str(lid), "disp": disp,
                              "color": "#" + str(colors.get(str(disp)) or lc.get("color") or "888"),
                              "nodes": [self.est_idx[e["id"]] for e in ests],
                              "d": {self.est_idx[e["id"]]: e["d"] for e in ests},
                              "path": lc.get("path"), "cum": lc.get("cum")}
        for k, ln in self.linies.items():
            for n in ln["nodes"]:
                self.de_node.setdefault(n, []).append(k)
        # veïns a peu per a transbords (rejilla de ~450 m)
        self.cel = {}
        for n in range(2, len(self.nodes)):
            self.cel.setdefault(self._cel(self.nodes[n]), []).append(n)

    @staticmethod
    def _cel(p):
        return (int(p["lat"] / 0.004), int(p["lng"] / 0.005))

    def a_prop(self, p, radi):
        c0, c1 = self._cel(p)
        r = int(radi / 400) + 1
        out = []
        for i in range(c0 - r, c0 + r + 1):
            for j in range(c1 - r, c1 + r + 1):
                for n in self.cel.get((i, j), ()):
                    q = self.nodes[n]
                    d = dist_m((p["lat"], p["lng"]), (q["lat"], q["lng"]))
                    if d <= radi:
                        out.append((n, d))
        return out


_graf: _Graf | None = None
_graf_ts = 0.0


def _get_graf() -> _Graf:
    global _graf, _graf_ts
    with _lock:
        if _graf is None or time.time() - _graf_ts > NET_TTL:
            _graf = _Graf()
            _graf_ts = time.time()
        return _graf


# ------------------------------------------------------------------ esperes de metro
def _espera_metro(g: _Graf, arr: dict, ln: dict, n_pujada: int, n_baixada: int, t: float):
    """(espera en s, ve dels panells?, destí del tren). t = segons des d'ara."""
    est = g.nodes[n_pujada]["id"]
    d0, d1 = ln["d"][n_pujada], ln["d"][n_baixada]
    sentit = 1 if d1 > d0 else -1
    edat = arr.get("edad_seg") or 0
    millor = None
    for a in (arr.get("estaciones") or {}).get(str(est), []):
        if a.get("linea") != ln["disp"]:
            continue
        nd = g.est_idx.get(g.est_nom.get(a.get("destino")))
        dd = ln["d"].get(nd) if nd is not None else None
        if dd is None or (dd - d0) * sentit <= 0:
            continue                                 # va cap a l'altre costat
        eta = (a.get("eta_seg") or 0) - edat
        if eta >= t and (millor is None or eta < millor[0]):   # el que passa abans d'arribar, perdut
            millor = (eta, a.get("destino"))
    if millor:
        return max(0.0, millor[0] - t), True, millor[1]
    return (ESPERA_TRAM if ln["mode"] == "tram" else ESPERA_METRO), False, None


# ------------------------------------------------------------------ la recerca
def _dijkstra(g: _Graf, orig, dest, modes: set[str], arr: dict, fora: set[str]):
    g.nodes[0] = {"tipus": "punt", "lat": orig[0], "lng": orig[1], "nom": "Origen"}
    g.nodes[1] = {"tipus": "punt", "lat": dest[0], "lng": dest[1], "nom": "Destí"}
    p_dest = g.nodes[1]
    a_dest = {n: d for n, d in g.a_prop(p_dest, WALK_MAX)}
    best = {0: 0.0}
    cost = {0: 0.0}                                   # temps + penalitzacions (el que s'ordena)
    prev = {}                                         # node -> (node anterior, tram)
    heap = [(0.0, 0.0, 0)]
    directe = dist_m(orig, dest) * DETOUR_PEU / PEU_MS
    cost[1] = directe if directe <= 3 * 3600 else math.inf
    best[1] = directe
    prev[1] = (0, {"tipus": "peu"})
    while heap:
        c, t, n = heapq.heappop(heap)
        if c > cost.get(n, math.inf) or n == 1:
            continue
        if c >= cost.get(1, math.inf):
            break

        def relaxa(m, tm, cm, tram):
            if cm < cost.get(m, math.inf):
                cost[m], best[m], prev[m] = cm, tm, (n, tram)
                heapq.heappush(heap, (cm, tm, m))

        p = g.nodes[n]
        # a peu
        if n == 0:
            for m, d in g.a_prop(p, WALK_MAX):
                s = d * DETOUR_PEU / PEU_MS
                relaxa(m, t + s, c + s, {"tipus": "peu"})
            continue
        if n in a_dest:
            s = a_dest[n] * DETOUR_PEU / PEU_MS
            relaxa(1, t + s, c + s, {"tipus": "peu"})
        if prev.get(n, (None, {}))[1].get("tipus") != "peu":   # no encadena dos trams a peu
            for m, d in g.a_prop(p, TRANSBORD_MAX):
                if m != n:
                    s = d * DETOUR_PEU / PEU_MS
                    relaxa(m, t + s, c + s, {"tipus": "peu"})
        # en vehicle
        for k in g.de_node.get(n, ()):
            ln = g.linies[k]
            mode = ln["mode"]
            if (mode == "bus" and "bus" not in modes) or (mode != "bus" and "metro" not in modes):
                continue
            if k in fora:                             # ara mateix no circula
                continue
            if prev.get(n, (None, {}))[1].get("linia") == k:
                continue                              # baixar i tornar a pujar a la mateixa
            for m in ln["nodes"]:
                if m == n:
                    continue
                q = g.nodes[m]
                if mode == "bus":
                    espera, viu, cap = ESPERA_BUS, False, None
                    viatge = _metres_bus(ln["nom"], p["i"], q["i"], (p["lat"], p["lng"]),
                                         (q["lat"], q["lng"])) / BUS_MS
                else:
                    espera, viu, cap = _espera_metro(g, arr, ln, n, m, t)
                    viatge = abs(ln["d"][m] - ln["d"][n]) / (TRAM_MS if mode == "tram" else METRO_MS)
                tm = t + espera + viatge
                relaxa(m, tm, c + espera + viatge + PENAL_PUJADA,
                       {"tipus": mode, "linia": k, "espera": espera, "viu": viu, "cap": cap,
                        "viatge": viatge})
    if 1 not in prev:
        return None
    trams = []
    n = 1
    while n != 0:
        a, tr = prev[n]
        trams.append((a, n, tr))
        n = a
    trams.reverse()
    return trams


# ------------------------------------------------------------------ geometria dels trams
def _graf_bus(linea: str):
    """Graf no dirigit dels punts del traçat d'una línia (extrems units a ~10 m)."""
    if linea in _geo:
        return _geo[linea]
    paths = _bus()["routes"].get(linea, {}).get("paths") or []
    key = lambda p: (round(p[0] * 9000), round(p[1] * 7000))    # ~12 m
    pos, adj = {}, {}
    for path in paths:
        prevk = None
        for p in path:
            k = key(p)
            pos.setdefault(k, (p[0], p[1]))
            if prevk is not None and prevk != k:
                d = dist_m(pos[prevk], pos[k])
                adj.setdefault(prevk, {})[k] = d
                adj.setdefault(k, {})[prevk] = d
            prevk = k
    _geo[linea] = (pos, adj)
    return _geo[linea]


def _cami_bus(linea: str, a, b):
    """(punts, metres) pel traçat de la línia entre dos punts; None si no connecta."""
    pos, adj = _graf_bus(linea)
    if not pos:
        return None

    def prop(p):
        k, dk = None, math.inf
        for kk, q in pos.items():
            d = (q[0] - p[0]) ** 2 + ((q[1] - p[1]) * 0.77) ** 2
            if d < dk:
                k, dk = kk, d
        return k if dist_m(pos[k], p) < 120 else None

    ka, kb = prop(a), prop(b)
    if ka is None or kb is None:
        return None
    dist, ant, heap = {ka: 0.0}, {}, [(0.0, ka)]
    while heap:
        d, k = heapq.heappop(heap)
        if k == kb:
            break
        if d > dist[k]:
            continue
        for k2, w in adj.get(k, {}).items():
            nd = d + w
            if nd < dist.get(k2, math.inf):
                dist[k2], ant[k2] = nd, k
                heapq.heappush(heap, (nd, k2))
    if kb not in dist:
        return None
    pts, k = [], kb
    while True:
        pts.append(list(pos[k]))
        if k == ka:
            break
        k = ant[k]
    pts.reverse()
    return pts, dist[kb]


def _cami_metro(ln: dict, d0: float, d1: float):
    path, cum = ln.get("path"), ln.get("cum")
    if not path or not cum:
        return None
    lo, hi = min(d0, d1), max(d0, d1)

    def punt(d):
        for i in range(1, len(cum)):
            if cum[i] >= d:
                f = 0 if cum[i] == cum[i - 1] else (d - cum[i - 1]) / (cum[i] - cum[i - 1])
                return [path[i - 1][0] + f * (path[i][0] - path[i - 1][0]),
                        path[i - 1][1] + f * (path[i][1] - path[i - 1][1])]
        return list(path[-1])

    pts = [punt(lo)] + [list(path[i]) for i in range(len(cum)) if lo < cum[i] < hi] + [punt(hi)]
    if d1 < d0:
        pts.reverse()
    return pts


# ------------------------------------------------------------------ resultat
def _descriu(g: _Graf, trams, orig, dest, buses_vius) -> dict:
    out, t = [], 0.0
    for a, b, tr in trams:
        pa, pb = g.nodes[a], g.nodes[b]
        A, B = [pa["lat"], pa["lng"]], [pb["lat"], pb["lng"]]
        if tr["tipus"] == "peu":
            d = dist_m(A, B) * DETOUR_PEU
            s = d / PEU_MS
            if out and out[-1]["tipus"] == "peu":     # dos a peu seguits: un de sol
                out[-1].update({"a": pb["nom"], "fins": B, "metres": out[-1]["metres"] + round(d),
                                "min": out[-1]["min"] + s / 60})
                out[-1]["geo"].append(B)
            else:
                out.append({"tipus": "peu", "de": pa["nom"], "a": pb["nom"], "des_de": A, "fins": B,
                            "metres": round(d), "min": s / 60, "geo": [A, B]})
            t += s
            continue
        ln = g.linies[tr["linia"]]
        leg = {"tipus": tr["tipus"], "linia": ln["nom"], "color": ln["color"],
               "de": pa["nom"], "a": pb["nom"], "des_de": A, "fins": B,
               "espera_min": tr["espera"] / 60, "espera_viva": tr["viu"], "cap_a": tr["cap"]}
        viatge = tr["viatge"]
        if tr["tipus"] == "bus":
            cami = _cami_bus(ln["nom"], A, B)
            if cami:
                leg["geo"], metres = cami
                viatge = metres / BUS_MS
            else:
                leg["geo"] = [A, B]
            leg["parada_de"], leg["parada_a"] = pa["id"], pb["id"]
            vius = [x for x in buses_vius if x.get("linea") == ln["nom"]]
            leg["busos_en_servei"] = len(vius)
            if vius:
                leg["bus_mes_proper_m"] = round(min(dist_m(A, (x["lat"], x["lng"])) for x in vius))
        else:
            d0, d1 = ln["d"][a], ln["d"][b]
            leg["geo"] = _cami_metro(ln, d0, d1) or [A, B]
            lo, hi = min(d0, d1), max(d0, d1)
            leg["parades"] = sum(1 for n in ln["nodes"] if lo < ln["d"][n] <= hi)
            leg["estacio_de"], leg["estacio_a"] = pa["id"], pb["id"]
        leg["min"] = viatge / 60
        t += tr["espera"] + viatge
        out.append(leg)
    for leg in out:
        for k in ("min", "espera_min"):
            if k in leg:
                leg[k] = round(leg[k], 1)
    modes = sorted({l["tipus"] for l in out if l["tipus"] != "peu"})
    return {"min": round(t / 60, 1), "modes": modes, "trams": out,
            "peu_m": sum(l["metres"] for l in out if l["tipus"] == "peu"),
            "pujades": sum(1 for l in out if l["tipus"] != "peu")}


def _sense_servei(g: _Graf, arr: dict, bus_fora: set[str] | None) -> set[str]:
    """Línies que ara no circulen: les de bus que diu l'API (surten al geoportal però
    no en la darrera mitja hora) i les de metro sense cap tren als panells (si els
    panells són recents i en porten d'altres línies)."""
    fora = {k for k, ln in g.linies.items() if ln["mode"] == "bus" and ln["nom"] in (bus_fora or ())}
    ests = arr.get("estaciones") or {}
    if ests and (arr.get("edad_seg") or 0) < 900:
        amb = {a.get("linea") for v in ests.values() for a in v}
        if len(amb) >= 2:
            fora |= {k for k, ln in g.linies.items()
                     if ln["mode"] == "metro" and ln["disp"] not in amb}
    return fora


def ruta(orig, dest, buses_vius=(), bus_fora: set[str] | None = None) -> dict:
    """orig/dest = (lat, lng). Alternatives ordenades per temps. buses_vius: l'última
    pasada (per a dir quants n'hi ha de cada línia); bus_fora: línies de bus que ara
    no circulen (no entren en la recerca)."""
    g = _get_graf()
    arr = _arribades()
    fora = _sense_servei(g, arr, bus_fora)
    alts, vistes = [], set()
    for nom, modes in (("combinada", {"bus", "metro"}), ("metro", {"metro"}), ("bus", {"bus"})):
        with _lock:                                   # el graf guarda origen/destí als nodes 0/1
            prepara()
            trams = _dijkstra(g, orig, dest, modes, arr, fora)
            if not trams:
                continue
            alt = _descriu(g, trams, orig, dest, list(buses_vius))
        firma = tuple((l["tipus"], l.get("linia"), l["de"], l["a"]) for l in alt["trams"])
        if firma in vistes:
            continue
        vistes.add(firma)
        if not alt["pujades"] and alt["min"] > 40:      # «només a peu» d'hora i mitja: no és ruta
            continue
        alt["nom"] = nom
        alts.append(alt)
    alts.sort(key=lambda a: a["min"])
    return {"origen": list(orig), "desti": list(dest), "alternatives": alts,
            "fora_de_servei": sorted(g.linies[k]["nom"] for k in fora),
            "distancies_reals": _dist_bus is not None,
            "metro_viu": arr.get("ts"), "ts": int(time.time())}
