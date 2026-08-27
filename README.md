# IndsatsBrief

IndsatsBrief er en dansk webapp til at samle et hurtigt, struktureret informationsgrundlag før og under en brand- og redningsindsats. Appen kombinerer adresseopslag, BBR/bygningsdata, vejr, kort, OSM-risikodata, mulige brandhaner, beredskabsstationer, ressourcer og en intern vidensbase.

> IndsatsBrief er et beslutningsstøtteværktøj. Registerdata og åbne datakilder kan være mangelfulde eller forsinkede og skal vurderes kritisk ved operativ brug.

## Teknologi

- Python 3.12 + Flask
- Gunicorn
- PostgreSQL via Flask-SQLAlchemy
- DanskAdresseAPI som primær BBR-kilde, når API-nøgle er konfigureret
- Datafordeleren som valgfri BBR-fallback
- Dataforsyningen/DAWA til adresseopslag
- OpenStreetMap/Overpass til åbne kort- og risikodata
- OSRM til vejafstand/køretid
- Open-Meteo til vejrdata
- OpenAI til valgte analysefunktioner
- Docker/GHCR til deployment

## Produktionsentrypoint

Docker-image starter `wsgi:app` med to Gunicorn-workers. `wsgi.py` er et tyndt produktionslag oven på den eksisterende applikation i `app.py` og håndterer blandt andet:

- PostgreSQL advisory lock under startup, så parallelle workers ikke racer ved stations-seeding
- `ProxyFix` bag Cloudflare/Nginx Proxy Manager
- sikre session-cookie defaults
- security headers og cache-policy
- `/health` endpoint
- adgangsbeskyttelse af diagnostiske endpoints
- DanskAdresseAPI som primær BBR-provider med kontrolleret fallback
- progressiv moderne UI-styling uden en risikabel total omskrivning af de eksisterende inline templates

## Centrale environment variables

```env
FLASK_SECRET_KEY=...
BRIEF_ACCESS_CODE=...
DATABASE_URL=postgresql://user:password@postgres:5432/database
APP_BASE_URL=https://indsatsbrief.example.dk

OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini

# Primær BBR-provider
DANSKADRESSE_API_KEY=...
DANSKADRESSE_BBR_CACHE_TTL_SECONDS=86400
DANSKADRESSE_FALLBACK_TO_DATAFORDELER=true

# Valgfri fallback til Datafordeleren
DATAFORDELER_API_KEY=...

# Reverse proxy / session
TRUST_PROXY_HEADERS=true
SESSION_COOKIE_SECURE=true
```

Appen accepterer også `INDSATSBRIEF_DANSKADRESSE_API_KEY` som alternativt navn til DanskAdresseAPI-nøglen.

## DanskAdresseAPI

Når `DANSKADRESSE_API_KEY` er sat, hentes BBR via:

```text
GET /v1/adgangsadresser/{access_address_id}?include=bbr
Authorization: Bearer <API_KEY>
```

Resultatet normaliseres til den samme interne bygningsstruktur, som resten af IndsatsBrief allerede bruger. Dermed kan rapportgeneratoren fortsætte uændret, selv om datakilden skiftes.

BBR-opslag caches som standard i 24 timer pr. worker for at reducere svartid og API-forbrug. Cachen indeholder kun eksterne registerdata og ligger i proceshukommelsen.

## Health check

```bash
curl http://127.0.0.1:8000/health
```

Eksempel:

```json
{
  "ok": true,
  "service": "indsatsbrief",
  "bbr_provider": "danskadresse"
}
```

## Docker

Byg lokalt:

```bash
docker build -t indsatsbrief .
```

Kør eksempelvis:

```bash
docker run --rm -p 8000:8000 --env-file .env indsatsbrief
```

GitHub Actions bygger automatisk multi-architecture images til `linux/amd64` og `linux/arm64`. Push til `main` publicerer `ghcr.io/flr45/indsatsbrief-api:latest`.

## Sikkerhed

Produktionslaget sender som standard blandt andet:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- begrænset `Permissions-Policy`
- HSTS når den offentlige base-URL bruger HTTPS
- `Cache-Control: no-store` på rapport-, admin- og API-sider

Diagnostiske endpoints som BBR-, hydrant-, OSM- og luftfototest er admin-beskyttede i produktionsentrypointet.

## UI

`static/modern.css` og `static/modern.js` lægges progressivt oven på de eksisterende sider. Målet er et mere moderne og operationelt udtryk med:

- bedre kontrast og informationshierarki
- større touch-targets
- tydelig tastaturfokus
- forbedret mobilvisning
- mere kompakt og læsbar rapportvisning
- printvenlig rapport
- `Ctrl/Cmd + K` til hurtigt fokus på adressefeltet
- huskning af ufølsomme UI-præferencer som radius, men aldrig adresse eller rapportindhold

## Projektstruktur

```text
app.py                 Legacy/applikationskerne og routes
wsgi.py                Produktionsruntime og sikker integrationslag
bbr_danskadresse.py    DanskAdresseAPI-adapter
static/modern.css      Moderne visuel overstyring
static/modern.js       Progressive UX-forbedringer
station_data/          Stations- og ressourcedata
Dockerfile             Produktionsimage
.github/workflows/     CI og GHCR build/publish
```

De ældre root-filer `index.html`, `style.css`, `app.js`, `manifest.json` og `service-worker.js` stammer fra et tidligere statisk projekt og indgår ikke i Flask-produktionsentrypointet. De bør fjernes i en separat oprydning, når det er bekræftet, at ingen ekstern deployment stadig bruger dem.
