# winGo-bot / bot.py
# Python 3.10+
# pip install -r requirements.txt
#
# Real-time WinGo signal bot:
# - Connects to a Socket.IO WebSocket (your provided hgzy.app endpoint)
# - Parses live numbers and generates BIG/SMALL + EVEN/ODD signals
# - Pushes signals to your InfinityFree PHP endpoint
# - (Optional) sends Telegram notifications
#
# Author: ChatGPT (GitHub-ready template)

import os
import re
import json
import time
import math
import signal
import socketio
import requests
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# ----- Load env & config -----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))
if not os.path.exists(CONFIG_PATH):
    # fallback to example for first run
    CONFIG_PATH = os.path.join(BASE_DIR, "config.example.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG: Dict[str, Any] = json.load(f)

# Allow env to override a few critical settings
CONFIG["WEB_PUSH_URL"] = os.environ.get("WEB_PUSH_URL", CONFIG.get("WEB_PUSH_URL", ""))
CONFIG["WEB_API_KEY"]  = os.environ.get("WEB_API_KEY",  CONFIG.get("WEB_API_KEY", ""))
CONFIG["TELEGRAM_BOT_TOKEN"] = os.environ.get("TELEGRAM_BOT_TOKEN", CONFIG.get("TELEGRAM_BOT_TOKEN", ""))
CONFIG["TELEGRAM_CHAT_ID"]   = os.environ.get("TELEGRAM_CHAT_ID",   CONFIG.get("TELEGRAM_CHAT_ID", ""))

# Dedup keys (recent issues) to avoid duplicate pushes
SEEN_KEYS: list[str] = []

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def log(*args):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}]", *args, flush=True)

def safe_get(d: Any, *keys, default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur

def parse_number_from_payload(data: Any) -> Optional[Dict[str, Any]]:
    """
    Flexible parser to extract 0–99 number and optional issue/period from unknown payload shapes.
    Returns dict: {"issue": str|None, "number": int, "raw": Any}
    """
    raw = data

    # 1) If dict, look for known fields
    if isinstance(data, dict):
        candidates = [
            safe_get(data, "number"),
            safe_get(data, "result"),
            safe_get(data, "openCode"),
            safe_get(data, "lucky"),
            safe_get(data, "lottery", "number"),
            safe_get(data, "data", "number"),
        ]
        for c in candidates:
            if isinstance(c, int):
                if 0 <= c <= 99:
                    issue = str(safe_get(data, "issue") or safe_get(data, "expect") or safe_get(data, "period") or "")
                    return {"issue": issue or None, "number": c, "raw": raw}
            elif isinstance(c, str):
                m = re.search(r"\d{1,2}$", c.strip())
                if m:
                    n = int(m.group())
                    if 0 <= n <= 99:
                        issue = str(safe_get(data, "issue") or safe_get(data, "expect") or safe_get(data, "period") or "")
                        return {"issue": issue or None, "number": n, "raw": raw}

        # Sometimes arrays live under common keys
        for k in ("list", "rows", "data", "resultList"):
            arr = safe_get(data, k)
            if isinstance(arr, list) and arr:
                return parse_number_from_payload(arr[-1])

    # 2) If string, maybe JSON or plain with trailing number
    if isinstance(data, str):
        s = data.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                return parse_number_from_payload(json.loads(s))
            except Exception:
                pass
        m = re.search(r"(\d{1,2})\s*$", s)
        if m:
            n = int(m.group(1))
            if 0 <= n <= 99:
                return {"issue": None, "number": n, "raw": data}

    # 3) If list, try last item
    if isinstance(data, list) and data:
        return parse_number_from_payload(data[-1])

    return None

def make_signal(n: int) -> Dict[str, Any]:
    big_threshold = int(CONFIG.get("BIG_THRESHOLD", 5))
    big_small = "BIG" if n >= big_threshold else "SMALL"
    even_odd = "EVEN" if (n % 2 == 0) else "ODD"

    # Simple confidence heuristic
    confidence = 60
    if n in (big_threshold, big_threshold - 1):
        confidence = 55
    if n in (0, 9):
        confidence = 65

    return {
        "decision": f"{big_small} / {even_odd}",
        "confidence": confidence,
        "meta": {
            "num": n,
            "notes": f"Num={n} → {big_small} & {even_odd}"
        }
    }

def push_to_web(payload: Dict[str, Any]):
    url = CONFIG.get("WEB_PUSH_URL", "")
    api_key = CONFIG.get("WEB_API_KEY", "")
    if not url or not api_key:
        log("WARN: WEB_PUSH_URL or WEB_API_KEY not set; skip push.")
        return
    try:
        p = dict(payload)
        p["api_key"] = api_key
        r = requests.post(url, json=p, timeout=10)
        log("WEB PUSH:", r.status_code, (r.text[:250] if isinstance(r.text, str) else r.text))
    except Exception as e:
        log("WEB PUSH error:", e)

def push_telegram(text: str):
    token = CONFIG.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = CONFIG.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode":"HTML"}, timeout=10)
    except Exception as e:
        log("TG error:", e)

def heartbeat():
    hb_url = CONFIG.get("UPDATE_STATUS_URL", "")
    if not hb_url:
        return
    try:
        requests.post(hb_url, json={"status":"online","ts":int(time.time())}, timeout=8)
    except Exception:
        pass

def dedup_key(issue: Optional[str], n: int) -> str:
    # If no issue, fallback to num+minute key
    base = issue or (f"{n}-{int(time.time()//60)}")
    return base

def handle_result(result: Dict[str, Any]):
    n = result["number"]
    issue = result.get("issue")
    dkey = dedup_key(issue, n)
    if dkey in SEEN_KEYS:
        return
    SEEN_KEYS.append(dkey)
    max_keep = int(CONFIG.get("DEDUP_WINDOW", 200))
    if len(SEEN_KEYS) > max_keep:
        del SEEN_KEYS[:-max_keep]

    sig = make_signal(n)
    payload = {
        "symbol":     CONFIG.get("SYMBOL", "WinGo"),
        "timeframe":  CONFIG.get("TIMEFRAME", "30s"),
        "game":       CONFIG.get("GAME", "WinGo_30S"),
        "issue":      issue,
        "created_at": now_iso(),
        "signal":     sig,
    }

    log(f"SIGNAL | Issue={issue} | {sig['decision']} ({sig['confidence']}%) | Num={n}")
    if CONFIG.get("SEND_TO_WEB", True):
        push_to_web(payload)

    if CONFIG.get("SEND_TO_TELEGRAM", False):
        msg = (
            f"🎯 <b>{payload['symbol']}</b> [{payload['timeframe']}] — <b>{sig['decision']}</b> ({sig['confidence']}%)\n"
            f"Game: {payload['game']}\nIssue: {issue or '-'}\nNumber: <b>{n}</b>\n{sig['meta']['notes']}"
        )
        push_telegram(msg)

# ----- Socket.IO client -----
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=2)

