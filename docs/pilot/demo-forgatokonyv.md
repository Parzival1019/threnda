# Tízperces demóforgatókönyv

**Előkészítés (demó előtt 5 perc):** demópéldány visszaállítása
(`demo-admin` → Members & settings → Reset demo data). Két böngészőablak: A
(normál) `demo-admin`, B (privát) `harbor-client` — jelszó
`Demo-Threnda-2026!`. A `demo-data/pilot/` mappa legyen kéznél.

**Az üzenet egy mondatban:** *„Egy helyen követhető minden finding a
riporttól az igazoltan lezárt javításig — és az ügyfél is ugyanott jelez,
nem e-mailben."*

| Idő | Lépés | Mit mutatsz / mondasz |
| --- | --- | --- |
| 0:00–1:00 | A ablak: **All projects** | „Ez a tanácsadó cég nézete: minden ügyfélprojekt, nyitott, kritikus, lejárt és retestre váró findingek." Mutasd a Kestrel sort. |
| 1:00–2:00 | 1. **Ügyfélprojekt létrehozása**: Clients → Harbor Analytics → `Harbor web app pentest` | „Ügyfelenként és projektenként teljesen elkülönítve: az ügyfél csak a saját projektjét látja." |
| 2:00–3:30 | 2. **Findingek importja**: Import findings CSV → `harbor-webapp-pentest.csv` | Mutasd meg a sablont. „Ha hibás a fájl, semmi sem kerül be, és soronként látod a hibát." (Ha van idő: előbb `harbor-broken-example.csv`.) Újratöltésnél nincs duplikátum. |
| 3:30–4:30 | 3. **Felelős és határidő**: Findings → HA-03 → Workflow: owner, due date; Discussion: belső megjegyzés | „Minden változás visszakövethető: ki, mikor, mit." Timeline fül. |
| 4:30–5:00 | Members & settings → add `harbor-client` (*Client user*) | „Élesben meghívólinket küldesz; a meghívott maga állít jelszót." |
| 5:00–6:30 | 4. **Ügyfél: javítás jelzése és bizonyíték**: B ablak → HA-03 → megjegyzés, *Mark as fixed*, feltöltés `evidence-reset-token-fix.txt` | „A belső megjegyzés nem látszik az ügyfélnek. A javítottnak jelölés **nem** zárja le a findinget." |
| 6:30–8:00 | 5. **Retest** és 6. **lezárás/újranyitás**: A ablak → Awaiting retest → HA-03 → Retest: *failed* (jegyzettel) → B ablakban látszik a jegyzet → újra fixed → A: *passed* | „Lezárni csak retest után lehet, és a retest eredménye dokumentált." |
| 8:00–9:30 | 7. **Összesített állapot**: Status report → Print / save as PDF; B ablak: ugyanaz a riport | „Ezt adod át az ügyfélnek vagy a vezetőségnek; CSV-exportként is." |
| 9:30–10:00 | Zárás | Self-hosted: az adatok nálatok maradnak. Pilot: 30 nap, egy ügyfélprojekt (lásd pilotajánlat). |

**Ha kérdezik — rövid válaszok:** lásd [gyik.md](gyik.md). Ne ígérj olyat,
ami nincs a termékben (pl. e-mail értesítés, SSO, Jira-integráció, mobilapp,
tanúsítvány) — ezek a [atadas.md](atadas.md) „hiányzik" listáján vannak.

**Ha elakadsz:** a demó bármikor visszaállítható; a Kestrel projekt
előre feltöltött állapotokkal (lejárt, retestre vár, sikertelen retest,
lezárt, kockázat elfogadva) önmagában is bemutatható.
