<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=2,8,15&height=180&section=header&text=Multi+TG+Manager&fontSize=50&fontColor=000000&fontAlignY=38&desc=Local+dashboard+for+managing+your+own+Telegram+accounts+from+one+screen&descAlignY=58&descSize=14&animation=fadeIn" width="100%"/>

<div align="center">

![Local](https://img.shields.io/badge/Local-127.0.0.1-BFE7FF?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Backend](https://img.shields.io/badge/Backend-FastAPI-C8F7DC?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Frontend](https://img.shields.io/badge/Frontend-React+%2B+Vite-FFF0B8?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Desktop](https://img.shields.io/badge/Desktop-Windows+BAT-FFC7C7?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)

</div>

## Windows runtime data

Run `START.bat`. Production releases include the built frontend, so end users
do not need Node.js. Runtime data is isolated from source code:

```text
data/
  database/app.db
  sessions/*.session
  secrets/twofa.bin
  backups/<timestamp>/
  logs/app.log
```

Legacy files under `backend/` are copied into this layout without deleting the
originals. Create backups from Settings. To restore, stop the app and run
`RESTORE_BACKUP.bat`; it creates another safety backup before replacing data.
The encrypted 2FA store remains tied to its Windows DPAPI user context.

Run automated tests with:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Create a sanitized Windows release with `BUILD_RELEASE.bat`.

<div align="center">
<i>A private local dashboard for account status, profile edits, groups, messages, security alerts, and bulk Telegram account actions.</i>
</div>

---

## Features

| Feature | What it does |
| --- | --- |
| One-click Windows start | `start.bat` creates the venv, installs the exact lockfile, serves the bundled frontend, and opens the app. |
| Local-only server | Runs on `127.0.0.1`, so the dashboard is not exposed to the internet. |
| Telegram sessions | Uses Telethon session files stored inside the backend folder. |
| Bulk tools | Bulk names, bios, profile photos, joins, leaves, message actions, and security checks. |
| Password gate | Dashboard login uses `APP_PASSWORD` and signed cookies. |

---

## Download and Run

```text
1. Install Python 3.10+ from https://python.org and tick Add Python to PATH.
2. Download a release ZIP (Node.js is not required for releases).
3. Open the folder and double-click start.bat.
4. Fill backend\.env when Notepad opens.
5. Save, close Notepad, and double-click start.bat again.
```

The app opens at `http://localhost:8000`.

---

## Setup

Fill these values in `backend/.env`:

```env
TG_API_ID=your_api_id_from_my_telegram_org
TG_API_HASH=your_api_hash_from_my_telegram_org
APP_PASSWORD=use_a_unique_password_of_12_to_256_characters
SESSION_SECRET=auto_generated_by_start_bat_minimum_48_characters
```

To check your new PC setup:

```powershell
python --version
```

Building from source additionally requires Node.js 20.19+ (or 22.12+).

---

## Project Structure

```text
multi-tg-manager/
  start.bat              -> first-run setup and server launcher
  stop.bat               -> stops the local server on port 8000
  backend/               -> FastAPI app, SQLite DB, sessions, env
  backend/requirements.lock -> exact Python dependency set used by start.bat
  frontend/              -> React and Vite dashboard
  frontend/package.json  -> frontend scripts
```

---

## Notes

- `backend/.env`, `backend/app.db`, and `backend/sessions/*.session` are private. Treat session files like passwords.
- Close the black server window or run `stop.bat` to stop the app.
- Only manage accounts you own or are allowed to operate.

---

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=2,8,15&height=90&section=footer" width="100%"/>

<p align="center">
  <sub>MIT License unless noted otherwise. Built by <a href="https://github.com/0xnurrabby">0xnurrabby</a>.</sub>
</p>
