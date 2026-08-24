# Telegram File Stream Bot

A simple Telegram bot for streaming/downloading files that you are authorized to distribute.

## Features
- Admin-only file storage
- Telegram file IDs stored in SQLite
- Generates `/file/<id>` web links
- Browser streaming/download endpoint
- Simple HTML player for video/audio
- `/start`, `/help`, `/files`
- Config through environment variables

## Setup

1. Create a Telegram bot with BotFather and copy its token.
2. Set environment variables:
   - `BOT_TOKEN` = Telegram bot token
   - `ADMIN_IDS` = comma-separated Telegram user IDs
   - `BASE_URL` = public HTTPS URL of the deployed web app
3. Install:
   `pip install -r requirements.txt`
4. Start:
   `python app.py`

The bot stores Telegram `file_id` values, not the uploaded binary files.

## Important
Use this only for files you own or are authorized to distribute. Free hosting providers can change or remove free tiers, so no free service can guarantee 3 years of uninterrupted uptime.
