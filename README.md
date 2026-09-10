# DHMZ Standalone Weather Dashboard

A self-contained weather dashboard for a single DHMZ (Croatian Meteorological
Service) station, meant for a wall-mounted tablet or kiosk screen. It's a
port of the [`DHMZ-home-assistant-custom-component`](../DHMZ-home-assistant-custom-component)
integration and the [`lovelace-dhmz-weather-card`](../lovelace-dhmz-weather-card)
Lovelace card, with Home Assistant removed from the picture entirely.

Shows: current conditions, an hourly forecast chart (temperature +
precipitation), today's/tomorrow's forecast text, sunrise/sunset, and the
animated radar composite.

## Why a backend at all

The frontend can't fetch DHMZ's XML feeds directly: `vrijeme.hr`/`meteo.hr`
don't send CORS headers, so a browser blocks reading the response body
cross-origin (verified directly — the request succeeds at the network level,
but `fetch()` throws). A tiny Python service does the fetching, parsing and
caching server-side and exposes a small JSON API plus the radar image; the
frontend is otherwise a static page with no build step.

The backend also owns two resilience behaviors ported from the original
integration:
- **TLS fallback**: `vrijeme.hr` has intermittently served a certificate
  issued for the wrong host. On a TLS failure the backend retries the same
  request over plain HTTP (these feeds are public, unauthenticated XML/image
  files, so this is a safe fallback — a browser can't do this itself due to
  mixed-content rules).
- **Stale-data resilience**: DHMZ reports `"-"` for the current weather
  symbol at night when none is available; the backend remembers the last
  real symbol (persisted to `data/state.json`) so the icon doesn't flicker to
  a generic "exceptional" state. A failed/malformed fetch of any one feed
  also doesn't wipe out the other cached data.

## Running it

```bash
docker compose up -d --build
```

