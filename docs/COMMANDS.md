# Command reference

Generated from the code: **173** canonical commands, **245** command names including aliases, and **163** Persian text forms that work without `/`.

- Persian text forms run only when the message starts with them and the sender is allowed; forwards, quotes and code are never treated as commands.
- *admin* = a group admin with the needed right. *owner* = `OWNER_ID` from `.env`, in the bot's private chat only.
- `/help word` searches inside the bot.

## 🛡 Moderation

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/admins` | «ادمین ها» | everyone | List admins |
| `/kickme` | — | everyone | Leave the group yourself |
| `/warns` | «اخطارها»، «اخطارهای من» | everyone | Warnings of a user or yourself |
| `/ban` | «بن»، «مسدود» | admin | Ban by reply, numeric id or username; optional duration |
| `/dban` | — | admin | Delete the message and ban its sender |
| `/del` | «حذف» | admin | Delete the replied message |
| `/demote` | «عزل»، «برکناری» | admin | Demote an admin the bot promoted |
| `/info` | «این کیه»، «مشخصات»، «اطلاعات کاربر» | admin | User details and status here |
| `/kick` | «اخراج»، «کیک» | admin | Kick; the user can rejoin |
| `/mute` | «سکوت»، «سایلنت»، «میوت» | admin | Mute, optional duration |
| `/pin` | «سنجاق»، «پین» | admin | Pin the replied message |
| `/promote` | «ارتقا»، «ادمین کن» | admin | Promote with limited rights |
| `/purge` | «پاکسازی» | admin | Delete from the replied message to now |
| `/purgeword` `/purgewords` | «حذف کلمه»، «پاکسازی کلمه» | admin | Delete last-48h messages containing a word (previewed) |
| `/resetwarns` | «پاک کردن اخطارها»، «ریست اخطار» | admin | Clear all of a user's warnings |
| `/rmwarn` `/unwarn` | «حذف اخطار» | admin | Remove the latest warning |
| `/tban` `/tempban` | «بن موقت» | admin | Temporary ban |
| `/tmute` `/tempmute` | — | admin | Temporary mute |
| `/trust` `/approve` | «اعتماد»، «معتمد» | admin | Trust a member; filters skip them |
| `/trusted` `/approved` | «لیست اعتماد»، «لیست معتمدها» | admin | Trusted members |
| `/unban` | «رفع بن»، «آنبن»، «حذف بن» | admin | Lift a ban without removing current members |
| `/unmute` | «رفع سکوت»، «رفع سایلنت»، «آنمیوت» | admin | Unmute |
| `/unpin` | «برداشتن سنجاق»، «حذف سنجاق» | admin | Unpin |
| `/unpinall` | — | admin | Unpin everything (confirmed) |
| `/untrust` `/unapprove` | «رفع اعتماد»، «حذف معتمد» | admin | Remove from trusted |
| `/warn` | «اخطار»، «وارن» | admin | Warn; the configured penalty runs at the limit |

## 👥 Members

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/membercount` `/members` | «تعداد اعضا» | everyone | Member count from Telegram plus observed members |
| `/seen` `/lastseen` | «آخرین بازدید» | everyone | When the bot last observed this user |
| `/adminrights` `/adminlist` | «دسترسی ادمین ها» | admin | Each admin's rights |
| `/banchannel` | «بن کانال» | admin | Ban the channel identity of a replied message |
| `/banlist` `/bans` | «لیست بن» | admin | Bans recorded by the bot |
| `/checkmember` `/ismember` | «بررسی عضویت» | admin | Check one user's current membership |
| `/mutelist` `/mutes` | «لیست سکوت» | admin | Active mutes recorded by the bot |
| `/namehistory` `/names` | «تاریخچه نام» | admin | Earlier names the bot observed |
| `/purgeuser` `/delall` | «پاکسازی کاربر»، «حذف پیام های کاربر» | admin | Delete a user's recent messages (confirmed) |
| `/unbanchannel` | «رفع بن کانال» | admin | Unban a channel identity by id |

