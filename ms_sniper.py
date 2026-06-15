#!/usr/bin/env python3
import os
import json
import time
import threading
import requests
import websocket
from datetime import datetime, timezone

BOUNTIES_PROGRAM_ID = "goGzNYTYkSEe4hUqz6dPmY5uf3CTt36AQAoujXDrKiV"

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
HELIUS_WS_URL  = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

API_URL = "https://livestream-api.pump.fun/bounties/v2/tasks"
PARAMS  = {"phase": "OPEN", "sort": "createdAt", "order": "desc", "limit": 50}
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/go/bounties",
}
BOUNTY_URL_TEMPLATE = "https://pump.fun/go/bounty/{task_id}"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

MIN_REWARD_USD = 0
KEYWORDS       = []
EXCLUDE_NSFW   = True
HIGH_VALUE_USD = 5000

SAFETY_POLL_SECONDS = 20
MIN_CHECK_SPACING   = 1.0
REQUEST_TIMEOUT     = 15

seen = set()
seen_lock = threading.Lock()
check_event = threading.Event()


def fetch_newest():
    r = requests.get(API_URL, params=PARAMS, headers=API_HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("items", []) if isinstance(data, dict) else []


def human_time_left(expires_at):
    if not expires_at:
        return None
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        secs = int((exp - datetime.now(timezone.utc)).total_seconds())
        if secs <= 0:
            return "expired"
        d, rem = divmod(secs, 86400)
        h = rem // 3600
        return f"{d}d {h}h left" if d else f"{h}h left"
    except Exception:
        return None


def normalize(task):
    reward = task.get("rewardTotalUsd")
    return {
        "uid": task.get("taskId"),
        "title": (task.get("title") or "Untitled bounty").strip(),
        "description": (task.get("bodyMarkdown") or "").strip(),
        "reward_usd": float(reward) if isinstance(reward, (int, float)) else None,
        "created": task.get("createdAt"),
        "expires": task.get("expiresAt"),
        "submissions": (task.get("counts") or {}).get("submissionCount"),
        "creator_followers": task.get("creatorXFollowerCount"),
        "creator_verified": task.get("creatorXVerified"),
        "nsfw": task.get("isNsfw", False),
        "link": BOUNTY_URL_TEMPLATE.format(task_id=task.get("taskId")),
    }


def passes_filters(b):
    if EXCLUDE_NSFW and b["nsfw"]:
        return False
    if MIN_REWARD_USD and (b["reward_usd"] is None or b["reward_usd"] < MIN_REWARD_USD):
        return False
    if KEYWORDS:
        hay = f"{b['title']} {b['description']}".lower()
        if not any(k.lower() in hay for k in KEYWORDS):
            return False
    return True


def format_msg(b):
    reward = f"${b['reward_usd']:,.0f}" if b["reward_usd"] is not None else "-"
    fire = " 🔥" if (HIGH_VALUE_USD and b["reward_usd"] and b["reward_usd"] >= HIGH_VALUE_USD) else ""
    verified = " ✅" if b["creator_verified"] else ""
    followers = f"{b['creator_followers']:,}" if isinstance(b["creator_followers"], int) else "?"
    tl = human_time_left(b["expires"])
    desc = b["description"].replace("\n", " ").strip()
    desc = (desc[:240] + "...") if len(desc) > 240 else desc
    lines = [f"⚡ NEW GO bounty (sniped){fire}", "", b["title"],
             f"💰 {reward}", f"👤 {followers} X followers{verified}"]
    if b["submissions"] is not None:
        lines.append(f"📥 {b['submissions']} submission(s)")
    if tl:
        lines.append(f"⏳ {tl}")
    lines += ["", desc, "", f"🔗 {b['link']}"]
    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] missing creds")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        print(f"[telegram] send failed: {e}")


def check_new_bounties():
    try:
        tasks = fetch_newest()
    except Exception as e:
        print(f"[api] fetch failed: {e}")
        return
    new = []
    with seen_lock:
        for t in tasks:
            tid = t.get("taskId")
            if not tid or tid in seen:
                continue
            b = normalize(t)
            if passes_filters(b):
                new.append(b)
            seen.add(tid)
    for b in sorted(new, key=lambda x: x["created"] or ""):
        print(f"NEW: {b['title']}")
        send_telegram(format_msg(b))


def baseline():
    try:
        tasks = fetch_newest()
        with seen_lock:
            for t in tasks:
                if t.get("taskId"):
                    seen.add(t["taskId"])
        print(f"[baseline] recorded {len(tasks)} existing bounties, watching for new drops...")
    except Exception as e:
        print(f"[baseline] failed: {e}")


def checker_loop():
    while True:
        check_event.wait(timeout=SAFETY_POLL_SECONDS)
        check_event.clear()
        check_new_bounties()
        time.sleep(MIN_CHECK_SPACING)


def on_open(ws):
    print("[ws] connected to Helius, subscribing...")
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [BOUNTIES_PROGRAM_ID]},
            {"commitment": "processed"},
        ],
    }))


def on_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        return
    if data.get("method") == "logsNotification":
        check_event.set()
    elif "result" in data and data.get("id") == 1:
        print(f"[ws] subscribed (id={data['result']}). Listening on-chain.")


def on_error(ws, error):
    print(f"[ws] error: {error}")


def on_close(ws, code, msg):
    print(f"[ws] closed ({code}). Reconnecting...")


def run_ws_forever():
    while True:
        try:
            ws = websocket.WebSocketApp(
                HELIUS_WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"[ws] crashed: {e}")
        time.sleep(3)


def main():
    print("Starting GO bounty MS sniper...")
    baseline()
    threading.Thread(target=checker_loop, daemon=True).start()
    run_ws_forever()


if __name__ == "__main__":
    main()
