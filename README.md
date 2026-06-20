<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=2,8,15&height=180&section=header&text=Multi+TG+Manager&fontSize=50&fontColor=000000&fontAlignY=38&desc=Local+dashboard+for+managing+your+own+Telegram+accounts+from+one+screen&descAlignY=58&descSize=14&animation=fadeIn" width="100%"/>

<div align="center">

![Local](https://img.shields.io/badge/Local-127.0.0.1-BFE7FF?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Backend](https://img.shields.io/badge/Backend-FastAPI-C8F7DC?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Frontend](https://img.shields.io/badge/Frontend-React+%2B+Vite-FFF0B8?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)
![Desktop](https://img.shields.io/badge/Desktop-Windows+BAT-FFC7C7?style=for-the-badge&labelColor=1a1a1a&logoColor=1a1a1a)

</div>

<div align="center">
<i>A private local dashboard for account status, profile edits, groups, messages, security alerts, and bulk Telegram account actions.</i>
</div>

---

## Features

| Feature | What it does |
| --- | --- |
| One-click Windows start | `start.bat` creates the venv, installs packages, builds the frontend, and opens the app. |
| Local-only server | Runs on `127.0.0.1`, so the dashboard is not exposed to the internet. |
| Telegram sessions | Uses Telethon session files stored inside the backend folder. |
| Bulk tools | Bulk names, bios, profile photos, joins, leaves, message actions, and security checks. |
| Password gate | Dashboard login uses `APP_PASSWORD` and signed cookies. |

---

## Download and Run

```text
1. Install Python 3.10+ from https://python.org and tick Add Python to PATH.
2. Install Node.js 18+ from https://nodejs.org.
3. Download this repo as ZIP or run: git clone https://github.com/0xnurrabby/multi-tg-manager.git
4. Open the folder.
5. Double-click start.bat.
6. Fill backend\.env when Notepad opens.
7. Save, close Notepad, and double-click start.bat again.
```

The app opens at `http://localhost:8000`.

---

## Setup

Fill these values in `backend/.env`:

```env
TG_API_ID=your_api_id_from_my_telegram_org
TG_API_HASH=your_api_hash_from_my_telegram_org
APP_PASSWORD=change_me_to_a_long_password
SESSION_SECRET=auto_generated_by_start_bat
SESSIONS_DIR=./sessions
DB_URL=sqlite+aiosqlite:///./app.db
```

To check your new PC setup:

```powershell
python --version
node --version
```

---

## Project Structure

```text
multi-tg-manager/
  start.bat              -> first-run setup and server launcher
  stop.bat               -> stops the local server on port 8000
  backend/               -> FastAPI app, SQLite DB, sessions, env
  backend/requirements.txt -> Python dependencies
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
