# Lovændringer Over Tid

Et lille Streamlit-dashboard, der viser hvor mange ændringslove der rammer tre udvalgte danske love pr. år:

- Udlændingeloven
- Færdselsloven
- Helligdagsloven

Appen bruger officielle ELI-metadata fra Retsinformation og bygger historikken ved at:

1. finde en seed-version for hver lov fra `underlying-data.md`
2. gå baglæns til den tidligste konsoliderede version via `eli:consolidates`
3. følge versionskæden frem via `eli:consolidated_by`
4. udlede ændringslove via `eli:changed_by`

## Filer

- `app.py`: Streamlit-dashboardet
- `law_history.py`: indlæsning, caching og udledning af lovhistorik
- `underlying-data.md`: datakilder og seed-links
- `slutproduktbeskrivelse.md`: produktbeskrivelse

## Krav

Installer afhængighederne i `requirements.txt`.

Et typisk setup er:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

På Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Kør appen

```bash
streamlit run app.py
```

## Caching

- Streamlit cacher de samlede historikker i 24 timer.
- `law_history.py` cacher rå `.rdfa`-svar i `.cache/eli-rdfa/` i 24 timer.
- En kold første indlæsning er langsommere, men efter opvarmning kan appen genstarte meget hurtigere.

## Datakilder

- Officiel ELI-teknisk dokumentation fra Retsinformation
- Officielle ELI-dokumenter for de tre udvalgte love, som er listet i `underlying-data.md`

## Begrænsninger

- Dashboardet afhænger af, at Retsinformation publicerer sammenhængende ELI-metadata for lovversioner og ændringslove.
- Tallene viser fundne ændringslove pr. år, ikke en semantisk vurdering af hvor store ændringerne var.