## 🔒 Locks

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/lockstatus` `/locks` | «وضعیت قفل» | everyone | Active locks and end times |
| `/slowmode` | — | everyone | How to set Telegram's own slow mode |
| `/cooldown` | — | admin | Bot-managed message cooldown (opt-in) |
| `/lock` | «قفل» | admin | Lock a content type; optional duration |
| `/lockdown` | «قفل گروه» | admin | Lock the whole group (confirmed) |
| `/night` | «قفل شبانه»، «حالت شب» | admin | Night lock using real group permissions |
| `/unlock` | «بازکردن قفل»، «باز کردن قفل»، «بازکردن گروه»، «باز کردن گروه» | admin | Unlock without lifting individual mutes |

## 🚫 Filters

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/blocklist` | «کلمات ممنوع»، «لیست کلمات ممنوع» | everyone | Blocked words |
| `/addblock` `/addblocklist` | «فیلتر کلمه»، «افزودن کلمه ممنوع» | admin | Add blocked words (comma separated) |
| `/antichannel` | «ضد کانال» | admin | Delete posts from outside channels; linked channel and anonymous admins pass |
| `/antidupe` `/antirepeat` | «ضد تکرار» | admin | Delete repeated messages |
| `/antiforward` | «ضد فوروارد» | admin | Delete forwards |
| `/antilink` | «ضد لینک» | admin | Delete links |
| `/antimention` | «ضد منشن» | admin | Delete mass-mention messages |
| `/antiraid` | «ضد رید» | admin | Auto-lock on a sudden join wave |
| `/antitabchi` `/antispambot` | «ضد تبچی» | admin | Limit links and forwards from newcomers |
| `/block` `/filteron` | — | admin | Turn on a content filter |
| `/blockclear` | «پاک کردن کلمات ممنوع» | admin | Clear all blocked words (confirmed) |
| `/blockexport` | «خروجی کلمات ممنوع» | admin | Export blocked words to private chat |
| `/captcha` | «کپچا» | admin | Join captcha for newcomers |
| `/flood` `/antiflood` | «ضد فلود» | admin | Flood control with set count and window |
| `/pause` | «توقف فیلترها» | admin | Pause all filters |
| `/probation` | «دوره آزمایشی» | admin | Newcomer probation: no links or media |
| `/resume` | «ادامه فیلترها» | admin | Resume filters |
| `/rmblock` `/rmblocklist` | «حذف کلمه ممنوع» | admin | Remove a blocked word |
| `/safereset` | — | admin | Turn every filter off, with preview |
| `/unblock` `/filteroff` | — | admin | Turn off a content filter |
| `/why` | «چرا پاک شد» | admin | Why recent messages were removed |

## 👋 Welcome

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/cleanservice` | — | admin | Delete Telegram join/leave service messages |
| `/cleanwelcome` | — | admin | Delete the previous welcome on the next join |
| `/goodbye` | «خداحافظی» | admin | Goodbye message on/off |
| `/resetwelcome` | — | admin | Reset the welcome |
| `/setgoodbye` | — | admin | Goodbye text |
| `/setwelcome` | «تنظیم خوشامد» | admin | Welcome text or media (reply to media) |
| `/welcome` | «خوشامد»، «پیش نمایش خوشامد → /welcome preview»، «روشن کردن خوشامد → /welcome on»، «خاموش کردن خوشامد → /welcome off» | admin | Welcome: on, off, preview |

## 📌 Rules & answers

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/faq` `/answers` `/notes` | «پاسخ ها» | everyone | List saved answers |
| `/get` | — | everyone | Show a saved answer |
| `/rules` | «قوانین» | everyone | Show the rules |
| `/autoanswer` | — | admin | Keyword auto-replies on/off |
| `/clear` `/rmanswer` | — | admin | Delete a saved answer |
| `/clearrules` | «حذف قوانین» | admin | Remove the rules |
| `/keyword` `/filter` | — | admin | Auto-reply to a keyword |
| `/save` `/addanswer` | «ذخیره» | admin | Save a named answer; call it with #name |
| `/setrules` | «تنظیم قوانین» | admin | Set the rules |
| `/stop` | — | admin | Remove a keyword auto-reply |

