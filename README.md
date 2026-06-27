# Tidsregistrering

Statisk PWA til registrering af ture på døgnvagt. Appen er klar til GitHub og Render og bruger kun disse filer:

- `index.html`
- `style.css`
- `app.js`
- `manifest.json`
- `service-worker.js`
- `README.md`

## Funktioner

- Dansk UI
- Vagtstart via `Ny vagt`
- `Gem tur` er slået fra, indtil vagtstart er valgt
- Vagtstart gemmes med dato og klokkeslæt i `localStorage`
- Start- og sluttid bevares ved reload
- Dag-vælger for start og slut: `Vagtdag` og `Næste dag`
- Ture beregnes absolut relativt til vagtstart
- Fremskudt pause: ingen, 30 min eller 60 min
- Mørk tilstand
- Service worker med cache-version, `skipWaiting` og `clients.claim`

## Tidsregler

Vagtstart er referencepunkt.

Eksempel med vagtstart 07:30:

- Før 07:30 samme dag = overtid før vagt
- 07:30 til 23:30 = A-tid
- 23:30 til 07:30 næste dag = B-tid
- Efter 07:30 næste dag = overtid efter vagt

A-tid må gerne overstige 510 minutter. Der er ikke 510-loft i opsummeringen.

Fremskudt pause trækkes fra normal A/B-total, først fra A hvis muligt og derefter fra B. Pausen reducerer ikke overtid før eller efter vagt.

## Test

Appen kører interne test ved opstart. Resultatet kan ses under `Teststatus` nederst på siden og i browserens console.

Testene dækker:

- 07:20-07:54 på vagtdag
- 07:30-08:30 på vagtdag
- 07:30-23:30 på vagtdag
- 23:30 vagtdag til 00:30 næste dag
- 06:00-07:45 næste dag
- Summering af flere ture
- Bevaring af start/slut-felter
- Disabled gem-knap før vagtstart

## Lokal kørsel

Åbn `index.html` direkte i browseren, eller kør en lille lokal server:

```bash
python3 -m http.server 8000
```

Besøg derefter `http://localhost:8000`.

## GitHub

1. Opret et nyt repository på GitHub.
2. Upload filerne eller push dem fra din lokale mappe.
3. Commit ændringerne til `main`.

## Render

Deploy som `Static Site`:

- Build command: tomt felt
- Publish directory: `.`
- Branch: `main`

Hvis appen ikke opdaterer efter deploy, så tryk `Opdater` i appen eller ryd browserens site data/service worker-cache.
