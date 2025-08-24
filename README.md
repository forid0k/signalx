# WinGo Realtime Signal Bot (Python)

Real-time Socket.IO bot that listens for WinGo results, generates **BIG/SMALL + EVEN/ODD** signals, and pushes them to your **InfinityFree PHP endpoint** (and optionally Telegram).

## Features
- Socket.IO WebSocket client (tested with `hgzy.app`-style endpoints)
- Flexible payload parser (works even if field names vary)
- BIG/SMALL + EVEN/ODD signal with simple confidence score
- Push to PHP endpoint (`push_signal.php`) on your InfinityFree site
- Optional Telegram alerts
- Backfill from history endpoint (optional)
- Heartbeat to status API (optional)
- De-duplication of issues

## Repo Structure
```
winGo-bot/
├── bot.py
├── config.example.json
├── requirements.txt
└── README.md
```

## Setup

1. **Clone & Install**
   ```bash
   git clone <your_repo_url>.git
   cd winGo-bot
   python -m venv .venv
   . .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure**
   - Copy example to actual config:
     ```bash
     cp config.example.json config.json
     ```
   - Edit `config.json`:
     - `WEB_PUSH_URL`: your InfinityFree API endpoint (e.g., `https://your-site.com/api/push_signal.php`)
     - `WEB_API_KEY`: the same secret you put in `push_signal.php`
     - Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` if you want Telegram alerts.
     - Adjust `BIG_THRESHOLD`, etc.

   - You can also use environment variables to override:
     - `WEB_PUSH_URL`, `WEB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CONFIG_PATH`

   - Optional `.env` file:
     ```env
     WEB_PUSH_URL=https://your-site.com/api/push_signal.php
     WEB_API_KEY=CHANGE_ME_SUPER_SECRET
     TELEGRAM_BOT_TOKEN=
     TELEGRAM_CHAT_ID=
     ```

3. **Run**
   ```bash
   python bot.py
   ```

## Notes
- Socket.IO servers often require **BASE+PATH** connect instead of a one-off `wss://...sid=...` URL (SIDs expire).
- If the server requires a subscribe message after connect, set `SOCKETIO.SUBSCRIBE_EVENT` & `SUBSCRIBE_PAYLOAD` in `config.json`.
- The flexible parser tries to find a numeric result in common fields, but if you know the exact event name/shape, add a dedicated `sio.on("your_event")` handler in `bot.py` for best results.
- InfinityFree cannot run Python. Host this bot on your PC/VPS/Render/Replit, and point it to your InfinityFree `push_signal.php`.

## License
MIT
