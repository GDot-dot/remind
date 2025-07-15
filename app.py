# app.py (完整修復版本)

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort
import logging

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
from apscheduler.executors.pool import ThreadPoolExecutor
from dateutil.parser import parse
import pytz

# 從我們自訂的 db 模組匯入
from db import init_db, get_db, Event, safe_db_operation, cleanup_db

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)

# 設定日誌等級
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 從環境變數讀取憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')

# 檢查必要環境變數
if not LINE_CHANNEL_ACCESS_TOKEN:
    logger.error("LINE_CHANNEL_ACCESS_TOKEN is not set")
    exit(1)
if not LINE_CHANNEL_SECRET:
    logger.error("LINE_CHANNEL_SECRET is not set")
    exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL is not set")
    exit(1)

# 設定時區常數
TAIPEI_TZ = pytz.timezone('Asia/Taipei')
UTC_TZ = pytz.UTC


jobstores = {
    'default': SQLAlchemyJobStore(url=DATABASE_URL)
}

# 優化執行器設定
executors = {
    'default': ThreadPoolExecutor(max_workers=1)  # 降低為1個工作線程
}

job_defaults = {
    'coalesce': True,
    'max_instances': 1,
    'misfire_grace_time': 30
}

# 線程鎖
scheduler_lock = threading.Lock()

# 重要：排程器使用UTC時區
scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone=UTC_TZ  # 使用UTC時區
)

# 安全啟動排程器
def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully with UTC timezone")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

# 初始化
try:
    init_db()
    safe_start_scheduler()
    logger.info("Application initialized successfully")
except Exception as e:
    logger.error(f"Initialization failed: {e}")
    exit(1)

# 初始化 LINE Bot API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ---------------------------------
# 資料庫輔助函式
# ---------------------------------
def add_event(creator_id, target_id, display_name, content, event_dt):
    """添加事件到資料庫"""
    def _add_event():
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
            return new_event.id
        except Exception as e:
            db.rollback()
            logger.error(f"Error adding event: {e}")
            raise
        finally:
            db.close()

    try:
        return safe_db_operation(_add_event)
    except Exception as e:
        logger.error(f"Failed to add event: {e}")
        return None

def update_reminder_time(event_id, reminder_dt):
    """更新提醒時間"""
    def _update_reminder():
        db_gen = get_db()
        db = next(db_gen)
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_time = reminder_dt
                db.commit()
                return True
            return False
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating reminder time: {e}")
            raise
        finally:
            db.close()

    try:
        return safe_db_operation(_update_reminder)
    except Exception as e:
        logger.error(f"Failed to update reminder time: {e}")
        return False

def get_event(event_id):
    """獲取事件資料"""
    def _get_event():
        db_gen = get_db()
        db = next(db_gen)
        try:
            return db.query(Event).filter(Event.id == event_id).first()
        finally:
            db.close()

    try:
        return safe_db_operation(_get_event)
    except Exception as e:
        logger.error(f"Failed to get event: {e}")
        return None

def mark_reminder_sent(event_id):
    """標記提醒已發送"""
    def _mark_sent():
        db_gen = get_db()
        db = next(db_gen)
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_sent = 1
                db.commit()
                return True
            return False
        except Exception as e:
            db.rollback()
            logger.error(f"Error marking reminder as sent: {e}")
            raise
        finally:
            db.close()

    try:
        return safe_db_operation(_mark_sent)
    except Exception as e:
        logger.error(f"Failed to mark reminder as sent: {e}")
        return False

def reset_reminder_sent_status(event_id):
    """重置提醒發送狀態"""
    def _reset_status():
        db_gen = get_db()
        db = next(db_gen)
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_sent = 0
                db.commit()
                return True
            return False
        except Exception as e:
            db.rollback()
            logger.error(f"Error resetting reminder status: {e}")
            raise
        finally:
            db.close()

    try:
        return safe_db_operation(_reset_status)
    except Exception as e:
        logger.error(f"Failed to reset reminder status: {e}")
        return False

