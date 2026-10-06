"""Расчёт матчей: выплаты, уведомления игрокам, экспрессы."""
import aiosqlite
from database import (settle_match, resettle_match, bet_won, get_match, add_balance,
                      coupons_with_match, coupon_legs_full, set_coupon_status, DB_NAME)
import achievements as ach


async def _notify(bot, uid, text):
    try:
        await bot.send_message(uid, text)
    except Exception:
        pass


async def evaluate_coupons(bot, match_id):
    """Проверяет активные экспрессы с этим матчем."""
    for coupon in await coupons_with_match(match_id):
        legs = await coupon_legs_full(coupon["id"])
        lost = False
        all_finished = True
        for leg in legs:
            if leg["mstatus"] != "finished":
                all_finished = False
            elif not bet_won(leg["bet_type"], leg["result"], leg["btts_result"]):
                lost = True
        if lost:
            await set_coupon_status(coupon["id"], "lose")
            await _notify(bot, coupon["user_id"],
                          "❌ Ваш экспресс не сыграл (один из исходов проиграл).")
        elif all_finished:
            await add_balance(coupon["user_id"], coupon["potential_win"])
            await set_coupon_status(coupon["id"], "win")
            await _notify(bot, coupon["user_id"],
                          f"✅🧾 Экспресс сыграл! Кэф x{coupon['coef']:.2f}\n"
                          f"Выигрыш: {coupon['potential_win']} монет!")
            await ach.grant(bot, coupon["user_id"], "express_win")


async def resettle_coupons(bot, match_id):
    """Пересчёт экспрессов после изменения результата матча."""
    await evaluate_coupons(bot, match_id)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT c.* FROM coupons c JOIN coupon_legs l ON l.coupon_id=c.id "
            "WHERE c.status IN ('win','lose') AND l.match_id=?", (match_id,))
        coupons = [dict(r) for r in await cur.fetchall()]
    for coupon in coupons:
        legs = await coupon_legs_full(coupon["id"])
        lost = False
        all_finished = True
        for leg in legs:
            if leg["mstatus"] != "finished":
                all_finished = False
            elif not bet_won(leg["bet_type"], leg["result"], leg["btts_result"]):
                lost = True
        new = "lose" if lost else ("win" if all_finished else None)
        if new and new != coupon["status"]:
            delta = (1 if new == "win" else -1) * coupon["potential_win"]
            await add_balance(coupon["user_id"], delta)
            await set_coupon_status(coupon["id"], new)
            await _notify(bot, coupon["user_id"],
                          f"🧾 Экспресс пересчитан по новому результату матча: теперь "
                          f"{'выигрыш' if new == 'win' else 'проигрыш'} ({delta:+d} монет).")


async def settle_match_full(bot, match_id, result, btts):
    """Полный расчёт: ставки + уведомления + экспрессы + достижения."""
    payouts = await settle_match(match_id, result, btts)
    mt = await get_match(match_id)
    title = f"{mt['team1']} — {mt['team2']}" if mt else f"матч #{match_id}"
    for uid, won, pot, coef in payouts:
        if won:
            await _notify(bot, uid, f"✅ Ваша ставка сыграла!\n⚽ {title}\nВыигрыш: +{pot} монет.")
        else:
            await _notify(bot, uid, f"❌ Ваша ставка не сыграла.\n⚽ {title}")
        await ach.check_bet(bot, uid, coef)
    await evaluate_coupons(bot, match_id)


async def resettle_match_full(bot, match_id, new_result, new_btts):
    changes = await resettle_match(match_id, new_result, new_btts)
    for uid, won, pot, coef in changes:
        if won:
            await _notify(bot, uid,
                          f"⚠️ Результат матча #{match_id} изменён — ваша ставка теперь ВЫИГРАЛА. "
                          f"Начислено: +{pot} монет.")
        else:
            await _notify(bot, uid,
                          f"⚠️ Результат матча #{match_id} изменён — ваша ставка теперь ПРОИГРАЛА. "
                          f"С баланса списан ранее выплаченный выигрыш: −{pot} монет. "
                          f"Это не новая ставка, а отмена выплаты.")
        await ach.check_bet(bot, uid, coef)
    await resettle_coupons(bot, match_id)