@sio.event
def connect():
    log("WS Connected.")
    sub_event    = CONFIG.get("SOCKETIO", {}).get("SUBSCRIBE_EVENT")
    sub_payload  = CONFIG.get("SOCKETIO", {}).get("SUBSCRIBE_PAYLOAD")
    if sub_event:
        try:
            sio.emit(sub_event, sub_payload or {})
            log(f"WS subscribe sent: {sub_event} -> {sub_payload}")
        except Exception as e:
            log("WS subscribe error:", e)

@sio.event
def disconnect():
    log("WS Disconnected. (auto-reconnect enabled)")

# Catch a variety of commonly-used event names if we don't know the exact one.
COMMON_EVENTS = ["message", "broadcast", "update", "push", "result", "lottery", "draw", "issue", "openCode"]
for ev in COMMON_EVENTS:
    def _make_handler(evname):
        def _handler(data=None):
            preview = str(data)
            if len(preview) > 200:
                preview = preview[:200] + "..."
            log(f"WS:{evname} ->", preview)
            parsed = parse_number_from_payload(data)
            if parsed:
                handle_result(parsed)
        return _handler
    sio.on(ev)(_make_handler(ev))

def connect_socket():
    sconf = CONFIG.get("SOCKETIO", {})
    base  = sconf.get("BASE", "https://hgzy.app")
    path  = sconf.get("PATH", "/socket.io")
    transports = sconf.get("TRANSPORTS", ["websocket"])
    query = sconf.get("QUERY", {"EIO": "4"})

    # Standard Socket.IO connect (recommended). The raw ws url with 'sid' usually expires.
    sio.connect(base, socketio_path=path, transports=transports, headers={}, query=query)
    sio.wait()

def poll_history_backfill():
    """ Optional: On startup, fetch last history to have an initial state. """
    url = CONFIG.get("HISTORY_URL", "")
    if not url:
        return
    try:
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}ts={int(time.time()*1000)}"
        r = requests.get(full, timeout=8)
        if r.status_code == 200:
            data = r.json()
            parsed = parse_number_from_payload(data)
            if parsed:
                handle_result(parsed)
    except Exception as e:
        log("History fetch error:", e)

def main():
    # Graceful shutdown
    def on_sigint(sig, frame):
        log("Received interrupt. Exiting...")
        try:
            sio.disconnect()
        except Exception:
            pass
        raise SystemExit(0)
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)

    last_hb = 0
    poll_history_backfill()

    while True:
        try:
            connect_socket()
        except SystemExit:
            return
        except Exception as e:
            log("WS connect error:", e)
            time.sleep(2)

        # Heartbeat loop (if socket thread returns)
        while True:
            try:
                if CONFIG.get("UPDATE_STATUS_URL") and (time.time() - last_hb > int(CONFIG.get("HEARTBEAT_SEC", 60))):
                    heartbeat()
                    last_hb = time.time()
                time.sleep(2)
            except SystemExit:
                return
            except Exception:
                time.sleep(2)

if __name__ == "__main__":
    main()
