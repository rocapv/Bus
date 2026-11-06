"""seguiment.assigna: el número de cada bus entre pasades."""
from types import SimpleNamespace as B

from backend.seguiment import assigna, metres


def b(linea, lat, lng, tray="A - B"):
    return B(linea=linea, trayecto=tray, lat=lat, lng=lng)


def p(id_, linea, lat, lng, tray="A - B", **kw):
    return dict(id=id_, linea=linea, trayecto=tray, lat=lat, lng=lng, **kw)


def test_el_mes_proxim_de_la_mateixa_linia_conserva_el_numero():
    prev = [p(1, "72", 39.4700, -0.3800), p(2, "72", 39.4800, -0.3800)]
    cur = [b("72", 39.4805, -0.3801), b("72", 39.4703, -0.3800)]
    ids, nxt = assigna(prev, cur, 60, [], 10)
    assert ids == [2, 1] and nxt == 10


def test_una_altra_linia_no():
    ids, nxt = assigna([p(1, "72", 39.47, -0.38)], [b("81", 39.47, -0.38)], 60, [], 10)
    assert ids == [10] and nxt == 11


def test_massa_lluny_per_al_temps_passat_es_un_bus_nou():
    # 5 km en 60 s no és possible
    ids, _ = assigna([p(1, "72", 39.47, -0.38)], [b("72", 39.515, -0.38)], 60, [], 10)
    assert ids == [10]


def test_en_capcalera_canvia_de_sentit_pero_es_el_mateix_bus():
    prev = [p(1, "72", 39.47, -0.38, "A - B")]
    ids, _ = assigna(prev, [b("72", 39.4701, -0.38, "B - A")], 60, [], 10)
    assert ids == [1]


def test_el_mateix_sentit_guanya_a_igualtat():
    prev = [p(1, "72", 39.4700, -0.38, "A - B"), p(2, "72", 39.4702, -0.38, "B - A")]
    ids, _ = assigna(prev, [b("72", 39.4701, -0.38, "B - A")], 60, [], 10)
    assert ids == [2]


def test_retroba_un_bus_perdut_fa_poc():
    perduts = [p(5, "72", 39.47, -0.38, edat=240)]
    ids, _ = assigna([], [b("72", 39.475, -0.38)], 60, perduts, 10)   # ~550 m en 4 min
    assert ids == [5]


def test_metres():
    assert 1100 < metres(39.47, -0.38, 39.48, -0.38) < 1120
