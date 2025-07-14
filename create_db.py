import sqlite3

# 定義資料庫檔案的名稱
DB_PATH = 'reminders.db'

print(f"正在建立資料庫檔案: {DB_PATH} ...")

# 連接到資料庫 (如果檔案不存在，SQLite 會自動建立它)
conn = sqlite3.connect(DB_PATH)

# 建立一個 cursor 物件，用來執行 SQL 指令
cursor = conn.cursor()

# 執行我們設計好的 SQL 指令來建立 events 資料表
# IF NOT EXISTS 可以確保如果資料表已經存在，就不會重複建立而報錯
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

# 提交變更，讓剛剛的指令生效
conn.commit()

# 關閉資料庫連線
conn.close()

print("資料庫和 events 資料表建立成功！")
>>>>>>> ed7162ff959226e1bfd720a61fedb89aa53b5c59
print("現在您可以在資料夾中看到 'reminders.db' 這個檔案了。")