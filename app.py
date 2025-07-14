import os
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent
)
from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.parser import parse
import sqlite3

# --- 初始化設定 ---
app = Flask(__name__)

# 從環境變數取得 Line Bot 憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
scheduler = BackgroundScheduler(timezone='Asia/Taipei') # 設定時區

DB_PATH = 'reminders.db'

# --- 資料庫輔助函式 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_user_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                event_content TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                reminder_time TEXT,
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def add_event(creator_id, target_id, content, event_dt):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (creator_user_id, target_user_id, event_content, event_datetime) VALUES (?, ?, ?, ?)",
            (creator_id, target_id, content, event_dt.isoformat())
        )
        conn.commit()
        return cursor.lastrowid # 返回新事件的 ID

def update_reminder_time(event_id, reminder_dt):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE events SET reminder_time = ? WHERE id = ?",
            (reminder_dt.isoformat(), event_id)
        )
        conn.commit()

def get_event(event_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        return cursor.fetchone()

# --- 排程任務 ---
def send_reminder(event_id):
    print(f"Executing reminder for event_id: {event_id}")
    event = get_event(event_id)
    if event and not event['reminder_sent']:
        target_user_id = event['target_user_id']
        event_dt = datetime.fromisoformat(event['event_datetime'])
        
        # 取得被提醒者的個人資料，來顯示名字
        try:
            profile = line_bot_api.get_profile(target_user_id)
            display_name = profile.display_name
        except:
            display_name = "您"

        reminder_message = f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event['event_content']}」喔！"
        
        line_bot_api.push_message(
            target_user_id,
            TextSendMessage(text=reminder_message)
        )
        
        # 更新資料庫狀態
        with sqlite3.connect(DB_PATH) as conn:
            conn.cursor().execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (event_id,))
            conn.commit()
        print(f"Reminder sent successfully for event_id: {event_id}")

# --- Webhook 路由 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 訊息事件處理 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id # 先取得發話者ID

    # 1. 解析【群組用】指令: "提醒 @用戶 日期 時間 事項"
    match_group = re.match(r'^提醒\s+@\S+\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    # 2. 解析【一對一用】指令: "提醒我 日期 時間 事項"
    match_private = re.match(r'^提醒我\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    if match_group and hasattr(event.message, 'mention'):
        # --- 原本的群組邏輯 ---
        mentioned_user_id = event.message.mention.mentionees[0].user_id
        datetime_str = match_group.group(1)
        content = match_group.group(2).strip()
        target_user_id = mentioned_user_id
        target_display_name_prefix = f"@{line_bot_api.get_profile(target_user_id).display_name}"
        
    elif match_private:
        # --- 新增的一對一邏輯 ---
        datetime_str = match_private.group(1)
        content = match_private.group(2).strip()
        target_user_id = creator_user_id # 提醒的對象就是發話者自己
        target_display_name_prefix = "您"

    else:
        # 如果兩種格式都不符合，就直接結束
        return

    # --- 共通的處理邏輯 ---
    try:
        event_dt = parse(datetime_str, yearfirst=False)
        event_id = add_event(creator_user_id, target_user_id, content, event_dt)
        
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
            QuickReplyButton(action=PostbackAction(label="1小時前", data=f"action=set_reminder&id={event_id}&type=hour&val=1")),
            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
        ])

        reply_text = f"已記錄：{target_display_name_prefix} {event_dt.strftime('%Y/%m/%d %H:%M')} {content}\n\n希望什麼時候提醒您呢？"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons)
        )

    except ValueError:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="日期時間格式好像不太對喔！\n請試試 '月/日 小時:分鐘'，例如: 7/14 14:00")
        )

# --- Postback 事件處理 (處理按鈕點擊) ---
@handler.add(PostbackEvent)
def handle_postback(event):
    # 解析 postback data, e.g., "action=set_reminder&id=123&type=day&val=1"
    data = dict(x.split('=') for x in event.postback.data.split('&'))
    
    action = data.get('action')
    if action == 'set_reminder':
        event_id = int(data.get('id'))
        reminder_type = data.get('type')
        
        event_record = get_event(event_id)
        if not event_record:
            return # 如果找不到事件，直接忽略

        event_dt = datetime.fromisoformat(event_record['event_datetime'])
        
        reply_msg_text = "設定完成！"

        if reminder_type == 'none':
            reply_msg_text = "好的，這個事件將不設定提醒。"
        else:
            value = int(data.get('val'))
            if reminder_type == 'day':
                reminder_dt = event_dt - timedelta(days=value)
            elif reminder_type == 'hour':
                reminder_dt = event_dt - timedelta(hours=value)
            elif reminder_type == 'minute':
                reminder_dt = event_dt - timedelta(minutes=value)
            
            # 更新資料庫並加入排程
            update_reminder_time(event_id, reminder_dt)
            scheduler.add_job(send_reminder, 'date', run_date=reminder_dt, args=[event_id])
            
            reply_msg_text = f"設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
        
        # 回覆使用者，告知設定結果
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg_text)
        )

# --- 主程式進入點 ---
if __name__ == "__main__":
    init_db()  # 啟動時檢查並建立資料庫
    scheduler.start() # 啟動排程器
    # 這裡的 host 和 port 根據您的部署環境調整
    # ngrok 或其他穿透服務需要將流量導到這個 port
=======
import os
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent
)
from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.parser import parse
import sqlite3

