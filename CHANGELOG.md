# Changelog

## 5.0.0-alpha.2 "Omni" (pre-release)

Hotfix for the signed updater, and a new README.

- **Fixed: `/update` failed with "download failed or incomplete" (`9617/2270 bytes`).** GitHub's API sends JSON gzip-compressed. aiohttp decompresses the body, but `Content-Length` still counts the compressed bytes, so the exact-length check rejected every API response. The updater now asks for uncompressed bytes (`Accept-Encoding: identity`) and applies the exact-length check only to uncompressed responses. The size caps still apply to the decompressed bytes, which also stops a compression bomb. Nothing else about verification changed: the Ed25519 signature, pinned key, manifest size, SHA-256, edition, version and archive checks are the same.
- Tested against the live `v5.0.0-alpha.1` release on GitHub: the updater downloaded it, verified the signature and hash, and staged it. Five new acceptance checks cover gzip responses, the identity request header, the compression-bomb cap and the `Content-Length` cap.
- **Fixed: a false "release is not signed" on `/update`.** The latest *stable* release on GitHub is still 4.1.0, which predates signed releases, so every check on v5 reported it as unsigned (and auto-update would have warned the owner daily). An unsigned release that is not newer than the running version now reads as "nothing newer"; an unsigned release that claims to be newer is still refused. Two new acceptance checks, and a live check against GitHub.
- **Fixed: link names swallowed by a duration.** `/newlink autumn 7d` and «لینک جدید پاییز ۷ روز» now keep the name and the 7-day expiry. A two-word duration («۲ ساعت», «دو ساعت») must start with a number or a number word.
- **Fixed: Persian comma in English replies.** Lists in stats, welcomes, lock status, block lists, admin rights and the group dashboard use `, ` in English and `، ` in Persian.
- README (English and Persian) rewritten in the 4.x layout, updated for v5, with new graphics: banner, counts, features, real bot replies, install options, signed-update steps and architecture. Download links and file names in the docs now always match the release.
- `/update` still installs **stable** releases only. Install pre-releases from files: send the zip, manifest and `.sig` to the bot in private chat, then run `/update inbox`. On `alpha.1`, whose `/update` has the bug above, install this release that way or with the zip.
- Database unchanged (schema v3). No new settings.

## 5.0.0-alpha.1 "Omni" (pre-release)

First pre-release of v5. All automated suites pass against the offline Telegram simulator (see the release notes for the exact counts). Live acceptance in real Telegram groups has **not** been run yet; 5.0.0 is tagged after it is. The 301+ new-capability target of the v5 plan is **not** met by this alpha; the counts below are what is actually registered in the code.

**Counts (from the code, compared with 4.1.0)**

| | 4.1.0 | 5.0.0-alpha.1 |
|---|---|---|
| Canonical commands | 114 | 173 |
| Command names incl. aliases | 147 | 245 |
| Persian text forms without `/` | 27 | 163 |
| Assistant action tools | 28 | 28 |
| Assistant read-only lookups | 0 | 13 |
| Group settings | 53 | 68 |

**Changed defaults and fixes**

- Adding or promoting the bot no longer posts an onboarding message in the group, and still changes nothing.
- Persian text commands are the main path: longest-phrase matching on normalized text (Arabic/Persian letters, digits, half-spaces, diacritics), only at the start of a message, never for forwards, quotes or code, and only for people allowed to run them. The original argument text is kept.
- Persian arguments: «قفل شبانه از ۱ تا ۸», «یادم بنداز ۱۰ دقیقه چای», «۲ ساعت دیگه», «هر ۶ ساعت».
- Commands addressed to another bot (`/ban@otherbot`) are ignored.
- `/help` is generated from the command registry: categories, pages, search, and only the commands the reader can use.
- `/forgetme`, `/mydata` and `/privacy` cover the new data (activity counters, joins/leaves, name history, AFK, assistant memory).
- `/owner` now opens the owner panel.

**New**

