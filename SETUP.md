# RTB Apprenticeship Tender Monitor — Setup Guide

This tool checks **Find a Tender** and **Contracts Finder** every weekday
morning and emails `appsbidsteam@raisethebar.co.uk` a digest of new
procurement notices that match RTB's apprenticeship criteria. It runs free
on GitHub Actions — no server, no software to install on your PC.

You do this **once**. After that it runs itself.

---

## What's in this folder

| File | What it does |
|------|--------------|
| `tender_monitor.py` | The script that does the searching and emailing |
| `.github/workflows/tender-alerts.yml` | Tells GitHub to run it each morning |
| `seen.json` | Remembers which notices you've already been told about |
| `requirements.txt` | Lists the one library the script needs |
| `SETUP.md` | This guide |

---

## Step 1 — Create the Gmail sending account

The script needs an email account to send *from*. Use a dedicated Gmail so it
never touches your personal mail.

1. Create a new Gmail account, e.g. `rtb.tenderbot@gmail.com`.
2. Turn on **2-Step Verification**: Google Account → Security → 2-Step
   Verification → follow the prompts. (App passwords won't exist until this
   is on.)
3. Create an **App Password**: Google Account → Security → App passwords
   (or go to https://myaccount.google.com/apppasswords). Name it "Tender
   Bot". Google gives you a **16-character code** like `abcd efgh ijkl mnop`.
   Copy it and **remove the spaces** → `abcdefghijklmnop`. You'll paste this
   into GitHub in Step 3. You won't see it again, so keep it safe for now.

---

## Step 2 — Put these files into a GitHub repository

If you don't have a GitHub account, create one free at https://github.com.

**Easiest (web upload):**
1. On GitHub click **New repository**. Name it `tender-alerts`. Set it to
   **Private**. Click **Create repository**.
2. On the new repo's page click **uploading an existing file**.
3. Drag in `tender_monitor.py`, `seen.json`, `requirements.txt`, `SETUP.md`.
4. The workflow file must keep its folder path. The simplest way: click
   **Add file → Create new file**, and in the name box type
   `.github/workflows/tender-alerts.yml` (GitHub creates the folders as you
   type the slashes), then paste the contents of the workflow file and commit.

That's everything uploaded.

---

## Step 3 — Add your secrets

Secrets are private values GitHub stores securely; the script reads them at
run time. In your repo:

**Settings → Secrets and variables → Actions → New repository secret.**

Add these three, one at a time:

| Name | Value |
|------|-------|
| `SMTP_USER` | the Gmail address you made, e.g. `rtb.tenderbot@gmail.com` |
| `SMTP_PASS` | the 16-character app password from Step 1 (no spaces) |
| `MAIL_TO` | `appsbidsteam@raisethebar.co.uk` |

(Names must be typed exactly, in capitals.)

---

## Step 4 — Test it now

1. Go to the **Actions** tab in your repo.
2. If prompted, click the green button to enable workflows.
3. Click **RTB Tender Alerts** on the left, then **Run workflow → Run
   workflow**.
4. Watch it run (a minute or two). Green tick = success. Check the
   `appsbidsteam` inbox — if any matching notices were published in the last
   couple of days, you'll get the digest. If nothing matched, the run still
   succeeds and simply sends nothing (the log will say "Nothing new to send").

That's it. From now on it runs automatically at ~08:15 UK time, Monday to
Friday.

---

## Tuning it later

Open `tender_monitor.py` and edit the lists near the top:

- **Add keywords**: add lines to the `KEYWORDS` list (lower-case).
- **Cut noise**: add words to the `EXCLUDE_WORDS` list — any notice
  containing one is dropped. (Currently empty, as you asked.)
- **Change who gets it**: edit the `MAIL_TO` secret in GitHub.
- **Change the time**: edit the `cron:` line in the workflow file. The
  numbers are `minute hour * * days`, in UTC. `15 7 * * 1-5` = 07:15 UTC,
  Mon–Fri.
- **Weekends too?** change `1-5` to `*`.

After editing on GitHub, just commit — the next scheduled run uses your
changes.

---

## Good to know

- **No duplicates**: once a notice has been emailed, its ID is stored in
  `seen.json` and never sent again.
- **If one source is down**: the script logs the error and still sends
  whatever the other source returned — a single outage won't break the run.
- **First run may be quiet**: it only looks back 2 days by default, so the
  first email reflects just the last couple of days. To do a bigger initial
  sweep, temporarily set a `LOOKBACK_DAYS` secret to e.g. `30`, run it once,
  then delete that secret.
- **Cost**: free. Private-repo Actions include a generous free monthly
  allowance; a daily 2-minute job uses a tiny fraction of it.
