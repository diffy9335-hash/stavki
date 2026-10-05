from config import ACHIEVEMENTS
from database import award_achievement, get_stats


async def grant(bot, user_id, code):
    """Выдаёт достижение и уведомляет. Возвращает награду (0 если уже было)."""
    reward = await award_achievement(user_id, code)
    if reward:
        name = ACHIEVEMENTS[code][0]
        try:
            await bot.send_message(user_id, f"🏅 Достижение «{name}»! Награда: +{reward} монет.")
        except Exception:
            pass
    return reward


async def check_bet(bot, user_id, coef: float):
    """Проверяет достижения, связанные со ставками."""
    await grant(bot, user_id, "first_bet")
    s = await get_stats(user_id)
    if s["bets"] >= 100:
        await grant(bot, user_id, "bettor_100")
    elif s["bets"] >= 10:
        await grant(bot, user_id, "bettor_10")
    if coef >= 10:
        await grant(bot, user_id, "big_win")
