#!/usr/bin/env python3
import os
import sys
import json
import time
import requests
from datetime import datetime, timezone

API_URL = "https://livestream-api.pump.fun/bounties/v2/tasks"
PARAMS = {
    "phase": "OPEN",
    "sort": "createdAt",
    "order": "desc",
    "limit": 50,
}

HEADERS = {
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

POLL_SECONDS = 30
SEEN_FILE    = "seen_bounties.json"
REQUEST_TIMEOUT = 20


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f)


def fetch_tasks():
    resp = requests.get(API_URL, params=PARAMS, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", []) if isinstance(data, dict) else []


def human_time_left(expires_at):
    if not expires_at:
        return None
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        delta = exp - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "expired"
        days, rem = divmod(secs, 86400)
        hours = rem // 3600
        if days > 0:
            return f"{days}d {hours}h left"
        return f"{hours}h left"
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
    time_left = human_time_left(b["expires"])

    desc = b["description"].replace("\n", " ").strip()
    desc = (desc[:240] + "...") if len(desc) > 240 else desc

    lines = [
        f"🆕 New GO bounty{fire}",
        "",
        f"{b['title']}",
        f"💰 {reward}",
        f"👤 {followers} X followers{verified}",
    ]
    if b["submissions"] is not None:
        lines.append(f"📥 {b['submissions']} submission(s) so far")
    if time_left:
        lines.append(f"⏳ {time_left}")
    lines += ["", desc, "", f"🔗 {b['link']}"]
    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print(f"Telegram send failed: {e}")


def check_once(seen):
    tasks = fetch_tasks()
    bounties = [normalize(t) for t in tasks if t.get("taskId")]

    first_run = len(seen) == 0
    new = [b for b in bounties if b["uid"] not in seen and passes_filters(b)]
    for b in bounties:
        seen.add(b["uid"])

    if first_run:
        print(f"First run - baselined {len(bounties)} existing bounties, no alerts sent.")
    elif new:
        for b in sorted(new, key=lambda x: x["created"] or ""):
            print(f"NEW: {b['title']}")
            send_telegram(format_msg(b))
    else:
        print(f"No new bounties. ({datetime.now().strftime('%H:%M:%S')})")

    save_seen(seen)
    return seen


def main():
    args = set(sys.argv[1:])
    if "--once" in args:
        check_once(load_seen())
        return
    seen = load_seen()
    print(f"Watching GO bounties every {POLL_SECONDS}s. Ctrl+C to stop.")
    while True:
        try:
            seen = check_once(seen)
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
