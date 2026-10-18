<p align="center">
  <img src="../assets/logo-256.png" width="96" alt="RayZen Pilot logo">
</p>

<h1 align="center">RayZen Pilot · host panel build</h1>

<p align="center">
  <a href="https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-host.zip"><b>Download rayzen-pilot-host.zip</b></a> ·
  <a href="../README.md#bot-host-panel">Full guide</a> ·
  <a href="#فارسی">فارسی</a>
</p>

For bot host panels that run a Python server (Pterodactyl Python egg). There's no installer here. The panel runs `app.py` and restarts it.

## Install from the zip

1. Create a server with the **Python** egg, version 3.10 or newer.
2. Download [`rayzen-pilot-host.zip`](https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-host.zip). In **Files**, upload it and choose **Unarchive**. `app.py` must be in the root folder.
3. Rename `.env.example` to `.env`, then fill in `BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `OWNER_ID` (your numeric id, from [@userinfobot](https://t.me/userinfobot)).
4. In **Startup**: *App py file* = `app.py`, *Requirements file* = `requirements.txt`.
5. Press **Start**. The console prints `up as @yourbot` when it's online.

If you press Start before step 3, the bot creates `.env` for you and stops.

## Install with Git

If the egg has a *Git Repo Address* field, set these in **Startup** and reinstall the server from **Settings**:

| Variable | Value |
|---|---|
| Git Repo Address | `https://github.com/matttsys/RayZen-Pilot` |
| Install Branch | `main` |
| User Uploaded Files | `0` |
| App py file | `host/app.py` |
| Requirements file | `host/requirements.txt` |
| Auto Update | `1` to pull the latest code on every start (optional) |

Then open `host/` in **Files**, rename `.env.example` to `.env`, fill it in and press **Start**.

## Update

- **Zip:** stop the server, upload the new zip, unarchive and overwrite. `.env` and `data/` aren't in the zip, so they stay.
- **Git:** with *Auto Update* on, restart the server.

## Files

| Path | |
|---|---|
| `app.py` | Entry point. Loads `.env`, runs the bot and restarts it if it crashes. |
| `pilot/` | The bot |
| `shared/env.py` | `.env` loader |
| `requirements.txt` | python-telegram-bot 22.8 and a few small packages |
| `.env.example` | Settings template. Copy it to `.env`. |
| `INSTALL.txt` | The steps above as plain text |
| `data/` | Created on first run. Holds the SQLite database. |

---

<div dir="rtl">

## فارسی

نسخهٔ مخصوص هاست ربات (egg پایتون در Pterodactyl). نصب‌کننده ندارد؛ پنل خودش ‎`app.py`‎ را اجرا و ری‌استارت می‌کند.

### نصب با زیپ

۱. یک سرور با egg **Python** نسخهٔ 3.10 یا بالاتر بساز.
۲. فایل [‎`rayzen-pilot-host.zip`‎](https://github.com/matttsys/RayZen-Pilot/releases/latest/download/rayzen-pilot-host.zip) را دانلود کن، در **Files** آپلودش کن و **Unarchive** را بزن. ‎`app.py`‎ باید در پوشهٔ اصلی باشد.
۳. اسم ‎`.env.example`‎ را به ‎`.env`‎ تغییر بده و ‎`BOT_TOKEN`‎ (از ‎[@BotFather](https://t.me/BotFather)‎) و ‎`OWNER_ID`‎ (آیدی عددی خودت، از ‎[@userinfobot](https://t.me/userinfobot)‎) را پر کن.
۴. در **Startup**: ‏*App py file* = ‎`app.py`‎ و *Requirements file* = ‎`requirements.txt`‎
۵. **Start** را بزن. وقتی ربات آنلاین شود، در کنسول ‎`up as @yourbot`‎ چاپ می‌شود.

### نصب با Git

اگر egg فیلد *Git Repo Address* دارد، مقدارهای جدول بالا را در **Startup** بگذار و از **Settings** سرور را Reinstall کن. بعد در پوشهٔ ‎`host/`‎ فایل ‎`.env`‎ را بساز و پر کن.

### به‌روزرسانی

- **زیپ:** سرور را Stop کن، زیپ جدید را آپلود و با بازنویسی Unarchive کن. ‎`.env`‎ و ‎`data/`‎ دست نمی‌خورند.
- **Git:** اگر *Auto Update* روشن است، فقط ری‌استارت کن.

راهنمای کامل: [README.fa.md](../README.fa.md#fa-install)

</div>
