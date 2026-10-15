# Community és fizetős csomag — funkciókülönbség-javaslat

> Javaslat, nem döntés. Kiindulópont a CLAUDE.md elve: a Community legyen
> valóban hasznos, mesterséges finding-/eszközlimit nélkül; a fizetős csomag
> a *tanácsadó cég ügyfélkapcsolati* és üzemeltetési igényeiért kérjen pénzt.

**Fontos tény:** a pilothoz elkészült összes funkció (ügyfélportál,
ügyfélszerepkör, meghívás, retest, riport) most a Community kódbázisban van,
és a forráskód licence még nincs kiválasztva. Ha ezek egy része fizetős lesz,
azt a licencdöntéssel együtt kell eldönteni (pl. külön modul az
`threnda-enterprise` privát repóban, vagy licenckulcsos funkciókapcsoló).
**[DÖNTENDŐ]**

## Javasolt felosztás

| Terület | Community (ingyenes, self-hosted) | Fizetős „Consultancy" csomag **[DÖNTENDŐ név, ár]** |
| --- | --- | --- |
| Findingek, import (CSV-sablon, scanner CSV/JSON/SARIF), deduplikáció, kockázati pontszám | ✓ korlátlan | ✓ |
| Kézi finding, felelős, határidő, történet, hozzászólás, csatolmány | ✓ | ✓ |
| Retest és igazolt lezárás | ✓ | ✓ |
| Belső szerepkörök (admin, pentester, viewer) | ✓ | ✓ |
| Ügyfelek és ügyfélprojektek | ✓ (javaslat: max. **[DÖNTENDŐ, pl. 2]** aktív ügyfél, *vagy* korlátlan) | ✓ korlátlan |
| Ügyfélportál és ügyféloldali felhasználók | javaslat: ✓ 1 ügyfélprojekthez (kipróbálásra) | ✓ korlátlan |
| Átadható státuszriport, CSV-export | ✓ | ✓ + márkázott (logó, színek) PDF-riport — *még nincs kész* |
| E-mail-értesítések (meghívó, határidő, javítás jelezve, retest-eredmény) | — | ✓ — *még nincs kész* |
| SSO (OIDC/SAML), kétfaktoros belépés | — | ✓ — *még nincs kész* |
| Jira / ServiceNow integráció | — | ✓ (Enterprise-ütemterv) — *még nincs kész* |
| Telepítési segítség, frissítési szolgáltatás | dokumentáció | ✓ **[DÖNTENDŐ terjedelem]** |
| Support | közösségi (GitHub issues) | ✓ **[DÖNTENDŐ csatorna, válaszidő]** |

A „még nincs kész" sorokat értékesítéskor csak ütemtervként szabad említeni,
dátum ígérete nélkül (amíg nincs döntés róla).

## Megfontolandó alternatíva

Minden funkció Community marad (nyílt forrás), és a bevétel a
**szolgáltatásból** jön: telepítés, üzemeltetés (managed példány), support,
egyedi riportsablon. Egyszerűbb licencelés, kisebb fejlesztési teher, de
gyengébb árazási erő. **[DÖNTENDŐ]**
