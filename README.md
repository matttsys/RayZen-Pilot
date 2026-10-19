<p align="center">
  <img src="assets/logo-256.png" width="104" alt="RayZen Pilot">
</p>

<h1 align="center">RayZen Pilot · v5.0.0 Omni</h1>

<p align="center">
  Persian-first Telegram group manager, safe by default<br>
  <a href="README.fa.md">فارسی</a> ·
  <a href="docs/COMMANDS.md">Command reference</a> ·
  <a href="docs/EXAMPLES.fa.md">Example replies</a> ·
  <a href="https://github.com/matttsys/RayZen-Pilot/releases">Download</a>
</p>

<p align="center">
  <img alt="License AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-1A86C8">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1A86C8">
  <img alt="python-telegram-bot 22.8" src="https://img.shields.io/badge/python--telegram--bot-22.8-1A86C8">
</p>

> **Status:** this is the pre-release `5.0.0-alpha.1`. Every automated test passes against an offline Telegram simulator, but live acceptance in real groups has not been run yet. Use it carefully on important groups until then. The stable 4.1.0 release is still on the Releases page.

## What it does

- **Moderation in plain Persian.** An admin replies to someone and writes «سکوت ۲ ساعت» (mute 2 hours), «اخطار» (warn), «بن» (ban). 163 Persian text forms work without `/`. Forwards, quotes and code are never treated as commands, and a regular member typing «بن» does nothing.
- **Safe by default.** Adding or promoting the bot sends nothing to the group and changes no settings. Every filter is off until an admin turns it on.
- **Real locks.** Content locks and the night lock use Telegram's own group permissions, survive restarts and don't erase individual mutes.
- **Assistant.** Called by its name (default «پایلوت»), a mention, or a reply to one of its answers. It answers questions, reports from this group's own data, and performs real admin actions with a preview and confirmation.
- **Statistics and invite links.** Message counts, busy hours, joins and leaves, top members, named invite links and joins per link. Only what the bot observed, with the coverage window stated.
- **Owner panel and signed updates.** `/panel` in the owner's private chat: health, groups, usage, AI, logs, errors, and signed updates with rollback.

All 173 commands are listed in [docs/COMMANDS.md](docs/COMMANDS.md), generated from the code.

## Downloads

