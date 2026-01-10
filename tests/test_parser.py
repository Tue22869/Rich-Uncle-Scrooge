"""Tests for LLM parser."""
import pytest
from unittest.mock import patch, MagicMock

from schemas.llm_schema import LLMResponse, LLMResponseData, PeriodSchema
from llm.parser import parse_message, _is_valid_response


# === LLM Response Validation Tests ===

def test_is_valid_response_success():
    """Test valid response detection."""
    response = LLMResponse(
        intent="expense",
        confidence=0.9,
        data=LLMResponseData(amount=320, currency="RUB"),
        errors=[]
    )
    assert _is_valid_response(response) is True


def test_is_valid_response_unknown_low_confidence():
    """Test invalid response: unknown with low confidence."""
    response = LLMResponse(
        intent="unknown",
        confidence=0.3,
        data=LLMResponseData(),
        errors=[]
    )
    assert _is_valid_response(response) is False


def test_is_valid_response_with_errors():
    """Test invalid response: has errors."""
    response = LLMResponse(
        intent="expense",
        confidence=0.9,
        data=LLMResponseData(),
        errors=["Missing amount"]
    )
    assert _is_valid_response(response) is False


# === Parse Message Tests (Mocked) ===

@pytest.fixture
def mock_expense_response():
    """Mock expense response."""
    return {
        "intent": "expense",
        "confidence": 0.95,
        "data": {
            "amount": 320,
            "currency": "RUB",
            "category": "Кафе и кофе",
            "subcategory": "кофе"
        },
        "errors": []
    }


@pytest.fixture
def mock_income_response():
    """Mock income response."""
    return {
        "intent": "income",
        "confidence": 0.95,
        "data": {
            "amount": 50000,
            "currency": "RUB",
            "category": "Зарплата",
            "subcategory": "оклад"
        },
        "errors": []
    }


@pytest.fixture
def mock_transfer_response():
    """Mock transfer response."""
    return {
        "intent": "transfer",
        "confidence": 0.9,
        "data": {
            "amount": 10000,
            "from_account_name": "карта",
            "to_account_name": "нал"
        },
        "errors": []
    }


@patch('llm.parser._call_llm_json_mode')
def test_parse_expense(mock_llm, mock_expense_response):
    """Test parsing expense message."""
    mock_llm.return_value = (mock_expense_response, None)
    
    result = parse_message("кофе 320", [], None, "Europe/London")
    
    assert result.intent == "expense"
    assert result.data.amount == 320
    assert result.data.category == "Кафе и кофе"


@patch('llm.parser._call_llm_json_mode')
def test_parse_income(mock_llm, mock_income_response):
    """Test parsing income message."""
    mock_llm.return_value = (mock_income_response, None)
    
    result = parse_message("+50000 зп", [], None, "Europe/London")
    
    assert result.intent == "income"
    assert result.data.amount == 50000


@patch('llm.parser._call_llm_json_mode')
def test_parse_transfer(mock_llm, mock_transfer_response):
    """Test parsing transfer message."""
    mock_llm.return_value = (mock_transfer_response, None)
    
    result = parse_message("переведи 10к с карты на нал", [], None, "Europe/London")
    
    assert result.intent == "transfer"
    assert result.data.from_account_name == "карта"
    assert result.data.to_account_name == "нал"


@patch('llm.parser._call_llm_json_mode')
def test_parse_show_accounts(mock_llm):
    """Test parsing show accounts message."""
    mock_llm.return_value = ({
        "intent": "show_accounts",
        "confidence": 1.0,
        "data": {},
        "errors": []
    }, None)
    
    result = parse_message("мои счета", [], None, "Europe/London")
    
    assert result.intent == "show_accounts"


@patch('llm.parser._call_llm_json_mode')
def test_parse_report(mock_llm):
    """Test parsing report message."""
    mock_llm.return_value = ({
        "intent": "report",
        "confidence": 0.95,
        "data": {
            "period": {
                "from": "2025-11-01",
                "to": "2025-11-30",
                "preset": "month"
            }
        },
        "errors": []
    }, None)
    
    result = parse_message("отчет за ноябрь", [], None, "Europe/London")
    
    assert result.intent == "report"


@patch('llm.parser._call_llm_json_mode')
def test_parse_list_transactions(mock_llm):
    """Test parsing list transactions message."""
    mock_llm.return_value = ({
        "intent": "list_transactions",
        "confidence": 1.0,
        "data": {
            "period": {"preset": "month"}
        },
        "errors": []
    }, None)
    
    result = parse_message("история операций", [], None, "Europe/London")
    
    assert result.intent == "list_transactions"


@patch('llm.parser._call_llm_json_mode')
def test_parse_delete_transaction(mock_llm):
    """Test parsing delete transaction message."""
    mock_llm.return_value = ({
        "intent": "delete_transaction",
        "confidence": 1.0,
        "data": {"transaction_id": 3},
        "errors": []
    }, None)
    
    result = parse_message("удали запись 3", [], None, "Europe/London")
    
    assert result.intent == "delete_transaction"
    assert result.data.transaction_id == 3


@patch('llm.parser._call_llm_json_mode')
def test_parse_insight(mock_llm):
    """Test parsing insight/analytics message."""
    mock_llm.return_value = ({
        "intent": "insight",
        "confidence": 1.0,
        "data": {
            "metric": "expense",
            "category": "Кафе и кофе"
        },
        "errors": []
    }, None)
    
    result = parse_message("почему так много на кофе", [], None, "Europe/London")
    
    assert result.intent == "insight"


@patch('llm.parser._call_llm_json_mode')
def test_parse_fallback_on_error(mock_llm):
    """Test fallback to secondary model on error."""
    # First call fails, second succeeds
    mock_llm.side_effect = [
        (None, "API error"),
        ({"intent": "expense", "confidence": 0.9, "data": {"amount": 100}, "errors": []}, None)
    ]
    
    result = parse_message("такси 100", [], None, "Europe/London")
    
    assert result.intent == "expense"
    assert mock_llm.call_count == 2


@patch('llm.parser._call_llm_json_mode')
def test_parse_both_models_fail(mock_llm):
    """Test both models failing returns unknown."""
    mock_llm.return_value = (None, "API error")
    
    result = parse_message("непонятный запрос", [], None, "Europe/London")
    
    assert result.intent == "unknown"
    assert len(result.errors) > 0
