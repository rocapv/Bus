"""
Poller de Bus: llig la capa «Buses EMT» del geoportal de València una vegada per
minut, dona a cada bus el seu número (seguiment.py), ho guarda compacte
(lectures.py) i apunta per quines parades ha passat cada bus (passos.py).

Fins al 06/11/2026 (EMTropello) el geoportal només es consultava quan algú
mirava el mapa i es guardaven les últimes 20 pasades: no hi havia ni història
ni registre. Ara el poller va sempre, com a MetroViu, i tot es guarda 7 dies.

    python -m backend.poller --once
    python -m backend.poller               # bucle (INTERVAL s)

Taules (data/bus.db, en WAL):
    snapshots(id, ts, n)            una per pasada
    lectures(snapshot_id, ts, dades) els busos de la pasada (blob)
    busos(id, linea, trayecto, ts_ini, ts_fi, lat, lng)   l'últim estat de cada bus
    passos(...)                     pas per parada
    xat(...)                        missatges
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path

import requests

from . import lectures, passos, seguiment, xat
from .arcgis import fetch_buses

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bus.db"
STOPS_FILE = ROOT / "data" / "stops.json"
PURGA_FILE = ROOT / "data" / "ultima_purga.txt"

INTERVAL = 60          # s entre pasades (el GPS d'EMT refresca cada ~20-30 s)
RETENIR_DIES = 7       # lectures, passos, busos i xat: una setmana
FORAT_MAX = 600        # s: més que açò entre dues pasades = no es calculen passos pel mig

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  INTEGER NOT NULL,
    n   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
CREATE TABLE IF NOT EXISTS busos (
    id       INTEGER PRIMARY KEY,
    linea    TEXT    NOT NULL,
    trayecto TEXT,
    ts_ini   INTEGER NOT NULL,
    ts_fi    INTEGER NOT NULL,
    lat      REAL    NOT NULL,
    lng      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_busos_fi ON busos(ts_fi);
"""


def connecta(timeout: float = 30) -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=timeout)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA secure_delete=OFF")      # dades públiques: no reescriure amb zeros (la SD)
    return con


def init_db() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    con = connecta()
    if con.execute("PRAGMA page_count").fetchone()[0] == 0:
        con.execute("PRAGMA auto_vacuum=INCREMENTAL")
    con.executescript(SCHEMA)
    lectures.init_db(con)
    passos.init_db(con)
    xat.init_db(con)
    con.commit()
    return con


_idx: passos.Index | None = None


def index_parades() -> passos.Index:
    global _idx
    if _idx is None:
        stops = json.loads(STOPS_FILE.read_text(encoding="utf-8")) if STOPS_FILE.exists() else []
        _idx = passos.Index(stops)
    return _idx


