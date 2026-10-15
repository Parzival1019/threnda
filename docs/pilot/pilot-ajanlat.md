# Pilotajánlat — vázlat

> Vázlat. A **[DÖNTENDŐ]** pontokat a tulajdonosnak kell véglegesítenie,
> mielőtt bármelyik ügyfélnek kiküldjük.

## Kinek

Kisebb pentest- és kiberbiztonsági tanácsadó cégeknek (kb. 2–20 fő), akik
ma táblázatban, e-mailben vagy a riport-PDF-ben követik, hogy az ügyfél mit
javított ki, és mit kell újratesztelni.

## Mit kap a pilotügyfél

- 30 nap használat, **egy ügyfélprojektre** (korlátlan finding, legfeljebb
  **[DÖNTENDŐ: pl. 5]** belső és **[DÖNTENDŐ: pl. 5]** ügyféloldali felhasználó).
- Telepítés a pilotügyfél szerverére (vagy **[DÖNTENDŐ]** általunk üzemeltetett,
  elkülönített példányra), HTTPS-sel, mentéssel.
- 60 perces betanítás a tanácsadó csapatnak, 15 perces portál-bemutató a
  végügyfélnek.
- Heti 30 perces egyeztetés; support: **[DÖNTENDŐ: csatorna, válaszidő,
  munkaidő]**.
- A pilot végén az adatok exportja (CSV/JSON + adatbázismentés), vagy kérésre
  törlése.

## Mit kérünk

- Egy valós projekt findingjeinek felvitele (CSV-sablonnal vagy kézzel).
- A végügyfél legalább egy felhasználójának bevonása.
- Rövid visszajelzés hetente; a végén 30 perces kiértékelés.
- **[DÖNTENDŐ]** hozzájárulás ahhoz, hogy (anonim vagy névvel) referenciaként
  hivatkozhassunk rájuk — csak írásbeli engedéllyel.

## Ár

**[DÖNTENDŐ]** — lehetőségek: ingyenes pilot / kedvezményes díj, amely
beszámít az első éves előfizetésbe / teljes díj elégedettségi garanciával.

## Mérhető sikerfeltételek (közösen rögzítjük a 0. napon)

| Mérőszám | Javasolt cél |
| --- | --- |
| A projekt findingjeinek aránya a Threndában | 100% |
| Findingek felelőssel és határidővel | ≥ 90% |
| A végügyfél jelezte-e legalább egy javítást a portálon (nem e-mailben) | igen |
| Lezárt findingek közül retesttel igazolt | 100% (a termék kikényszeríti) |
| Státuszriport előállításának ideje | < 5 perc (a mostani kézi riporthoz képest mérve) |
| A tanácsadó csapat szerint marad-e a Threnda a pilot után | igen / nem + indoklás |

Ha a sikerfeltételek teljesülnek: **[DÖNTENDŐ]** átállás fizetős csomagra
(lásd [csomagok.md](csomagok.md)) a pilotadatok megtartásával.

## Kizárva a pilotból

Egyedi fejlesztés, integrációk (Jira stb.), SSO, többnyelvű felület,
helyszíni munka **[DÖNTENDŐ, ha kell]**.