- Members: `/seen`, `/checkmember`, `/namehistory`, `/banlist`, `/mutelist`, `/membercount`, `/adminrights`, `/banchannel`, `/unbanchannel`, `/purgeuser`, `/trust`, `/untrust`, `/trusted`.
- Protection (all off by default): `/antitabchi` (links/forwards from new members), `/antimention`, `/antidupe`, `/probation`, `/blockexport`, `/blockclear`, `/botclean` (auto-delete short bot replies).
- Statistics (counts only, observed only): `/stats`, `/mystats`, `/userstats`, `/top`, `/hours`, `/joins`, `/newmembers`, `/leavers`, `/inactive`, `/topadders`, `/cohort`, `/statsreport`, `/statsmode`.
- Invite links: `/newlink`, `/requestlink`, `/links`, `/linkstats`, `/revokelink`, `/mylink`, `/memberlinks`, `/topinviters`.
- Assistant: answers without `/ai` (assistant name, mention, reply), read-only lookups of this group's data, short per-user memory (30 min context, deleted after a day), `/summarize`, `/translate`, `/fixtext`, `/explain`, `/forgetchat`, `/ainame`. Provider token usage, latency and attempts are recorded when the provider reports them; cost is shown only if prices are configured.
- Utilities: `/jalali`, `/gregorian`, `/time`, `/calc`, `/countdown`, `/daysbetween`, `/timer`, `/choose`, `/random`, `/convert`, `/afk`.
- Owner panel `/panel`: health, groups (leave with confirmation), observed users, command usage, AI, logs, errors, updates. Owner-only on every button.
- Signed updates `/update`: Ed25519-signed manifest, pinned key, size and SHA-256 check, edition and version checks, archive safety checks, backup, crash-guard rollback, manual rollback, opt-in auto-update window, offline install from files sent to the bot.
- Two editions from one build: `selfhost` (public) and `owner-hosted` (adds the [@Rzroot](https://t.me/Rzroot) request on private `/start`).
- Source link on private `/start` (AGPL section 13); forks set `SOURCE_URL`.

**Database.** Schema v3 adds counters, member events, invite links, trusted users, AFK, usage, assistant memory and scheduled bot-message cleanup, and new AI accounting columns. It upgrades automatically on first start, with a backup in `data/backups/` first. 4.1.0 code still opens a v3 database.

**License.** v5 is released under AGPL-3.0 with a NOTICE file. 4.0.0 and 4.1.0 remain MIT.

## 4.1.0

**New: `/purgeword`.** Deletes every message from the last 48 hours that contains a word.

- `/purgeword متین` matches the word and its attached forms: `متینو`, `متینه`, `متین‌جان`, `متینجان`, `متین جان`. Arabic/Persian letter variants (`ي`/`ی`, `ك`/`ک`), diacritics, tatweel and half-spaces are normalized first. Words that only contain it in the middle (`ثمتین`) don't match.
- Several spellings in one run, comma-separated: `/purgeword متین، matin`. Several words without commas must all be in the same message.
- Always asks first: "N messages with «word» from K people in the last 48h. Delete them?" with Yes / Cancel. Only the admin who asked can confirm.
- Needs the **Delete messages** admin right, and the bot needs it too. It deletes everyone's matching messages, admins included.
- Deletes in batches of 100 and reports truthfully: deleted, couldn't delete, or more left.
- Persian text command: «حذف کلمه متین» or «پاکسازی کلمه متین». The AI assistant can do it too, and it always shows the count and asks for confirmation.
- `/purgeword on|off` turns the index on or off for a group. `off` also clears it.

**Privacy change.** Telegram bots can't search a chat, so the bot now keeps a 48-hour word index for `/purgeword`. It stores normalized words with the chat, message id, sender id and time. It doesn't keep the message text. Rows older than 48 hours are deleted every 10 minutes. Commands, messages to the bot and messages removed by filters aren't indexed. Edits update the index. `/forgetme`, `/resetdata` and `/purgeword off` remove it.

**Database.** Schema v2 adds the `rz_msg_index` table. It upgrades automatically on first start, and a backup is written to `data/backups/` before the upgrade.

## 4.0.0

First release of the clean rebuild: one router and service layer, safe defaults (every filter off), real Telegram permission locks with restore, verified log routes, history with undo, cases and appeals, scheduling, member desk, AI assistant with preview and confirm, and deploy targets for bot host panels, VPS (systemd) and Docker.
