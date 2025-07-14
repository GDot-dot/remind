# app.py

import os
import re
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort, logging

# 官方 Line Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent
)

# 排程與日期工具
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from dateutil.parser import parse
import pytz

# 從我們自訂的 db 模組匯入
from db import init_db, get_db, Event

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)

# 從 Render 的環境變數讀取憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN','J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')
DATABASE_URL = os.getenv('DATABASE_URL','postgresql://remine_db_user:Gxehx2wmgDUSZenytj4Sd4r0Z6UHE4Xp@dpg-d1qcfms9c44c739ervm0-a/remine_db')

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, DATABASE_URL]):
    app.logger.error("Required environment variables are not set.")
    # 在實際部署中，你可能希望應用程式在此處停止啟動
    # raise ValueError("Missing critical environment variables")

# 初始化資料庫 (建立表格)
init_db()

# 設定排程器使用 PostgreSQL 作為儲存後端
jobstores = {
    'default': SQLAlchemyJobStore(url=DATABASE_URL)
}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone=pytz.timezone('Asia/Taipei'))

# 啟動排程器
scheduler.start()
app.logger.info("Scheduler started with SQLAlchemyJobStore.")

# 初始化 LINE Bot API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# ---------------------------------
# 資料庫輔助函式 (使用 SQLAlchemy)
# ---------------------------------
def add_event(creator_id, target_id, display_name, content, event_dt):
    db_gen = get_db()
    db = next(db_gen)
    new_event = Event(
        creator_user_id=creator_id,
        target_user_id=target_id,
        target_display_name=display_name,
        event_content=content,
        event_datetime=event_dt
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    event_id = new_event.id
    db.close()
    return event_id

def update_reminder_time(event_id, reminder_dt):
    db_gen = get_db()
    db = next(db_gen)
    event = db.query(Event).filter(Event.id == event_id).first()
    if event:
        event.reminder_time = reminder_dt
        db.commit()
    db.close()

def get_event(event_id):
    db_gen = get_db()
    db = next(db_gen)
    event = db.query(Event).filter(Event.id == event_id).first()
    db.close()
    return event

# ---------------------------------
# 排程任務
# ---------------------------------
def send_reminder(event_id):
    # 在排程任務中，我們需要重新初始化 app context 以便存取資料庫和 log
    with app.app_context():
        app.logger.info(f"Executing reminder for event_id: {event_id}")
        event = get_event(event_id)
        if event and not event.reminder_sent:
            target_id = event.target_user_id
            display_name = event.target_display_name
            event_dt = event.event_datetime.astimezone(pytz.timezone('Asia/Taipei'))
            event_content = event.event_content
            reminder_message = f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event_content}」喔！"
            
            try:
                line_bot_api.push_message(target_id, TextSendMessage(text=reminder_message))
                
                # 更新資料庫中的發送狀態
                db_gen = get_db()
                db = next(db_gen)
                db_event = db.query(Event).filter(Event.id == event_id).first()
                if db_event:
                    db_event.reminder_sent = 1
                    db.commit()
                db.close()
                app.logger.info(f"Reminder sent successfully for event_id: {event_id}")

            except LineBotApiError as e:
                app.logger.error(f"Scheduler: Failed to push message for event_id {event_id}. Error: {e}")
        else:
             app.logger.warning(f"Skipping reminder for event_id {event_id}. Event not found or already sent.")

# ---------------------------------
# Webhook 路由
# ---------------------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body: {body}")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Please check your channel secret.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error occurred in callback: {e}")
        abort(500)
    return 'OK'

# ---------------------------------
# 核心訊息處理邏輯
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    creator_user_id = event.source.user_id
    
    # 提醒 我 2025/07/15 17:20 最終測試
    match = re.match(r'^提醒\s+(\S+)\s+([\d/\s:]+)\s+(.+)$', text)

    if not match: return

    who_to_remind_text = match.group(1)
    datetime_str = match.group(2)
    content = match.group(3).strip()

    if who_to_remind_text == '我':
        try:
            profile = line_bot_api.get_profile(creator_user_id)
            target_user_id = creator_user_id
            target_display_name = profile.display_name
        except LineBotApiError as e:
            app.logger.error(f"Could not get profile for user {creator_user_id}: {e}")
            target_user_id = creator_user_id
            target_display_name = "您" # 備用名稱
    else:
        # 注意：在群組中，你無法輕易透過 @名字 取得 userID，這裡先簡化處理
        target_user_id = creator_user_id # 暫時先提醒自己，這部分功能較複雜
        target_display_name = who_to_remind_text

    try:
        # 解析時間並設定時區為台北
        naive_dt = parse(datetime_str, yearfirst=False)
        taipei_tz = pytz.timezone('Asia/Taipei')
        event_dt = taipei_tz.localize(naive_dt)

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
    except Exception as e:
        app.logger.error(f"Error in handle_message: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="處理請求時發生錯誤，請檢查指令格式。")
        )

# ---------------------------------
# Postback 事件處理
# ---------------------------------
@handler.add(PostbackEvent)
def handle_postback(event):
    data = dict(x.split('=') for x in event.postback.data.split('&'))
    action = data.get('action')
    if action == 'set_reminder':
        event_id = int(data.get('id'))
        reminder_type = data.get('type')
        event_record = get_event(event_id)
        if not event_record: return

        event_dt = event_record.event_datetime
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
                # 使用 APScheduler 新增任務
                scheduler.add_job(
                    send_reminder, 
                    'date', 
                    run_date=reminder_dt, 
                    args=[event_id],
                    id=f'reminder_{event_id}', # 給予一個唯一的 job ID
                    replace_existing=True # 如果已存在相同ID的任務，則替換它
                )
                app.logger.info(f"Scheduled job for event_id {event_id} at {reminder_dt}")
                reply_msg_text = f"設定完成！將於 {reminder_dt.astimezone(pytz.timezone('Asia/Taipei')).strftime('%Y/%m/%d %H:%M')} 提醒您。"
            else:
                reply_msg_text = "設定提醒時發生未知的錯誤。"
        
        update_reminder_time(event_id, reminder_dt)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg_text))

# ---------------------------------
# 主程式進入點
# ---------------------------------
if __name__ == "__main__":
    # 為了讓 ngrok 在本地測試時能運作
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
    
    # LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
    # LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')