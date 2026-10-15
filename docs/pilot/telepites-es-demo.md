# Telepítés, konfiguráció, frissítés és demó indítása

Részletes angol nyelvű referencia: [../deployment.md](../deployment.md),
[../backup-restore.md](../backup-restore.md), [../demo.md](../demo.md).

## 1. Éles (pilot) telepítés — Docker Compose + PostgreSQL

Követelmény: Linux szerver vagy VM, Docker + Docker Compose, 2 vCPU / 4 GB
RAM elég egy kis tanácsadó céghez (beépített helyi AI nélkül; azzal 6 GB).
HTTPS-hez fordított proxy (pl. Caddy) — példa a deployment.md-ben.

```bash
git clone <repository> threnda && cd threnda
cp .env.example .env
```

A `.env`-ben kötelező beállítani:

| Változó | Mit |
| --- | --- |
| `THRENDA_SECRET_KEY` | egyedi, legalább 32 karakteres titok: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `POSTGRES_PASSWORD` | erős adatbázis-jelszó |
| `THRENDA_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | csak az első indításhoz: az első tanácsadói admin (jelszó: min. 12 karakter, betű + szám/jel) |
| `THRENDA_PUBLIC_BASE_URL` | a meghívólinkekben használt cím, pl. `https://threnda.cegnev.hu` |

Opcionális: `THRENDA_ATTACHMENT_MAX_BYTES` (alap 10 MiB),
`THRENDA_INVITATION_TTL_HOURS` (alap 72), `THRENDA_AI_PROVIDER=disabled`, ha
nem kell a helyi AI. `THRENDA_DEMO_MODE` éles példányon **mindig false**.

```bash
docker compose up -d --build
docker compose logs -f app      # "applying migrations" ... "starting application"
```

Az alkalmazás a `127.0.0.1:8080` címen figyel; kívülről csak a HTTPS-proxyn
át legyen elérhető. Az első bejelentkezés után a `.env`-ből törölhető a
bootstrap-jelszó.

### Első lépések a felületen

1. Bejelentkezés a bootstrap adminnal → **Clients**.
2. Ügyfél létrehozása → az ügyfél kártyáján projekt létrehozása.
3. **Members & settings** → *Invite a new user*: pentesterek (*Pentester*) és az
   ügyfél felhasználói (*Client user*) meghívása. A megjelenő egyszer
   használható linket a saját csatornánkon (e-mail, chat) kell elküldeni —
   a Threnda nem küld e-mailt.
4. Findingek: **Import findings CSV** (sablon letölthető) vagy **New finding**.

### Frissítés és adatbázis-migráció

```bash
docker compose exec -T db pg_dump -U threnda -Fc threnda > threnda-elotte.dump   # mentés
git pull
docker compose up -d --build
```

A konténer indításkor mindig lefuttatja az `alembic upgrade head`-et, a
migrációk automatikusan érvényesülnek. A pilot-kiadás migrációja
(`b479d2b9c183`) a meglévő munkaterületeket "belső" projektként megtartja,
a meglévő munkaterület-adminokat tanácsadói adminná teszi.

### Mentés és visszaállítás

Lásd [../backup-restore.md](../backup-restore.md). Minden adat — a
csatolmányokat is beleértve — az adatbázisban van, egy `pg_dump` mindent
ment. **Kézi teendő:** a PostgreSQL mentés–visszaállítást az első pilot
előtt egyszer próbáld ki a saját szervereden (itt csak a SQLite-változat
lett ténylegesen lefuttatva, mert nem volt Docker démon).

## 2. Demó indítása

Külön példány, külön adatbázissal, csak fiktív adatokkal:

```bash
docker compose -f docker-compose.demo.yml -p threnda-demo up -d --build
# böngésző: http://localhost:8090
```

Docker nélkül (Python 3.12+):

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
export THRENDA_DATABASE_URL=sqlite:///./data/demo.db THRENDA_DEMO_MODE=true \
       THRENDA_SECRET_KEY=demo-only-secret THRENDA_AI_PROVIDER=disabled
mkdir -p data && alembic upgrade head && threnda demo reset --yes
uvicorn threnda.app.main:app --host 127.0.0.1 --port 8090
```

Fiókok (jelszó: `Demo-Threnda-2026!`, vagy a `THRENDA_DEMO_PASSWORD` értéke):
`demo-admin`, `demo-pentester`, `kestrel-client`, `bellwether-client`,
`harbor-client`.

Visszaállítás minden demó előtt: `demo-admin` → **Members & settings** →
**Reset demo data**, vagy `threnda demo reset --yes`.

Ha a demót interneten keresztül is elérhetővé teszed (pl. érdeklődőknek):
HTTPS mögé tedd, állíts be saját `THRENDA_DEMO_PASSWORD`-ot és
`THRENDA_DEMO_SECRET_KEY`-t, `THRENDA_DEMO_ENV=production` mellett.
