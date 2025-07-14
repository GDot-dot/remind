import logging
import datetime
from linebot import LineBot, Update, WebhookHandler
import sqlite3
from schedule import every, delayed, job
import time
import threading

# 設定 Log
logging.basicConfig(level=logging.INFO)

# 設定 Line Bot token
LINE_BOT_TOKEN = "YOUR_LINE_BOT_TOKEN" # 請替換成你的 Line Bot Token
DATABASE_FILE = "calendar.db"

# 資料庫連線函數
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row  # 讓結果以字典形式返回
    return conn

# 資料庫操作函數
def create_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            description TEXT NOT NULL,
            datetime TEXT NOT NULL,
            reminder_time TEXT NOT NULL,
            is_reminder BOOLEAN DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

def add_event(user_id, description, datetime, reminder_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO calendar (user_id, description, datetime, reminder_time)
            VALUES (?, ?, ?, ?)
        """, (user_id, description, datetime, reminder_time))
        conn.commit()
        logging.info(f"Event added for user {user_id}: {description} at {datetime}")
        return True
    except Exception as e:
        logging.error(f"Error adding event: {e}")
        return False
    finally:
        conn.close()

def get_events_for_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM calendar WHERE user_id = ?", (user_id,))
    events = cursor.fetchall()
    conn.close()
    return events

def mark_as_reminded(event_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE calendar SET is_reminder = 0 WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()

# 提醒函數
def send_reminder(user_id, description, datetime, reminder_time):
    logging.info(f"Sending reminder to user {user_id}: {description} at {datetime}")
    # 在這裡加入你的 Line API 呼叫，發送提醒訊息
    # 例如：
    # bot.sendMessage(user_id, f"提醒您：{description}，時間：{datetime}")
    print(f"提醒您：{description}，時間：{datetime}")


# Line Bot Handler
class MyHandler(WebhookHandler):
    def __init__(self):
        super(MyHandler, self).__init__()

    def handle(self, request: Update):
        logging.info(f"Received update: {request}")
        user_id = request.message.user_id
        text = request.message.text

        if text.startswith("提醒@"):
            try:
                _, description, datetime_str, reminder_time = text.split("(", 3)  # 拆分訊息
                description = description.strip()
                datetime = datetime.datetime.strptime(datetime_str.strip(), "%Y-%m-%d %H:%M")
                reminder_time = reminder_time.strip()

                # 儲存事件到資料庫
                if add_event(user_id, description, datetime.strftime("%Y-%m-%d %H:%M"), reminder_time):

                    # 安排提醒
                    if reminder_time == "1天前":
                        delayed(send_reminder, args=(user_id, description, datetime, datetime + datetime.timedelta(days=1)))
                    elif reminder_time == "【    】分鐘前":
                         # TODO:  加入分鐘選項，並根據分鐘數計算提醒時間
                        print("未實現分鐘提醒功能")
                    elif reminder_time == "不提醒":
                        print("未安排提醒")
                    else:
                        print("其他提醒時間，請重新設定")

                    return "已記錄 @{}: {}， {}， {}。 甚麼時候提醒您呢？".format(user_id, description, datetime.strftime("%Y-%m-%d %H:%M"), "1天前")
                else:
                    return "發生錯誤，請稍後再試。"
            except ValueError as e:
                logging.error(f"Invalid input: {e}")
                return "輸入錯誤，請重新輸入。"

        else:
            return "請使用 '提醒@使用者(訊息) (時間)' 格式輸入。"



# 啟動 Bot
if __name__ == "__main__":
    create_table()
    bot = LineBot(token=LINE_BOT_TOKEN)
    bot.set_webhook(url="YOUR_WEBHOOK_URL")  # 請替換成你的 Webhook URL
    handler = MyHandler()
    bot.handle_line_events(handler)

    # 執行提醒任務
    # 這裡可以定期檢查提醒任務並發送通知
    # 例如，每分鐘檢查一次：
    # while True:
    #     now = datetime.datetime.now()
    #     for event in get_events_for_user("your_user_id"):
    #         if event[2] <= now and event[3] == 1:
    #             send_reminder(event[0], event[1], event[2], event[3])
    #             mark_as_reminded(event[0])
    #     time.sleep(60)