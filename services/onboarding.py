"""Lightweight 3-step onboarding tour.

Linear sequence anchored to user.onboarding_step:
  0 -> 1 : first account created
  1 -> 2 : first income/expense/transfer confirmed
  2 -> 3 : second income/expense/transfer confirmed (tour complete)
"""
from typing import Optional

from sqlalchemy.orm import Session

from db.models import User


TIP_STEP_1 = (
    "✨ Готово! Чтобы записать расход, просто напиши: `кофе 320`.\n"
    "Я покажу превью, ты подтвердишь — и операция сохранена."
)
TIP_STEP_2 = (
    "✨ Так же работает доход — напиши `+50000 зп`.\n"
    "А перевод между счетами: `переведи 10к с карты на нал`."
)
TIP_STEP_3 = (
    "✨ Готов осваивать отчёты? Напиши `отчёт за месяц` или нажми «📈 Отчёты» в главном меню.\n\n"
    "Полная инструкция: /help"
)


def advance_after_first_account(db: Session, user: User) -> Optional[str]:
    """Called after a user's very first account is created. Returns the tip text or None."""
    if user.onboarding_step == 0:
        user.onboarding_step = 1
        db.commit()
        return TIP_STEP_1
    return None


def advance_after_confirmed_op(db: Session, user: User) -> Optional[str]:
    """Called after a confirmed income/expense/transfer. Returns the tip text or None."""
    if user.onboarding_step == 1:
        user.onboarding_step = 2
        db.commit()
        return TIP_STEP_2
    if user.onboarding_step == 2:
        user.onboarding_step = 3
        db.commit()
        return TIP_STEP_3
    return None
