# Changelog

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
