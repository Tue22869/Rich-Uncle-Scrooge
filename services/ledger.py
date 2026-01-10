"""Ledger operations service."""
import logging
from decimal import Decimal
from datetime import datetime, date
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session

from db.models import User, Account, Transaction, TransactionType
from utils.dates import now_in_timezone

logger = logging.getLogger(__name__)


def get_or_create_user(db: Session, tg_user_id: int, timezone: str = "Europe/London") -> User:
    """Get or create user."""
    user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
    if not user:
        user = User(tg_user_id=tg_user_id, timezone=timezone)
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Created new user: {user.id}")
    return user


def find_account_by_name(db: Session, user_id: int, account_name: str, exact_only: bool = False) -> Optional[Account]:
    """Find account by name (exact or fuzzy match)."""
    accounts = db.query(Account).filter(Account.user_id == user_id).all()
    
    # Exact match first
    for acc in accounts:
        if acc.name.lower() == account_name.lower():
            return acc
    
    if exact_only:
        return None
    
    # Fuzzy match (contains)
    account_name_lower = account_name.lower()
    for acc in accounts:
        if account_name_lower in acc.name.lower() or acc.name.lower() in account_name_lower:
            return acc
    
    return None


def add_income(
    db: Session,
    user_id: int,
    amount: Decimal,
    currency: str,
    account_id: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    description: Optional[str] = None,
    operation_date: Optional[datetime] = None
) -> Transaction:
    """
    Add income transaction.
    Atomically increases account balance and creates transaction.
    """
    if operation_date is None:
        user = db.query(User).filter(User.id == user_id).first()
        operation_date = now_in_timezone(user.timezone if user else "Europe/London")
    
    account = db.query(Account).filter(Account.id == account_id, Account.user_id == user_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found for user {user_id}")
    
    try:
        # Atomic transaction
        account.balance += amount
        
        transaction = Transaction(
            user_id=user_id,
            type=TransactionType.INCOME,
            amount=amount,
            currency=currency,
            account_id=account_id,
            category=category,
            subcategory=subcategory,
            description=description,
            operation_date=operation_date
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        logger.info(f"Added income: {amount} {currency} to account {account_id}")
        return transaction
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to add income: {e}")
        raise


def add_expense(
    db: Session,
    user_id: int,
    amount: Decimal,
    currency: str,
    account_id: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    description: Optional[str] = None,
    operation_date: Optional[datetime] = None
) -> Transaction:
    """
    Add expense transaction.
    Atomically decreases account balance and creates transaction.
    """
    if operation_date is None:
        user = db.query(User).filter(User.id == user_id).first()
        operation_date = now_in_timezone(user.timezone if user else "Europe/London")
    
    account = db.query(Account).filter(Account.id == account_id, Account.user_id == user_id).first()
    if not account:
        raise ValueError(f"Account {account_id} not found for user {user_id}")
    
    if account.balance < amount:
        raise ValueError(f"Insufficient balance: {account.balance} < {amount}")
    
    try:
        # Atomic transaction
        account.balance -= amount
        
        transaction = Transaction(
            user_id=user_id,
            type=TransactionType.EXPENSE,
            amount=amount,
            currency=currency,
            account_id=account_id,
            category=category,
            subcategory=subcategory,
            description=description,
            operation_date=operation_date
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        logger.info(f"Added expense: {amount} {currency} from account {account_id}")
        return transaction
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to add expense: {e}")
        raise


def transfer(
    db: Session,
    user_id: int,
    amount: Decimal,
    currency: str,
    from_account_id: int,
    to_account_id: int,
    to_amount: Optional[Decimal] = None,  # For cross-currency transfers
    to_currency: Optional[str] = None,    # For cross-currency transfers
    description: Optional[str] = None,
    operation_date: Optional[datetime] = None
) -> Transaction:
    """
    Transfer between accounts.
    Supports cross-currency transfers with manual amount specification.
    Atomically decreases from_account, increases to_account, creates transaction.
    """
    if operation_date is None:
        user = db.query(User).filter(User.id == user_id).first()
        operation_date = now_in_timezone(user.timezone if user else "Europe/London")
    
    from_account = db.query(Account).filter(
        Account.id == from_account_id,
        Account.user_id == user_id
    ).first()
    to_account = db.query(Account).filter(
        Account.id == to_account_id,
        Account.user_id == user_id
    ).first()
    
    if not from_account:
        raise ValueError(f"From account {from_account_id} not found")
    if not to_account:
        raise ValueError(f"To account {to_account_id} not found")
    
    if from_account.balance < amount:
        raise ValueError(f"Insufficient balance: {from_account.balance} < {amount}")
    
    # Use to_amount for cross-currency, otherwise same amount
    credit_amount = to_amount if to_amount is not None else amount
    
    try:
        # Atomic transaction
        from_account.balance -= amount
        to_account.balance += credit_amount
        
        transaction = Transaction(
            user_id=user_id,
            type=TransactionType.TRANSFER,
            amount=amount,
            currency=currency,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            description=description,
            operation_date=operation_date
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        logger.info(f"Transfer: {amount} {currency} from {from_account_id} to {to_account_id}")
        return transaction
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to transfer: {e}")
        raise


def create_account(
    db: Session,
    user_id: int,
    name: str,
    currency: str = "RUB",
    initial_balance: Decimal = Decimal("0.00")
) -> Account:
    """Create new account."""
    # Check if this is the first account for the user
    existing_accounts = db.query(Account).filter(Account.user_id == user_id).count()
    is_first_account = existing_accounts == 0
    
    account = Account(
        user_id=user_id,
        name=name,
        currency=currency,
        balance=initial_balance,
        is_default=is_first_account  # First account is default
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    
    # Also set user.default_account_id if first account
    if is_first_account:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.default_account_id = account.id
            db.commit()
    
    logger.info(f"Created account: {name} for user {user_id} (default={is_first_account})")
    return account


def delete_account(db: Session, user_id: int, account_id: int) -> bool:
    """Delete account (only if balance is zero)."""
    account = db.query(Account).filter(
        Account.id == account_id,
        Account.user_id == user_id
    ).first()
    
    if not account:
        raise ValueError(f"Account {account_id} not found")
    
    if account.balance != Decimal("0.00"):
        raise ValueError(f"Cannot delete account with non-zero balance: {account.balance}")
    
    db.delete(account)
    db.commit()
    logger.info(f"Deleted account: {account_id}")
    return True


def rename_account(
    db: Session,
    user_id: int,
    account_id: int,
    new_name: str
) -> Account:
    """Rename account."""
    account = db.query(Account).filter(
        Account.id == account_id,
        Account.user_id == user_id
    ).first()
    
    if not account:
        raise ValueError(f"Account {account_id} not found")
    
    account.name = new_name
    db.commit()
    db.refresh(account)
    logger.info(f"Renamed account {account_id} to {new_name}")
    return account


def set_default_account(db: Session, user_id: int, account_id: int) -> Account:
    """Set default account for user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    
    account = db.query(Account).filter(
        Account.id == account_id,
        Account.user_id == user_id
    ).first()
    
    if not account:
        raise ValueError(f"Account {account_id} not found")
    
    # Unset previous default
    db.query(Account).filter(
        Account.user_id == user_id,
        Account.is_default == True
    ).update({"is_default": False})
    
    # Set new default
    account.is_default = True
    user.default_account_id = account_id
    db.commit()
    db.refresh(account)
    logger.info(f"Set default account {account_id} for user {user_id}")
    return account


def list_user_transactions(
    db: Session,
    user_id: int,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    transaction_type: Optional[str] = None,
    limit: int = 50
) -> List[Tuple[int, Transaction]]:
    """
    Get list of user transactions with row numbers.
    Returns list of (row_number, transaction) tuples.
    """
    query = db.query(Transaction).filter(Transaction.user_id == user_id)
    
    if from_date:
        query = query.filter(Transaction.operation_date >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        query = query.filter(Transaction.operation_date <= datetime.combine(to_date, datetime.max.time()))
    if transaction_type:
        if transaction_type == "income":
            query = query.filter(Transaction.type == TransactionType.INCOME)
        elif transaction_type == "expense":
            query = query.filter(Transaction.type == TransactionType.EXPENSE)
    
    transactions = query.order_by(Transaction.operation_date.desc()).limit(limit).all()
    
    # Add row numbers (1-based, most recent first)
    return [(i + 1, tx) for i, tx in enumerate(transactions)]


def get_transaction_by_row_number(
    db: Session,
    user_id: int,
    row_number: int,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None
) -> Optional[Transaction]:
    """
    Get transaction by row number in the current list.
    Row numbers are 1-based, most recent first.
    """
    transactions = list_user_transactions(db, user_id, from_date, to_date, limit=100)
    
    for num, tx in transactions:
        if num == row_number:
            return tx
    return None


def update_transaction(
    db: Session,
    user_id: int,
    transaction_id: int,
    new_amount: Optional[Decimal] = None,
    new_category: Optional[str] = None,
    new_description: Optional[str] = None
) -> Transaction:
    """
    Update transaction. Adjusts account balance if amount changes.
    """
    transaction = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.user_id == user_id
    ).first()
    
    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")
    
    try:
        # If amount changes, need to adjust account balance
        if new_amount is not None and new_amount != transaction.amount:
            diff = new_amount - transaction.amount
            
            if transaction.type == TransactionType.INCOME:
                account = db.query(Account).filter(Account.id == transaction.account_id).first()
                if account:
                    account.balance += diff
            elif transaction.type == TransactionType.EXPENSE:
                account = db.query(Account).filter(Account.id == transaction.account_id).first()
                if account:
                    account.balance -= diff  # Expense: more expense = less balance
            
            transaction.amount = new_amount
        
        if new_category is not None:
            transaction.category = new_category
        
        if new_description is not None:
            transaction.description = new_description
        
        db.commit()
        db.refresh(transaction)
        logger.info(f"Updated transaction {transaction_id}")
        return transaction
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update transaction: {e}")
        raise


def delete_transaction_by_id(
    db: Session,
    user_id: int,
    transaction_id: int
) -> bool:
    """
    Delete transaction and reverse the account balance change.
    """
    transaction = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.user_id == user_id
    ).first()
    
    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")
    
    try:
        # Reverse the balance change
        if transaction.type == TransactionType.INCOME:
            account = db.query(Account).filter(Account.id == transaction.account_id).first()
            if account:
                account.balance -= transaction.amount
        elif transaction.type == TransactionType.EXPENSE:
            account = db.query(Account).filter(Account.id == transaction.account_id).first()
            if account:
                account.balance += transaction.amount
        elif transaction.type == TransactionType.TRANSFER:
            from_account = db.query(Account).filter(Account.id == transaction.from_account_id).first()
            to_account = db.query(Account).filter(Account.id == transaction.to_account_id).first()
            if from_account:
                from_account.balance += transaction.amount
            if to_account:
                to_account.balance -= transaction.amount
        
        db.delete(transaction)
        db.commit()
        logger.info(f"Deleted transaction {transaction_id}")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete transaction: {e}")
        raise


def clear_user_data(db: Session, user_id: int) -> Tuple[int, int]:
    """
    Delete all transactions and accounts for a user.
    Returns (transactions_deleted, accounts_deleted).
    """
    try:
        # Delete all transactions first (foreign key constraint)
        tx_count = db.query(Transaction).filter(Transaction.user_id == user_id).delete()
        
        # Delete all accounts
        acc_count = db.query(Account).filter(Account.user_id == user_id).delete()
        
        # Reset user's default_account_id
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.default_account_id = None
        
        db.commit()
        logger.info(f"Cleared user {user_id} data: {tx_count} transactions, {acc_count} accounts")
        return (tx_count, acc_count)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to clear user data: {e}")
        raise


def create_transaction_raw(
    db: Session,
    user_id: int,
    transaction_type: str,
    amount: Decimal,
    currency: str,
    account_id: int,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    description: Optional[str] = None,
    operation_date: Optional[datetime] = None,
    from_account_id: Optional[int] = None,
    to_account_id: Optional[int] = None,
) -> Transaction:
    """
    Create transaction WITHOUT updating account balance.
    Used for importing data where balances are set separately.
    """
    if operation_date is None:
        user = db.query(User).filter(User.id == user_id).first()
        operation_date = now_in_timezone(user.timezone if user else "Europe/London")
    
    # Map string type to enum
    type_map = {
        "income": TransactionType.INCOME,
        "expense": TransactionType.EXPENSE,
        "transfer": TransactionType.TRANSFER,
    }
    tx_type = type_map.get(transaction_type.lower(), TransactionType.EXPENSE)
    
    transaction = Transaction(
        user_id=user_id,
        type=tx_type,
        amount=amount,
        currency=currency,
        account_id=account_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        category=category,
        subcategory=subcategory,
        description=description,
        operation_date=operation_date
    )
    
    db.add(transaction)
    # Don't commit here - let caller handle the transaction
    return transaction

