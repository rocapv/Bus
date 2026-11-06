"""
Pas dels busos per les parades (el «Registre» d'una parada i el d'un bus).

Entre dues pasades el bus recorre un tram (posició anterior -> actual). Una
parada d'eixa línia passa si queda a menys de RADI_M del tram, i l'hora es
reparteix al llarg del tram segons on cau la parada. Amb una pasada per minut un
bus fa uns 300 m entre lectures: mirar només la posició (i no el tram) perdia
quasi totes les parades.

Sentit: les parades d'anada i de tornada estan a banda i banda del carrer, a
15-30 m. A València es circula per la dreta i la parada queda a la DRETA del
bus: una parada clarament a l'esquerra del tram (més de LATERAL_M) és la de
l'altre sentit i no compta. El tram és recte i el carrer pot fer corba: en les
corbes tancades es pot perdre o colar alguna parada.

Un mateix bus no torna a «passar» per la mateixa parada en REPETIX_S (un bus
parat a la parada o fent cua dona diverses lectures al costat).
"""
from __future__ import annotations

import math
import sqlite3

RADI_M = 30.0
LATERAL_M = 6.0
REPETIX_S = 1200
CEL = 0.002            # graus (~200 m) de la quadrícula de parades

DDL = """
CREATE TABLE IF NOT EXISTS passos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    parada   INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    linea    TEXT    NOT NULL,
    trayecto TEXT,
    ts       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_passos_parada ON passos(parada, ts);
CREATE INDEX IF NOT EXISTS idx_passos_track ON passos(track_id, ts);
CREATE INDEX IF NOT EXISTS idx_passos_ts ON passos(ts);
"""


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


class Index:
    """Parades per línia en una quadrícula, per a buscar les del voltant d'un tram."""

    def __init__(self, stops: list[dict]):
        self.stops = {s["id"]: s for s in stops}
        self.grid: dict[tuple, list[dict]] = {}
        for s in stops:
            self.grid.setdefault(self._cel(s["lat"], s["lng"]), []).append(s)

    @staticmethod
    def _cel(lat: float, lng: float) -> tuple:
        return (int(math.floor(lat / CEL)), int(math.floor(lng / CEL)))

    def prop(self, lat0, lng0, lat1, lng1):
        a, b = self._cel(min(lat0, lat1), min(lng0, lng1)), self._cel(max(lat0, lat1), max(lng0, lng1))
        for i in range(a[0] - 1, b[0] + 2):
            for j in range(a[1] - 1, b[1] + 2):
                yield from self.grid.get((i, j), ())


def _proj(lat0, lng0, lat1, lng1, plat, plng):
    """(distància, t en [0,1], lateral) del punt p al tram 0->1, en metres.
    lateral > 0: a la dreta del sentit de marxa."""
    cf = math.cos(math.radians(plat)) * 111_000
    ax, ay = lng0 * cf, lat0 * 111_000
    bx, by = lng1 * cf, lat1 * 111_000
    px, py = plng * cf, plat * 111_000
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    qx, qy = ax + t * dx, ay + t * dy
    dist = math.hypot(px - qx, py - qy)
    lateral = 0.0 if l2 == 0 else ((dx * (py - ay) - dy * (px - ax)) / math.sqrt(l2)) * -1.0
    return dist, t, lateral


def detecta(idx: Index, linea: str, lat0, lng0, ts0: int, lat1, lng1, ts1: int) -> list[tuple[int, int]]:
    """[(parada, ts)] de les parades de `linea` per on passa el tram."""
    out = []
    for s in idx.prop(lat0, lng0, lat1, lng1):
        if linea not in s.get("lineas", ()):
            continue
        dist, t, lateral = _proj(lat0, lng0, lat1, lng1, s["lat"], s["lng"])
        if dist > RADI_M:
            continue
        if lateral < -LATERAL_M:
            continue                    # a l'esquerra: la parada de l'altre sentit
        out.append((s["id"], int(ts0 + t * (ts1 - ts0))))
    return sorted(out, key=lambda x: x[1])


def registra(con: sqlite3.Connection, idx: Index, moviments: list[tuple]) -> int:
    """moviments: [(track_id, linea, trayecto, lat0, lng0, ts0, lat1, lng1, ts1)].
    Guarda els passos nous (sense repetir en REPETIX_S). Torna quants."""
    n = 0
    for tid, linea, tray, la0, ln0, t0, la1, ln1, t1 in moviments:
        for parada, ts in detecta(idx, linea, la0, ln0, t0, la1, ln1, t1):
            if con.execute("SELECT 1 FROM passos WHERE track_id=? AND parada=? AND ts>?",
                           (tid, parada, ts - REPETIX_S)).fetchone():
                continue
            con.execute("INSERT INTO passos(parada, track_id, linea, trayecto, ts) VALUES (?,?,?,?,?)",
                        (parada, tid, linea, tray, ts))
            n += 1
    return n


def de_parada(con: sqlite3.Connection, parada: int, t0: int, t1: int) -> list[dict]:
    return [{"ts": ts, "id": tid, "linea": l, "trayecto": tr}
            for ts, tid, l, tr in con.execute(
                "SELECT ts, track_id, linea, trayecto FROM passos WHERE parada=? AND ts BETWEEN ? AND ? "
                "ORDER BY ts DESC", (parada, t0, t1))]


def de_bus(con: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    return [{"ts": ts, "parada": p, "linea": l, "trayecto": tr}
            for ts, p, l, tr in con.execute(
                f"SELECT ts, parada, linea, trayecto FROM passos WHERE track_id IN ({q}) ORDER BY ts",
                ids)]


def purga(con: sqlite3.Connection, fins_ts: int) -> int:
    cur = con.execute("DELETE FROM passos WHERE ts<?", (fins_ts,))
    con.commit()
    return cur.rowcount
