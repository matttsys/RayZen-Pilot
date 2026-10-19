<h1 align="center">RayZen Pilot · VPS and Docker</h1>

<p align="center"><a href="../README.md">Full guide</a> · <a href="../README.fa.md">راهنمای کامل</a> · <a href="#فارسی">فارسی</a></p>

For Linux servers with systemd, and for Docker. Same bot as [`host/`](../host) plus an installer, a hardened systemd unit and the `rayzen` command.

## Install

```bash
curl -LO https://github.com/matttsys/RayZen-Pilot/releases/download/v5.0.0-alpha.2/rayzen-pilot-v5.0.0-alpha.2-omni-selfhost.zip
unzip rayzen-pilot-v5.0.0-alpha.2-omni-selfhost.zip
cd rayzen-pilot-v5.0.0-alpha.2-omni-selfhost
sudo bash install.sh
```

The installer finds Python 3.10+, creates the `rayzen` user, installs to `/opt/rayzen-pilot` with its own virtualenv, asks for the token and owner id, and starts the `rayzen-pilot` service. Or from this folder after `git clone https://github.com/matttsys/RayZen-Pilot.git && cd RayZen-Pilot/vps`. Unattended:

```bash
sudo BOT_TOKEN=... OWNER_ID=... AI_API_KEY=... bash install.sh
```

## Manage

| Command | |
|---|---|
| `rayzen status` | running or stopped, uptime, bot username |
| `rayzen logs -f` | follow the log |
| `rayzen restart` | restart the service |
| `rayzen config` | ask the setup questions again |
| `rayzen backup` | database and `.env` to `/opt/rayzen-pilot/backups` |
| `rayzen update <zip or folder>` | install a release from the shell, keep `.env` and data, run pip |
| `rayzen uninstall` | remove the service |

`rayzen update` installs whatever archive you give it. Verify downloads first (see [SECURITY.md](../SECURITY.md)), or use `/update` in Telegram, which checks the signature itself.

## Docker

```bash
cp .env.example .env    # or: python3 configure.py
docker compose up -d --build
```

Data lives in the `pilot-data` volume. The code inside the image isn't writable by the bot, so `/update` can verify a release but not install it; rebuild the image instead.

---

<div dir="rtl">

## فارسی

برای سرور لینوکس با systemd و داکر. نصب: زیپ را باز کنید، وارد پوشه شوید و ⁧`sudo bash install.sh`⁩ را اجرا کنید. نصب‌کننده کاربر `rayzen` و virtualenv می‌سازد، توکن را می‌پرسد و سرویس را روشن می‌کند.

دستورها: `rayzen status`، `rayzen logs -f`، `rayzen restart`، `rayzen backup`، `rayzen update فایل.zip`.

⁧`rayzen update`⁩ هر فایلی بدهید نصب می‌کند؛ اول امضا را بررسی کنید (⁧[SECURITY.md](../SECURITY.md)⁩) یا از `/update` در تلگرام استفاده کنید که خودش امضا را چک می‌کند. در داکر برای آپدیت ایمیج را دوباره بسازید.

</div>
