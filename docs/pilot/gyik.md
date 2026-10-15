# GYIK — telepítés, adatkezelés, jogosultságok, support, korlátok

## Telepítés

**Felhős szolgáltatás?** Nem. A Threnda self-hosted: a tanácsadó cég (vagy
megbízásából mi) a saját szerverén futtatja Docker Compose-zal
(PostgreSQL-lel). Egygépes macOS Desktop-változat is van, de az egy
felhasználóra szól, és még nincs aláírt, nyilvános telepítője.

**Mi kell hozzá?** Linux szerver/VM Dockerrel, kb. 2 vCPU és 4 GB RAM, HTTPS-hez
fordított proxy. Internetkapcsolat működés közben nem szükséges.

**Mennyi idő a telepítés?** Egy rutinos üzemeltetőnek kb. egy óra a
HTTPS-sel együtt. Lépések: [telepites-es-demo.md](telepites-es-demo.md).

**Hogyan frissül?** `git pull` + `docker compose up -d --build`; az
adatbázis-migráció automatikusan lefut. Előtte mentés.

## Adatkezelés

**Hol vannak az adatok?** Kizárólag a saját szerveren, a PostgreSQL
adatbázisban — a feltöltött bizonyítékfájlokkal együtt. Telemetria nincs, a
Threnda nem küld adatot sehova. (A webhookot csak admin állíthatja be.)

**Van mentés?** Az adatbázis mentése a szokásos `pg_dump`-pal; leírás:
[../backup-restore.md](../backup-restore.md). A mentés ütemezése az
üzemeltető feladata.

**Titkosítás?** HTTPS a fordított proxyn; jelszavak Argon2id-vel hashelve;
munkamenet- és meghívótokenek csak hash-ként tárolva. Az adatbázis és a
mentések nyugalmi titkosítása a szerver/tároló szintjén oldandó meg.

**Mi kerül a naplókba?** Jelszó, munkamenet-token és meghívótoken nem. Az
admin-auditnapló a fiókokat, tagságokat, meghívásokat, exportokat,
fájlletöltéseket rögzíti; a findingek minden változása a finding saját
történetében látszik.

**Adatfeldolgozói szerződés / GDPR?** Mivel self-hosted, az adatkezelő és a
feldolgozó a tanácsadó cég; ha mi üzemeltetjük, az külön megállapodás
**[DÖNTENDŐ]**.

## Jogosultságok

**Milyen szerepkörök vannak?** Tanácsadói admin, pentester, belső
olvasó (viewer), ügyféloldali felhasználó. Projektenként adhatók.

**Mit lát az ügyfél?** Csak a saját projektjét: findingek, nem belső
megjegyzések és fájlok, a releváns előzmények, a státuszriport. Nem lát
belső megjegyzést, belső fájlt, importot, más ügyfelet. Ezt a szerver
ellenőrzi, nem csak a felület rejti el — automatizált tesztek fedik le.

**Az ügyfél le tudja zárni a findinget?** Nem. Jelezheti, hogy dolgozik
rajta vagy kijavította; lezárni csak a pentester tudja sikeres retest után.

**SSO / kétfaktoros belépés?** Jelenleg nincs; helyi felhasználónév +
jelszó (fiókzárolással, sebességkorláttal).

**Hogyan kerül be egy új felhasználó?** Az admin egyszer használatos,
lejáró meghívólinket hoz létre, amelyet saját csatornán küld el; a
meghívott maga állít jelszót. A Threnda nem küld e-mailt.

## Support

**Milyen supportot kapunk?** **[DÖNTENDŐ]** — a pilotajánlatban szereplő
supportvállalás (csatorna, válaszidő, időablak) még nincs eldöntve.

**Hova jelezzünk hibát?** **[DÖNTENDŐ]** (e-mail cím / ticketrendszer).

## Korlátok (jelenlegi verzió)

- Nincs e-mail-értesítés (meghívó, határidő, státuszváltozás) — a linket kézzel kell továbbítani.
- Nincs SSO/SAML/OIDC, nincs kétfaktoros belépés, nincs önkiszolgáló jelszó-visszaállítás.
- A felület angol nyelvű.
- Hozzászólás és csatolmány nem szerkeszthető és nem törölhető.
- PDF-riport a böngésző „Nyomtatás → Mentés PDF-be" funkciójával készül; nincs márkázható riportsablon.
- Nincs Jira/ServiceNow vagy más élő integráció; CSV/JSON export és általános webhook van.
- Egy példány = egy tanácsadó cég (nem többbérlős SaaS).
- Független biztonsági audit még nem készült.

**Van tanúsítványunk (ISO 27001, SOC 2)?** Nincs. Ne állítsunk ilyet.

**Vannak referenciaügyfelek?** Még nincsenek; a pilotok célja többek között ez.
