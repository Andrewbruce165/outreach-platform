#!/usr/bin/env python3
"""
Скрипт для получения логов API Telegram и переписки за сегодня с номером +79308902205
"""

import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import MessageLog, Conversation
import os
from dotenv import load_dotenv

# Загрузим переменные окружения
load_dotenv()


async def get_logs_and_messages():
    """Получить логи и переписку за сегодня"""
    
    async with AsyncSessionLocal() as session:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        phone = "+79308902205"
        
        print("=" * 80)
        print(f"Поиск данных за {today.date()}")
        print(f"Номер телефона: {phone}")
        print("=" * 80)
        
        # 1. Получить все сообщения с этим номером за сегодня
        print("\n📱 ПЕРЕПИСКА С НОМЕРОМ +79308902205 ЗА СЕГОДНЯ:")
        print("-" * 80)
        
        query = select(MessageLog).where(
            (MessageLog.recipient_phone == phone) &
            (MessageLog.created_at >= today) &
            (MessageLog.created_at < tomorrow)
        ).order_by(MessageLog.created_at)
        
        result = await session.execute(query)
        messages = result.scalars().all()
        
        if not messages:
            print("❌ Сообщений не найдено")
        else:
            print(f"✅ Найдено {len(messages)} сообщений:\n")
            for i, msg in enumerate(messages, 1):
                print(f"\n📨 Сообщение #{i}")
                print(f"   ID: {msg.id}")
                print(f"   Время: {msg.created_at}")
                print(f"   Отправитель (ID): {msg.sender_id}")
                print(f"   Получатель: {msg.recipient_name} ({msg.recipient_phone})")
                print(f"   Telegram ID: {msg.recipient_telegram_id}")
                print(f"   Статус: {msg.message_type.value}")
                print(f"   Текст: {msg.message_text[:200]}..." if len(msg.message_text) > 200 else f"   Текст: {msg.message_text}")
                if msg.error_message:
                    print(f"   ❌ Ошибка: {msg.error_message}")
                if msg.extra_data:
                    print(f"   Доп. данные: {msg.extra_data}")
        
        # 2. Получить активные разговоры с этим номером
        print("\n\n" + "=" * 80)
        print("💬 АКТИВНЫЕ РАЗГОВОРЫ С ЭТИМ НОМЕРОМ:")
        print("-" * 80)
        
        conv_query = select(Conversation).where(
            Conversation.contact_phone == phone
        )
        
        conv_result = await session.execute(conv_query)
        conversations = conv_result.scalars().all()
        
        if not conversations:
            print("❌ Активных разговоров не найдено")
        else:
            print(f"✅ Найдено {len(conversations)} разговоров:\n")
            for conv in conversations:
                print(f"\n🔗 Разговор ID: {conv.id}")
                print(f"   Отправитель: {conv.sender_id}")
                print(f"   Контакт: {conv.contact_name} ({conv.contact_phone})")
                print(f"   Telegram ID: {conv.contact_telegram_id}")
                print(f"   AI включен: {'✅ Да' if conv.ai_enabled else '❌ Нет'}")
                print(f"   Создан: {conv.created_at}")
        
        # 3. Получить все логи API за сегодня (все сообщения)
        print("\n\n" + "=" * 80)
        print(f"📊 ВСЕ ЛОГИ API TELEGRAM ЗА СЕГОДНЯ ({today.date()}):")
        print("-" * 80)
        
        all_logs_query = select(MessageLog).where(
            (MessageLog.created_at >= today) &
            (MessageLog.created_at < tomorrow)
        ).order_by(MessageLog.created_at.desc())
        
        all_logs = await session.execute(all_logs_query)
        all_messages = all_logs.scalars().all()
        
        if not all_messages:
            print("❌ Логов не найдено")
        else:
            print(f"✅ Всего {len(all_messages)} операций за день:\n")
            
            # Статистика
            sent = sum(1 for m in all_messages if m.message_type.value == "sent")
            failed = sum(1 for m in all_messages if m.message_type.value == "failed")
            draft = sum(1 for m in all_messages if m.message_type.value == "draft")
            
            print(f"📈 Статистика:")
            print(f"   ✅ Отправлено: {sent}")
            print(f"   ❌ Ошибок: {failed}")
            print(f"   📝 Черновиков: {draft}")
            
            print(f"\n📋 Последние 20 операций:")
            for i, msg in enumerate(all_messages[:20], 1):
                status_icon = "✅" if msg.message_type.value == "sent" else "❌" if msg.message_type.value == "failed" else "📝"
                print(f"{i:2}. {status_icon} {msg.created_at} | {msg.recipient_phone} | {msg.message_text[:60]}...")


if __name__ == "__main__":
    asyncio.run(get_logs_and_messages())
