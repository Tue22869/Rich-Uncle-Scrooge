"""Database models."""
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, 
    DECIMAL, Boolean, Text, Enum as SQLEnum, JSON
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

