"""
API de Bus (FastAPI) — busos d'EMT València en directe (abans EMTropello).

    GET  /api/buses            busos de l'última pasada, amb el seu número (id)
    GET  /api/ultimo           marca de l'última pasada (+ últim id del xat)
    GET  /api/network          traçat de les línies i parades (amb el seu enllaç)
    GET  /api/registre_parada  busos que han passat per una parada (?id=&dia=)
    GET  /api/registre_bus     parades per on ha passat un bus (?id=)
    GET  /api/historia_bus     recorregut d'un bus encara que ja no circule (?id=)
    GET  /api/xat              missatges: ?tipus=&lloc= (d'un lloc) o ?des_de=ID (feed)
    POST /api/xat              publica {tipus, lloc, alias, text}
    GET  /api/health

Les dades les escriu el poller (servei a banda, una pasada per minut); ací només
es llig. Enllaços directes: bus.pr1.es/<parada> i bus.pr1.es/<número de bus>.

Accés (com MetroViu): en LAN sense token; de fora, token (data/token.txt), llevat
que data/open_until.txt diga `siempre` o una hora futura.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.convertors import Convertor, register_url_convertor

from . import historia, passos, poller, xat

ROOT = Path(__file__).resolve().parent.parent
DB = poller.DB
WEB = ROOT / "web"
ROUTES_FILE = ROOT / "data" / "routes.json"
STOPS_FILE = ROOT / "data" / "stops.json"
TOKEN_FILE = ROOT / "data" / "token.txt"
OPEN_FILE = ROOT / "data" / "open_until.txt"

MIN_DT_SEC = 40        # la pasada «anterior» per a la velocitat: entre 40 s...
MAX_DT_SEC = 200       # ... i 200 s abans (si no, velocitat falsa: tot parat)

app = FastAPI(title="Bus", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=2000)
poller.init_db().close()


def _token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return os.environ.get("BUS_TOKEN", "").strip()


def _is_lan(ip: str) -> bool:
    return (ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("192.168.")
            or ip.startswith("10.") or any(ip.startswith(f"172.{n}.") for n in range(16, 32)))


def _open_now() -> bool:
    """Finestra sense token: `siempre` (no caduca) o un epoch futur."""
    try:
        v = OPEN_FILE.read_text(encoding="utf-8").strip()
        return v.lower() == "siempre" or time.time() < float(v)
    except Exception:  # noqa: BLE001
        return False


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")   # Apache hi afig la IP real al final
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else ""


@app.middleware("http")
async def acces(request: Request, call_next):
    q = request.query_params.get("token")
    if _is_lan(_client_ip(request)) or _open_now():
        resp = await call_next(request)
    else:
        token = _token()
        ok = bool(token) and (q == token or request.cookies.get("bustoken") == token)
        if not ok:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        resp = await call_next(request)
        if q == token:
            arrel = request.headers.get("x-forwarded-prefix") == "/"   # bus.pr1.es (si no, /bus en LAN)
            resp.set_cookie("bustoken", token, max_age=31536000, path="/" if arrel else "/bus",
                            httponly=True, samesite="lax",
                            secure=request.headers.get("x-forwarded-proto") == "https")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    return con


_stops: list[dict] | None = None
_stops_id: dict[int, dict] = {}


def _parades() -> list[dict]:
    global _stops
    if _stops is None:
        _stops = json.loads(STOPS_FILE.read_text(encoding="utf-8")) if STOPS_FILE.exists() else []
        enll = historia.enllacos(_stops)
        for s in _stops:
            s["enllac"] = enll[s["id"]]
            _stops_id[s["id"]] = s
    return _stops


def _rang_dia(dia: str | None) -> tuple[int, int]:
    d = dt.date.fromisoformat(dia) if dia else dt.date.today()
    t0 = int(dt.datetime.combine(d, dt.time()).timestamp())
    return t0, t0 + 86400 - 1


@app.get("/api/buses")
def buses():
    from . import lectures
    con = _con()
    cur = con.execute("SELECT id, ts FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not cur:
        return {"ts": None, "pts": None, "n": 0, "busos": []}
    prev = con.execute("SELECT id, ts FROM snapshots WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1",
                       (cur["ts"] - MAX_DT_SEC, cur["ts"] - MIN_DT_SEC)).fetchone()
    abans = {f[0]: f for f in lectures.de_pasada(con, prev["id"])} if prev else {}
    busos = []
    for f in lectures.de_pasada(con, cur["id"]):
        p = abans.get(f[0])
        busos.append({"id": f[0], "linea": f[1], "trayecto": f[2], "lat": f[3], "lng": f[4],
                      "fecha": f[5], "plat": p[3] if p else None, "plng": p[4] if p else None})
    return {"ts": cur["ts"], "pts": prev["ts"] if prev else None,
            "edad_seg": int(time.time()) - cur["ts"], "n": len(busos), "busos": busos}


@app.get("/api/ultimo")
def ultimo():
    con = _con()
    r = con.execute("SELECT ts FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return {"ts": r["ts"] if r else None, "xat": xat.ultim_id(con)}


_net_cache: dict | None = None


@app.get("/api/network")
def network():
    global _net_cache
    if _net_cache is None:
        routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8")) if ROUTES_FILE.exists() else {}
        _net_cache = {"routes": routes, "stops": _parades()}
    return _net_cache


@app.get("/api/registre_parada")
def registre_parada(id: int, dia: str | None = None):
    _parades()
    if id not in _stops_id:
        return JSONResponse({"error": "parada desconeguda"}, status_code=404)
    t0, t1 = _rang_dia(dia)
    return {"parada": id, "passos": passos.de_parada(_con(), id, t0, t1)}


@app.get("/api/registre_bus")
def registre_bus(id: int):
    _parades()
    ps = passos.de_bus(_con(), [id])
    for p in ps:
        s = _stops_id.get(p["parada"])
        p["nom"] = s["nom"] if s else str(p["parada"])
        p["enllac"] = s["enllac"] if s else None
    return {"id": id, "passos": ps}


@app.get("/api/historia_bus")
def historia_bus(id: int):
    _parades()
    res = historia.historia(_con(), _stops_id, id)
    if res is None:
        return JSONResponse({"error": "no hi ha registre d'eixe bus"}, status_code=404)
    return res


@app.get("/api/xat")
def xat_get(tipus: str | None = None, lloc: int | None = None, des_de: int | None = None,
            n: int = 20):
    con = _con()
    n = max(1, min(n, xat.MAX_FEED))
    if tipus and lloc is not None:
        return {"missatges": xat.del_lloc(con, tipus, lloc, n)}
    return {"missatges": xat.des_de(con, des_de or 0, n), "ultim_id": xat.ultim_id(con)}


@app.post("/api/xat")
async def xat_post(request: Request):
    try:
        j = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "petició invàlida"}, status_code=400)
    _parades()
    con = _con()
    m, err = xat.publicar(con, _stops_id, _client_ip(request), j.get("tipus"), j.get("lloc"),
                          j.get("alias", ""), j.get("text", ""))
    if err:
        return JSONResponse({"error": err}, status_code=400)
    return m


@app.get("/api/health")
def health():
    con = _con()
    r = con.execute("SELECT ts, n FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return {"ok": True, "ultimo_ts": r["ts"] if r else None, "busos": r["n"] if r else 0,
            "edad_seg": int(time.time()) - r["ts"] if r else None}


# ---------------------------------------------------------------- enllaços
class _Enllac(Convertor):
    regex = "[a-z0-9]+"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor("enllac", _Enllac())
_enllacos: set[str] = set()


def _index():
    return FileResponse(WEB / "index.html")


@app.get("/", include_in_schema=False)
def inici():
    return _index()


@app.get("/{enllac:enllac}", include_in_schema=False)
def enllac(enllac: str):
    if (WEB / f"{enllac}.html").is_file():          # una pàgina pròpia amb eixe nom mana
        return FileResponse(WEB / f"{enllac}.html")
    if not _enllacos:
        _enllacos.update(s["enllac"] for s in _parades())
    if enllac.isdigit() or enllac in _enllacos:
        return _index()
    return RedirectResponse("./", status_code=302)


if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