Everything is on the [Releases page](https://github.com/matttsys/RayZen-Pilot/releases):

| File | For |
|---|---|
| `rayzen-pilot-v5.0.0-alpha.1-omni-selfhost.zip` | The public edition to run your own bot (host panel, VPS, Docker) |
| `rayzen-pilot-v5.0.0-alpha.1-omni-owner-hosted.zip` | Only the project owner's hosted bot; asks users to join @Rzroot |
| `…-manifest.json`, `…-manifest.json.sig` | Signed manifest used by `/update` |
| `…-SHA256SUMS.txt` | Checksums |

The public edition has no channel-membership requirement, no hidden calls to other servers and no telemetry.

## Quick start

Get a token from @BotFather and your numeric id from @userinfobot.

### Bot host panel (Pterodactyl, Python egg)

1. Create a Python 3.10+ server.
2. Upload the `selfhost` zip in **Files** and **Unarchive** it. This creates a folder named after the version.
3. Open the folder, select everything and **Move** it to `../` so that `app.py` is in the root.
4. Rename `.env.example` to `.env` and fill in `BOT_TOKEN` and `OWNER_ID`.
5. In **Startup**: App py file = `app.py`, Requirements file = `requirements.txt`. Then **Start**.

The console prints `up as @yourbot` when it is online. If you start before step 4, the bot creates `.env` and stops.

### Linux server (VPS with systemd)

```bash
unzip rayzen-pilot-v5.0.0-alpha.1-omni-selfhost.zip
cd rayzen-pilot-v5.0.0-alpha.1-omni-selfhost
sudo bash install.sh
```

The installer checks Python, creates the `rayzen` system user and a virtualenv, asks for the token and starts the service. Then: `rayzen status`, `rayzen logs -f`, `rayzen restart`, `rayzen backup`.

### Docker

```bash
cp .env.example .env    # fill it in
docker compose up -d --build
```

### Essential `.env` values

| Variable | Required | |
|---|---|---|
| `BOT_TOKEN` | yes | Token from @BotFather |
| `OWNER_ID` | yes | Owner's numeric id; the panel and updates are only for this user |
| `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` | no | Any OpenAI-compatible API. Empty = no assistant; everything else works |
| `AI_FALLBACK_MODELS` | no | Comma-separated fallback models |
| `TZ` | no | Default group timezone (`Asia/Tehran`) |
| `LOG_CHANNEL` | no | Where owner notices go; empty = the owner's private chat |
| `SOURCE_URL` | forks only | Where your modified source is published (AGPL) |

## Rights the bot needs

Make the bot an admin and give only what you use. `سلامت گروه` (or `/health`) shows which rights are missing.

| Workflow | Bot right |
|---|---|
| Ban, mute, kick, locks, night lock, captcha | Ban users |
| Delete messages, filters, `/purge`, `/purgeword` | Delete messages |
| Pin | Pin messages |
| Invite links, join requests | Invite users via link |
| Forum topics | Manage topics |
| Welcome, rules, answers, stats | Send messages only |

Admins can only do what Telegram lets them do themselves: muting needs an admin with "Ban users". Group settings need "Change group info".

## Persian commands

Replying to a user's message:

```text
سکوت ۲ ساعت (mute 2h)    اخطار تبلیغ (warn: ads)    بن (ban)    رفع سکوت (unmute)
حذف (delete)             سنجاق (pin)                 اعتماد (trust)   مشخصات (info)
```

Without a reply:

```text
راهنما (help)            راهنما لینک (search help)   آمار (stats)    فعال ترین ها (top)
قفل عکس ۳۰ دقیقه (lock photos 30m)   قفل شبانه از ۱ تا ۸ (night 01-08)   لینک جدید (new link)
تاریخ (Jalali date)      حساب کن ۲+۳*۴ (calc)        یادم بنداز ۱۰ دقیقه چای (remind me in 10 min: tea)
```

Every command also has a slash form (`/mute 2h`, `/night 01:00 08:00`). English UI: `/lang en`. Full list with aliases: [docs/COMMANDS.md](docs/COMMANDS.md).

## Assistant

With `AI_API_KEY` set:

- **Calling it:** a message that starts with the assistant name (default «پایلوت», change with `/ainame`), a mention of the bot, a reply to one of its answers, or `/ai`. In the bot's private chat every ordinary message goes to the assistant.
- **Admin actions:** ban, mute, warn, lock, night lock, welcome, rules, filters, announcements, reminders, polls, pin, word purge and more. The model only proposes; the action runs through the same service as the normal command, with the asking user's rights. High-impact actions always show a preview and a confirm button.
- **Questions about the group (read-only):** stats, top members, rules, admins, and for admins: one member's details, recent actions, joins/leaves, inactive members, settings. Only this group's data, with the time the bot started recording.
- **Text tools:** `خلاصه کن` (summarize), `ترجمه کن` (translate), `غلط گیری` (proofread), `توضیح بده` (explain) on a replied message.
- **Memory:** the last six turns per person, for up to 30 minutes, are given to the model; they are deleted after a day. `فراموش کن` clears them immediately.
- **Limits:** per-user, per-group and daily caps (configurable in `.env`). Provider and model names are never shown; the UI always says `Model hidden`.

Group message text is not stored or sent to the assistant, except the message you address to it or reply to.

## Statistics and invite links

- Stats are **counts only**: messages and media per member per day, messages per hour, the joins and leaves the bot observed. No message text. `/statsmode off` turns it off.
- The bot only sees what happens while it is in the group. Every report shows when recording started and never claims to cover "all members".
- `/newlink` creates a named link with expiry or a member limit; `/linkstats` shows joins per link. Joins through links the bot created are counted exactly; Telegram gives the bot only a truncated form of other admins' links. The bot must be an admin to receive join/leave updates.
- «قفل لینک» (lock links) only disables link previews through Telegram permissions; turn on the link filter (`/antilink`) to delete messages with links.

## Owner panel

`/panel` (or «پنل») is only for `OWNER_ID` and only in the bot's private chat. Every button re-checks the owner.

- **Home:** version, edition, uptime, database, scheduler, log queue, assistant state, command latency
- **Groups:** active and left groups, last activity, leave a group (confirmed)
- **Users and usage:** observed users and the most used commands
- **AI:** successes and failures, error kinds, latency, tokens (when the provider reports them) and cost (only if you set prices in `.env`)
- **Logs and errors:** broken routes and how to repair them
- **Updates:** check, install, roll back, auto-update

The panel never shows tokens, keys or message text.

## Signed updates

- `/update` or the panel's Updates view fetches the latest **stable** release of the official repository, verifies the manifest's Ed25519 signature against the key pinned in the code, checks the zip's size and SHA-256 against the manifest, refuses older versions and the wrong edition, then asks before installing.
- Code and database are backed up first. `.env` and `data/` are never touched. If the new version exits twice before it is up, the previous code is restored automatically; "Roll back" in the panel does it on request.
- Nothing is installed with pip from chat. If a release needs new dependencies, the owner gets the exact command.
- **Auto-update** is off until the owner turns it on, and only runs inside the chosen window (default 04:00–05:00).
- **Pre-releases** (like this alpha) are not fetched from GitHub by `/update`. Send the zip, manifest and signature to the bot's private chat and run `/update inbox`; the same checks apply.
- In **Docker** the code folder isn't writable by the bot, so update by rebuilding the image. On a VPS, `sudo rayzen update file.zip` also works.

Signature details and manual verification: [SECURITY.md](SECURITY.md).

## Upgrading from 4.1.0

1. Back up `data/pilot.db` and `.env` (`rayzen backup` on a VPS).
2. Put the new files over the old ones. VPS: `sudo rayzen update rayzen-pilot-v5.0.0-alpha.1-omni-selfhost.zip`. Host panel: stop the server, upload and unarchive the zip, move the folder contents to the root (`.env` and `data/` stay).
3. Start. The database moves from schema 2 to 3 automatically, with a backup in `data/backups/` first.

The schema change is additive; if you need to go back to 4.1.0 code, it still opens the new database. `/owner` now opens the owner panel (`/panel`).

## Privacy

- Stored: id, name and username (with change history), warnings, cases, action history, message counts, joins and leaves.
- Group message text is not stored. Normalized words are kept for 48 hours only, for `/purgeword`.
- Members in the bot's private chat: `/mydata` (export) and `/forgetme` (delete). Admins: `/privacy`, `/exportdata`, `/resetdata`, `/retention`.

## Limitations

- Live Telegram acceptance for this pre-release has not been run yet; test results are from the offline simulator.
- The bot can't read history from before it joined (Bot API limit). Without admin rights and with privacy mode on, it doesn't receive every message.
- Telegram doesn't allow deleting messages older than 48 hours.
- Groups allow about 20 bot messages per minute; the bot keeps its replies short and few.
- Stats and "last seen" only cover what the bot observed.
- One token, one running instance. Stop the other copy first.

## Editions

Both zips are built from the same code. The `owner-hosted` edition adds one file (`pilot/gate.py`) that shows a short request to join @Rzroot, with a join button and "Check again", on private `/start`. Groups, welcomes, tickets and appeals never depend on channel membership. The `selfhost` edition doesn't contain that file, and refuses to load it if someone adds it.

## License and attribution

- Since 5.0.0 the project is licensed under the **GNU AGPL-3.0** ([LICENSE](LICENSE)). 4.0.0 and 4.1.0 were released under MIT and keep that license.
- In plain words: you may copy, modify, run and redistribute it. If you distribute a modified version, or run it as a bot that other people use over Telegram, you must offer those users the source of that version under the same license (section 13), and keep [NOTICE](NOTICE) with the attribution to the original project. This does not cover your users' data or separate programs on your server.
- AGPL doesn't forbid forks. The name and logo policy is separate: [TRADEMARKS.md](TRADEMARKS.md).
- This is not legal advice; the license text is what counts.

Original project: https://github.com/matttsys/RayZen-Pilot · Security: [SECURITY.md](SECURITY.md) · Changes: [CHANGELOG.md](CHANGELOG.md) · Support: @Rzroot
