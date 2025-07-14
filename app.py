# ---------------------------------
# 匯入必要的函式庫
# ---------------------------------
import os
import re
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, abort

# 官方 Line Bot SDK，包含重要的例外類別
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent
)

# 強大的排程與日期解析工具
from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.parser import parse

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)


# LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
# LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')
# 從 Render 的環境變數讀取憑證，這是雲端部署的標準做法
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')

# 初始化 Line Bot API 和 Webhook Handler
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化背景排程器，並設定時區為台北
scheduler = BackgroundScheduler(timezone='Asia/Taipei')

# 資料庫檔案路徑
DB_PATH = 'reminders.db'

# ---------------------------------
# 資料庫輔助函式 (Database Helpers)
# ---------------------------------
def init_db():
    """初始化資料庫，如果 events 資料表不存在就建立它"""
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
    """新增一筆事件到資料庫"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (creator_user_id, target_user_id, event_content, event_datetime) VALUES (?, ?, ?, ?)",
            (creator_id, target_id, content, event_dt.isoformat())
        )
        conn.commit()
        return cursor.lastrowid

def update_reminder_time(event_id, reminder_dt):
    """更新事件的提醒時間 (如果 reminder_dt 是 None，則存入 NULL)"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE events SET reminder_time = ? WHERE id = ?",
            (reminder_dt.isoformat() if reminder_dt else None, event_id)
        )
        conn.commit()

def get_event(event_id):
    """根據 ID 獲取單一事件"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        return cursor.fetchone()

# ---------------------------------
# 排程任務 (Scheduler Job)
# ---------------------------------
def send_reminder(event_id):
    """從資料庫讀取事件並發送提醒訊息"""
    print(f"Executing reminder for event_id: {event_id}")
    event = get_event(event_id)
    if event and not event['reminder_sent']:
        target_user_id = event['target_user_id']
        event_dt = datetime.fromisoformat(event['event_datetime'])
        
        display_name = "您" # 預設稱呼
        try:
            profile = line_bot_api.get_profile(target_user_id)
            display_name = profile.display_name
        except LineBotApiError as e:
            print(f"Scheduler: Failed to get profile for {target_user_id}. Error: {e}")

        reminder_message = f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event['event_content']}」喔！"
        
        try:
            line_bot_api.push_message(
                target_user_id,
                TextSendMessage(text=reminder_message)
            )
            # 只有在成功發送後，才更新資料庫狀態
            with sqlite3.connect(DB_PATH) as conn:
                conn.cursor().execute("UPDATE events SET reminder_sent = 1 WHERE id = ?", (event_id,))
                conn.commit()
            print(f"Reminder sent successfully for event_id: {event_id}")
        except LineBotApiError as e:
            print(f"Scheduler: Failed to push message for event_id {event_id}. Error: {e}")

# ---------------------------------
# Webhook 路由 (Flask Route)
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
    except LineBotApiError as e:
        app.logger.error(f"LineBotApiError occurred: {e}")
        abort(500)
    return 'OK'

# ---------------------------------
# 核心訊息處理邏輯
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id

    # 【最終版正規表達式】
    # 群組用：不再匹配 @用戶名
    match_group = re.match(r'^提醒\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)
    # 一對一用
    match_private = re.match(r'^提醒我\s+([\d/]+\s+[\d:]+)\s+(.+)$', text)

    # 初始化變數
    target_user_id, target_display_name_prefix, datetime_str, content = None, "", "", ""

    # 1. 判斷是否為【群組指令】
    # 條件：訊息中有提及資料(@) 且 文字格式符合 "提醒 日期 事項"
    if event.message.mention and event.message.mention.mentionees and match_group:
        mentioned = event.message.mention.mentionees[0]
        # 【健壯性修改】排除 @All 等沒有 user_id 的特殊提及
        if hasattr(mentioned, 'user_id'):
            target_user_id = mentioned.user_id
            datetime_str = match_group.group(1)
            content = match_group.group(2).strip()
            
            # 【健壯性修改】嘗試取得對方名字，如果失敗 (非好友) 則使用通用稱呼
            try:
                profile = line_bot_api.get_profile(target_user_id)
                target_display_name_prefix = f"@{profile.display_name}"
            except LineBotApiError:
                target_display_name_prefix = "@被提醒的人" # 安全的備用稱呼

    # 2. 判斷是否為【一對一指令】
    elif match_private:
        target_user_id = creator_user_id # 提醒對象就是自己
        datetime_str = match_private.group(1)
        content = match_private.group(2).strip()
        target_display_name_prefix = "您"

    # 如果兩種指令格式都不符合，就直接結束，不回應
    else:
        return

    # --- 共通的處理邏輯 ---
    try:
        # 確保前面的邏輯已成功解析出 target_user_id
        if target_user_id and datetime_str:
            event_dt = parse(datetime_str, yearfirst=False)
            event_id = add_event(creator_user_id, target_user_id, content, event_dt)
            
            # 建立快速回覆按鈕
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
    except (ValueError, Exception) as e:
        app.logger.error(f"Error during event processing: {e}")
        # 可以在此回覆一條通用的錯誤訊息給使用者
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="處理您的請求時發生錯誤，請檢查日期時間格式或指令是否正確。")
        )

# ---------------------------------
# Postback 事件處理 (處理按鈕點擊)
# ---------------------------------
@handler.add(PostbackEvent)
def handle_postback(event):
    data = dict(x.split('=') for x in event.postback.data.split('&'))
    
    action = data.get('action')
    if action == 'set_reminder':
        event_id = int(data.get('id'))
        reminder_type = data.get('type')
        
        event_record = get_event(event_id)
        if not event_record:
            return

        event_dt = datetime.fromisoformat(event_record['event_datetime'])
        reminder_dt = None # 預設不提醒
        reply_msg_text = ""
        
        if reminder_type == 'none':
            reply_msg_text = "好的，這個事件將不設定提醒。"
        else:
            value = int(data.get('val'))
            delta = timedelta()
            if reminder_type == 'day':
                delta = timedelta(days=value)
            elif reminder_type == 'hour':
                delta = timedelta(hours=value)
            elif reminder_type == 'minute':
                delta = timedelta(minutes=value)
            
            if delta:
                reminder_dt = event_dt - delta
                # 只有在成功計算出提醒時間後，才加入排程
                scheduler.add_job(send_reminder, 'date', run_date=reminder_dt, args=[event_id])
                reply_msg_text = f"設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
            else:
                reply_msg_text = "設定提醒時發生未知的錯誤。"

        # 更新資料庫中的提醒時間 (即使是 None 也要更新)
        update_reminder_time(event_id, reminder_dt)

        # 回覆使用者最終設定結果
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_msg_text)
        )

# ---------------------------------
# 主程式進入點
# ---------------------------------
if __name__ == "__main__":
    init_db()
    scheduler.start()
    # 為了適應 Render 的部署環境，從環境變數讀取 PORT
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
    
    # LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
    # LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')