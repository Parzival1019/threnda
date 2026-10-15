# Kipróbálási és bevezetési útmutató

Két része van: **(A)** egy külső tesztelő ezzel a leírással, segítség nélkül
végig tudja vinni az alapfolyamatot (elfogadási teszt); **(B)** egy pilotügyfél
bevezetési terve.

## A) Az alapfolyamat végigvitele (kb. 30 perc)

Előfeltétel: futó demópéldány (lásd [telepites-es-demo.md](telepites-es-demo.md)
2. pont), frissen visszaállítva. Két böngészőablak kell: egy normál és egy
privát (inkognitó), hogy egyszerre legyen bejelentkezve a tanácsadó és az
ügyfél. Jelszó minden fióknál: `Demo-Threnda-2026!`.

A fájlok a repóban: `demo-data/pilot/`.

| # | Lépés | Elvárt eredmény |
| --- | --- | --- |
| 1 | Normál ablak: bejelentkezés `demo-admin`-ként. | Megjelenik az **All projects** áttekintés két projekttel (Kestrel, Bellwether), sárga DEMO sávval. |
| 2 | **Clients** → *Harbor Analytics (fictional)* kártyáján új projekt: `Harbor web app pentest` → *Create project*. | A projekt állapotoldala nyílik meg, üres számlálókkal. |
| 3 | Bal menü **Import findings CSV** → feltöltés: `harbor-broken-example.csv`. | Hibalista: 1. sor `ownr` ismeretlen oszlop, 2. sor hibás dátum, 3. sor hiányzó cím, 4. sor ismeretlen súlyosság. „Nothing was imported". |
| 4 | Ugyanott: *Download template* — megnyílik/letöltődik a sablon. Majd feltöltés: `harbor-webapp-pentest.csv`. | Összesítő: 7 sor, 7 new. |
| 5 | Ugyanazt a fájlt még egyszer feltölteni. | 0 new, 7 unchanged — nincs duplikátum. |
| 6 | **Findings** → `HA-03 Password reset token…` sor → *Workflow* fül: Owner = `Harbor dev team`, Due date = egy múltbeli dátum → *Save*. | A fejlécben megjelenik a felelős és a „(overdue)" jelzés; a *Timeline* fülön látszik a változás és ki csinálta. |
| 7 | *Discussion* fül: belső megjegyzés írása, *Internal* bepipálva. | A megjegyzés szaggatott keretes, „internal — hidden from client" felirattal. |
| 8 | **Members & settings** → *Add an existing account*: `harbor-client`, szerep *Client user* → *Add member*. | Megjelenik a listában „client account" jelöléssel. |
| 9 | Privát ablak: bejelentkezés `harbor-client`-ként. | Rögtön a portál projektoldala nyílik meg 7 findinggel. Más ügyfél projektje nem látszik. |
| 10 | Portálon a HA-03 finding megnyitása. | A belső megjegyzés **nem** látszik. |
| 11 | *Report a fix*: megjegyzés + **Mark as fixed — request retest**; majd *Upload evidence*: `evidence-reset-token-fix.txt`. | „Fixed — awaiting retest" állapot, a fájl a listában, a történetben „Marked fixed". A finding **nincs** lezárva. |
| 12 | Normál ablak (demo-admin): bal menü **Awaiting retest**. | A HA-03 szerepel; a *Discussion* fülön látszik az ügyfél fájlja (letölthető). |
| 13 | *Retest* fül: jegyzet „Still reusable once" → **Retest failed — reopen**. | Állapot: Open; a retest-előzményben FAILED. Az ügyfél portálján a jegyzet látszik. |
| 14 | Privát ablak: újra *Mark as fixed*. Normál ablak: *Retest* → **Retest passed — close as verified**. | Állapot: **Verified closed**. |
| 15 | **Status report**. | Számlálók: Verified closed 1, Overdue stb.; a táblázatban a HA-03 „passed" retesttel. *Print / save as PDF* működik. A portálon (privát ablak, *Status report*) ugyanazok a számok. |
| 16 | **All projects**. | A Harbor projekt sora a nyitott, kritikus, lejárt, retestre váró és lezárt számokkal. |
| 17 | Opcionális: `harbor-webapp-pentest-v2.csv` importja. | 1 new (HA-08), 1 updated (HA-01 új cím, ugyanaz az ID — nincs duplikátum), „1 previously imported finding not in this file" (HA-05) — a HA-05 állapota **nem** változik. |
| 18 | Opcionális elkülönítési próba: privát ablakban kézzel megnyitni egy Kestrel finding portál-URL-jét (pl. `/portal/projects/1/findings/1`). | „Not Found". |

Ha bármelyik elvárt eredmény nem teljesül, jegyezd fel a lépés számát, a
képernyőt és az időpontot.

## B) Bevezetés egy pilotügyfélnél (javaslat)

| Mikor | Ki | Mi |
| --- | --- | --- |
| 0. nap | mi + ügyfél IT | Telepítés az ügyfél (tanácsadó cég) szerverére vagy általunk előkészített VM-re; HTTPS; bootstrap admin; mentés beállítása és egy visszaállítási próba. |
| 1. nap | tanácsadó admin | 60 perces betanítás: ügyfél és projekt létrehozása, meghívás, CSV-sablon, retest. |
| 1–3. nap | pentester | Egy valós (vagy a legutóbbi) projekt findingjeinek importja a sablonnal; felelősök, határidők. |
| 3. nap | tanácsadó + végügyfél | A végügyfél felhasználóinak meghívása; 15 perces portál-bemutató nekik. |
| 4–25. nap | mindenki | Napi munka a Threndában; heti 30 perces egyeztetés velünk; hibák/kérések gyűjtése. |
| 26–30. nap | mi + tanácsadó | Sikerfeltételek kiértékelése (lásd [pilot-ajanlat.md](pilot-ajanlat.md)), döntés a folytatásról, adatmentés átadása vagy törlése. |
