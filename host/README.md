<h1 align="center">RayZen Pilot · host panel</h1>

<p align="center"><a href="../README.md">Full guide</a> · <a href="../README.fa.md">راهنمای کامل</a> · <a href="#فارسی">فارسی</a></p>

The bot for host panels that run a Python app (Pterodactyl Python egg). No installer: the panel runs `app.py`, and `app.py` restarts the bot if it stops. Same code as [`vps/`](../vps), generated from one source by the release build.

## Install from the release zip

1. Create a Python 3.10+ server.
2. Upload [`rayzen-pilot-v5.0.0-alpha.2-omni-selfhost.zip`](https://github.com/matttsys/RayZen-Pilot/releases/download/v5.0.0-alpha.2/rayzen-pilot-v5.0.0-alpha.2-omni-selfhost.zip) in **Files** and **Unarchive** it.
3. Open the new folder, select everything and **Move** it to `../`, so `app.py` is in the root.
4. Rename `.env.example` to `.env`, fill in `BOT_TOKEN` and `OWNER_ID`.
5. **Startup**: App py file = `app.py`, Requirements file = `requirements.txt`. Press **Start**.

## Install with Git

| Startup variable | Value |
|---|---|
| Git Repo Address | `https://github.com/matttsys/RayZen-Pilot` |
| Install Branch | `main` (currently the v5 pre-release) |
| App py file | `host/app.py` |
| Requirements file | `host/requirements.txt` |

Keep *Auto Update* off if you don't want every restart to pull new code; use `/update` from Telegram instead.

## Update

- From Telegram: `/update` (owner only, signed releases). For a pre-release, send its zip, manifest and `.sig` to the bot in private and run `/update inbox`.
- By hand: stop the server, upload the new zip, unarchive, move the folder contents to the root. `.env` and `data/` aren't in the zip, so they stay.

## Files

| Path | |
|---|---|
| `app.py` | Entry point. Loads `.env`, runs the bot, restarts it, rolls back a failed update |
| `pilot/` | The bot |
| `shared/` | `.env` loader and the update crash guard |
| `requirements.txt` | python-telegram-bot 22.8, aiohttp, cryptography, tzdata |
| `.env.example` | Settings template |
| `data/` | Created on first run: SQLite database, backups, update state |

---

<div dir="rtl">

## فارسی

نسخهٔ هاست ربات (egg پایتون در Pterodactyl). پنل ⁧`app.py`⁩ را اجرا می‌کند و ⁧`app.py`⁩ اگر ربات متوقف شود دوباره روشنش می‌کند.

۱. سرور Python نسخهٔ 3.10 یا جدیدتر بساز.
۲. زیپ `selfhost` را در **Files** آپلود و **Unarchive** کن.
۳. وارد پوشهٔ جدید شو، همه را انتخاب کن و با **Move** به ⁧`../`⁩ ببر تا ⁧`app.py`⁩ در ریشه باشد.
۴. ⁧`.env.example`⁩ را به ⁧`.env`⁩ تغییر نام بده و `BOT_TOKEN` و `OWNER_ID` را پر کن.
۵. در **Startup**: ‏App py file = `app.py` و Requirements file = `requirements.txt`. بعد **Start**.

به‌روزرسانی از تلگرام با `/update` (فقط مالک، فقط انتشار امضاشده). برای پیش‌انتشار: زیپ، مانیفست و ⁧`.sig`⁩ را در پیوی به ربات بفرست و `/update inbox` را بزن. دستی: سرور را Stop کن، زیپ جدید را Unarchive کن و محتوای پوشه را به ریشه ببر؛ ⁧`.env`⁩ و ⁧`data/`⁩ می‌مانند.

</div>
