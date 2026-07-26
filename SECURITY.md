# Security model

The default deployment is private-by-construction:

- Nginx publishes only `127.0.0.1:8080`; it is not reachable on a public server interface.
- FastAPI and PostgreSQL have no published host ports.
- PostgreSQL is attached only to an internal Docker network and uses SCRAM-SHA-256.
- FastAPI has a separate outbound network because Telegram connectivity requires internet egress.
- Telegram API credentials, database credentials, the session encryption key, and the bootstrap password hash are mounted as Docker secrets.
- Telegram authorization sessions are encrypted with Fernet before being stored in PostgreSQL.
- Admin passwords are stored as Argon2id hashes. Browser sessions are random server-side records with expiry, HttpOnly cookies, SameSite=Strict, and CSRF tokens.
- Login attempts are rate-limited at Nginx and locked in the application after repeated failures.
- The application and gateway run read-only, without Linux capabilities, with `no-new-privileges` and limited tmpfs mounts.

## Server boundary

Use Debian or Ubuntu with current security updates and Docker Engine 28 or newer. Keep only SSH open in the host firewall. Disable SSH password authentication and root login; use SSH keys. Reach the panel through a tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 crm-admin@server
```

Then open `http://127.0.0.1:8080`. Do not change the Compose mapping to `0.0.0.0` unless a separately reviewed VPN or authenticated TLS gateway is in front of it.

## Data protection

- Encrypt the server disk. Docker volume encryption is not provided by Compose.
- Back up PostgreSQL with `pg_dump`, encrypt the backup before moving it off-host, and test restore procedures.
- Back up the session encryption key separately. Losing it makes Telegram sessions unrecoverable; leaking it together with the database defeats session encryption.
- Never copy the `secrets` directory into images, Git, CI logs, or unencrypted backups.
- Rotate admin passwords and revoke Telegram sessions after any suspected host compromise.
- Pin container image digests after testing updates in staging, and apply OS/Docker/container security updates regularly.

No architecture can make a database impossible to compromise. This design minimizes exposure and blast radius; host compromise, stolen SSH keys, malicious administrators, vulnerable dependencies, and unencrypted backups remain material risks.