Then open `http://<host>:8080/`. The frontend polls the backend every few
minutes and the backend re-fetches from DHMZ only when its own cache has gone
stale (see [Performance / low-power hardware](#performance--low-power-hardware)
below) — there's no background job running when nobody's looking at the page.

## Configuring your station

Edit the `environment:` block in [`docker-compose.yml`](docker-compose.yml).
DHMZ identifies your location across **four independent lists** — this is a
quirk of the underlying feeds, not something this project invented:

| Variable | Feed it's matched against | Example |
|---|---|---|
| `DHMZ_STATION_NAME` | current conditions | `Zagreb-Maksimir` |
| `DHMZ_FORECAST_REGION_NAME` | today/tomorrow forecast text | `Zagreb` |
| `DHMZ_FORECAST_TEXT` | today/tomorrow forecast text | `zg_text` |
| `DHMZ_FORECAST_STATION_NAME` | hourly/7-day chart | `ZAGREB-MAKSIMIR` |

Valid values for each are listed at the bottom of this file. Latitude and
longitude (used for sunrise/sunset and the optional radar location marker)
are read automatically from the `DHMZ_STATION_NAME` entry — no need to set
them yourself unless you want to override them (`DHMZ_LATITUDE`/`DHMZ_LONGITUDE`).

Other variables:

| Variable | Default | Meaning |
|---|---|---|
| `DHMZ_TIMEZONE` | `Europe/Zagreb` | used for sunrise/sunset calculation |
| `DHMZ_WEATHER_CACHE_SECONDS` | `1800` | how long weather data is cached before re-fetching |
| `DHMZ_RADAR_CACHE_SECONDS` | `600` | how long the radar image is cached before re-fetching |
| `DHMZ_MARK_LOCATION` | `true` | draw a red dot for your station on the radar image |
| `DHMZ_RADAR_FORMAT` | `WEBP` | `WEBP` (smaller) or `GIF` (original) |

After changing environment variables: `docker compose up -d --build`.

## Performance / low-power hardware

There's no polling loop or scheduled job running in the background. The
frontend polls the backend on a timer (every 5 min for weather, every 3 min
for radar — [`frontend/app.js`](frontend/app.js)), but the backend only
actually contacts DHMZ when its own in-memory cache has expired
(`DHMZ_WEATHER_CACHE_SECONDS` / `DHMZ_RADAR_CACHE_SECONDS`, above). That cache
is shared by every viewer, so having the dashboard open on several
tablets/browsers at once doesn't multiply the work. If nothing is polling
the backend at all, it does nothing.

On something like a Raspberry Pi 3B+, the part actually worth tuning is the
**radar image**, not the weather JSON: parsing five small XML files with
`lxml` is trivial, but decoding a multi-frame animated GIF, drawing a marker
on every frame, and re-encoding it as WebP via Pillow is genuine CPU work,
and it currently happens once per `DHMZ_RADAR_CACHE_SECONDS`. Options, from
mild to aggressive:

- Raise `DHMZ_RADAR_CACHE_SECONDS` further (e.g. `900`–`1800`) if you don't
  need the radar loop to be near-real-time.
- Set `DHMZ_MARK_LOCATION=false` to skip drawing on every frame — Pillow
  still has to decode/re-encode for the GIF→WebP conversion, but skips the
  per-frame `ImageDraw` call.
- Set `DHMZ_RADAR_FORMAT=GIF` **and** `DHMZ_MARK_LOCATION=false` together:
  the backend then just proxies DHMZ's original GIF bytes through unchanged
  — no Pillow decode/encode at all, the cheapest possible path (trade-off:
  no location marker, and GIF is a larger download than WebP).

Raising `DHMZ_WEATHER_CACHE_SECONDS` further (e.g. to `3600`) is also safe —
DHMZ's own current-conditions/forecast feeds don't update more often than
about once an hour regardless.

## Project layout

```
backend/
  app/
    config.py        # env-var configuration
    dhmz_client.py    # fetch/parse/cache DHMZ feeds, ported from the HA integration
    main.py           # stdlib http.server app: /api/weather, /api/radar, serves frontend/
  requirements.txt
  Dockerfile
frontend/
  index.html
  style.css
  app.js              # polls the API, renders the dashboard + Chart.js graph
data/                  # persisted last-good weather symbol (bind-mounted)
docker-compose.yml
```

## Differences from the original HA card

A couple of things were changed rather than ported verbatim:
- The wind direction arrow rotates continuously (compass letter converted to
  degrees) instead of picking from 8 fixed icon glyphs — same information,
  smoother to look at. (DHMZ reports wind bearing as a compass abbreviation
  like `SW`, not a numeric angle, despite the original integration's field
  table labeling it as degrees.)
- The chart no longer has a "low temperature" line — the original always
  fed it `undefined` (that field was never populated by the integration), so
  it never rendered anything.
- No `mode: daily` vs `hourly` toggle — the underlying data was always
  hourly regardless of that setting in the original card.

## Reference: valid configuration values

### `DHMZ_STATION_NAME`

    RC Bilogora, Bjelovar, Crikvenica, Crni Lug-NP Risnjak, Daruvar, Delnice,
    Dubrovnik, Dubrovnik-aerodrom, Gospić, RC Gorice (kod Nove Gradiške),
    RC Gradište (kod Županje), Gruda, Hvar, Imotski, Karlovac, Knin, Komiža,
    Krapina, Krk, Križevci, Kukuljanovo, Kutjevo, Lastovo, Lipik, Malinska,
    Makarska, Mali Lošinj, NP Mljet, RC Monte Kope, Ogulin, Opatija,
    RC Osijek-Čepin, Osijek-aerodrom, Palagruža, Parg-Čabar, Pazin,
    NP Plitvička jezera, Ploče, Poreč, Porer - svjetionik, Prevlaka, Pula,
    Pula-aerodrom, RC Puntijarka, Rab, Rijeka, Rijeka-aerodrom,
    Sv. Ivan na pučini - svjetionik, Senj, Sinj, Sisak, Slavonski Brod,
    Split-Marjan, Split-aerodrom, Šibenik, Varaždin, Veli Rat - svjetionik,
    Vinkovci, Zadar, Zadar-aerodrom, Zagreb-Grič, Zagreb-Maksimir,
    Zagreb-aerodrom, Zavižan

### `DHMZ_FORECAST_REGION_NAME`

    sredisnja, istocna, gorska, unutrasnjost Dalmacije, sjeverni Jadran,
    srednji Jadran, juzni Jadran, Zagreb

### `DHMZ_FORECAST_TEXT`

    rh_text, zg_text

### `DHMZ_FORECAST_STATION_NAME`

    BABINA GREDA, BAKAR, BAŠKA, BAŠKA VODA, BAŠKE OŠTARIJE, BEDNJA,
    BEGOVO RAZDOLJE, BELI MANASTIR, BELIŠĆE, BENKOVAC, BILOGORA,
    BIOGRAD NA MORU, BISKO, BISTRA, BIZOVAC, BIŠEVO, BJELOVAR,
    BLATO NA KORČULI, BOL, BOSILJEVO, BOŽAVA, BRELA, BREZNIČKI HUM, BRINJE,
    BRODSKI STUPNIK, BUJE, BUZET, CAVTAT, CISTA PROVO, CRES, CRIKVENICA,
    ČABAR, ČAKOVEC, ČAVLE, ČAZMA, ČAČINCI, DALJ, DARDA, DARUVAR, DAVOR,
    DELNICE, DONJA DUBRAVA, DONJA STUBICA, DONJI LAPAC, DONJI MIHOLJAC,
    DRNIŠ, DRVENIK, DUBROVNIK, DUGA RESA, DUGO SELO, DUGOPOLJE, DVOR NA UNI,
    ĐAKOVO, ĐURMANEC, ĐURĐEVAC, FAŽANA, FUŽINE, GAREŠNICA, GENERALSKI STOL,
    GLINA, GORIČAN, GORNJA PLOČA, GOSPIĆ, GOVEĐARI U MORU, GRADAC, GRADINA,
    GRADIŠTE, GRAČAC, GROŽNJAN, GRUBIŠNO POLJE, GRUDA, GUNJA, GVOZD, HRELJIN,
    HRVATSKA DUBICA, HRVATSKA KOSTAJNICA, HVAR, ILOK, IMOTSKI, IST, IVANEC,
    IVANIĆ GRAD, JABLANAC, JASENOVAC, JASTREBARSKO, JELENJE, JELSA, JOSIPDOL,
    KANFANAR, KARLOBAG, KARLOVAC, KASTAV, KLANJEC, KLEK, KLOŠTAR PODRAVSKI,
    KNIN, KOLOČEP, KOMIN, KOMIŽA, KOPRIVNICA, KORENICA, KORČULA, KOSTRENA,
    KRALJEVICA, KRAPINA, KRASNO POLJE, KRAVARSKO, KRIŽEVCI, KRIŽIŠĆE, KRK,
    KUKULJANOVO, KUNA, KUTINA, KUTJEVO, LABIN, LASTOVO, LEKENIK, LIPIK,
    LIPOVAC, LIČKI OSIK, LOPAR, LOPUD, LOVRAN, LOVREĆ, LUBENICE, LUDBREG,
    LUMBARDA, LUPOGLAV, MACELJ, MAKARSKA, MALI LOŠINJ, MALINSKA,
    MARIJA BISTRICA, MASLENICA, MATULJI, MEDULIN, METKOVIĆ, MILNA, MOLAT,
    MOLVE, MOST KRK, MOST PAG, MOTOVUN, MOŠĆENIČKA DRAGA, MRKOPALJ,
    MURSKO SREDIŠĆE, MURTER, NAŠICE, NEREZINE, NIN, NOVA GRADIŠKA-CERNIK,
    NOVA KAPELA, NOVALJA, NOVI MAROF, NOVI VINODOLSKI, NOVIGRAD - ISTRA,
    NOVSKA, NP BRIJUNI, NP KORNATI, NP KRKA - LOZOVAC, NP MLJET - KORITA,
    NP PAKLENICA - STARIGRAD, NP PLITVIČKA JEZERA, NP RISNJAK - CRNI LUG,
    OBROVAC, OGULIN, OKUČANI, OLIB, OMIŠ, OPATIJA, OPUZEN, ORAHOVICA,
    OREBIĆ, OSIJEK, OTOČAC, OZALJ, PAG, PAKOŠTANE, PAKRAC, PALAGRUŽA, PAZIN,
    PERUŠIĆ, PETRINJA, PIROVAC, PISAROVINA, PITOMAČA, PLETERNICA, PLOČE,
    PODSTRANA, PODSUSED, POKUPSKO, POLAČA, POPOVAČA, POREČ, POROZINA,
    POSEDARJE, POSTIRA, POVILE, POVLJA, POVLJANA, POŽEGA, PP UČKA, PREGRADA,
    PRELOG, PREVLAKA, PRGOMET, PRIMOŠTEN, PRIZNA, PULA, PUNTIJARKA, RAB,
    RAVNA GORA, RAVČA, RAŽANAC, RIJEKA, ROGOTIN, ROGOZNICA, ROVINJ, RUGVICA,
    RUPA, SALI, SAMOBOR, SAVUDRIJA, SELCE, SENJ, SESVETE, SEVERIN NA KUPI,
    SILBA, SINJ, SISAK, SKRAD, SKRADIN, SLANO, SLATINA, SLAVONSKI BROD,
    SLAVONSKI ŠAMAC, SLUNJ, SOLIN, SPLIT, SREDANCI, STARIGRAD - HVAR, STON,
    STRUŽEC, SUHOPOLJE, SUKOŠAN, SUMARTIN, SUPETAR, SUTIVAN, SV. IVAN ZELINA,
    SV. IVAN ŽABNO, SV. KRIŽ ZAČRETJE, SV. MARTIN NA MURI, SV. NEDJELJA,
    SVETI ROK, ŠESTANOVAC, ŠIBENIK, ŠOLTA, TISNO, TKON, TOPUSKO, TOVARNIK,
    TRAKOŠĆAN, TRIBUNJ, TRILJ, TROGIR, TRSTENIK, TRSTENO, TUHELJ, TUČEPI,
    UDBINA, UGLJAN, UMAG, UNIJE, URINJ, USKOPLJE, VALPOVO, VARAŽDIN,
    VARAŽDINSKE TOPLICE, VARDARAC, VELA LUKA, VELIKA, VELIKA GORICA,
    VELIKO TRGOVIŠĆE, VINKOVCI, VIR, VIRJE, VIROVITICA, VIS,
    VODICE - DALMACIJA, VODNJAN, VOJNIĆ, VRBOVEC, VRBOVSKO, VRGORAC, VRLIKA,
    VRPOLJE - ŠIBENIK, VRSAR, VUKOVA GORICA, VUKOVAR, ZABOK, ZADAR,
    ZAGREB-GRIČ, ZAGREB-MAKSIMIR, ZAGREB-NOVI ZAGREB, ZAGVOZD, ZAPREŠIĆ,
    ZATON OBROVAČKI, ZAVIŽAN, ZLARIN, ZLATAR, ZRAČNA LUKA DUBROVNIK,
    ZRAČNA LUKA MALI LOŠINJ, ZRAČNA LUKA OSIJEK, ZRAČNA LUKA PULA,
    ZRAČNA LUKA RIJEKA, ZRAČNA LUKA SPLIT, ZRAČNA LUKA ZADAR,
    ZRAČNA LUKA ZAGREB, ŽIRJE, ŽIVOGOŠĆE, ŽMINJ, ŽRNOVNICA, ŽUPANJA
