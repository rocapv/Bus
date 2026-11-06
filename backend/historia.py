"""
Recorregut d'un bus (encara que ja no circule) i enllaços de les parades.

    historia(con, stops, id) -> {id, linea, trayecto, estat, punts, passos, fi}

`punts` són les posicions GPS de cada pasada (lectures), de la primera a l'última
vegada que es va veure; `passos`, les parades per on va passar (passos.py). Un
bus conserva el número tot el torn (seguiment.py: en capçalera canvia de sentit
però és el mateix), així que el recorregut és el torn sencer, amb tots els
viatges.

Enllaços: bus.pr1.es/<parada> amb el nom en minúscules, sense accents ni
símbols; si dues parades es diuen igual (62 noms, 131 parades: «Porta de la
Mar» n'hi ha 5), s'hi afig el número de la parada (portadelamar771).
bus.pr1.es/<número> és un bus.
"""
from __future__ import annotations

import re
import unicodedata

from . import lectures, passos


def slug(nom: str) -> str:
    s = unicodedata.normalize("NFD", nom or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]", "", s)


def enllacos(stops: list[dict]) -> dict[int, str]:
    """{id de parada: tros d'URL}. La web fa el mateix (app.js, enllacParada)."""
    n: dict[str, int] = {}
    for s in stops:
        k = slug(s.get("nom") or "")
        n[k] = n.get(k, 0) + 1
    out = {}
    for s in stops:
        k = slug(s.get("nom") or "") or "parada"
        out[s["id"]] = k if n.get(k) == 1 else f"{k}{s['id']}"
    return out


_cache: dict[int, dict] = {}     # recorreguts de busos que ja no circulen (no canvien)


def historia(con, stops: dict, track_id: int) -> dict | None:
    if track_id in _cache:
        return _cache[track_id]
    r = con.execute("SELECT linea, trayecto, ts_ini, ts_fi, lat, lng FROM busos WHERE id=?",
                    (track_id,)).fetchone()
    if not r:
        return None
    linea, trayecto, t0, t1, lat, lng = r
    ultima = con.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    punts = []
    for ts, files in lectures.entre(con, t0, t1):
        for f in files:
            if f[0] == track_id:
                punts.append({"ts": ts, "lat": f[3], "lng": f[4], "linea": f[1], "trayecto": f[2]})
                break
    if not punts:
        return None
    ps = passos.de_bus(con, [track_id])
    for p in ps:
        s = stops.get(p["parada"])
        p["nom"] = s["nom"] if s else str(p["parada"])
    viu = t1 == ultima
    prop = None
    if not viu:      # on va acabar: la parada més pròxima a l'última posició
        from .seguiment import metres
        millor = min(stops.values(), key=lambda s: metres(lat, lng, s["lat"], s["lng"]), default=None)
        if millor:
            prop = {"nom": millor["nom"], "m": round(metres(lat, lng, millor["lat"], millor["lng"]))}
    res = {"id": track_id, "linea": linea, "trayecto": trayecto,
           "estat": "viu" if viu else "fi", "ini": t0, "fi": t1, "prop": prop,
           "linies": sorted({p["linea"] for p in punts}),
           "viatges": 1 + sum((a["linea"], a["trayecto"]) != (b["linea"], b["trayecto"])
                              for a, b in zip(punts, punts[1:])),
           "punts": punts, "passos": ps}
    if not viu:
        _cache[track_id] = res
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return res