def poll_once(con: sqlite3.Connection, session: requests.Session) -> int:
    buses = fetch_buses(session)
    ts = int(time.time())
    prev_row = con.execute("SELECT id, ts, n FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    # Guarda anti-pasada-anòmala: a vegades el geoportal torna dades parcials (50 en
    # lloc de ~280). Es descarta la pasada i es conserva l'última bona.
    if not buses or (prev_row and prev_row[2] > 80 and len(buses) < 0.5 * prev_row[2]):
        print(f"[{time.strftime('%H:%M:%S')}] pasada anòmala descartada: "
              f"{len(buses)} busos (anterior {prev_row[2] if prev_row else 0})")
        return prev_row[0] if prev_row else -1

    prev, dt_ = [], 0.0
    if prev_row:
        dt_ = float(ts - prev_row[1])
        prev = [{"id": f[0], "linea": f[1], "trayecto": f[2], "lat": f[3], "lng": f[4]}
                for f in lectures.de_pasada(con, prev_row[0])]
    vius_abans = {p["id"] for p in prev}
    perduts = [{"id": i, "linea": l, "trayecto": tr, "lat": la, "lng": ln, "edat": ts - tf}
               for i, l, tr, la, ln, tf in con.execute(
                   "SELECT id, linea, trayecto, lat, lng, ts_fi FROM busos WHERE ts_fi>=? AND ts_fi<?",
                   (ts - seguiment.RESCAT_S, prev_row[1] if prev_row else ts))
               if i not in vius_abans]
    next_id = (con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM busos").fetchone()[0])
    ids, _ = seguiment.assigna(prev, buses, dt_, perduts, next_id)

    cur = con.execute("INSERT INTO snapshots(ts, n) VALUES (?, ?)", (ts, len(buses)))
    snap_id = cur.lastrowid
    lectures.guarda(con, snap_id, ts, [(ids[i], b.linea, b.trayecto, round(b.lat, 6), round(b.lng, 6), b.fecha)
                                       for i, b in enumerate(buses)])
    # estat de cada bus i trams recorreguts (per als passos per parada)
    abans = {p["id"]: p for p in prev}
    ultim = {i: (la, ln, tf) for i, la, ln, tf in con.execute(
        f"SELECT id, lat, lng, ts_fi FROM busos WHERE id IN ({','.join('?' * len(ids))})", ids)}
    moviments = []
    for i, b in enumerate(buses):
        tid = ids[i]
        if tid in ultim:
            la0, ln0, t0 = ultim[tid]
            if ts - t0 <= FORAT_MAX:
                moviments.append((tid, b.linea, b.trayecto, la0, ln0, t0, b.lat, b.lng, ts))
            con.execute("UPDATE busos SET linea=?, trayecto=?, ts_fi=?, lat=?, lng=? WHERE id=?",
                        (b.linea, b.trayecto, ts, b.lat, b.lng, tid))
        else:
            con.execute("INSERT INTO busos(id, linea, trayecto, ts_ini, ts_fi, lat, lng) VALUES (?,?,?,?,?,?,?)",
                        (tid, b.linea, b.trayecto, ts, ts, b.lat, b.lng))
    n_passos = passos.registra(con, index_parades(), moviments)
    con.commit()
    nous = sum(1 for i in ids if i >= next_id)
    retro = sum(1 for i in ids if i not in abans and i < next_id)
    print(f"[{time.strftime('%H:%M:%S')}] pasada {snap_id}: {len(buses)} busos, "
          f"{len({b.linea for b in buses})} línies, {nous} nous, {retro} retrobats, {n_passos} passos")
    return snap_id


def purga(con: sqlite3.Connection) -> dict:
    """Tot el que té més de RETENIR_DIES dies. Sense secure_delete, esborrar és
    marcar pàgines lliures; l'auto_vacuum incremental les torna a poc a poc."""
    fins = int(time.time()) - RETENIR_DIES * 86400
    res = {"lectures": lectures.purga(con, fins), "passos": passos.purga(con, fins)}
    cur = con.execute("DELETE FROM busos WHERE ts_fi<?", (fins,))
    res["busos"] = cur.rowcount
    cur = con.execute("DELETE FROM snapshots WHERE ts<?", (fins,))
    res["snapshots"] = cur.rowcount
    con.commit()
    res["xat"] = xat.purgar(con)
    libres = con.execute("PRAGMA freelist_count").fetchone()[0]
    if libres:
        con.execute(f"PRAGMA incremental_vacuum({libres})")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return res


def _purga_diaria(con: sqlite3.Connection) -> None:
    try:
        ultima = float(PURGA_FILE.read_text().strip())
    except Exception:  # noqa: BLE001
        ultima = 0.0
    if time.time() - ultima > 20 * 3600 and dt.datetime.now().hour in (3, 4):
        print("purga:", purga(con))
        PURGA_FILE.write_text(str(int(time.time())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="una sola pasada i eixir")
    ap.add_argument("--interval", type=int, default=INTERVAL)
    args = ap.parse_args()
    con = init_db()
    session = requests.Session()
    if args.once:
        poll_once(con, session)
        return
    while True:
        t0 = time.time()
        try:
            poll_once(con, session)
            _purga_diaria(con)
        except Exception as e:  # noqa: BLE001 - el bucle no ha de morir
            print("error en pasada:", e)
        time.sleep(max(1.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
