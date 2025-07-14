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

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
scheduler = BackgroundScheduler(timezone='Asia/Taipei')
DB_PATH = 'reminders.db'

# ---------------------------------
# 資料庫輔助函式 (已更新結構)
# ---------------------------------
def init_db():
    """初始化資料庫，如果 events 資料表不存在就建立它 (包含新的 target_display_name 欄位)"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_user_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                target_display_name TEXT NOT NULL,  -- 【新欄位】儲存提醒時的稱呼
                event_content TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                reminder_time TEXT,
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def add_event(creator_id, target_id, display_name, content, event_dt):
    """新增一筆事件到資料庫 (包含新的 display_name)"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (creator_user_id, target_user_id, target_display_name, event_content, event_datetime) VALUES (?, ?, ?, ?, ?)",
            (creator_id, target_id, display_name, content, event_dt.isoformat())
        )
        conn.commit()
        return cursor.lastrowid

# ... update_reminder_time 和 get_event 函式不變 ...
def update_reminder_time(event_id, reminder_dt):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE events SET reminder_time = ? WHERE id = ?", (reminder_dt.isoformat() if reminder_dt else None, event_id))
        conn.commit()

def get_event(event_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        return cursor.fetchone()

# ---------------------------------
# 排程任務 (已更新邏輯)
# ---------------------------------
def send_reminder(event_id):
    """發送提醒訊息，稱呼來自資料庫"""
    print(f"Executing reminder for event_id: {event_id}")
    event = get_event(event_id)
    if event and not event['reminder_sent']:
        target_id = event['target_user_id']
        display_name = event['target_display_name']
        event_dt = datetime.fromisoformat(event['event_datetime'])
        event_content = event['event_content']

        # 組裝提醒訊息
        reminder_message = f"⏰ 提醒！\n\n{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event_content}」喔！"
        
        try:
            line_bot_api.push_message(target_id, TextSendMessage(text=reminder_message))
            
            with sqlite3.connect(DB_PATH) as conn:
                conn.cursor().execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (event_id,))
                conn.commit()
            print(f"Reminder sent successfully for event_id: {event_id}")
        except LineBotApiError as e:
            print(f"Scheduler: Failed to push message for event_id {event_id}. Error: {e}")

# ---------------------------------
# Webhook 路由 (不變)
# ---------------------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ---------------------------------
# 核心訊息處理邏輯 (V5 - 完整重構版)
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id
    
    # 【全新統一版正規表達式】匹配 "提醒 [誰] [日期時間] [事項]"
    match = re.match(r'^提醒\s+(\S+)\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    if not match:
        return # 不符合格式，直接忽略

    # 從正規表達式中解析資訊
    who_to_remind_text = match.group(1)   # 文字 "我" 或 "小明" 或 "大家"
    datetime_str = match.group(2)       # 文字 "7/15 15:30"
    content = match.group(3).strip()    # 文字 "喝水"

    # 根據 [誰] 來決定提醒的目標 ID 和顯示名稱
    if who_to_remind_text == '我':
        target_user_id = creator_user_id
        target_display_name = "您" # 提醒訊息中會顯示「您」
    else:
        # 在群組中提醒別人，目標就是這個群組本身
        target_user_id = event.source.sender_id
        target_display_name = f"@{who_to_remind_text}" # 提醒訊息中會顯示「@小明」

    # --- 共通的處理邏輯 ---
    try:
        event_dt = parse(datetime_str, yearfirst=False)
        # 將所有需要的資訊存入資料庫
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

# ... handle_postback 函式不變 ...
@handler.add(PostbackEvent)
def handle_postback(event):
    data = dict(x.split('=') for x in event.postback.data.split('&'))
    action = data.get('action')
    if action == 'set_reminder':
        event_id = int(data.get('id'))
        reminder_type = data.get('type')
        event_record = get_event(event_id)
        if not event_record: return

        event_dt = datetime.fromisoformat(event_record['event_datetime'])
        reminder_dt = None
        
        if reminder_type == 'none':
            reply_msg_text = "好的，這個事件將不設定提醒。"
        else:
            value = int(data.get('val'))
            delta = timedelta()
            if reminder_type == 'day': delta = timedelta(days=value)
            elif reminder_type == 'hour': delta = timedelta(hours=value)
            elif reminder_type == 'minute': delta = timedelta(minutes=value)
            
            if delta:
                reminder_dt = event_dt - delta
                scheduler.add_job(send_reminder, 'date', run_date=reminder_dt, args=[event_id])
                reply_msg_text = f"設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
            else:
                reply_msg_text = "設定提醒時發生未知的錯誤。"
        
        update_reminder_time(event_id, reminder_dt)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg_text))

# ---------------------------------
# 主程式進入點 (不變)
# ---------------------------------
if __name__ == "__main__":
    init_db()
    scheduler.start()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
    
    # LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
    # LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')