## 📊 Statistics

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/hours` `/busyhours` | «ساعت های شلوغ» | everyone | Busiest hours |
| `/mystats` | «آمار من» | everyone | Your own stats here |
| `/stats` `/activity` | «آمار»، «آمار گروه» | everyone | Messages and members since tracking began |
| `/top` `/topactive` | «فعال ترین ها»، «برترین ها» | everyone | Most active members for a period |
| `/cohort` `/retentionstats` | «ماندگاری اعضا» | admin | How many newcomers stayed |
| `/inactive` | «غیرفعال ها» | admin | Observed members silent for a while |
| `/joins` `/growth` | «ورود و خروج» | admin | Daily joins and leaves |
| `/leavers` | — | admin | Recent leavers |
| `/newmembers` | «اعضای جدید» | admin | Recent newcomers |
| `/statsmode` | — | admin | Stats tracking on/off (counts only) |
| `/statsreport` | «گزارش آمار» | admin | Automatic daily or weekly stats report |
| `/topadders` | «اضافه کننده ها» | admin | Members who added others (observed only) |
| `/userstats` | «آمار کاربر» | admin | One member's stats |

## 🔗 Invite links

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/mylink` `/invitelink` | «لینک دعوت من»، «لینک من» | everyone | Your personal invite link |
| `/topinviters` `/inviters` | «دعوت کننده ها» | everyone | Most joins via personal links |
| `/links` `/invitelinks` | «لینک ها» | admin | Links created by the bot |
| `/linkstats` | «آمار لینک ها» | admin | Joins per link |
| `/memberlinks` | «لینک شخصی اعضا» | admin | Allow personal links for members |
| `/newlink` `/createlink` | «لینک جدید» | admin | Create a named invite link with expiry or limit |
| `/requestlink` | «لینک درخواستی» | admin | Invite link that needs approval |
| `/revokelink` | «باطل کردن لینک» | admin | Revoke a link |

## ⏰ Scheduling

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/events` | — | everyone | Upcoming events |
| `/eventsummary` | — | everyone | Event attendance summary |
| `/poll` | «نظرسنجی» | everyone | Quick poll |
| `/remind` `/reminder` | «یادآوری»، «یادم بنداز» | everyone | Reminder for you or the group |
| `/reminders` | — | everyone | Your active reminders |
| `/announce` `/schedule` | «اطلاعیه» | admin | Scheduled or repeating announcement |
| `/announcements` `/schedules` | — | admin | Manage announcements |
| `/event` | «رویداد» | admin | Event with RSVPs |
| `/eventcancel` | — | admin | Cancel an event |
| `/pollclose` `/closepoll` | — | admin | Close a poll |

## 📨 Desk

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/contact` `/ticket` `/admin_contact` | «تماس با ادمین» | everyone | Private message to admins (ticket) |
| `/mycases` `/appeal` | — | everyone · private | Your cases and appeals |
| `/report` | «گزارش»، «ریپورت» | everyone | Report a message to admins |
| `/treply` | — | everyone | Reply to a ticket |
| `/appeals` | — | admin | Open appeals |
| `/joinauto` | — | admin | Auto-approve after an answer |
| `/joindesk` | — | admin | Admins review join requests |
| `/joinquestion` | — | admin | Question before join approval |
| `/requests` | — | admin | Pending join requests |
| `/tickets` | — | admin | Open tickets |
| `/topic` `/topics` | — | admin | Forum topics: create, close, reopen |

