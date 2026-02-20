"""Pydantic schemas for LLM responses."""
from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator


class PeriodSchema(BaseModel):
    """Period schema for reports and insights."""
    from_date: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    preset: Optional[Literal["today", "week", "month", "year", "custom"]] = None

    model_config = {"populate_by_name": True}


class AccountNewSchema(BaseModel):
    """New account schema."""
    name: str
    currency: str = "RUB"
    initial_balance: float = 0.0


class InsightQuerySchema(BaseModel):
    """Insight query schema."""
    metric: Literal["expense", "income", "net"]
    category: Optional[str] = None
    period: Optional[PeriodSchema] = None
    compare_to: Optional[Literal["prev_period", "prev_month", "prev_year", "avg_3m", "none"]] = "prev_month"
    account_name: Optional[str] = None
    currency: Optional[str] = None


class LLMResponseData(BaseModel):
    """Data section of LLM response."""
    amount: Optional[float] = None
    currency: Optional[str] = None
    account_name: Optional[str] = None
    from_account_name: Optional[str] = None
    to_account_name: Optional[str] = None
    to_amount: Optional[float] = None  # For cross-currency transfers
    to_currency: Optional[str] = None  # For cross-currency transfers
    operation_date: Optional[str] = None
    period: Optional[PeriodSchema] = None
    account_new: Optional[AccountNewSchema] = None
    account_old_name: Optional[str] = None
    account_new_name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None  # Subcategory for detailed analysis
    description: Optional[str] = None
    # Transaction history management
    transaction_id: Optional[int] = None  # Transaction number for edit/delete
    new_amount: Optional[float] = None  # New amount for edit
    new_category: Optional[str] = None  # New category for edit
    new_subcategory: Optional[str] = None  # New subcategory for edit
    new_description: Optional[str] = None  # New description for edit
    transaction_type: Optional[Literal["income", "expense"]] = None  # Filter for list_transactions
    clarify_question: Optional[str] = None
    insight_query: Optional[InsightQuerySchema] = None
    # Insight fields (LLM may return these directly instead of nested in insight_query)
    metric: Optional[Literal["expense", "income", "net"]] = None
    compare_to: Optional[Literal["prev_period", "prev_month", "prev_year", "avg_3m", "none"]] = None


class SingleOperation(BaseModel):
    """Single operation within a batch."""
    intent: Literal[
        "income", "expense", "transfer", "report", 
        "list_transactions", "edit_transaction", "delete_transaction",
        "account_add", "account_delete", "account_rename", 
        "set_default_account", "show_accounts", "clarify", "unknown", "insight",
        "clear_all_data"
    ]
    data: LLMResponseData


class LLMResponse(BaseModel):
    """Full LLM response schema - supports single or batch operations."""
    intent: Literal[
        "income", "expense", "transfer", "report", 
        "list_transactions", "edit_transaction", "delete_transaction",
        "account_add", "account_delete", "account_rename", 
        "set_default_account", "show_accounts", "clarify", "unknown", "insight",
        "batch",  # New: batch of multiple operations
        "clear_all_data"  # New: clear all user data
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    data: LLMResponseData
    errors: List[str] = Field(default_factory=list)
    # For batch operations
    operations: Optional[List[SingleOperation]] = None

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, v):
        """Validate intent."""
        valid_intents = [
            "income", "expense", "transfer", "report",
            "list_transactions", "edit_transaction", "delete_transaction",
            "account_add", "account_delete", "account_rename",
            "set_default_account", "show_accounts", "clarify", "unknown", "insight",
            "batch", "clear_all_data"
        ]
        if v not in valid_intents:
            raise ValueError(f"Invalid intent: {v}")
        return v

