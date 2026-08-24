import os
import asyncio
import sqlite3
import threading
import secrets
from flask import Flask, request, Response, render_template_string, abort
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8000"))
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

DB = "files.db"
app = Flask(__name__)
lock = threading.Lock()

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT,
            mime_type TEXT,
            size INTEGER
        )""")
        con.commit()

def is_admin(uid):
    return uid in ADMIN_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📁 File Stream Bot\n\n"
        "Send an authorized file to the bot (admin only).\n"
        "Users can open generated links in a browser.\n\n"
        "/help - Help"
        "\n/files - Recent files (admin)"
    )
    await update.message.reply_text(text)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Admin: send a document/video/audio file here. "
        "The bot stores its Telegram file_id and returns a stream/download URL."
    )

async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    with db() as con:
        rows = con.execute("SELECT * FROM files ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.message.reply_text("No files yet.")
        return
    lines = []
    for r in rows:
        url = f"{BASE_URL}/file/{r['token']}" if BASE_URL else "(set BASE_URL)"
        lines.append(f"#{r['id']} {r['file_name'] or 'file'}\n{url}")
    await update.message.reply_text("\n\n".join(lines))

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Upload is restricted to admins.")
        return

    msg = update.message
    tg_file_id = None
    name = None
    mime = None
    size = None

    if msg.document:
        tg_file_id = msg.document.file_id
        name = msg.document.file_name
        mime = msg.document.mime_type
        size = msg.document.file_size
    elif msg.video:
        tg_file_id = msg.video.file_id
        name = msg.video.file_name or "video.mp4"
        mime = msg.video.mime_type or "video/mp4"
        size = msg.video.file_size
    elif msg.audio:
        tg_file_id = msg.audio.file_id
        name = msg.audio.file_name or "audio"
        mime = msg.audio.mime_type or "audio/mpeg"
        size = msg.audio.file_size
    else:
        return

    token = secrets.token_urlsafe(10)
    with db() as con:
        con.execute(
            "INSERT INTO files(token,file_id,file_name,mime_type,size) VALUES(?,?,?,?,?)",
            (token, tg_file_id, name, mime, size)
        )
        con.commit()

    url = f"{BASE_URL}/file/{token}" if BASE_URL else f"/file/{token}"
    await msg.reply_text(f"✅ Saved\n\n{name}\n\n🔗 {url}")

def get_record(token):
    with db() as con:
        return con.execute("SELECT * FROM files WHERE token=?", (token,)).fetchone()

PAGE = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ name }}</title>
<style>
body{font-family:Arial,sans-serif;background:#111;color:#fff;margin:0;padding:20px}
.card{max-width:900px;margin:auto;background:#1d1d1d;padding:18px;border-radius:14px}
video,audio{width:100%;max-height:75vh}
a.btn{display:inline-block;margin-top:15px;padding:11px 16px;background:#fff;color:#111;text-decoration:none;border-radius:9px}
</style>
</head>
<body><div class="card">
<h3>{{ name }}</h3>
{% if media == 'video' %}
<video controls preload="metadata" src="/media/{{ token }}"></video>
{% elif media == 'audio' %}
<audio controls src="/media/{{ token }}"></audio>
{% else %}
<p>This file cannot be previewed in the browser.</p>
{% endif %}
<a class="btn" href="/media/{{ token }}?download=1">Download</a>
</div></body></html>"""

@app.get("/file/<token>")
def file_page(token):
    r = get_record(token)
    if not r:
        abort(404)
    mime = r["mime_type"] or ""
    media = "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "other"
    return render_template_string(PAGE, name=r["file_name"] or "File", token=token, media=media)

@app.get("/media/<token>")
def media(token):
    r = get_record(token)
    if not r:
        abort(404)

    tg = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": r["file_id"]},
        timeout=30
    ).json()
    if not tg.get("ok"):
        abort(502)

    path = tg["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"

    # Stream through the app. Range support is intentionally kept simple;
    # deployment proxies may buffer large files.
    upstream = requests.get(url, stream=True, timeout=60)
    if upstream.status_code != 200:
        abort(502)

    headers = {}
    content_type = r["mime_type"] or upstream.headers.get("Content-Type", "application/octet-stream")
    headers["Content-Type"] = content_type
    if request.args.get("download") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{r["file_name"] or "file"}"'
    elif r["file_name"]:
        headers["Content-Disposition"] = "inline"

    def generate():
        for chunk in upstream.iter_content(chunk_size=1024*128):
            if chunk:
                yield chunk

    return Response(generate(), headers=headers)

@app.get("/health")
def health():
    return {"status": "ok"}

def run_bot():
    # Python 3.14 no longer creates an event loop automatically in new threads.
    # Create and register one explicitly before python-telegram-bot starts polling.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("files", files_cmd))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO, receive_file))
    application.run_polling()

if __name__ == "__main__":
    init_db()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
