import asyncio
import time
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_IDS, RATE_LIMIT, CHANNEL_ID, CHANNEL_URL
from database import (init_db, get_user, close_due_matches, matches_starting_soon,
                      match_bettors, refund_crashed_games, DB_NAME)


async def _is_subscribed(bot: Bot, uid: int) -> bool:
    """True, если подписан. При недоступности канала пропускает (не блокируем навсегда)."""
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return True


class SubscribeMiddleware(BaseMiddleware):
    """Обязательная подписка на канал: без неё бот не отвечает на действия."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in ADMIN_IDS and CHANNEL_ID:
            # кнопку проверки подписки пропускаем всегда
            if isinstance(event, CallbackQuery) and event.data == "sub:check":
                return await handler(event, data)
            u = await get_user(user.id)
            if u and not await _is_subscribed(data.get("bot"), user.id):
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
                    [InlineKeyboardButton(text="✅ Я подписался", callback_data="sub:check")],
                ])
                text = "📢 Для использования бота подпишитесь на канал @StavkiRofl:"
                if isinstance(event, Message):
                    await event.answer(text, reply_markup=kb)
                elif isinstance(event, CallbackQuery):
                    await event.answer("📢 Подпишитесь на канал @StavkiRofl!", show_alert=True)
                return
        return await handler(event, data)


class BannedMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in ADMIN_IDS:
            u = await get_user(user.id)
            if u and u["banned"]:
                if isinstance(event, Message):
                    await event.answer("⛔ Вы заблокированы администрацией.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔ Вы заблокированы.", show_alert=True)
                return
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Простая защита от спама: не более RATE_LIMIT апдейтов за 10 секунд."""

    def __init__(self):
        self.calls = defaultdict(deque)

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            dq = self.calls[user.id]
            t = time.monotonic()
            while dq and t - dq[0] > 10:
                dq.popleft()
            if len(dq) >= RATE_LIMIT:
                if isinstance(event, Message):
                    await event.answer("⏳ Слишком много запросов. Подождите несколько секунд.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⏳ Слишком быстро!", show_alert=True)
                return
            dq.append(t)
        return await handler(event, data)


async def auto_close_job(bot: Bot):
    """Каждую минуту: закрывает приём ставок по start_time и шлёт уведомления о начале."""
    while True:
        try:
            await asyncio.sleep(60)
            closed = await close_due_matches()
            starting = await matches_starting_soon(5)
            for mt in starting:
                for uid in await match_bettors(mt["id"]):
                    try:
                        await bot.send_message(
                            uid, f"🔔 Матч начинается: {mt['team1']} — {mt['team2']}!\n🕒 {mt['start_time']}")
                    except Exception:
                        pass
        except Exception:
            await asyncio.sleep(60)


async def backup_job(bot: Bot):
    """Автобэкап базы раз в сутки с отправкой админам."""
    while True:
        await asyncio.sleep(86400)
        try:
            for aid in ADMIN_IDS:
                await bot.send_document(aid, FSInputFile(DB_NAME), caption="💾 Ежедневный бэкап базы")
        except Exception:
            pass


async def main():
    await init_db()
    # возврат ставок crash-игр, прерванных перезапуском
    refunded = await refund_crashed_games()
    bot = Bot(token=BOT_TOKEN)
    if refunded:
        for aid in ADMIN_IDS:
            try:
                await bot.send_message(aid, f"⚠️ Перезапуск: возвращено {refunded} незавершённых crash-ставок.")
            except Exception:
                pass
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(SubscribeMiddleware())
    dp.callback_query.middleware(SubscribeMiddleware())
    dp.message.middleware(BannedMiddleware())
    dp.callback_query.middleware(BannedMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())
    from handlers import user_router
    from games import games_router
    from admin import admin_router
    dp.include_router(admin_router)
    dp.include_router(games_router)
    dp.include_router(user_router)
    asyncio.create_task(auto_close_job(bot))
    asyncio.create_task(backup_job(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
