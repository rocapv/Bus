"""
Identitat dels busos entre pasades.

El geoportal no numera els busos: el `gid` es reassigna a cada consulta. Fins al
06/11/2026 la identitat la reconstruïa cada navegador per proximitat, i per
això no hi havia ni enllaç a un bus, ni recorregut, ni registre de pas. Ara la
dona el servidor, com el `track_id` de MetroViu, però amb una avantatja enorme:
ací la posició és GPS real, no estimada.

    assigna(prev, cur, dt, perduts, next_id) -> (ids, next_id)

- Mateixa línia, el més pròxim, amb porta de distància segons el temps que ha
  passat (un bus no fa més de VEL_MAX m/s). Si el sentit (`trayecto`) canvia,
  costa PEN_SENTIT metres: en capçalera el bus gira i canvia de sentit però és
  el MATEIX bus (MetroViu va necessitar tot un mecanisme de relleu per a açò;
  amb GPS, el bus que ix és el que estava parat al mateix lloc).
- Un bus que no apareix en una pasada (forat del GPS o del geoportal) es pot
  retrobar fins a RESCAT_S després, prop d'on es va perdre.
- Emparellament voraç per cost creixent: amb ~300 busos i la porta de
  distància no cal res més fi.
"""
from __future__ import annotations

import math

VEL_MAX = 22.0        # m/s (~80 km/h): per damunt, no és el mateix bus
MARGE_M = 250.0       # m de soroll del GPS i de la pasada
PEN_SENTIT = 300.0    # m de penalització si el sentit (trayecto) és un altre
RESCAT_S = 900        # s: un bus perdut fa menys d'açò es pot retrobar
RESCAT_MAX_M = 3000.0


def metres(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    return math.hypot((a_lat - b_lat) * 111_000,
                      (a_lng - b_lng) * math.cos(math.radians(a_lat)) * 111_000)


def _porta(dt: float) -> float:
    return MARGE_M + VEL_MAX * max(0.0, dt)


def assigna(prev: list[dict], cur: list, dt: float, perduts: list[dict], next_id: int):
    """prev:    busos de la pasada anterior [{id, linea, trayecto, lat, lng}].
    cur:     busos d'ara (objectes amb .linea .trayecto .lat .lng).
    dt:      segons entre les dues pasades.
    perduts: busos que fa menys de RESCAT_S que no es veuen
             [{id, linea, trayecto, lat, lng, ts_fi}] amb `ara` implícit en `edat`
             (segons des que es van perdre).
    Torna (ids, next_id): ids[i] = id del bus cur[i]."""
    ids: list[int | None] = [None] * len(cur)
    parelles = []
    porta = _porta(dt)
    per_linia: dict[str, list[dict]] = {}
    for p in prev:
        per_linia.setdefault(p["linea"], []).append(p)
    for i, c in enumerate(cur):
        for p in per_linia.get(c.linea, ()):
            d = metres(c.lat, c.lng, p["lat"], p["lng"])
            if d > porta:
                continue
            cost = d + (PEN_SENTIT if (p.get("trayecto") or "") != (c.trayecto or "") else 0.0)
            parelles.append((cost, i, p["id"]))
    usats: set[int] = set()
    for _c, i, pid in sorted(parelles):
        if ids[i] is None and pid not in usats:
            ids[i] = pid
            usats.add(pid)

    # rescat: els que no han casat, amb els perduts recents de la mateixa línia
    parelles = []
    for i, c in enumerate(cur):
        if ids[i] is not None:
            continue
        for p in perduts:
            if p["id"] in usats or p["linea"] != c.linea:
                continue
            d = metres(c.lat, c.lng, p["lat"], p["lng"])
            if d <= min(RESCAT_MAX_M, _porta(p.get("edat", RESCAT_S))):
                parelles.append((d, i, p["id"]))
    for _d, i, pid in sorted(parelles):
        if ids[i] is None and pid not in usats:
            ids[i] = pid
            usats.add(pid)

    for i in range(len(cur)):
        if ids[i] is None:
            ids[i] = next_id
            next_id += 1
    return ids, next_id
