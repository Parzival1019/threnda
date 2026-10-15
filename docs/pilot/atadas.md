# Átadás — Threnda pilot-felkészítés

Ág: `feat/pilot-readiness` (a `feat/community-v0.1` ágról, 3+ commit; nincs
pusholva, nincs összefésülve). A `feat/threat-signals` WIP ág érintetlen; ha
később összefésülöd, a két ág Alembic-migrációi ugyanarra a szülőre
(`dd748501af61`) épülnek — egy merge-migráció kell majd.

## 1. Kiinduló állapot (audit) és mi lett belőle

| Célállapot-elem | Kiinduláskor | Most |
| --- | --- | --- |
| Tanácsadó szervezet, ügyfelek, ügyfélprojektek | **hiányzott** (csak „workspace") | kész: ügyfelek, projektek, tanácsadói admin |
| Bejelentkezés | működött (Argon2id, munkamenetek, zárolás) | változatlan, használva |
| Felhasználómeghívás | **hiányzott** (admin adott jelszót) | kész: egyszer használható, lejáró, visszavonható link |
| Szerepkörök: tanácsadói admin, pentester, ügyfél | részleges (admin/analyst/viewer) | kész: + ügyféloldali szerep, ügyfélhez kötve |
| Ügyfél csak a saját adatait éri el | **hiányzott** | kész, szerveroldalon, tesztekkel |
| Kézi findingfelvétel és szerkesztés | **hiányzott** (csak import) | kész |
| CSV-import mintafájllal, ellenőrzéssel, hibalistával, összesítővel | részleges (általános CSV-varázsló, részleges import) | kész: sablon, teljes ellenőrzés, semmi sem kerül be hibánál, összesítő |
| Ismételt import duplikáció nélkül, dokumentált szabályok | működött (scanner-importnál) | sablonnál is, ID-alapú azonosítással, dokumentálva |
| Findingmezők (cím, leírás, súlyosság, érintett rendszer, javaslat, felelős, határidő, állapot) | részleges (érintett rendszer nem volt mező; cím/súlyosság nem volt szerkeszthető) | kész |
| Lista keresés/szűrés | részleges (csak cím) | cím, leírás, rendszer, felelős; lejárt-szűrő |
| Hozzászólás, csatolmány, eseménytörténet | csak történet + belső megjegyzés | kész: belső/ügyfélnek látható hozzászólás, csatolmány, szűrt történet |
| Ügyfélportál, javításjelzés | **hiányzott** | kész |
| Retest, igazolt lezárás / újranyitás | **hiányzott** („resolved" bárki által beállítható volt) | kész: lezárás csak sikeres retesttel |
| Áttekintés: nyitott, kritikus, lejárt, retestre váró | részleges (projektenként, retest nélkül) | kész: projektenként és összes projektre |
| Átadható státuszriport, CSV-export | csak export | kész: nyomtatható riport (PDF-be menthető) + bővített export |
| Javított ≠ ellenőrzött lezárás; import-eltűnés nem zár le | az import nem zárt le, de a kézi „resolved" igen | kikényszerítve |
| Szerveroldali jogosultság, elkülönítés tesztelve | működött workspace-szinten | ügyfélszinten is, 8 elkülönítési tesztcsoport |
| Csatolmány-hozzáférés, méret- és típuskorlát | — | kész |
| Titkok, naplók | jó alap | + meghívótoken hash-elve, naplóból kitakarva |
| Self-hosted telepítés, konfiguráció, frissítés, migráció doksi | megvolt | kiegészítve (új változók, migrációs megjegyzés) |
| Mentés és visszaállítás dokumentálva és kipróbálva | csak dokumentálva | SQLite: parancs + automatizált teszt; PostgreSQL: dokumentálva, **nem kipróbálva** |
| Visszakövethetőség | megvolt | + retest, hozzászólás, meghívás, riport, letöltés |
| Fiktív, visszaállítható demó | **hiányzott** (csak szintetikus importfájlok) | kész, két biztonsági zárral |
| Landing page, útmutatók, demóforgatókönyv, GYIK, pilotajánlat, csomagok | **hiányzott** | kész (vázlatok [DÖNTENDŐ] jelölésekkel) |

Közben javított, már korábban is meglévő hiba: több projekt esetén a
részletpanel (állapot, felelős, határidő, kockázati mezők), a review- és a
dokumentum-űrlapok projektazonosító nélkül küldtek, és a projektválasztó süti
sosem mentődött — más projektben 404 lett az eredmény. Javítva, regressziós
teszttel.

## 2. Teszteredmények

| Ellenőrzés | Eredmény |
| --- | --- |
| Teljes tesztkészlet (`pytest`) | **723 sikeres**, 0 hibás (kiinduláskor 686) |
| Ebből új pilot-tesztek (`tests/test_pilot_*.py`) | 37: alapfolyamat végig; ügyfelek közti elkülönítés findingre, fájlra, történetre, riportra, exportra és közvetlen API-kérésre; belső tartalom szűrése; lezárási jogosultságok (ügyfél, viewer, pentester); tagság-kötési szabályok; hibás CSV-k (oszlop, súlyosság, dátum, duplikáció, kódolás, üres fájl); ismételt import (nincs duplikátum, nincs automatikus lezárás, Threnda-oldali szerkesztés megmarad); csatolmány típus/méret/elérési út/letöltési fejlécek; meghívó egyszeri, lejárt, visszavont, gyenge jelszó, naplóredakció; demó-visszaállítás csak demó-adatbázison; mentés → sérülés → visszaállítás |
| Ruff lint + formázás, szigorú mypy, `pip check` | rendben |
| Bandit (magas súlyosság), `pip-audit` | nincs találat |
| Alembic upgrade → downgrade → upgrade (SQLite) | rendben |
| A [kipróbálási útmutató](kiprobalasi-utmutato.md) 18 lépése élő demópéldányon, fej nélküli Chrome-ban végigjátszva | 21/21 ellenőrzés rendben |
| Landing page demókérő űrlap kézbesítése helyi fogadóra (`site/check_form.py`) | rendben (siker, fogadói hiba, beállítatlan állapot) |
| Docker image build, Compose futtatás, PostgreSQL mentés/visszaállítás | **nem futott** — a gépen nem futott a Docker démon. A Compose-fájlok szintaktikai ellenőrzése (`docker compose config`) rendben. |

Futtatás:

```bash
cd ~/Projects/threnda && source .venv/bin/activate
pytest && ruff check . && mypy threnda tests
```

## 3. Kipróbálás, telepítés, demó

- Külső tesztelőnek: [kiprobalasi-utmutato.md](kiprobalasi-utmutato.md) (A rész).
- Telepítés és demó indítása: [telepites-es-demo.md](telepites-es-demo.md).
- Demó gyorsan, Docker nélkül: a telepítési leírás 2. pontja (kb. 3 perc).

## 4. Ismert korlátok

- Nincs e-mail-küldés: meghívólinket, határidő-figyelmeztetést, státuszértesítést a Threnda nem küld.
- Nincs SSO, kétfaktoros belépés, önkiszolgáló jelszó-visszaállítás (elfelejtett jelszónál: az admin eltávolítja és újra meghívja — ehhez a régi fiókot más névvel kell meghívni, mert a felhasználónév foglalt; ez kényelmetlen, ütemtervre való).
- A felület angol nyelvű; a pilotdokumentáció magyar.
- Hozzászólás és csatolmány nem szerkeszthető/törölhető; csatolmányoknak nincs projektenkénti kvótája.
- A PDF-riport a böngésző nyomtatásával készül; nincs márkázható sablon.
- Az ügyfél nem rendelhet felelőst és határidőt (csak a tanácsadó).
- Az ügyfelekhez és projektekhez nincs archiválás/törlés a felületen.
- A projekt nevek példányszinten egyediek.
- Az instance-szintű auditesemények (ügyfél létrehozása, demó-visszaállítás, sikertelen belépés ismeretlen névvel) az adatbázisban vannak, de a projektszintű Audit log oldalon nem jelennek meg.
- A „Scanner data" részek (importvarázsló, források, dokumentumok, review) változatlanok; a pilot fő folyamatához nem szükségesek.
- A macOS Desktop-változat továbbra sem nyilvános (aláírás, notarizáció hiányzik), és egyfelhasználós.
- Független biztonsági audit nem készült.

## 5. Kézi teendők

1. **Kód átnézése és összefésülése** (`feat/pilot-readiness`), push.
2. **Docker-próba:** `docker compose up -d --build` és
   `docker compose -f docker-compose.demo.yml -p threnda-demo up -d --build`,
   majd a [kipróbálási útmutató](kiprobalasi-utmutato.md) egyszeri kézi végigjátszása a konténeres demón.
3. **PostgreSQL mentés–visszaállítás próba** a [../backup-restore.md](../backup-restore.md) szerint, az első pilot előtt.
4. **Landing page űrlap kézbesítése:** `site/config.js` — `formEndpoint` (pl. Formspree) vagy `contactEmail` beállítása, adatkezelési tájékoztató link a hozzájárulás mellé, majd egy valódi tesztkérés elküldése és megérkezésének ellenőrzése. **Jelenleg az űrlap nem kézbesít sehova** (ezt az oldal ki is írja).
5. Landing page domain és tárhely; utána a képernyőképek frissítése, ha a felület változik.
6. Ha a demót nyilvánosan elérhetővé teszed: saját `THRENDA_DEMO_PASSWORD` és `THRENDA_DEMO_SECRET_KEY`, HTTPS.
7. A [pilotajánlat](pilot-ajanlat.md) és a [GYIK](gyik.md) **[DÖNTENDŐ]** pontjainak kitöltése.

## 6. Üzleti döntések

Külön lista: [uzleti-dontesek.md](uzleti-dontesek.md) (ár, support, üzemeltetési
modell, licenc, Community/fizetős felosztás, demókérések fogadása, domain stb.).
