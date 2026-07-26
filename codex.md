# Codex project guide: Telegram CRM

## 1. Назначение проекта

Telegram CRM — приватная веб-панель для подключения и управления несколькими пользовательскими Telegram-аккаунтами через MTProto/Telethon. Это не Telegram Bot API и не crawler. Приложение предназначено для аккаунтов владельца системы и не должно использоваться для скрытого сбора данных, спама или передачи чужих сессий.

Основной production-сценарий: Debian/Ubuntu server, Docker Compose, доступ к панели только через SSH tunnel или приватный VPN.

## 2. Текущий стек

- Python 3.12;
- FastAPI + Uvicorn;
- Telethon 1.x;
- SQLAlchemy 2.x;
- PostgreSQL 16 production / SQLite local development;
- Argon2id для admin-паролей;
- Fernet для шифрования Telegram StringSession;
- Nginx как закрытый gateway;
- vanilla HTML/CSS/JavaScript без frontend build step;
- Docker Compose.

## 3. Структура репозитория

```text
app/
  main.py          FastAPI lifecycle, Telegram runtime, API endpoints
  config.py        Settings из env и Docker secret files
  database.py      SQLAlchemy engine/session/Base
  models.py        ORM-модели
  security.py      admin session, CSRF, audit helpers
  admin_cli.py     add/enable/disable/list administrators
static/
  index.html       SPA markup
  app.js           frontend state, API calls, polling, interactions
  styles.css       responsive UI
deploy/
  nginx.conf       loopback gateway, CSP, headers, login rate limit
scripts/
  init_secrets.py  first-time generation of production secrets
  install_server.sh automated Debian/Ubuntu installation
Dockerfile
docker-compose.yml
README.md          user and operations documentation
SECURITY.md        threat model and hardening
codex.md           this file
```

Local state (`.env`, `.env.docker`, `data/`, `secrets/`, `backups/`, virtual environments) must never enter Git.

## 4. Runtime architecture

### 4.1 Browser and admin authentication

1. Administrator sends username/password to `POST /auth/login`.
2. Password is checked against an Argon2id hash.
3. A random server-side `AdminSession` is stored in the database.
4. Browser receives an HttpOnly session cookie and a SameSite=Strict CSRF cookie.
5. Every mutating authenticated request must include `X-CSRF-Token` equal to the CSRF cookie.
6. `require_admin` validates the server-side session, enabled admin and CSRF token.

Never replace this with a hardcoded frontend key, localStorage token or client-side-only check.

### 4.2 Telegram accounts

- `api_id` and `api_hash` identify the Telegram API application.
- Each Telegram account is authenticated separately by phone, code and optional 2FA password.
- Telethon StringSession is encrypted before database storage.
- `AccountRuntime` keeps a connected Telethon client and short-lived dialog/history/avatar caches in process memory.
- New/edited/deleted message events invalidate affected caches.
- Runtime clients disconnect during FastAPI shutdown.

Do not expose, log or return decrypted StringSession values. Do not add session export endpoints without an explicit security review.

### 4.3 CRM refresh

- Frontend polls active account dialogs and active history every second.
- Backend caches dialogs/history for up to 30 seconds but event handlers invalidate the cache immediately.
- Manual refresh uses `force=true`.
- Only the visible active CRM view polls.
- Opening a visible dialog sends `send_read_acknowledge`; the same incoming message ID is not acknowledged repeatedly.

### 4.4 Account profile synchronization

- `username`, `telegram_user_id`, `telegram_peer_id` and `profile_checked_at` are stored in `telegram_accounts`.
- A background loop runs hourly.
- Telegram profile data is fetched only when `profile_checked_at` is at least 24 hours old.
- `GET /accounts` also waits for due profile synchronization.

### 4.5 Media

- Message history contains metadata only, not raw file bytes.
- `GET /accounts/{account_id}/media/{peer_key}/{message_id}` authenticates the admin, resolves the peer from the account runtime and downloads through Telethon.
- Local default cache: `data/media-cache`.
- Docker cache: `/tmp/media-cache`.
- Limit: 100 MiB per file and 200 MiB total by default.
- Old cache files are evicted first; cache is always recoverable from Telegram.
- File paths are derived only from validated account ID, HMAC peer key and numeric message ID.

## 5. Database model

### `telegram_accounts`

- internal UUID;
- phone and optional label;
- encrypted session ciphertext;
- status (`pending`/`active`);
- Telegram user ID and peer ID;
- current username;
- timestamp of last profile check;
- created/updated timestamps.

### `login_attempts`

Temporary phone code hash for an unfinished Telegram authorization.

### `admin_users`

Admin username, Argon2id password hash, enabled flag and login timestamps.

### `admin_sessions`

Hashed browser session token, hashed CSRF token, expiry and activity timestamp.

### `audit_logs`

Administrator, action, target, IP and timestamp for security-relevant actions.

There is no Alembic yet. Startup performs additive migration for account profile columns. Do not implement destructive startup migrations. For future nontrivial schema changes, introduce reviewed versioned migrations.

## 6. API surface

Public routes:

- `GET /` — frontend;
- `GET /health` — health check;
- `POST /auth/login` — admin login.

Admin authentication:

- `GET /auth/me`;
- `POST /auth/logout`.

Telegram authorization:

- `POST /accounts/login/start`;
- `POST /accounts/{account_id}/login/complete`;
- `POST /accounts/{account_id}/login/password`.

Accounts and avatars:

- `GET /accounts`;
- `GET /accounts/{account_id}/me`;
- `GET /accounts/{account_id}/avatar`;
- `GET /accounts/{account_id}/avatars/{peer_key}`.

CRM:

- `GET /accounts/{account_id}/dialogs`;
- `POST /accounts/{account_id}/chat-history`;
- `POST /accounts/{account_id}/chat-profile`;
- `POST /accounts/{account_id}/mark-read`;
- `GET /accounts/{account_id}/search`;
- `POST /accounts/{account_id}/send-message`;
- `POST /accounts/{account_id}/bot-button`;
- `GET /accounts/{account_id}/media/{peer_key}/{message_id}`;
- `POST /accounts/{account_id}/dialogs/create`.

Security-sensitive operation:

- `POST /accounts/{account_id}/sessions/revoke-others`.

All account routes require an authenticated admin. Mutating requests require CSRF.

## 7. Product behavior

Implemented UI:

- burger sidebar with CRM/accounts/connect sections;
- account picker with avatar and username;
- dialogs with avatars, unread counters and global search;
- Telegram-like chat header/profile panel;
- message history, media previews, bot buttons and text composer;
- one-second updates and read synchronization;
- create personal chat/supergroup/channel modal;
- accounts screen with username, phone and peer ID;
- double confirmation before revoking other Telegram sessions.

Unsupported or intentionally limited:

- sending media from the composer;
- public username assignment for newly created channels;
- automatic removal of Telegram accounts from CRM;
- automatic phone/location sharing through bot buttons;
- files larger than 100 MiB;
- horizontal scaling of in-memory Telegram runtimes across multiple app replicas.

## 8. Security invariants

These rules must be preserved:

1. Nginx stays bound to `127.0.0.1:8080` by default.
2. PostgreSQL and FastAPI do not publish host ports in Compose.
3. Secrets are loaded from Docker secret files in production.
4. Telegram sessions remain encrypted at rest.
5. Admin passwords remain Argon2id hashes.
6. Browser sessions remain server-side and revocable.
7. State-changing requests remain protected by CSRF.
8. Login rate limits remain at Nginx and application levels.
9. Containers remain read-only, capability-free and `no-new-privileges` where configured.
10. Never log phone codes, 2FA passwords, API hash, Fernet key, DB password, cookies or StringSession.
11. Never make bot phone/location request buttons active without explicit consent design.
12. Never test valid session revocation, bot callbacks, message sending, chat creation or participant invitations against a live account unless the user explicitly authorizes that exact test.

## 9. Local development

The local server normally runs at `http://127.0.0.1:8000` with SQLite.

Windows:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

When restarting locally, identify and stop only the exact process listening on port 8000. Do not kill unrelated Python processes.

## 10. Production operations

First install:

```bash
bash scripts/install_server.sh
```

Start/update:

```bash
docker compose --env-file .env.docker build --pull
docker compose --env-file .env.docker up -d
```

Health/logs:

```bash
curl -fsS http://127.0.0.1:8080/health
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail=200 app gateway db
```

Read `README.md` for full installation and operations and `SECURITY.md` before changing network exposure.

## 11. Validation checklist

Run checks proportionally to the change:

```bash
python -m py_compile app/main.py app/models.py app/security.py app/config.py
node --check static/app.js
bash -n scripts/install_server.sh
docker compose --env-file .env.docker config --quiet
```

Safe API smoke tests:

- `/health` returns `{"status":"ok"}`;
- login and `/auth/me` work;
- authenticated account/dialog/history/profile/search reads work;
- media metadata exists and a small media GET returns the expected MIME type;
- dangerous endpoints reject invalid payloads before any Telegram action.

Do not expose private message text, usernames, phone numbers or downloaded media in test output. Report counts, booleans, HTTP statuses and MIME types.

## 12. Editing guidance

- Preserve vanilla frontend unless a migration is explicitly requested.
- Keep API error messages user-readable in Russian.
- Escape all Telegram-provided text before inserting HTML.
- Do not place raw entity IDs/access hashes in browser-visible URLs; use encrypted dialog refs and HMAC peer keys.
- Keep frontend polling non-overlapping (`pollBusy`).
- Keep per-account Telethon operations serialized with `AccountRuntime.lock` where state/cache consistency matters.
- Invalidate dialog/history caches after sends, bot actions and chat creation.
- Any external Telegram mutation must be deliberate in UI and audited where security-relevant.
- Keep README and this file synchronized when adding endpoints, env variables, deployment steps or security assumptions.

## 13. Git handoff checklist

Before the first push:

1. verify `.env`, `.env.docker`, `data/`, `secrets/`, `backups/`, `.venv/` and `.setup-venv/` are ignored;
2. search for API hash, session strings, passwords, cookies and phone codes;
3. choose a license or keep the repository private;
4. initialize Git and inspect the complete staged diff;
5. create the remote only after the owner provides hosting/provider/repository details;
6. never commit generated databases, media cache or backup files.
