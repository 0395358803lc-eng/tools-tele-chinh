<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=2,8,15&height=180&section=header&text=Multi+TG+Manager&fontSize=50&fontColor=000000&fontAlignY=38&desc=Local+dashboard+for+managing+your+own+Telegram+accounts+from+one+screen&descAlignY=58&descSize=14&animation=fadeIn" width="100%"/>

<div align="center">

![Local](https://img.shields.io/badge/Local-127.0.0.1-BFE7FF?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Backend](https://img.shields.io/badge/Backend-FastAPI-C8F7DC?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Frontend](https://img.shields.io/badge/Frontend-React+%2B+Vite-FFF0B8?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Desktop](https://img.shields.io/badge/Desktop-Windows+EXE-FFC7C7?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)

</div>

## Windows Desktop runtime data

Windows Desktop releases run in their own native application window. End users
do not need Python, Node.js, PowerShell, or `START.bat`.

Desktop runtime data is stored under the current Windows user's local profile:

```text
%LOCALAPPDATA%\MultiTGManager\
  backend\.env
  data\database\app.db
  data\sessions\*.session
  data\secrets\twofa.bin
  data\backups\<timestamp>\
  data\logs\app.log
```

The source/developer launcher still uses the repository-local `data/` layout:

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
cd ..\frontend
npm test
npm run build
```

Run endurance checks from the project root:

```powershell
# Scheduler stress test; does not contact Telegram.
SOAK_TEST.bat -Mode scheduler -Minutes 5 -Accounts 100

# Poll a running app's health/database/secure-store state over time.
SOAK_TEST.bat -Mode health -Minutes 30 -IntervalSeconds 2
```

The health soak requires Multi TG Manager to already be running on `127.0.0.1:8000`.
Create the legacy/source ZIP with `BUILD_RELEASE.bat`. Build native Windows Desktop EXEs with `build_desktop.ps1` on Windows.

<div align="center">
<i>A private local dashboard for account status, profile edits, groups, messages, security alerts, and bulk Telegram account actions.</i>
</div>

---

## Features

| Feature | What it does |
| --- | --- |
| Windows Desktop | Installer and single-file Portable EXE open the dashboard in a native desktop window and embed the Python backend/runtime. |
| Local-only server | Runs on `127.0.0.1`, so the dashboard is not exposed to the internet. |
| Telegram sessions | Uses Telethon session files stored under `data/sessions/`, outside the source tree. |
| Bulk tools | Bulk names, bios, profile photos, joins, leaves, message actions, and security checks. |
| Password gate | Dashboard login uses `APP_PASSWORD` and signed cookies. |

---

## Download and Run — Windows Desktop

Choose one of the Windows x64 release files:

```text
MultiTGManager-Setup-1.0.0-x64.exe
  -> Recommended. Installs the app, Start Menu entry, and Desktop shortcut.

MultiTGManager-Portable-1.0.0-x64.exe
  -> No installation. Double-click the single EXE to run.
```

On the first launch, a small setup window asks for:

```text
Telegram API ID
Telegram API Hash
Dashboard Password
```

The application generates its own session secret and stores configuration only
under `%LOCALAPPDATA%\MultiTGManager`. The dashboard then opens inside the
Windows desktop application window. Python and Node.js are not required.

The local backend remains bound only to `127.0.0.1:8000`.

---

## Source / developer setup

When running from source rather than the Windows Desktop EXE, fill these values
in `backend/.env`:

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

Building or running directly from a fresh source checkout additionally requires Node.js 20.19+ (or 22.12+). `START.bat` automatically runs `npm ci` and builds the frontend when `backend/static/index.html` is absent.

---

## Project Structure

```text
multi-tg-manager/
  start.bat              -> first-run setup and server launcher
  stop.bat               -> stops the local server on port 8000
  backend/               -> FastAPI app, migrations, env, bundled frontend
  data/                  -> runtime database, sessions, encrypted secrets, backups, logs
  backend/requirements.lock -> exact Python dependency set used by start.bat
  frontend/              -> React and Vite dashboard
  frontend/package.json  -> frontend scripts
```

---

## Notes

- `backend/.env`, `data/database/app.db`, `data/sessions/*.session`, and `data/secrets/twofa.bin` are private. Treat session files like passwords.
- Close the black server window or run `stop.bat` to stop the app.
- Only manage accounts you own or are allowed to operate.

---

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=2,8,15&height=90&section=footer" width="100%"/>

<p align="center">
  <sub>MIT License unless noted otherwise. Built by <a href="https://github.com/0xnurrabby">0xnurrabby</a>.</sub>
</p>