# --- 初始化設定 ---
app = Flask(__name__)

# 從環境變數取得 Line Bot 憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
scheduler = BackgroundScheduler(timezone='Asia/Taipei') # 設定時區

DB_PATH = 'reminders.db'

# --- 資料庫輔助函式 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_user_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                event_content TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                reminder_time TEXT,
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def add_event(creator_id, target_id, content, event_dt):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (creator_user_id, target_user_id, event_content, event_datetime) VALUES (?, ?, ?, ?)",
            (creator_id, target_id, content, event_dt.isoformat())
        )
        conn.commit()
        return cursor.lastrowid # 返回新事件的 ID

def update_reminder_time(event_id, reminder_dt):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE events SET reminder_time = ? WHERE id = ?",
            (reminder_dt.isoformat(), event_id)
        )
        conn.commit()

def get_event(event_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        return cursor.fetchone()

# --- 排程任務 ---
def send_reminder(event_id):
    print(f"Executing reminder for event_id: {event_id}")
    event = get_event(event_id)
    if event and not event['reminder_sent']:
        target_user_id = event['target_user_id']
        event_dt = datetime.fromisoformat(event['event_datetime'])
        
        # 取得被提醒者的個人資料，來顯示名字
        try:
            profile = line_bot_api.get_profile(target_user_id)
            display_name = profile.display_name
        except:
            display_name = "您"

        reminder_message = f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event['event_content']}」喔！"
        
        line_bot_api.push_message(
            target_user_id,
            TextSendMessage(text=reminder_message)
        )
        
        # 更新資料庫狀態
        with sqlite3.connect(DB_PATH) as conn:
            conn.cursor().execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (event_id,))
            conn.commit()
        print(f"Reminder sent successfully for event_id: {event_id}")

# --- Webhook 路由 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# --- 訊息事件處理 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id # 先取得發話者ID

    # 1. 解析【群組用】指令: "提醒 @用戶 日期 時間 事項"
    match_group = re.match(r'^提醒\s+@\S+\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    # 2. 解析【一對一用】指令: "提醒我 日期 時間 事項"
    match_private = re.match(r'^提醒我\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    if match_group and hasattr(event.message, 'mention'):
        # --- 原本的群組邏輯 ---
        mentioned_user_id = event.message.mention.mentionees[0].user_id
        datetime_str = match_group.group(1)
        content = match_group.group(2).strip()
        target_user_id = mentioned_user_id
        target_display_name_prefix = f"@{line_bot_api.get_profile(target_user_id).display_name}"
        
    elif match_private:
        # --- 新增的一對一邏輯 ---
        datetime_str = match_private.group(1)
        content = match_private.group(2).strip()
        target_user_id = creator_user_id # 提醒的對象就是發話者自己
        target_display_name_prefix = "您"

    else:
        # 如果兩種格式都不符合，就直接結束
        return

    # --- 共通的處理邏輯 ---
    try:
        event_dt = parse(datetime_str, yearfirst=False)
        event_id = add_event(creator_user_id, target_user_id, content, event_dt)
        
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
            QuickReplyButton(action=PostbackAction(label="1小時前", data=f"action=set_reminder&id={event_id}&type=hour&val=1")),
            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
        ])

        reply_text = f"已記錄：{target_display_name_prefix} {event_dt.strftime('%Y/%m/%d %H:%M')} {content}\n\n希望什麼時候提醒您呢？"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons)
        )

    except ValueError:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="日期時間格式好像不太對喔！\n請試試 '月/日 小時:分鐘'，例如: 7/14 14:00")
        )

# --- Postback 事件處理 (處理按鈕點擊) ---
@handler.add(PostbackEvent)
def handle_postback(event):
    # 解析 postback data, e.g., "action=set_reminder&id=123&type=day&val=1"
    data = dict(x.split('=') for x in event.postback.data.split('&'))
    
    action = data.get('action')
    if action == 'set_reminder':
        event_id = int(data.get('id'))
        reminder_type = data.get('type')
        
        event_record = get_event(event_id)
        if not event_record:
            return # 如果找不到事件，直接忽略

        event_dt = datetime.fromisoformat(event_record['event_datetime'])
        
        reply_msg_text = "設定完成！"

        if reminder_type == 'none':
            reply_msg_text = "好的，這個事件將不設定提醒。"
        else:
            value = int(data.get('val'))
            if reminder_type == 'day':
                reminder_dt = event_dt - timedelta(days=value)
            elif reminder_type == 'hour':
                reminder_dt = event_dt - timedelta(hours=value)
            elif reminder_type == 'minute':
                reminder_dt = event_dt - timedelta(minutes=value)
            
            # 更新資料庫並加入排程
            update_reminder_time(event_id, reminder_dt)
            scheduler.add_job(send_reminder, 'date', run_date=reminder_dt, args=[event_id])
            
            reply_msg_text = f"設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
        
        # 回覆使用者，告知設定結果
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg_text)
        )

# --- 主程式進入點 ---
if __name__ == "__main__":
    init_db()  # 啟動時檢查並建立資料庫
    scheduler.start() # 啟動排程器
    # 這裡的 host 和 port 根據您的部署環境調整
    # ngrok 或其他穿透服務需要將流量導到這個 port
>>>>>>> ed7162ff959226e1bfd720a61fedb89aa53b5c59
    app.run(host='0.0.0.0', port=5001)