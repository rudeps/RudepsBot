# -*- coding: utf-8 -*-
"""
Инициализация всех обработчиков
"""

# Экспортируем функции регистрации для удобства
from .common import register_handlers as register_common
from .comment import register_handlers as register_comment
from .withdraw import register_handlers as register_withdraw
from .admin import register_handlers as register_admin

__all__ = ['register_common', 'register_comment', 'register_withdraw', 'register_admin']
