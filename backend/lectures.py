"""
Pasades del geoportal, compactes: UNA fila per pasada (com a MetroViu des del
04/11/2026, on la taula d'una fila per arribada s'havia menjat 990 MB).

    lectures(snapshot_id PK, ts, dades)
    dades = zlib(JSON [[id, linea, trayecto, lat, lng, fecha], ...])

~300 busos per pasada, una pasada per minut: uns 5 KB per pasada, ~7 MB per dia.
"""
from __future__ import annotations

import json
import sqlite3
import zlib

DDL = """
CREATE TABLE IF NOT EXISTS lectures (
    snapshot_id INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    dades       BLOB    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lectures_ts ON lectures(ts);
"""


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def empaqueta(files: list) -> bytes:
    return zlib.compress(json.dumps([list(f) for f in files], ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8"), 6)


def desempaqueta(blob: bytes) -> list[list]:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def gps_real(fecha_ms: int, ts: int) -> float | None:
    """Instant real (epoch s) del GPS d'un bus. El `fecha` del geoportal és l'hora
    LOCAL escrita com si fóra UTC (ve 1 h avançat a l'hivern, 2 a l'estiu), i ~1 de
    cada 10 lectures ve sense eixe desfasament: el mateix bus salta una hora amunt i
    avall d'una pasada a l'altra. Es lleva el nombre sencer d'hores que el separa de
    l'hora de la pasada (`ts`): el GPS real sempre és d'uns segons abans."""
    if not fecha_ms:
        return None
    f = fecha_ms / 1000
    return round(f - round((f - ts) / 3600) * 3600, 1)


def guarda(con: sqlite3.Connection, snap_id: int, ts: int, files: list) -> None:
    con.execute("INSERT OR REPLACE INTO lectures(snapshot_id, ts, dades) VALUES (?,?,?)",
                (snap_id, ts, empaqueta(files)))


def de_pasada(con: sqlite3.Connection, snap_id: int) -> list[list]:
    r = con.execute("SELECT dades FROM lectures WHERE snapshot_id=?", (snap_id,)).fetchone()
    return desempaqueta(r[0]) if r else []


def entre(con: sqlite3.Connection, t0: int, t1: int):
    """(ts, files) de les pasades amb t0 <= ts <= t1, en ordre."""
    for ts, blob in con.execute("SELECT ts, dades FROM lectures WHERE ts BETWEEN ? AND ? "
                                "ORDER BY ts", (t0, t1)):
        yield ts, desempaqueta(blob)


def purga(con: sqlite3.Connection, fins_ts: int) -> int:
    cur = con.execute("DELETE FROM lectures WHERE ts<?", (fins_ts,))
    con.commit()
    return cur.rowcount
