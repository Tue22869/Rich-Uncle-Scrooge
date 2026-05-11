"""Database models."""
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, 
    DECIMAL, Boolean, Enum as SQLEnum, JSON
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class TransactionType(PyEnum):
    """Transaction type enum."""
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class ActionType(PyEnum):
    """Pending action type enum."""
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"
    ACCOUNT_ADD = "account_add"
    ACCOUNT_DELETE = "account_delete"
    ACCOUNT_RENAME = "account_rename"
    SET_DEFAULT_ACCOUNT = "set_default_account"
    EDIT_TRANSACTION = "edit_transaction"
    DELETE_TRANSACTION = "delete_transaction"
    BATCH = "batch"
    SHEETS_IMPORT = "sheets_import"
    CLEAR_ALL_DATA = "clear_all_data"
    CLARIFICATION = "clarification"


class PendingStatus(PyEnum):
    """Pending action status enum."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class User(Base):
    """User model."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    tg_user_id = Column(Integer, unique=True, index=True, nullable=False)
    timezone = Column(String, default="Europe/London", nullable=False)
    # Note: ForeignKey constraint added after Account table is defined
    default_account_id = Column(Integer, nullable=True)
    # Per-user Google Sheets spreadsheet id (not a file path, just the id from URL)
    google_sheets_spreadsheet_id = Column(String, nullable=True)
    # Trial fields
    trial_used = Column(Boolean, default=False, nullable=False)
    trial_activated_at = Column(DateTime, nullable=True)
    # Retention: streak tracking
    streak_days = Column(Integer, default=0, nullable=False)
    last_activity_date = Column(String, nullable=True)  # ISO date string
    total_operations = Column(Integer, default=0, nullable=False)
    achievements_json = Column(JSON, nullable=True)  # list of achievement codes
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    accounts = relationship(
        "Account",
        primaryjoin="User.id == Account.user_id",
        foreign_keys="[Account.user_id]",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    pending_actions = relationship("PendingAction", back_populates="user", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    budgets = relationship("Budget", back_populates="user", cascade="all, delete-orphan")


class Account(Base):
    """Account model."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    currency = Column(String, nullable=False, default="RUB")
    balance = Column(DECIMAL(15, 2), default=Decimal("0.00"), nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="accounts")
    transactions_from = relationship(
        "Transaction",
        foreign_keys="Transaction.from_account_id",
        back_populates="from_account"
    )
    transactions_to = relationship(
        "Transaction",
        foreign_keys="Transaction.account_id",
        back_populates="account"
    )


class Transaction(Base):
    """Transaction model."""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(SQLEnum(TransactionType), nullable=False, index=True)
    amount = Column(DECIMAL(15, 2), nullable=False)
    currency = Column(String, nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # for income/expense
    from_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # for transfer
    to_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # for transfer
    category = Column(String, nullable=True, index=True)
    subcategory = Column(String, nullable=True, index=True)
    description = Column(String, nullable=True)
    operation_date = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="transactions")
    account = relationship("Account", foreign_keys=[account_id], back_populates="transactions_to")
    from_account = relationship("Account", foreign_keys=[from_account_id], back_populates="transactions_from")


class PendingAction(Base):
    """Pending action model for confirmations."""
    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(SQLEnum(ActionType), nullable=False)
    payload_json = Column(JSON, nullable=False)
    preview_message_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(SQLEnum(PendingStatus), default=PendingStatus.PENDING, nullable=False, index=True)

    # Relationships
    user = relationship("User", back_populates="pending_actions")


class SubscriptionPlan(PyEnum):
    """Subscription plan enum."""
    TRIAL = "trial"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(PyEnum):
    """Subscription status enum."""
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SubscriptionProvider:
    """Payment provider constants (not an Enum — kept as plain strings for forward-compat)."""
    TRIAL = "trial"
    YOOKASSA = "yookassa"
    STARS = "stars"


class Subscription(Base):
    """Subscription model for tracking user payments and trial."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan = Column(SQLEnum(SubscriptionPlan), nullable=False)
    status = Column(SQLEnum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE, nullable=False)
    # provider: "trial" | "yookassa" | "stars" (see SubscriptionProvider)
    provider = Column(String, default=SubscriptionProvider.YOOKASSA, nullable=False, index=True)
    # For yookassa: YooKassa payment.id; for stars: telegram_payment_charge_id.
    payment_id = Column(String, nullable=True, index=True)
    paid_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="subscriptions")


class Budget(Base):
    """Budget model for category spending limits."""
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String, nullable=False)
    monthly_limit = Column(DECIMAL(15, 2), nullable=False)
    currency = Column(String, default="RUB", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="budgets")


class UsageEventKind:
    """Kinds of UsageEvent stored in the analytics table."""

    # User-facing engagement (no LLM cost)
    MESSAGE_IN = "message_in"           # any inbound text message
    VOICE_IN = "voice_in"               # inbound voice message
    REPORT_VIEW = "report_view"         # user opened a report
    ANALYSIS_VIEW = "analysis_view"     # user requested LLM analysis (counts before LLM call)
    COMMAND = "command"                 # /start, /menu, /accounts ... (meta.cmd)
    FEATURE_FIRST = "feature_first"    # first time a user uses a feature (aha-moment funnel)

    # LLM/Whisper cost-bearing events
    PARSE_LLM = "parse_llm"             # parse_message() → OpenAI
    ANALYSIS_LLM = "analysis_llm"       # generate_analysis() → OpenAI
    DIGEST_LLM = "digest_llm"           # weekly/monthly digest LLM rewrite
    WHISPER = "whisper"                 # voice → text via Whisper

    # Money
    SUBSCRIPTION_PAID = "subscription_paid"
    TRIAL_STARTED = "trial_started"


class UsageEvent(Base):
    """Single analytics / billing event.

    Two purposes:
      1. **Cost accounting**: every OpenAI / Whisper call writes input/output tokens
         and pre-computed cost in micros (1 USD = 1_000_000 micros). Integer math
         throughout, no rounding drift.
      2. **Engagement metrics**: lightweight events (message_in, voice_in,
         feature_first) for DAU/MAU, retention, funnels — no cost columns set.
    """

    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Cost-bearing fields (nullable for non-LLM events)
    model = Column(String, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    cached_input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    audio_seconds = Column(Integer, nullable=True)
    cost_usd_micro = Column(Integer, nullable=True)   # 1 USD = 1_000_000

    # Free-form context (intent, plan, provider, ...) — JSON for flexibility
    meta = Column(JSON, nullable=True)

