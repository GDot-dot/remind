# app.py

import os
import re
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort

# 官方 Line Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent,
    ConfirmTemplate, TemplateSendMessage, PostbackTemplateAction
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

# 初始化資料庫 (建立表格)
init_db()

# 設定排程器使用 PostgreSQL 作為儲存後端
jobstores = {
    'default': SQLAlchemyJobStore(url=DATABASE_URL)
}
job_defaults = {
    'coalesce': False,
    'max_instances': 3,
    'misfire_grace_time': 30
}
scheduler = BackgroundScheduler(
    jobstores=jobstores, 
    job_defaults=job_defaults,
    timezone=pytz.timezone('Asia/Taipei')
)

# 啟動排程器
try:
    scheduler.start()
    app.logger.info("Scheduler started with SQLAlchemyJobStore.")
except Exception as e:
    app.logger.error(f"Failed to start scheduler: {e}")
    # 如果排程器啟動失敗，嘗試清除舊任務後重新啟動
    try:
        scheduler.remove_all_jobs()
        scheduler.start()
        app.logger.info("Scheduler restarted after clearing jobs.")
    except Exception as e2:
        app.logger.error(f"Failed to restart scheduler: {e2}")

# 初始化 LINE Bot API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ---------------------------------
# 資料庫輔助函式 (使用 SQLAlchemy)
# ---------------------------------
def add_event(creator_id, target_id, display_name, content, event_dt):
    db_gen = get_db()
    db = next(db_gen)
    try:
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
        return event_id
    except Exception as e:
        app.logger.error(f"Error adding event: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def update_reminder_time(event_id, reminder_dt):
    db_gen = get_db()
    db = next(db_gen)
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event:
            event.reminder_time = reminder_dt
            db.commit()
            return True
    except Exception as e:
        app.logger.error(f"Error updating reminder time: {e}")
        db.rollback()
    finally:
        db.close()
    return False

def get_event(event_id):
    db_gen = get_db()
    db = next(db_gen)
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        return event
    except Exception as e:
        app.logger.error(f"Error getting event: {e}")
        return None
    finally:
        db.close()

def mark_reminder_sent(event_id):
    db_gen = get_db()
    db = next(db_gen)
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event:
            event.reminder_sent = 1
            db.commit()
            return True
    except Exception as e:
        app.logger.error(f"Error marking reminder as sent: {e}")
        db.rollback()
    finally:
        db.close()
    return False

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
            
            # 建立確認模板
            confirm_template = ConfirmTemplate(
                text=f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event_content}」喔！",
                actions=[
                    PostbackTemplateAction(
                        label="確認收到",
                        data=f"action=confirm_reminder&id={event_id}"
                    ),
                    PostbackTemplateAction(
                        label="延後5分鐘",
                        data=f"action=snooze_reminder&id={event_id}&minutes=5"
                    )
                ]
            )
            
            template_message = TemplateSendMessage(
                alt_text=f"提醒：{event_content}",
                template=confirm_template
            )
            
            try:
                line_bot_api.push_message(target_id, template_message)
                mark_reminder_sent(event_id)
                app.logger.info(f"Reminder sent successfully for event_id: {event_id}")

            except LineBotApiError as e:
                app.logger.error(f"Scheduler: Failed to push message for event_id {event_id}. Error: {e}")
        else:
            app.logger.warning(f"Skipping reminder for event_id {event_id}. Event not found or already sent.")

