# Bus · busos d'EMT València en directe

**https://bus.pr1.es/** — els autobusos de l'EMT de València sobre el mapa, amb la
seua posició **GPS real**, el registre de pas de cada parada, el recorregut de
cada bus i un xat per bus i per parada. Abans es deia EMTropello; el 06/11/2026
va passar a dir-se **Bus** i va heretar el que es va aprendre a
[MetroViu](https://metro.pr1.es/).

## Font de dades

ArcGIS MapServer públic (sense token) del geoportal de l'Ajuntament:

```
https://geoportal.valencia.es/server/rest/services/EMT/Seguimiento_EMT/MapServer
```

- **Capa 384 · «Buses EMT»**: un registre per bus en circulació, amb `linea`,
  `trayecto` (sentit) i `fecha` (última posició GPS). Es demana `outSR=4326`.
- ⚠️ El `gid` **no és estable** (es reassigna a cada consulta) i el `fecha` no és
  un epoch UTC fiable (ve avançat). La identitat la posa el servidor (vegeu baix)
  i l'antiguitat del GPS es mesura respecte al bus més fresc de la pasada.
- ~300 busos en hora punta, ~40 línies.

## Com funciona

- **`backend/poller.py`** (servei `bus-poller`): una pasada per minut, sempre (abans
  només quan algú mirava). Descarta les pasades parcials del geoportal.
- **`backend/seguiment.py`**: cada bus rep un **número** que conserva tot el torn.
  Mateixa línia, el més pròxim dins d'una porta de distància segons el temps; en
  capçalera canvia de sentit però és el mateix bus; un bus perdut fins a 15 min es
  retroba.
- **`backend/lectures.py`**: cada pasada és una fila (blob zlib): ~7 MB per dia.
- **`backend/passos.py`**: entre dues lectures el bus recorre un tram; les parades
  de la seua línia a menys de 30 m del tram, i a la dreta del sentit de marxa
  (la de l'altre sentit és a l'esquerra), compten com a pas.
- **`backend/historia.py`**: el recorregut d'un bus (totes les posicions del torn) i
  els enllaços de les parades.
- **`backend/xat.py`**: missatges per bus o per parada, sense comptes, 7 dies.
- **`backend/api.py`** (servei `bus-web`, uvicorn a 127.0.0.1:8791).
- Base de dades `data/bus.db` en **WAL**, `secure_delete=OFF` i **7 dies** de tot
  (purga de matinada, `auto_vacuum` incremental).

| Endpoint | Paràmetres | Què |
|---|---|---|
| `GET /api/buses` | | busos de l'última pasada, amb el seu número |
| `GET /api/ultimo` | | marca de l'última pasada (+ últim missatge del xat) |
| `GET /api/network` | | traçat de les línies i parades (amb el seu enllaç) |
| `GET /api/registre_parada` | `id`, `dia` | busos que han passat per la parada |
| `GET /api/registre_bus` | `id` | parades per on ha passat el bus |
| `GET /api/historia_bus` | `id` | recorregut del bus, encara que ja no circule |
| `GET/POST /api/xat` | `tipus`, `lloc` / `des_de` | missatges |
| `GET /api/intermodal` | `o`, `d` (`lat,lng`) | ruta **metro + bus** entre dos punts (fins a tres alternatives); MetroViu la demana per mitjà del seu propi `/api/intermodal` |

## Metro + Bus

`backend/intermodal.py`: Dijkstra per hora d'arribada sobre les parades d'EMT i
les estacions de Metrovalència (la xarxa la dona MetroViu, `127.0.0.1:8770` o
`metro.pr1.es`), a peu entre elles (≤ 1,2 km a l'inici i al final, ≤ 400 m en
un transbord). Metro: distància sobre la via i **l'espera real dels panells de
FGV** (el primer tren de la línia en el sentit bo que passa després d'arribar a
l'estació). Bus: EMT no publica arribades, així que espera estimada (6 min) i la
distància pel recorregut de la línia entre les dues parades (precalculada,
`data/intermodal_dist.json`, gitignored). Les línies que no han tingut cap bus
en la darrera mitja hora (els nocturns de dia...) i les de metro sense cap tren
als panells no entren. Dona fins a tres alternatives: combinada, només metro i
només bus.

`web/intermodal.js` és el MATEIX fitxer que a MetroViu (`data-app="bus"`): panell
**Metro+Bus** (toca el mapa: origen i destí; pulsació llarga en qualsevol moment)
i botó **⇄ Metro**, que obri `metro.pr1.es` en el mateix tros de mapa i amb la
mateixa ruta (`#v=lat,lng,zoom&o=lat,lng&d=lat,lng`).

## La web

- Cada bus avança **al llarg de la seua ruta** entre una dada i la següent i es
  reajusta quan n'arriba una de nova (la web pregunta cada 5 s si n'hi ha).
- **Enllaços directes**: `bus.pr1.es/torresdelsserrans` (parada: nom en minúscules
  sense accents ni símbols; si dues parades es diuen igual, s'hi afig el número:
  `portadelamar771`) i `bus.pr1.es/1234` (bus; si ja no circula, el seu
  recorregut). El nom del popup o la icona de compartir copien l'enllaç.
- **Popups amb pestanyes**: parada (Registre · Xat) i bus (Ara · Registre · Xat).
- **Recorregut**: el torn del bus reproduït amb el rellotge accelerat, el traç, les
  parades per on va passar i on es va deixar de veure.

## Desplegament (Raspa)

```
git clone git@github.com:rocapv/Bus.git ~/Bus && cd ~/Bus
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/bus-poller.service deploy/bus-web.service /etc/systemd/system/
sudo systemctl enable --now bus-poller bus-web
sudo cp deploy/apache-bus.conf /etc/apache2/conf-available/bus.conf && sudo a2enconf bus
# domini: deploy/bus.pr1.es.conf i deploy/bus.pr1.es-ssl.conf (certbot --webroot)
```

- `data/token.txt`: token d'accés de fora de la LAN. `data/open_until.txt`
  = `siempre` deixa la web oberta a tothom (com metro.pr1.es).
- En LAN: `http://192.168.1.100/bus/`. `/EMT/` es manté per a l'APK vella.

## Tests

```
python -m pytest -q tests
```
