# Pociągi PZ ⇄ KRK

Osobista PWA: najbliższe odjazdy **Poznań Główny ⇄ Kraków Główny** na ~4 godziny w obie strony,
opóźnienie na żywo (kolor: zielony 0, żółty 1–15, czerwony >15 min) i peron/tor.
Pod iPhone'a i iPada dodane do ekranu głównego, tryb ciemny, odświeżanie co 60 s i pull-to-refresh.
Faza 2: „obserwuj pociąg” → powiadomienie push przy zmianie opóźnienia o ≥5 min, podaniu/zmianie peronu
albo odwołaniu.

Dane: [Otwarte Dane Kolejowe PKP PLK](https://pdp-api.plk-sa.pl). Szczegóły API, ID stacji i ograniczenia:
[`docs/API_RECON.md`](docs/API_RECON.md).

> **Status:** kod i testy są gotowe, ale nie były jeszcze uruchomione na prawdziwym API
> (w środowisku, w którym powstały, host PLK był zablokowany). Testy działają na danych
> **syntetycznych** zbudowanych według oficjalnej specyfikacji OpenAPI v1.1.4.
> Pierwszy krok po zdobyciu klucza: [Weryfikacja na prawdziwym API](#weryfikacja-na-prawdziwym-api).

## Jak to działa

```
iPhone/iPad (PWA)  ──60 s──▶  FastAPI /api/departures  ──czyta──▶  SQLite (cache)
                                                                       ▲
                               worker w tle ──co 120 s──▶ PLK /operations ┘ (opóźnienia)
                                             ──2× dziennie─▶ PLK /schedules  (rozkład + peron/tor)
```

- Frontend **nigdy** nie odpytuje PLK — tylko backend, przez jednego workera.
- Cache jest w SQLite, więc restart kontenera nie zużywa limitu i nie daje pustej tablicy.
- Gdy dane na żywo są stare, lista dalej się wyświetla, a w nagłówku widać „sprzed X min”.
- Bez sieci service worker pokazuje ostatnio pobrane odjazdy.
- **Peron/tor pochodzi z rozkładu** — API PLK nie podaje peronów na żywo, więc zmiana peronu
  w dniu kursowania nie będzie widoczna.

### Limity PLK

| Pakiet | Na godzinę | Na dobę |
|---|---|---|
| Basic | 100 | 1 000 |
| Standard | 500 | 5 000 |

Domyślnie: realizacja co 120 s z przerwą 01:00–04:30 (~620 zapytań na dobę) + ~5 na rozkład i przewoźników.
Przy HTTP 429 worker czeka (Retry-After albo rosnąco do 15 min). Zużycie widać w `GET /api/health`.
Po przejściu na Standard ustaw `PLK_POLL_SECONDS=60`.

## Uruchomienie lokalne

Wymagania: Python 3.12+.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# podgląd bez klucza (zmyślone pociągi, znacznik DEMO):
DEMO_MODE=true uvicorn app.main:factory --factory --reload --port 8000

# z prawdziwymi danymi:
cp ../.env.example ../.env   # wpisz PLK_API_KEY
set -a; source ../.env; set +a
uvicorn app.main:factory --factory --port 8000
```

Otwórz http://localhost:8000. Dokumentacja API: http://localhost:8000/api/docs.

Testy:

```bash
cd backend && pytest
```

Z Dockerem: `cp .env.example .env`, uzupełnij, potem `docker compose up --build`.

## Deploy na VPS (Hetzner)

1. Na serwerze zainstaluj Docker z pluginem compose i sklonuj repo:
   ```bash
   git clone https://github.com/mprzybyl1/Train-pa-krk.git pociagi && cd pociagi
   cp .env.example .env && nano .env      # PLK_API_KEY, opcjonalnie klucze VAPID
   docker compose up -d --build
   docker compose logs -f                 # "Rozkład odświeżony ..."
   curl -s localhost:8000/api/health
   ```
   Kontener słucha tylko na `127.0.0.1:8000`; z internetu ruch idzie przez reverse proxy.
   Cache trzyma wolumen `pz-data`.

2. **HTTPS jest wymagany** (service worker, instalacja PWA, push). Przykład dla Caddy
   (sam pobiera certyfikat Let's Encrypt), `/etc/caddy/Caddyfile`:
   ```
   pociagi.twojadomena.pl {
       encode gzip
       reverse_proxy 127.0.0.1:8000
   }
   ```
   Albo nginx (certyfikat np. z certbota):
   ```nginx
   server {
       server_name pociagi.twojadomena.pl;
       listen 443 ssl http2;
       # ssl_certificate ... ; ssl_certificate_key ... ;
       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
   Aplikacja nie ma logowania. Jeśli nie chcesz jej mieć publicznie, najprościej ograniczyć dostęp
   po IP albo użyć Tailscale. Basic auth w proxy działa słabo z PWA na iOS.

3. Aktualizacja: `git pull && docker compose up -d --build`.

Uruchamiaj **jeden** proces (domyślne ustawienia): każdy dodatkowy worker uvicorn odpytywałby PLK osobno.

## iPhone / iPad

Safari → strona → **Udostępnij → Do ekranu początkowego**. Aplikacja otwiera się na pełnym ekranie.
Na iPhonie są zakładki kierunków, na iPadzie (szerokość ≥ 820 px) dwie kolumny obok siebie.
Pociągnięcie w dół odświeża, a dotknięcie wieku danych w prawym górnym rogu też.

## Faza 2: powiadomienia push

1. Wygeneruj klucze VAPID i wklej je do `.env`:
   ```bash
   docker compose run --rm app python -m app.gen_vapid
   # albo lokalnie: cd backend && python -m app.gen_vapid
   ```
   Ustaw też `VAPID_SUBJECT=mailto:twoj@email`. Potem `docker compose up -d`.
2. Na iOS (16.4+) powiadomienia działają **tylko** w aplikacji dodanej do ekranu głównego.
3. Przy każdym pociągu pojawi się dzwonek. Pierwsze dotknięcie prosi o zgodę na powiadomienia.
   Obserwacja wygasa sama po odjeździe pociągu.

Nie zmieniaj kluczy VAPID po włączeniu: istniejące subskrypcje przestaną działać i trzeba je włączyć ponownie.

## Weryfikacja na prawdziwym API

Po dostaniu klucza (zużywa ~6 zapytań):

```bash
cd backend
PLK_API_KEY=sk_live_... python -m app.capture
```

Skrypt:
- wyszukuje „Poznań Główny” i „Kraków Główny” w słowniku stacji PLK (potwierdzenie ID 30601 / 80416),
- zapisuje prawdziwe odpowiedzi do `backend/tests/fixtures/real/`,
- wypisuje liczbę kursów i ile z nich ma peron.

Te pliki zastąpią syntetyczne fixtures w testach. Do sprawdzenia: strefa czasowa w `/operations`
i zachowanie `departureDelayMinutes` przy zerowym opóźnieniu (lista w `docs/API_RECON.md`).

## Konfiguracja

Wszystko przez zmienne środowiskowe — pełna lista z opisami w [`.env.example`](.env.example).
Sekrety trzymaj tylko w `.env` (jest w `.gitignore`).

## Struktura

```
backend/app/
  plk/client.py   klient HTTP PLK (X-API-Key, ponawianie, 429)
  plk/parse.py    JSON PLK -> modele (czyste funkcje)
  departures.py   rozkład + realizacja -> lista odjazdów
  worker.py       pętla w tle, budżet zapytań, przerwa nocna
  store.py        SQLite: cache, stan, subskrypcje push
  push.py         wykrywanie zmian i Web Push
  demo.py         tryb DEMO
  capture.py      zrzut prawdziwych odpowiedzi
  main.py         FastAPI
backend/tests/    testy + fixtures
frontend/         PWA bez frameworka (index.html, app.js, styles.css, sw.js, manifest, ikony)
scripts/          generator ikon
```

Peron na żywo z KOLEO (nieoficjalne API) nie jest zaimplementowany — nie dało się go zweryfikować.
To kandydat na rozszerzenie.