# ---------------------------------
# 時間解析輔助函式
# ---------------------------------
def parse_datetime(datetime_str):
    """解析各種時間格式"""
    try:
        # 常見格式
        formats = [
            '%Y/%m/%d %H:%M',
            '%Y-%m-%d %H:%M',
            '%m/%d %H:%M',
            '%m-%d %H:%M',
            '%Y/%m/%d',
            '%Y-%m-%d',
            '%m/%d',
            '%m-%d'
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(datetime_str, fmt)
                # 如果沒有年份，使用當前年份
                if dt.year == 1900:
                    current_year = datetime.now().year
                    dt = dt.replace(year=current_year)
                # 如果沒有時間，設定為當前時間
                if dt.hour == 0 and dt.minute == 0 and '%H:%M' not in fmt:
                    now = datetime.now()
                    dt = dt.replace(hour=now.hour, minute=now.minute)
                return dt
            except ValueError:
                continue
        
        # 如果以上都失敗，嘗試使用 dateutil.parser
        return parse(datetime_str, yearfirst=False)
    except Exception as e:
        app.logger.error(f"Error parsing datetime: {e}")
        return None

# ---------------------------------
# Webhook 路由
# ---------------------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        app.logger.error("No signature found in request headers")
        abort(400)
        
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body length: {len(body)}")
    
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
    
    # 更靈活的正則表達式，支援多種格式
    # 提醒 我 2025/07/15 17:20 最終測試
    # 提醒 我 7/15 17:20 最終測試
    # 提醒 我 明天 17:20 最終測試
    match = re.match(r'^提醒\s+(\S+)\s+([\d/\-\s:]+|明天|後天)\s*(\d{1,2}:\d{2})?\s+(.+)$', text)

    if not match:
        # 如果不符合格式，提供使用說明
        if text.startswith('提醒'):
            help_text = """請使用以下格式：
提醒 我 2025/07/15 17:20 做某事
提醒 我 7/15 17:20 做某事
提醒 我 明天 17:20 做某事

支援的時間格式：
- 年/月/日 時:分
- 月/日 時:分
- 明天 時:分
- 後天 時:分"""
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=help_text)
            )
        return

    who_to_remind_text = match.group(1)
    date_str = match.group(2)
    time_str = match.group(3)
    content = match.group(4).strip()

    # 處理特殊日期
    if date_str == '明天':
        tomorrow = datetime.now() + timedelta(days=1)
        date_str = tomorrow.strftime('%Y/%m/%d')
    elif date_str == '後天':
        day_after_tomorrow = datetime.now() + timedelta(days=2)
        date_str = day_after_tomorrow.strftime('%Y/%m/%d')
    
    # 組合日期和時間
    if time_str:
        datetime_str = f"{date_str} {time_str}"
    else:
        datetime_str = date_str

    # 判斷提醒對象
    if who_to_remind_text == '我':
        try:
            profile = line_bot_api.get_profile(creator_user_id)
            target_user_id = creator_user_id
            target_display_name = profile.display_name
        except LineBotApiError as e:
            app.logger.error(f"Could not get profile for user {creator_user_id}: {e}")
            target_user_id = creator_user_id
            target_display_name = "您"
    else:
        # 在群組中提醒他人的功能較複雜，這裡先簡化處理
        # 實際上需要透過 mention 或其他方式來識別用戶
        target_user_id = creator_user_id
        target_display_name = who_to_remind_text

    try:
        # 解析時間並設定時區為台北
        naive_dt = parse_datetime(datetime_str)
        if not naive_dt:
            raise ValueError("無法解析時間格式")
            
        taipei_tz = pytz.timezone('Asia/Taipei')
        
        # 檢查是否為 naive datetime
        if naive_dt.tzinfo is None:
            event_dt = taipei_tz.localize(naive_dt)
        else:
            event_dt = naive_dt.astimezone(taipei_tz)
        
        # 檢查時間是否在過去
        current_time = datetime.now(taipei_tz)
        if event_dt <= current_time:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ 提醒時間不能設定在過去喔！請重新設定。")
            )
            return

        event_id = add_event(creator_user_id, target_user_id, target_display_name, content, event_dt)
        
        if not event_id:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 建立提醒失敗，請稍後再試。")
            )
            return
        
        # 修改快捷回覆選項，加入 10分鐘前
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="10分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=10")),
            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
        ])

        reply_text = f"✅ 已記錄：{target_display_name} {event_dt.strftime('%Y/%m/%d %H:%M')} {content}\n\n希望什麼時候提醒您呢？"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons)
        )
    except Exception as e:
        app.logger.error(f"Error in handle_message: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ 處理請求時發生錯誤，請檢查時間格式是否正確。")
        )