# ---------------------------------
# 排程任務
# ---------------------------------
def send_reminder(event_id):
    """發送提醒"""
    try:
        with app.app_context():
            logger.info(f"Executing reminder for event_id: {event_id}")
            logger.info(f"Current UTC time: {datetime.now(UTC_TZ)}")
            logger.info(f"Current Taipei time: {datetime.now(TAIPEI_TZ)}")
            
            event = get_event(event_id)
            if not event or event.reminder_sent:
                logger.warning(f"Skipping reminder for event_id {event_id}")
                return
            
            target_id = event.target_user_id
            display_name = event.target_display_name
            
            # 確保事件時間是台北時區
            event_dt = event.event_datetime
            if event_dt.tzinfo is None:
                event_dt = TAIPEI_TZ.localize(event_dt)
            else:
                event_dt = event_dt.astimezone(TAIPEI_TZ)
            
            event_content = event.event_content
            
            logger.info(f"Sending reminder to {target_id} for event at {event_dt}")
            
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
            
            # 發送訊息
            line_bot_api.push_message(target_id, template_message)
            mark_reminder_sent(event_id)
            logger.info(f"Reminder sent successfully for event_id: {event_id}")
            
    except Exception as e:
        logger.error(f"Error in send_reminder for event_id {event_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ---------------------------------
# 安全的排程器操作
# ---------------------------------
def safe_add_job(func, run_date, args, job_id):
    """安全地添加任務到排程器"""
    try:
        with scheduler_lock:
            if not scheduler.running:
                safe_start_scheduler()
            
            # 移除現有任務
            try:
                scheduler.remove_job(job_id)
            except:
                pass
            
            # 確保使用UTC時間給排程器
            if run_date.tzinfo is None:
                run_date_utc = UTC_TZ.localize(run_date)
            else:
                run_date_utc = run_date.astimezone(UTC_TZ)
            
            # 添加新任務
            scheduler.add_job(
                func,
                'date',
                run_date=run_date_utc,
                args=args,
                id=job_id,
                replace_existing=True
            )
            
            # 日誌顯示兩種時間格式
            taipei_time = run_date_utc.astimezone(TAIPEI_TZ)
            logger.info(f"Successfully scheduled job: {job_id}")
            logger.info(f"  UTC time: {run_date_utc}")
            logger.info(f"  Taipei time: {taipei_time}")
            
            return True
    except Exception as e:
        logger.error(f"Error scheduling job {job_id}: {e}")
        return False

# ---------------------------------
# 時間解析輔助函式
# ---------------------------------
def parse_datetime(datetime_str):
    """解析各種時間格式"""
    try:
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
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                if dt.hour == 0 and dt.minute == 0 and '%H:%M' not in fmt:
                    now = datetime.now()
                    dt = dt.replace(hour=now.hour, minute=now.minute)
                return dt
            except ValueError:
                continue
        
        return parse(datetime_str, yearfirst=False)
    except Exception as e:
        logger.error(f"Error parsing datetime '{datetime_str}': {e}")
        return None

# ---------------------------------
# Webhook 路由
# ---------------------------------
@app.route("/callback", methods=['POST'])
def callback():
    """處理 LINE Webhook 回調"""
    try:
        signature = request.headers.get('X-Line-Signature')
        if not signature:
            logger.error("No signature found")
            abort(400)
            
        body = request.get_data(as_text=True)
        handler.handle(body, signature)
        return 'OK'
        
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        abort(500)

# ---------------------------------
# 核心訊息處理邏輯
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理文字訊息"""
    try:
        text = event.message.text.strip()
        creator_user_id = event.source.user_id
        
        # 提供使用說明
        if text.startswith('提醒') and not re.match(r'^提醒\s+(\S+)\s+([\d/\-\s:]+|明天|後天)\s*(\d{1,2}:\d{2})?\s+(.+)$', text):
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

        # 解析提醒指令
        match = re.match(r'^提醒\s+(\S+)\s+([\d/\-\s:]+|明天|後天)\s*(\d{1,2}:\d{2})?\s+(.+)$', text)
        if not match:
            return

        who_to_remind_text = match.group(1)
        date_str = match.group(2)
        time_str = match.group(3)
        content = match.group(4).strip()

        # 處理特殊日期
        if date_str == '明天':
            tomorrow = datetime.now(TAIPEI_TZ) + timedelta(days=1)
            date_str = tomorrow.strftime('%Y/%m/%d')
        elif date_str == '後天':
            day_after_tomorrow = datetime.now(TAIPEI_TZ) + timedelta(days=2)
            date_str = day_after_tomorrow.strftime('%Y/%m/%d')
        
        # 組合日期和時間
        datetime_str = f"{date_str} {time_str}" if time_str else date_str

        # 判斷提醒對象
        if who_to_remind_text == '我':
            try:
                profile = line_bot_api.get_profile(creator_user_id)
                target_user_id = creator_user_id
                target_display_name = profile.display_name
            except LineBotApiError:
                target_user_id = creator_user_id
                target_display_name = "您"
        else:
            target_user_id = creator_user_id
            target_display_name = who_to_remind_text

        # 解析時間
        naive_dt = parse_datetime(datetime_str)
        if not naive_dt:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 時間格式有誤，請檢查後重新輸入。")
            )
            return
        
        # 設定時區 - 強制轉換為台北時區
        if naive_dt.tzinfo is None:
            event_dt = TAIPEI_TZ.localize(naive_dt)
        else:
            event_dt = naive_dt.astimezone(TAIPEI_TZ)
        
        # 檢查時間是否在過去
        current_time = datetime.now(TAIPEI_TZ)
        logger.info(f"Event time: {event_dt}, Current time: {current_time}")
        
        if event_dt <= current_time:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ 提醒時間不能設定在過去喔！請重新設定。")
            )
            return

        # 儲存事件
        event_id = add_event(creator_user_id, target_user_id, target_display_name, content, event_dt)
        
        if not event_id:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 建立提醒失敗，請稍後再試。")
            )
            return
        
        # 建立快捷回覆
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
        logger.error(f"Error in handle_message: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 處理請求時發生錯誤，請稍後再試。")
            )
        except:
            pass

# ---------------------------------
# Postback 事件處理
# ---------------------------------
@handler.add(PostbackEvent)
def handle_postback(event):
    """處理 Postback 事件"""
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

            # 確保事件時間是台北時區
            event_dt = event_record.event_datetime
            if event_dt.tzinfo is None:
                event_dt = TAIPEI_TZ.localize(event_dt)
            else:
                event_dt = event_dt.astimezone(TAIPEI_TZ)
            
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
                    # 計算提醒時間（台北時區）
                    reminder_dt = event_dt - delta
                    
                    # 檢查提醒時間是否在過去
                    current_time = datetime.now(TAIPEI_TZ)
                    logger.info(f"Current time: {current_time}")
                    logger.info(f"Reminder time: {reminder_dt}")
                    
                    if reminder_dt <= current_time:
                        line_bot_api.reply_message(
                            event.reply_token,
                            TextSendMessage(text="⚠️ 提醒時間已過，無法設定提醒。")
                        )
                        return
                    
                    logger.info(f"Scheduling reminder:")
                    logger.info(f"  Event time (Taipei): {event_dt}")
                    logger.info(f"  Reminder time (Taipei): {reminder_dt}")
                    
                    # 安全地添加任務 - 會自動轉換為UTC
                    success = safe_add_job(
                        send_reminder,
                        reminder_dt,  # 傳入台北時間，函式內會轉換為UTC
                        [event_id],
                        f'reminder_{event_id}'
                    )
                    
                    if success:
                        reply_msg_text = f"✅ 設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
                    else:
                        reply_msg_text = "❌ 設定提醒時發生錯誤。"
                else:
                    reply_msg_text = "❌ 設定提醒時發生未知的錯誤。"
            
            # 更新資料庫 - 儲存台北時間
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
            
            if not reset_reminder_sent_status(event_id):
                logger.error(f"Failed to reset reminder status for snooze, event_id: {event_id}")
            
            # 重新排程提醒
                snooze_time = datetime.now(TAIPEI_TZ) + timedelta(minutes=minutes)
            
                success = safe_add_job(
                    send_reminder,
                    snooze_time,
                    [event_id],
                    f'reminder_{event_id}'
                )
            
            if success:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"⏰ 好的，{minutes}分鐘後再次提醒您！")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 延後提醒設定失敗。")
                )
                
    except Exception as e:
        logger.error(f"Error in handle_postback: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 處理請求時發生錯誤。")
            )
        except:
            pass

# ---------------------------------
# 健康檢查端點
# ---------------------------------
@app.route("/health", methods=['GET'])
def health_check():
    """健康檢查端點"""
    return {
        "status": "healthy", 
        "scheduler_running": scheduler.running,
        "scheduled_jobs": len(scheduler.get_jobs()) if scheduler.running else 0,
        "current_utc_time": datetime.now(UTC_TZ).isoformat(),
        "current_taipei_time": datetime.now(TAIPEI_TZ).isoformat()
    }

@app.route("/", methods=['GET'])
def index():
    """根路由"""
    return "LINE Bot Reminder Service is running!"

# ---------------------------------
# 清理函式
# ---------------------------------
def cleanup():
    """應用程式關閉時的清理工作"""
    try:
        if scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler shut down successfully")
        cleanup_db()
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

# 註冊清理函式
import atexit
atexit.register(cleanup)

# ---------------------------------
# 主程式進入點
# ---------------------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
    
  #  LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN','J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU=')
#LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '74df866d9f3f4c47f3d5e86d67fcb673')
#DATABASE_URL = os.getenv('DATABASE_URL','postgresql://remine_db_user:Gxehx2wmgDUSZenytj4Sd4r0Z6UHE4Xp@dpg-d1qcfms9c44c739ervm0-a/remine_db')