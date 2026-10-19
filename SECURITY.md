# Security

## Reporting

Please report vulnerabilities privately: open a GitHub security advisory at
https://github.com/matttsys/RayZen-Pilot/security/advisories/new, or message the maintainer on Telegram via @Rzroot.
Don't post tokens, database files or member data in public issues.

## Secrets

- `.env` holds `BOT_TOKEN`, `AI_API_KEY` and `OWNER_ID`. It is never part of a release archive and the updater never touches it.
- If a token was ever pasted into a chat, an issue or a screenshot, revoke it: `/revoke` in @BotFather, and rotate the AI key at your provider.
- The bot never prints the token or the AI key; logs and the owner panel redact them.

## Signed releases and `/update`

Every release since 5.0.0-alpha.1 has a manifest (`rayzen-pilot-vX-omni-manifest.json`) signed with the project's Ed25519 release key (`…-manifest.json.sig`). The manifest lists, for each edition, the archive name, size, SHA-256 and top-level folder.

The updater (`/update` and the owner panel) only installs an archive when:

1. the manifest signature verifies against the public key pinned in `pilot/updater.py` (or `UPDATE_PUBKEY` in `.env`);
2. the version is newer than the running one (no downgrades) and the edition matches this build;
3. the archive size and SHA-256 match the signed manifest;
4. the archive has a single top-level folder, no absolute paths, no `..`, no symlinks, no `.env`/`data/`, sane sizes;
5. the requirements in the new version are already installed (it never runs `pip` from chat).

The checksum file next to the archives is integrity metadata only. It comes from the same place as the archives, so the updater does not treat it as proof of origin.

Release public key (base64, raw Ed25519):

```
V5rxvn78gNnqWjT4JJ0VjuxPKS81y2lY/frxZK0AuRk=
```

Verify a download yourself (Python with `cryptography` installed):

```bash
python3 - <<'EOF'
import base64, hashlib, json, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
slug = "rayzen-pilot-v5.0.0-alpha.1-omni"
key = Ed25519PublicKey.from_public_bytes(base64.b64decode("V5rxvn78gNnqWjT4JJ0VjuxPKS81y2lY/frxZK0AuRk="))
raw = open(f"{slug}-manifest.json", "rb").read()
key.verify(base64.b64decode(open(f"{slug}-manifest.json.sig", "rb").read()), raw)   # raises if forged
for ed, a in json.loads(raw)["assets"].items():
    try:
        ok = hashlib.sha256(open(a["name"], "rb").read()).hexdigest() == a["sha256"]
    except FileNotFoundError:
        ok = "not downloaded"
    print(ed, a["name"], ok)
EOF
```

The private signing key is kept offline by the maintainer. It is not in the repository, the archives or the bot.

## Running as a service

`install.sh` creates a system user and runs the bot with systemd hardening (`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome`, write access only to the install folder). Don't run the bot as root; the owner panel warns if it is.
