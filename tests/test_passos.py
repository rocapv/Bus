"""passos.detecta: per quines parades passa un bus entre dues lectures."""
from backend.passos import Index, detecta

# carrer nord-sud; el bus puja cap al nord (lat creix)
PAR_DRETA = {"id": 1, "nom": "Dreta", "lat": 39.4750, "lng": -0.37985, "lineas": ["72"]}     # ~13 m a l'est
PAR_ESQ = {"id": 2, "nom": "Esquerra", "lat": 39.4752, "lng": -0.38015, "lineas": ["72"]}    # ~13 m a l'oest
ALTRA = {"id": 3, "nom": "Altra línia", "lat": 39.4751, "lng": -0.37990, "lineas": ["81"]}
LLUNY = {"id": 4, "nom": "Lluny", "lat": 39.4750, "lng": -0.3780, "lineas": ["72"]}
IDX = Index([PAR_DRETA, PAR_ESQ, ALTRA, LLUNY])


def test_passa_per_la_parada_de_la_dreta_i_no_per_la_de_lenfront():
    r = detecta(IDX, "72", 39.4730, -0.3800, 1000, 39.4770, -0.3800, 1060)
    assert [p for p, _ in r] == [1]
    assert 1020 < r[0][1] < 1040          # a mig camí, a mig temps


def test_en_sentit_contrari_es_al_reves():
    r = detecta(IDX, "72", 39.4770, -0.3800, 1000, 39.4730, -0.3800, 1060)
    assert [p for p, _ in r] == [2]


def test_nomes_les_parades_de_la_seua_linia():
    r = detecta(IDX, "81", 39.4730, -0.3800, 1000, 39.4770, -0.3800, 1060)
    assert [p for p, _ in r] == [3]


def test_parat_a_la_parada():
    r = detecta(IDX, "72", 39.4750, -0.3799, 1000, 39.4750, -0.3799, 1060)
    assert 1 in [p for p, _ in r]
