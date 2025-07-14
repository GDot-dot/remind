# ---------------------------------
# 匯入必要的函式庫
# ---------------------------------
import os
import re
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent
)

from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.parser import parse

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)

 # LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
    # LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')

# 從 Render 的環境變數讀取憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
DB_PATH = 'reminders_v2.db'

# ---------------------------------
# 資料庫輔助函式 (結構不變)
# ---------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_user_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                target_display_name TEXT NOT NULL,
                event_content TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                reminder_time TEXT,
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("Database initialized.") # 加上日誌，方便在 Render Log 中確認

# 【V6 最終修正】將初始化函式移到這裡，確保在 gunicorn 啟動時也能執行
init_db()

# 初始化 API 和排程器
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
scheduler = BackgroundScheduler(timezone='Asia/Taipei')

# 【V6 最終修正】排程器也應該在這裡啟動
scheduler.start()
print("Scheduler started.")

# (中間所有函式，從 add_event 到 handle_postback 都不變，這裡省略以節省篇幅)
# 您可以從您現有的程式碼中保留那些函式

# ---------------------------------
# 核心訊息處理邏輯 (V5 - 完整重構版)
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id
    
    match = re.match(r'^提醒\s+(\S+)\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    if not match: return

    who_to_remind_text = match.group(1)
    datetime_str = match.group(2)
    content = match.group(3).strip()

    if who_to_remind_text == '我':
        target_user_id = creator_user_id
        target_display_name = "您"
    else:
        target_user_id = event.source.sender_id
        target_display_name = f"@{who_to_remind_text}"

    try:
        event_dt = parse(datetime_str, yearfirst=False)
        event_id = add_event(creator_user_id, target_user_id, target_display_name, content, event_dt)
        
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
            QuickReplyButton(action=PostbackAction(label="1小時前", data=f"action=set_reminder&id={event_id}&type=hour&val=1")),
            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
        ])

        reply_text = f"已記錄：{target_display_name} {event_dt.strftime('%Y/%m/%d %H:%M')} {content}\n\n希望什麼時候提醒您呢？"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons)
        )
    except (ValueError, Exception) as e:
        app.logger.error(f"Error during event processing: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="處理請求時發生錯誤，請檢查日期時間格式。")
        )
        
# ... handle_postback 和 send_reminder 函式請保留您原本的 ...

# ---------------------------------
# 主程式進入點 (現在只剩下 app.run)
# ---------------------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
    
    # LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
    # LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')