## 📝 Logs

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/linklog` | — | everyone | Register the log code at the destination |
| `/logstatus` | — | admin | Log delivery status |
| `/logtest` `/logrepair` | — | admin | Test and repair log delivery |
| `/setlog` | — | admin | Set a log group/channel with a code |
| `/unsetlog` | — | admin | Turn logs off |

## 💬 Assistant

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/ai` `/ask` | — | everyone | Ask the assistant (name or reply works too) |
| `/explain` | «توضیح بده» | everyone | Explain simply |
| `/fixtext` `/proofread` | «ویرایش متن»، «غلط گیری» | everyone | Proofread a text |
| `/forgetchat` `/newchat` | «فراموش کن» | everyone | Start a fresh conversation |
| `/summarize` `/tldr` | «خلاصه کن» | everyone | Summarize the replied message |
| `/translate` `/tr` | «ترجمه کن»، «ترجمه» | everyone | Translate the replied message |
| `/ainame` | «اسم دستیار» | admin | Name that addresses the assistant |

## 🧮 Utilities

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/calc` `/calculate` | «حساب کن»، «محاسبه» | everyone | Calculator |
| `/choose` `/pick` | «انتخاب کن» | everyone | Pick one of the options |
| `/convert` `/unit` | «تبدیل واحد» | everyone | Unit conversion |
| `/countdown` `/daysuntil` | «روز شمار» | everyone | Days left until a date |
| `/daysbetween` `/datediff` | «فاصله تاریخ» | everyone | Days between two dates |
| `/gregorian` `/miladi` | «تاریخ میلادی» | everyone | Convert Jalali to Gregorian |
| `/jalali` `/date` `/today` | «تاریخ»، «امروز» | everyone | Today in Jalali, or convert to Jalali |
| `/random` `/roll` | «عدد تصادفی» | everyone | Random number |
| `/time` `/now` | «ساعت» | everyone | Current time in the group zone or a city |
| `/timer` | «تایمر» | everyone | Short timer |

## ⚙️ Settings

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/afk` `/away` | «حالت غیبت» | everyone | Away mode; people who mention you are told |
| `/help` | «راهنما»، «دستورات» | everyone | This help; add a word to search |
| `/id` | «آیدی»، «ایدی» | everyone | Numeric ids of chat, you and the replied message |
| `/start` | — | everyone | Start |
| `/backup` `/export` | — | admin | Settings backup to private chat |
| `/botclean` `/autodelete` | «پاک شدن پیام ربات» | admin | Auto-delete short bot replies |
| `/case` | — | admin | Case details |
| `/cases` | — | admin | Recent cases |
| `/clone` `/copyconfig` | — | admin | Copy settings from another group you admin |
| `/digest` | «گزارش روزانه» | admin | Daily moderation digest |
| `/health` `/dashboard` | «سلامت گروه» | admin | Group status and bot rights |
| `/history` | «تاریخچه» | admin | Action history |
| `/lang` `/language` | «زبان» | admin | Bot language |
| `/legacy` | — | admin | Earlier-version settings to review |
| `/restore` `/import` | — | admin | Restore from a backup (previewed) |
| `/set` | — | admin | Change one setting directly |
| `/settings` `/config` | «تنظیمات» | admin | Group settings menu |
| `/timezone` `/tz` | «منطقه زمانی» | admin | Group timezone |
| `/undo` | «برگرداندن» | admin | Undo an action |
| `/watchdog` | — | admin | Alert on permission changes |

## 🔐 Privacy

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/forgetme` | — | everyone · private | Delete your data (confirmed) |
| `/mydata` | — | everyone · private | Your data |
| `/privacy` | «حریم خصوصی» | everyone | What is stored and for how long |
| `/exportdata` | — | admin | Export group data |
| `/resetdata` | — | admin | Reset group data (previewed) |
| `/retention` | — | admin | Record retention period |

## 🛠 Bot owner

| Command | Persian | Who | What it does |
|---|---|---|---|
| `/panel` `/owner` | «پنل»، «پنل مدیریت» | owner · private | Owner console: health, groups, usage, AI, updates |
| `/update` `/upgrade` | «آپدیت»، «بروزرسانی» | owner · private | Check and install a signed release |

