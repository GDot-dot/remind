# db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, DateTime

# 從環境變數讀取資料庫 URL
DATABASE_URL = os.getenv('DATABASE_URL','postgresql://remine_db_user:Gxehx2wmgDUSZenytj4Sd4r0Z6UHE4Xp@dpg-d1qcfms9c44c739ervm0-a/remine_db')
if not DATABASE_URL:
    raise ValueError("No DATABASE_URL set for Flask application")

# 建立資料庫引擎
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 定義事件的資料庫模型 (ORM Model)
class Event(Base):
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True, index=True)
    creator_user_id = Column(String, nullable=False)
    target_user_id = Column(String, nullable=False)
    target_display_name = Column(Text, nullable=False)
    event_content = Column(Text, nullable=False)
    event_datetime = Column(DateTime(timezone=True), nullable=False)
    reminder_time = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, server_default='CURRENT_TIMESTAMP')

# 提供一個函式來取得資料庫 session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 初始化資料庫表格的函式
def init_db():
    # 這會檢查資料庫中是否存在 'events' 和 'apscheduler_jobs' 表格，如果不存在則建立。
    Base.metadata.create_all(bind=engine)
    print("Database tables checked/created.")