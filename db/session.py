"""Database session management."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import text

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smartfinances.db")

# For SQLite, use StaticPool to allow multiple threads
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
else:
    engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    from db.models import Base
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for SQLite (no Alembic in this project)
    if DATABASE_URL.startswith("sqlite"):
        _ensure_sqlite_schema()


def _ensure_sqlite_schema() -> None:
    """Ensure new nullable columns exist on SQLite tables."""
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        existing = {row[1] for row in cols}  # row[1] is column name

        migrations = {
            "google_sheets_spreadsheet_id": "ALTER TABLE users ADD COLUMN google_sheets_spreadsheet_id VARCHAR",
            "trial_used": "ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT 0 NOT NULL",
            "trial_activated_at": "ALTER TABLE users ADD COLUMN trial_activated_at DATETIME",
            "streak_days": "ALTER TABLE users ADD COLUMN streak_days INTEGER DEFAULT 0 NOT NULL",
            "last_activity_date": "ALTER TABLE users ADD COLUMN last_activity_date VARCHAR",
            "total_operations": "ALTER TABLE users ADD COLUMN total_operations INTEGER DEFAULT 0 NOT NULL",
            "achievements_json": "ALTER TABLE users ADD COLUMN achievements_json JSON",
        }

        for col_name, sql in migrations.items():
            if col_name not in existing:
                conn.execute(text(sql))
                conn.commit()

