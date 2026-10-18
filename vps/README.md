<p align="center">
  <img src="../assets/logo-256.png" width="96" alt="RayZen Pilot logo">
</p>

<h1 align="center">RayZen Pilot · VPS build</h1>

<p align="center">
  <a href="https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-vps.zip"><b>Download rayzen-pilot-vps.zip</b></a> ·
  <a href="../README.md#vps">Full guide</a> ·
  <a href="#فارسی">فارسی</a>
</p>

For Linux servers with systemd, and for Docker. Same bot as [`host/`](../host), plus an installer, a systemd service and the `rayzen` command.

## Install

```bash
git clone https://github.com/matttsys/RayZen-Pilot.git
cd RayZen-Pilot/vps
sudo bash install.sh
```

The installer finds or installs Python 3.10+, creates a `rayzen` user, installs to `/opt/rayzen-pilot`, asks for the bot token and owner id, then starts the `rayzen-pilot` service.

Unattended:

```bash
sudo BOT_TOKEN=123456:ABC... OWNER_ID=111222333 AI_API_KEY=sk-... bash install.sh
```

No git on the server? The release zip has the same files:

```bash
curl -LO https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-vps.zip
unzip rayzen-pilot-vps.zip && cd rayzen-pilot && sudo bash install.sh
```

## Manage

| Command | What it does |
|---|---|
| `rayzen status` | Running or stopped, uptime, bot username |
| `rayzen logs -f` | Follow the log |
| `rayzen restart` | Restart the service |
| `rayzen config` | Ask the setup questions again |
| `rayzen backup` | Database and `.env` to `/opt/rayzen-pilot/backups` |
| `rayzen update <zip or folder>` | Install a new version, keep `.env` and data |
| `rayzen uninstall` | Remove the service |

## Update

```bash
cd RayZen-Pilot
git pull
sudo rayzen update vps
```

## Docker

```bash
cd RayZen-Pilot/vps
python3 configure.py        # or: cp .env.example .env && nano .env
docker compose up -d
```

## Files

| Path | |
|---|---|
| `install.sh` | systemd installer |
| `configure.py` | Interactive `.env` setup. Checks the token with Telegram. |
| `deploy/` | systemd unit and the `rayzen` command |
| `Dockerfile`, `docker-compose.yml` | Docker setup. Data lives in the `pilot-data` volume. |
| `app.py` | Entry point and supervisor |
| `pilot/`, `shared/` | The bot |
| `.env.example` | Settings template |

---

<div dir="rtl">

## فارسی

نسخهٔ مخصوص سرور لینوکس با systemd و داکر. ربات همان ربات پوشهٔ ‎`host/`‎ است، به‌اضافهٔ نصب‌کننده، سرویس systemd و دستور ‎`rayzen`‎.

### نصب

</div>

```bash
git clone https://github.com/matttsys/RayZen-Pilot.git
cd RayZen-Pilot/vps
sudo bash install.sh
```

<div dir="rtl">

نصب‌کننده پایتون 3.10 به بالا را پیدا یا نصب می‌کند، کاربر ‎`rayzen`‎ را می‌سازد، ربات را در ‎`/opt/rayzen-pilot`‎ نصب می‌کند، توکن و آیدی مالک را می‌پرسد و سرویس ‎`rayzen-pilot`‎ را اجرا می‌کند.

اگر روی سرور git نداری، زیپ ‎[rayzen-pilot-vps.zip](https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-vps.zip)‎ همین فایل‌ها را دارد.

### به‌روزرسانی

</div>

```bash
cd RayZen-Pilot
git pull
sudo rayzen update vps
```

<div dir="rtl">

راهنمای کامل: [README.fa.md](../README.fa.md#fa-install)

</div>
