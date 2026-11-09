from backend.lectures import gps_real

TS = 1794264330  # una pasada (epoch s)


def test_fecha_amb_hora_local_de_hivern():
    # el geoportal escriu l'hora local com si fóra UTC: +1 h a l'hivern
    assert gps_real((TS + 3600 - 26) * 1000, TS) == TS - 26


def test_fecha_sense_desfasament():
    # ~1 de cada 10 lectures ve sense l'hora: la mateixa correcció la deixa bé
    assert gps_real((TS - 26) * 1000, TS) == TS - 26


def test_fecha_estiu_i_buida():
    assert gps_real((TS + 7200 - 40) * 1000, TS) == TS - 40
    assert gps_real(0, TS) is None


def test_gps_de_fa_mes_de_mitja_hora_no_es_confon():
    # un GPS vell de veritat (25 min) no es «corregix» a una hora
    assert gps_real((TS + 3600 - 1500) * 1000, TS) == TS - 1500
