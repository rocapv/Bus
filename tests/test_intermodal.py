"""intermodal.ruta: metro + bus entre dos punts, amb una xarxa de metro de mentida
(sense xarxa: no es demana res a MetroViu)."""
import time

import pytest

from backend import intermodal as im

# una línia de metro est-oest de 8,5 km al sud de València, lluny de tot
EST_A = {"id": 901, "nombre": "Prova Oest", "lat": 39.40, "lng": -0.45, "lineas": [1]}
EST_B = {"id": 902, "nombre": "Prova Est", "lat": 39.40, "lng": -0.35, "lineas": [1]}
NET = {
    "estaciones": [EST_A, EST_B],
    "colores_comercial": {"1": "FEC601"},
    "comerciales": {"1": {"color": "FEC601", "variante_de": None,
                          "estaciones": [{"id": 901, "d": 0.0}, {"id": 902, "d": 8500.0}],
                          "path": [[39.40, -0.45], [39.40, -0.35]], "cum": [0.0, 8500.0]}},
}


def _arr(eta, desti="Prova Est"):
    return {"ts": 1, "edad_seg": 0,
            "estaciones": {"901": [{"linea": 1, "destino": desti, "eta_seg": eta}]}}


@pytest.fixture
def xarxa(monkeypatch):
    monkeypatch.setattr(im, "_metro", lambda: NET)
    monkeypatch.setattr(im, "_graf", None)
    yield
    im._graf = None


def test_agafa_el_metro_amb_lespera_dels_panells(xarxa, monkeypatch):
    monkeypatch.setattr(im, "_arribades", lambda: _arr(900))
    r = im.ruta((39.4005, -0.4500), (39.4005, -0.3500))
    alt = r["alternatives"][0]
    metro = [l for l in alt["trams"] if l["tipus"] == "metro"]
    assert len(metro) == 1 and metro[0]["linia"] == "L1"
    assert metro[0]["espera_viva"] and metro[0]["cap_a"] == "Prova Est"
    # a peu fins a l'estació (~70 m) i esperar el tren dels 900 s
    assert 13 < metro[0]["espera_min"] < 15


def test_un_tren_en_laltre_sentit_no_serveix(xarxa, monkeypatch):
    monkeypatch.setattr(im, "_arribades", lambda: _arr(60, desti="Prova Oest"))
    r = im.ruta((39.4005, -0.4500), (39.4005, -0.3500))
    metro = [l for a in r["alternatives"] for l in a["trams"] if l["tipus"] == "metro"]
    assert metro and not metro[0]["espera_viva"]
    assert metro[0]["espera_min"] == im.ESPERA_METRO / 60


def test_el_tren_que_passa_abans_darribar_es_perd(xarxa, monkeypatch):
    # 10 s: impossible arribar a peu a temps
    monkeypatch.setattr(im, "_arribades", lambda: _arr(10))
    r = im.ruta((39.4005, -0.4500), (39.4005, -0.3500))
    metro = [l for a in r["alternatives"] for l in a["trams"] if l["tipus"] == "metro"]
    assert metro and not metro[0]["espera_viva"]


def test_al_centre_hi_ha_ruta_en_bus_i_les_linies_sense_servei_no_entren(xarxa, monkeypatch):
    monkeypatch.setattr(im, "_arribades", lambda: {"ts": None, "estaciones": {}})
    o, d = (39.4699, -0.3763), (39.4628, -0.3245)        # Ajuntament -> platja
    r = im.ruta(o, d)
    bus = [a for a in r["alternatives"] if "bus" in a["modes"]]
    assert bus, "hi ha d'haver alguna alternativa amb bus"
    linies = {l["linia"] for a in bus for l in a["trams"] if l["tipus"] == "bus"}
    # si les que ha triat no circulen, ha de buscar-ne una altra (o cap)
    r2 = im.ruta(o, d, bus_fora=linies)
    linies2 = {l["linia"] for a in r2["alternatives"] for l in a["trams"] if l["tipus"] == "bus"}
    assert not (linies & linies2)
    assert set(r2["fora_de_servei"]) >= linies


def test_els_nocturns_no_entren_de_dia(xarxa, monkeypatch):
    monkeypatch.setattr(im, "_arribades", lambda: {"ts": None, "estaciones": {}})
    migdia = time.struct_time((2026, 11, 6, 12, 0, 0, 4, 310, 0))
    monkeypatch.setattr(im.time, "localtime", lambda *a: migdia)
    r = im.ruta((39.4699, -0.3763), (39.4628, -0.3245))
    assert all(n.startswith("N") for n in r["fora_de_servei"]) and "N9" in r["fora_de_servei"]
    linies = {l["linia"] for a in r["alternatives"] for l in a["trams"] if l["tipus"] == "bus"}
    assert not any(l.startswith("N") for l in linies)
