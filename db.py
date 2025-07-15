# db.py (優化版本)
import os
import time
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, DateTime
from sqlalchemy.pool import QueuePool

# 從環境變數讀取資料庫 URL
DATABASE_URL = os.getenv('DATABASE_URL','postgresql://remine_db_user:Gxehx2wmgDUSZenytj4Sd4r0Z6UHE4Xp@dpg-d1qcfms9c44c739ervm0-a/remine_db')
if not DATABASE_URL:
    raise ValueError("No DATABASE_URL set for Flask application")

# 建立資料庫引擎 - 針對免費方案優化連線池設定
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=3,  # 減少連線池大小，適合免費方案
    max_overflow=5,  # 減少最大溢出連線
    pool_recycle=1800,  # 30分鐘回收連線
    pool_pre_ping=True,
    pool_timeout=30,  # 連線超時設定
    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=30s"  # SQL 語句超時設定
    }
)

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
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

# 提供一個安全的資料庫 session 函式
def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        print(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

# 安全的資料庫操作函式
def safe_db_operation(operation, max_retries=3):
    """執行資料庫操作，包含重試機制"""
    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as e:
            print(f"Database operation failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(1)  # 等待1秒後重試

# 初始化資料庫表格的函式
def init_db():
    def _init():
        Base.metadata.create_all(bind=engine)
        print("Database tables checked/created.")
    
    try:
        safe_db_operation(_init)
    except Exception as e:
        print(f"Error creating database tables: {e}")
        raise

# 測試資料庫連線的函式
def test_db_connection():
    def _test():
        with engine.connect() as connection:
            result = connection.execute("SELECT 1")
            print("Database connection successful!")
            return True
    
    try:
        return safe_db_operation(_test)
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False

# 清理資料庫連線的函式
def cleanup_db():
    """清理資料庫連線池"""
    try:
        engine.dispose()
        print("Database connections cleaned up.")
    except Exception as e:
        print(f"Error cleaning up database connections: {e}")

# 上下文管理器用於安全的資料庫操作
class DatabaseSession:
    def __init__(self):
        self.session = None
    
    def __enter__(self):
        self.session = SessionLocal()
        return self.session
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.session.rollback()
        else:
            self.session.commit()
        self.session.close()