# ---------------------------------
# Postback 事件處理
# ---------------------------------
@handler.add(PostbackEvent)
def handle_postback(event):
    try:
        data = dict(x.split('=') for x in event.postback.data.split('&'))
        action = data.get('action')
        
        if action == 'set_reminder':
            event_id = int(data.get('id'))
            reminder_type = data.get('type')
            event_record = get_event(event_id)
            
            if not event_record:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 找不到該提醒事件。")
                )
                return

            event_dt = event_record.event_datetime
            reminder_dt = None
            
            if reminder_type == 'none':
                reply_msg_text = "✅ 好的，這個事件將不設定提醒。"
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
                    
                    # 檢查提醒時間是否在過去
                    current_time = datetime.now(pytz.timezone('Asia/Taipei'))
                    if reminder_dt <= current_time:
                        line_bot_api.reply_message(
                            event.reply_token,
                            TextSendMessage(text="⚠️ 提醒時間已過，無法設定提醒。")
                        )
                        return
                    
                    try:
                        # 檢查是否已存在相同的任務
                        existing_job = scheduler.get_job(f'reminder_{event_id}')
                        if existing_job:
                            scheduler.remove_job(f'reminder_{event_id}')
                            app.logger.info(f"Removed existing job for event_id {event_id}")
                        
                        # 使用 APScheduler 新增任務
                        scheduler.add_job(
                            send_reminder, 
                            'date', 
                            run_date=reminder_dt, 
                            args=[event_id],
                            id=f'reminder_{event_id}',
                            replace_existing=True
                        )
                        app.logger.info(f"Scheduled job for event_id {event_id} at {reminder_dt}")
                        reply_msg_text = f"✅ 設定完成！將於 {reminder_dt.astimezone(pytz.timezone('Asia/Taipei')).strftime('%Y/%m/%d %H:%M')} 提醒您。"
                    except Exception as e:
                        app.logger.error(f"Error scheduling job: {e}")
                        reply_msg_text = "❌ 設定提醒時發生錯誤。"
                else:
                    reply_msg_text = "❌ 設定提醒時發生未知的錯誤。"
            
            if update_reminder_time(event_id, reminder_dt):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg_text))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 更新提醒時間失敗。"))
        
        elif action == 'confirm_reminder':
            event_id = int(data.get('id'))
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="✅ 提醒已確認收到！")
            )
            
        elif action == 'snooze_reminder':
            event_id = int(data.get('id'))
            minutes = int(data.get('minutes', 5))
            
            # 重新排程提醒
            snooze_time = datetime.now(pytz.timezone('Asia/Taipei')) + timedelta(minutes=minutes)
            
            try:
                # 檢查是否已存在相同的任務
                existing_job = scheduler.get_job(f'snooze_{event_id}')
                if existing_job:
                    scheduler.remove_job(f'snooze_{event_id}')
                
                scheduler.add_job(
                    send_reminder,
                    'date',
                    run_date=snooze_time,
                    args=[event_id],
                    id=f'snooze_{event_id}',
                    replace_existing=True
                )
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"⏰ 好的，{minutes}分鐘後再次提醒您！")
                )
            except Exception as e:
                app.logger.error(f"Error scheduling snooze: {e}")
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 延後提醒設定失敗。")
                )
                
    except Exception as e:
        app.logger.error(f"Error in handle_postback: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ 處理請求時發生錯誤。")
        )

# ---------------------------------
# 健康檢查端點
# ---------------------------------
@app.route("/health", methods=['GET'])
def health_check():
    return {"status": "healthy", "scheduler": scheduler.running}

# ---------------------------------
# 主程式進入點
# ---------------------------------
if __name__ == "__main__":
    # 為了讓 ngrok 在本地測試時能運作
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)