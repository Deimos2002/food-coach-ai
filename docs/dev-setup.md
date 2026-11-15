# Dev setup

Gets a clean checkout of Food Coach running locally: backend API, dev auth, and the web PWA.

For Azure bootstrap, deployment, backup/restore and credential rotation, see the
[deployment and operations runbooks](deploy.md).

## 1. Start Postgres

**Primary: Docker Compose.**

```
docker compose up -d db
```

This brings up Postgres 16 on `localhost:5432` with the credentials baked into
`backend/app/config.py`'s default (`postgres` / `dev`).

**Alternative: portable binaries.** If Docker isn't available, a portable Postgres 16
build works the same way. Point `PATH` at its `bin` directory and start the server with
`pg_ctl`, e.g.:

```
set PATH=C:\path\to\pg16\pgsql\bin;%PATH%
pg_ctl -D C:\path\to\pg16\data -l logfile start
```

Either way, end up with a running server on `localhost:5432`, user `postgres`, password
`dev`, reachable with `psql -h localhost -U postgres`.

## 2. Migrate and import Ciqual

From `backend/`, with the venv active:

```
cd backend
python -m alembic upgrade head
python -m app.importers.ciqual data/ciqual_2025.xlsx
python -m app.importers.curated catalogue_data
```

This creates the schema and imports the Ciqual catalogue (3,484 products as of the
2025 release). `DATABASE_URL` defaults to `foodcoach`; override it (see step 5) to point
at a different database, including a disposable one for end-to-end testing — see
"End-to-end testing" below.

## 3. Run the dev auth server

Local development uses `scripts/dev_auth.py`: a local
JWKS server plus a CLI that mints matching tokens. Both refuse to run unless
`FOODCOACH_DEV_AUTH=1` is set, so this can never be pointed at anything but a local setup.

```
cd backend
set FOODCOACH_DEV_AUTH=1
python -m scripts.dev_auth serve --port 8001
```

Leave this running in its own terminal.

## 4. Mint a token

In another terminal:

```
cd backend
set FOODCOACH_DEV_AUTH=1
python -m scripts.dev_auth token --oid dev-user --hours 12
```

Copy the printed JWT into `web/.env.local`:

```
VITE_DEV_TOKEN=<paste the token here>
```

`.env.local` is gitignored (`*.local`). The web app only reads `VITE_DEV_TOKEN` when
`import.meta.env.DEV` is true, so it never ships in a production build
(`web/src/lib/auth.ts`). Tokens expire — remint one with the same command when
requests start failing with 401s.

## 5. Run the backend

Before starting the API or deleting data, create the ledger directory and an empty
`backend/data/.ledger-volume` marker. From the repository root in PowerShell:

```powershell
New-Item -ItemType Directory -Force backend/data | Out-Null
New-Item -ItemType File -Force backend/data/.ledger-volume | Out-Null
```

The marker validates the ledger directory; keep the existing `deletion_ledger.jsonl`
intact. The path below is relative to `backend/`, where uvicorn runs. The same marker
is required beside a scratch ledger used for end-to-end testing.

```
cd backend
set DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5432/foodcoach
set ENTRA_ISSUER=http://localhost:8001/
set ENTRA_AUDIENCE=api://foodcoach-dev
set JWKS_URL=http://localhost:8001/.well-known/jwks.json
set ALLOWED_OID=dev-user
set DELETION_LEDGER_PATH=data\deletion_ledger.jsonl
python -m uvicorn app.main:create_app --factory
```

This serves the API on `http://localhost:8000`.

## 6. Run the web app

```
cd web
npm install
npm run dev
```

Open `http://localhost:5173`.

## 7. Using the app from a phone on the LAN

1. Add the PC's LAN origin (e.g. `http://192.168.1.50:5173`) to the backend's
   `CORS_ORIGINS` setting (a JSON list), then restart uvicorn:

   ```
   set CORS_ORIGINS=["http://localhost:5173","http://192.168.1.50:5173"]
   ```

2. Run Vite bound to all interfaces:

   ```
   npm run dev -- --host
   ```

3. Open the printed LAN URL on the phone. The phone and the PC must be on the same
   network, and the PC's firewall must allow inbound connections on 5173 and 8000.

## End-to-end testing

`web/e2e/smoke.spec.ts` runs against the **real** backend — no mocks. It never touches
the `foodcoach` database used for day-to-day manual testing. Instead it runs against a
disposable scratch database, `foodcoach_e2e`, so a failed or half-finished run can't
corrupt real data and `DELETE /me/data` is never a risk to anything that matters.

**One-time setup** (repeatable — the database survives and can be reused for reruns):

```
psql -h localhost -U postgres -c "CREATE DATABASE foodcoach_e2e;"
cd backend
set DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5432/foodcoach_e2e
python -m alembic upgrade head
python -m app.importers.ciqual data/ciqual_2025.xlsx
python -m app.importers.curated catalogue_data
```

**Each run**, start three processes against that database (steps 3-6 above, but with
`DATABASE_URL` pointed at `foodcoach_e2e` and `DELETION_LEDGER_PATH` pointed at a scratch
file), then:

```
cd web
npx playwright install chromium   # first time only
npm run e2e
```

`PLAYWRIGHT_BASE_URL` overrides the web origin the test visits (default
`http://localhost:5173`). The test runs at a 390x844 viewport and resizes to 360px
partway through to check for horizontal scroll.

The `foodcoach_e2e` database is left in place after a run — rerunning the test reuses
the same products and doesn't need the import step again. Drop and recreate it if you
want a clean slate.

## Installability check

`npm run build && npm run preview` serves the production build. If the Lighthouse CLI
isn't available offline, verify installability manually instead:

- `web/dist/manifest.webmanifest` exists and declares `start_url`, `display`
  (`standalone`), and both `icon-192.png` and `icon-512.png`.
- `web/dist/sw.js` exists (generated by `vite-plugin-pwa`'s `generateSW` mode).

Both are produced by every `npm run build`.
