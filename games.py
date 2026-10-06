import random
import math
import asyncio
import time
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import MIN_BET
from database import (get_balance, take_balance, add_balance, record_bet,
                      save_game, get_game, delete_game)
from keyboards import games_kb, btn, main_menu
import achievements as ach

games_router = Router()

CANCEL_WORDS = {"отмена", "cancel", "стоп", "/cancel"}


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[btn("❌ Отмена", "ui:cancel")]])


@games_router.message(F.text.lower().in_(CANCEL_WORDS))
async def games_cancel_input(m: Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await m.answer("❌ Действие отменено.", reply_markup=main_menu())


# Запущенные crash-партии (только живые таймеры; состояние — в БД)
CRASH_TASKS: dict = {}


class MinesBet(StatesGroup):
    amount = State()


class WheelBet(StatesGroup):
    amount = State()


class CrashBet(StatesGroup):
    amount = State()


class CoinBet(StatesGroup):
    amount = State()


class DiceBet(StatesGroup):
    amount = State()


class SlotsBet(StatesGroup):
    amount = State()


class BjBet(StatesGroup):
    amount = State()


# Колесо фортуны: (множитель, вес в %), EV = 95.5%
WHEEL = [(0.0, 30), (0.5, 25), (1.0, 20), (1.5, 10),
         (2.0, 8), (3.0, 4), (5.0, 2), (10.0, 1)]

SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "💎", "7️⃣"]


# ================= УТИЛИТЫ =================

def spin_wheel() -> float:
    r = random.uniform(0, 100)
    acc = 0
    for mult, w in WHEEL:
        acc += w
        if r < acc:
            return mult
    return 0.0


def mines_mult(opened: int, mines: int) -> float:
    m = 1.0
    for i in range(opened):
        m *= (25 - i) / (25 - mines - i)
    return m * (0.98 ** opened)


def crash_point() -> float:
    """Честный краш-поинт с эджем 3%: P(краш >= m) = 0.97 / m."""
    u = random.random()
    p = max(1.0, math.floor((0.97 / max(u, 1e-9)) * 100) / 100)
    return min(p, 100.0)


def crash_mult(elapsed: float) -> float:
    return round(1 + 0.25 * elapsed + 0.02 * elapsed * elapsed, 2)


async def has_active_game(uid) -> bool:
    return await get_game(uid) is not None


# ================= МИНЫ (состояние в БД) =================

def mines_kb(opened):
    rows = []
    for r in range(5):
        row = []
        for ci in range(5):
            i = r * 5 + ci
            row.append(btn("✅" if i in opened else "⬜", f"mn:c:{i}"))
        rows.append(row)
    rows.append([btn("💰 Забрать выигрыш", "mn:cash")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mines_grid_text(g: dict, reveal: bool = False) -> str:
    rows = []
    for r in range(5):
        line = []
        for ci in range(5):
            i = r * 5 + ci
            if reveal:
                if i in g["mines"]:
                    line.append("💥")
                elif i in g["opened"]:
                    line.append("✅")
                else:
                    line.append("⬛")
            else:
                line.append("✅" if i in g["opened"] else "⬜")
        rows.append("".join(line))
    return "\n".join(rows)


def mines_text(g: dict) -> str:
    pot = int(g["bet"] * g["mult"])
    return (f"🧨 Мины: {g['mines_n']} | Открыто: {len(g['opened'])}/{25 - g['mines_n']}\n"
            f"💰 Ставка: {g['bet']} | Кэф: {g['mult']:.2f}x | Выигрыш: {pot}")


@games_router.callback_query(F.data == "gm:mines")
async def mines_menu(c: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("💣 1 мина", "gm:mn:1"), btn("💣💣💣 3 мины", "gm:mn:3")],
        [btn("💣 x5 мин", "gm:mn:5"), btn("💣 x10 мин", "gm:mn:10")],
        [btn("⬅️ Назад", "gm:back")],
    ])
    await c.message.edit_text("🧨 <b>Мины</b>\nВыберите количество мин:", parse_mode="HTML", reply_markup=kb)
    await c.answer()


@games_router.callback_query(F.data == "gm:back")
async def games_back(c: CallbackQuery):
    await c.message.edit_text("🎮 <b>Игры:</b>", parse_mode="HTML", reply_markup=games_kb())
    await c.answer()


@games_router.callback_query(F.data.startswith("gm:mn:"))
async def mines_ask_bet(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    mines_n = int(c.data.split(":")[2])
    await state.update_data(mines=mines_n)
    await state.set_state(MinesBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🧨 Мины: {mines_n}\nВведите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(MinesBet.amount)
async def mines_start(m: Message, state: FSMContext):
    data = await state.get_data()
    mines_n = data["mines"]
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    cells = random.sample(range(25), mines_n)
    g = {"bet": amount, "mines": list(cells), "mines_n": mines_n,
         "opened": [], "mult": 1.0}
    await save_game(m.from_user.id, "mines", g, amount)
    await state.clear()
    await m.answer(mines_text(g), reply_markup=mines_kb([]))


@games_router.callback_query(F.data.startswith("mn:c:"))
async def mines_open(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "mines":
        return await c.answer("Игра не найдена.", show_alert=True)
    g = game["state"]
    i = int(c.data.split(":")[2])
    if i in g["opened"]:
        return await c.answer()
    if i in g["mines"]:
        await record_bet(uid, None, "mines", "mines", g["bet"], g["mult"], "lose")
        grid = mines_grid_text(g, reveal=True)
        await delete_game(uid)
        await c.message.edit_text(
            f"💥 <b>Бах! Вы попали на мину.</b>\nСтавка {g['bet']} монет сгорела.\n\n{grid}",
            parse_mode="HTML")
        await c.answer("💥 Мина!")
        return
    g["opened"].append(i)
    g["mult"] = mines_mult(len(g["opened"]), g["mines_n"])
    if len(g["opened"]) >= 15:
        await ach.grant(bot, uid, "mines_15")
    if len(g["opened"]) >= 25 - g["mines_n"]:
        pot = int(g["bet"] * g["mult"])
        await record_bet(uid, None, "mines", "mines", g["bet"], g["mult"], "win")
        await add_balance(uid, pot)
        grid = mines_grid_text(g, reveal=True)
        await delete_game(uid)
        await c.message.edit_text(
            f"🏆 <b>Все клетки открыты!</b>\nВыигрыш: {pot} монет\n\n{grid}",
            parse_mode="HTML")
        await c.answer("🏆")
        return
    await save_game(uid, "mines", g, g["bet"])
    await c.message.edit_text(mines_text(g), reply_markup=mines_kb(g["opened"]))
    await c.answer()


@games_router.callback_query(F.data == "mn:cash")
async def mines_cash(c: CallbackQuery):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "mines":
        return await c.answer("Игра не найдена.", show_alert=True)
    g = game["state"]
    pot = int(g["bet"] * g["mult"])
    await record_bet(uid, None, "mines", "mines", g["bet"], g["mult"], "win")
    await add_balance(uid, pot)
    grid = mines_grid_text(g, reveal=True)
    profit = pot - g["bet"]
    await delete_game(uid)
    await c.message.edit_text(
        f"💰 <b>Забрано: {pot} монет</b> (профит {profit:+d})\n\n{grid}",
        parse_mode="HTML")
    await c.answer()


# ================= CRASH =================

def crash_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("💰 Забрать", "cr:cash")],
    ])


@games_router.callback_query(F.data == "gm:crash")
async def crash_ask(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    await state.set_state(CrashBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🚀 <b>Crash</b>\nКоэффициент растёт — успейте забрать до взрыва!\n\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", parse_mode="HTML", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(CrashBet.amount)
async def crash_start(m: Message, state: FSMContext, bot: Bot):
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    point = crash_point()
    t0 = time.monotonic()
    await save_game(m.from_user.id, "crash", {"point": point, "t0": t0}, amount)
    msg = await m.answer("🚀 Старт! Множитель: <b>1.00x</b>", parse_mode="HTML",
                         reply_markup=crash_kb())
    task = asyncio.create_task(crash_run(bot, m.chat.id, m.from_user.id, msg.message_id, amount, point, t0))
    CRASH_TASKS[m.from_user.id] = task


async def crash_run(bot: Bot, chat_id: int, uid: int, msg_id: int, bet: int, point: float, t0: float):
    try:
        while True:
            await asyncio.sleep(0.6)
            game = await get_game(uid)
            if not game or game["game"] != "crash":
                CRASH_TASKS.pop(uid, None)
                return  # уже рассчитано (cashout/перезапуск)
            cur = crash_mult(time.monotonic() - t0)
            if cur >= point:
                await record_bet(uid, None, "crash", "crash", bet, point, "lose")
                await delete_game(uid)
                await bot.edit_message_text(
                    f"💥 <b>Краш на {point:.2f}x!</b>\nСтавка {bet} монет сгорела.",
                    chat_id=chat_id, message_id=msg_id, parse_mode="HTML")
                CRASH_TASKS.pop(uid, None)
                return
            try:
                await bot.edit_message_text(
                    f"🚀 Множитель: <b>{cur:.2f}x</b>\nВыигрыш: {int(bet * cur)}",
                    chat_id=chat_id, message_id=msg_id, parse_mode="HTML", reply_markup=crash_kb())
            except Exception:
                pass
    except asyncio.CancelledError:
        return
    except Exception:
        CRASH_TASKS.pop(uid, None)


@games_router.callback_query(F.data == "cr:cash")
async def crash_cash(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "crash":
        return await c.answer("Игра не найдена.", show_alert=True)
    g = game["state"]
    cur = crash_mult(time.monotonic() - g["t0"])
    task = CRASH_TASKS.pop(uid, None)
    if task:
        task.cancel()
    if cur >= g["point"]:
        await record_bet(uid, None, "crash", "crash", game["bet"], g["point"], "lose")
        await delete_game(uid)
        await c.message.edit_text(
            f"💥 Не успели! Краш на {g['point']:.2f}x.\nСтавка {game['bet']} монет сгорела.",
            parse_mode="HTML")
        await c.answer("💥 Поздно!")
        return
    pot = int(game["bet"] * cur)
    await record_bet(uid, None, "crash", "crash", game["bet"], cur, "win")
    await add_balance(uid, pot)
    await delete_game(uid)
    await c.message.edit_text(
        f"💰 <b>Забрано на {cur:.2f}x: {pot} монет</b> (профит {pot - game['bet']:+d})",
        parse_mode="HTML")
    await c.answer()
    await ach.check_bet(bot, uid, cur)


# ================= МОНЕТКА (орёл/решка + удвоение) =================

COIN_SIDES = {"heads": ("Орёл", "🦅"), "tails": ("Решка", "🪙")}


@games_router.callback_query(F.data == "gm:coin")
async def coin_choose(c: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🦅 Орёл", "cf:side:heads"), btn("🪙 Решка", "cf:side:tails")],
        [btn("⬅️ Назад", "gm:back")],
    ])
    await c.message.edit_text("🪙 <b>Монетка</b>\nУгадайте сторону. Выплата x1.95\n"
                              "После выигрыша можно удвоить всё подряд!",
                              parse_mode="HTML", reply_markup=kb)
    await c.answer()


@games_router.callback_query(F.data.startswith("cf:side:"))
async def coin_ask_bet(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    side = c.data.split(":")[2]
    await state.update_data(side=side)
    await state.set_state(CoinBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🪙 Ваша сторона: {COIN_SIDES[side][0]}\nВведите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(CoinBet.amount)
async def coin_start(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    side = data["side"]
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    await bot.send_chat_action(m.chat.id, "typing")
    await asyncio.sleep(1)
    await coin_flip(bot, m.chat.id, m.from_user.id, amount, side)


async def coin_flip(bot: Bot, chat_id: int, uid: int, amount: int, side: str):
    result = random.choice(["heads", "tails"])
    win = result == side
    pot = int(amount * 1.95) if win else 0
    await record_bet(uid, None, "coin", side, amount, 1.95 if win else 0.0,
                     "win" if win else "lose")
    if win:
        await add_balance(uid, pot)
        await save_game(uid, "coin", {"amount": pot, "side": side}, pot)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn(f"🔄 Удвоить {pot}", "cf:dbl")],
            [btn(f"💰 Забрать {pot}", "cf:take")],
        ])
        await bot.send_message(
            chat_id,
            f"🪙 Выпала сторона: {COIN_SIDES[result][0]} — <b>вы угадали!</b>\n"
            f"💰 +{pot} монет. Рискнёте удвоить?", parse_mode="HTML", reply_markup=kb)
    else:
        await delete_game(uid)
        await bot.send_message(
            chat_id,
            f"🪙 Выпала сторона: {COIN_SIDES[result][0]} — <b>не угадали.</b>\n"
            f"Ставка {amount} сгорела.", parse_mode="HTML")


@games_router.callback_query(F.data == "cf:take")
async def coin_take(c: CallbackQuery):
    """Забрать выигрыш в монетке и завершить игру."""
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "coin":
        return await c.answer("Нечего забирать.", show_alert=True)
    pot = game["state"]["amount"]
    await delete_game(uid)
    await c.message.edit_text(
        f"💰 <b>Забрано: {pot} монет.</b> Игра завершена, приходите ещё!",
        parse_mode="HTML")
    await c.answer()


@games_router.callback_query(F.data == "cf:dbl")
async def coin_double(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "coin":
        return await c.answer("Нечего удваивать.", show_alert=True)
    g = game["state"]
    amount = g["amount"]
    if not await take_balance(uid, amount):
        await delete_game(uid)
        return await c.answer("Недостаточно монет — выигрыш уже потрачен.", show_alert=True)
    await c.answer("🪙 Подбрасываем...")
    await coin_flip(bot, c.message.chat.id, uid, amount, g["side"])


# ================= КОСТИ (больше/меньше) =================

@games_router.callback_query(F.data == "gm:dice")
async def dice_choose(c: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("📈 Больше 3 (4-6) x1.9", "dc:side:hi"), btn("📉 Меньше 4 (1-3) x1.9", "dc:side:lo")],
        [btn("⬅️ Назад", "gm:back")],
    ])
    await c.message.edit_text(
        "🎲 <b>Кости</b>\nБот кидает кубик. Угадайте: больше 3 или меньше 4. Выплата x1.9",
        parse_mode="HTML", reply_markup=kb)
    await c.answer()


@games_router.callback_query(F.data.startswith("dc:side:"))
async def dice_ask_bet(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    side = c.data.split(":")[2]
    await state.update_data(side=side)
    await state.set_state(DiceBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🎲 Ваш выбор: {'Больше 3' if side == 'hi' else 'Меньше 4'}\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(DiceBet.amount)
async def dice_play(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    side = data["side"]
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    msg = await bot.send_dice(m.chat.id, emoji="🎲")
    value = msg.dice.value
    await asyncio.sleep(3)
    win = (value > 3) if side == "hi" else (value < 4)
    pot = int(amount * 1.9) if win else 0
    await record_bet(m.from_user.id, None, "dice", side, amount, 1.9 if win else 0.0,
                     "win" if win else "lose")
    if win:
        await add_balance(m.from_user.id, pot)
        await m.answer(f"🎲 Выпало <b>{value}</b> — вы угадали!\n💰 +{pot} монет", parse_mode="HTML")
    else:
        await m.answer(f"🎲 Выпало <b>{value}</b> — не угадали.\nСтавка {amount} сгорела.",
                       parse_mode="HTML")


# ================= СЛОТЫ (send_dice 🎰) =================

@games_router.callback_query(F.data == "gm:slots")
async def slots_ask(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    await state.set_state(SlotsBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        "🎰 <b>Слоты</b>\n💎💎💎 = x20 | 7️⃣7️⃣7️⃣ = x40 | любые три = x8 | две подряд = x2\n\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", parse_mode="HTML", reply_markup=cancel_kb())
    await c.answer()


def slot_reels(value: int):
    """value 1..64 (Telegram 🎰) -> три символа (base-5)."""
    n = (value - 1) % 125
    return [SLOT_SYMBOLS[(n // 25) % 5], SLOT_SYMBOLS[(n // 5) % 5], SLOT_SYMBOLS[n % 5]]


def slot_payout(reels) -> float:
    if reels[0] == reels[1] == reels[2]:
        return 40.0 if reels[0] == "7️⃣" else (20.0 if reels[0] == "💎" else 8.0)
    if reels[0] == reels[1] or reels[1] == reels[2]:
        return 2.0
    return 0.0


@games_router.message(SlotsBet.amount)
async def slots_play(m: Message, state: FSMContext, bot: Bot):
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    msg = await bot.send_dice(m.chat.id, emoji="🎰")
    reels = slot_reels(msg.dice.value)
    mult = slot_payout(reels)
    await asyncio.sleep(3)
    pot = int(amount * mult)
    await record_bet(m.from_user.id, None, "slots", "-".join(reels), amount, mult,
                     "win" if pot > 0 else "lose")
    if pot > 0:
        await add_balance(m.from_user.id, pot)
    line = "".join(reels)
    if pot > 0:
        await m.answer(f"🎰 {line}\n💰 Выигрыш: {pot} монет (x{mult:g})!", parse_mode="HTML")
    else:
        await m.answer(f"🎰 {line}\nНе повезло, ставка {amount} сгорела.", parse_mode="HTML")


# ================= БЛЭКДЖЕК =================

BJ_SUITS = ["♠️", "♥️", "♦️", "♣️"]
BJ_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def bj_draw():
    """Карта с мастью, привязанной один раз при роздаче."""
    return random.choice(BJ_RANKS) + random.choice(BJ_SUITS)


def bj_rank(c: str) -> str:
    return c[:-2]  # отрезаем масть (два символа юникода: знак + VS16)


def bj_value(cards) -> int:
    total, aces = 0, 0
    for c in cards:
        r = bj_rank(c)
        if r in ("J", "Q", "K"):
            total += 10
        elif r == "A":
            total += 11
            aces += 1
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def bj_str(cards, hide_first=False) -> str:
    out = []
    for i, c in enumerate(cards):
        out.append("🂠" if hide_first and i == 0 else c)
    return " ".join(out)


def bj_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🃏 Ещё", "bj:hit"), btn("✋ Хватит", "bj:stand")],
    ])


@games_router.callback_query(F.data == "gm:bj")
async def bj_ask(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    await state.set_state(BjBet.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🃏 <b>Блэкджек</b>\nБлэкджек платит x2.5, победа x2, ничья — возврат.\n\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", parse_mode="HTML", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(BjBet.amount)
async def bj_start(m: Message, state: FSMContext, bot: Bot):
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    g = {"bet": amount, "player": [bj_draw(), bj_draw()], "dealer": [bj_draw(), bj_draw()]}
    pv, dv = bj_value(g["player"]), bj_value(g["dealer"])
    if pv == 21:
        # блэкджек игрока
        if dv == 21:
            await record_bet(m.from_user.id, None, "bj", "bj", amount, 1.0, "win")
            await add_balance(m.from_user.id, amount)
            await m.answer(f"🃏 У вас блэкджек, у дилера тоже! Ничья — возврат.\n"
                           f"Вы: {bj_str(g['player'])} | Дилер: {bj_str(g['dealer'])}", parse_mode="HTML")
        else:
            pot = int(amount * 2.5)
            await record_bet(m.from_user.id, None, "bj", "bj", amount, 2.5, "win")
            await add_balance(m.from_user.id, pot)
            await m.answer(f"🃏 <b>Блэкджек!</b>\nВы: {bj_str(g['player'])}\n"
                           f"💰 Выигрыш: {pot} монет (x2.5)", parse_mode="HTML")
            await ach.check_bet(bot, m.from_user.id, 2.5)
        return
    await save_game(m.from_user.id, "bj", g, amount)
    await m.answer(
        f"🃏 <b>Ваши карты:</b> {bj_str(g['player'])} ({pv})\n"
        f"Дилер: {bj_str(g['dealer'], hide_first=True)}\n\nЕщё или хватит?",
        parse_mode="HTML", reply_markup=bj_kb())


@games_router.callback_query(F.data == "bj:hit")
async def bj_hit(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "bj":
        return await c.answer("Игра не найдена.", show_alert=True)
    g = game["state"]
    g["player"].append(bj_draw())
    pv = bj_value(g["player"])
    if pv > 21:
        await record_bet(uid, None, "bj", "bj", g["bet"], 0.0, "lose")
        await delete_game(uid)
        await c.message.edit_text(
            f"💥 <b>Перебор ({pv})!</b>\nВаши карты: {bj_str(g['player'])}\n"
            f"Ставка {g['bet']} сгорела.", parse_mode="HTML")
        await c.answer("💥 Перебор")
        return
    if pv == 21:
        await save_game(uid, "bj", g, g["bet"])
        await c.answer("21! Дилер открывает карты...")
        await bj_dealer_play(c.message.chat.id, uid, g, bot)
        return
    await save_game(uid, "bj", g, g["bet"])
    await c.message.edit_text(
        f"🃏 <b>Ваши карты:</b> {bj_str(g['player'])} ({pv})\n"
        f"Дилер: {bj_str(g['dealer'], hide_first=True)}\n\nЕщё или хватит?",
        parse_mode="HTML", reply_markup=bj_kb())
    await c.answer()


@games_router.callback_query(F.data == "bj:stand")
async def bj_stand(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    game = await get_game(uid)
    if not game or game["game"] != "bj":
        return await c.answer("Игра не найдена.", show_alert=True)
    g = game["state"]
    await c.answer("Открываем карты...")
    await bj_dealer_play(c.message.chat.id, uid, g, bot)


async def bj_dealer_play(chat_id: int, uid: int, g: dict, bot: Bot):
    while bj_value(g["dealer"]) < 17:
        g["dealer"].append(bj_draw())
    pv, dv = bj_value(g["player"]), bj_value(g["dealer"])
    bet = g["bet"]
    await delete_game(uid)
    if dv > 21 or pv > dv:
        pot = int(bet * 2.0)
        await record_bet(uid, None, "bj", "bj", bet, 2.0, "win")
        await add_balance(uid, pot)
        text = (f"🏆 <b>Вы выиграли!</b>\nВы: {bj_str(g['player'])} ({pv}) | "
                f"Дилер: {bj_str(g['dealer'])} ({dv})\n💰 +{pot} монет")
        await ach.check_bet(bot, uid, 2.0)
    elif pv == dv:
        await record_bet(uid, None, "bj", "bj", bet, 1.0, "win")
        await add_balance(uid, bet)
        text = (f"🤝 <b>Ничья.</b>\nВы: {bj_str(g['player'])} ({pv}) | "
                f"Дилер: {bj_str(g['dealer'])} ({dv})\nВозврат {bet} монет.")
    else:
        await record_bet(uid, None, "bj", "bj", bet, 0.0, "lose")
        text = (f"❌ <b>Дилер выиграл.</b>\nВы: {bj_str(g['player'])} ({pv}) | "
                f"Дилер: {bj_str(g['dealer'])} ({dv})\nСтавка {bet} сгорела.")
    await bot.send_message(chat_id, text, parse_mode="HTML")


# ================= КОЛЕСО ФОРТУНЫ =================

@games_router.callback_query(F.data == "gm:wheel")
async def wheel_ask_bet(c: CallbackQuery, state: FSMContext):
    if await has_active_game(c.from_user.id):
        return await c.answer("Сначала завершите текущую игру!", show_alert=True)
    await state.set_state(WheelBet.amount)
    bal = await get_balance(c.from_user.id)
    sectors = ", ".join(f"{m:g}x ({w}%)" for m, w in WHEEL)
    await c.message.edit_text(
        f"🎡 <b>Колесо фортуны</b>\n\nСектора: {sectors}\n\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", parse_mode="HTML", reply_markup=cancel_kb())
    await c.answer()


@games_router.message(WheelBet.amount)
async def wheel_spin(m: Message, state: FSMContext, bot: Bot):
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    await state.clear()
    mult = spin_wheel()
    pot = int(amount * mult)
    status = "win" if mult >= 1.0 else "lose"
    await record_bet(m.from_user.id, None, "wheel", "wheel", amount, mult, status)
    await add_balance(m.from_user.id, pot)
    diff = pot - amount
    if mult == 0:
        text = f"🎡 Колесо остановилось на <b>0x</b>...\nСтавка {amount} сгорела. 😢"
    elif pot >= amount:
        text = f"🎡 Выпало <b>{mult:g}x</b>!\nВыигрыш: {pot} монет ({diff:+d}) 🎉"
    else:
        text = f"🎡 Выпало <b>{mult:g}x</b>\nВозврат: {pot} монет ({diff:+d})"
    await m.answer(text, parse_mode="HTML")
    if mult >= 10:
        await ach.grant(bot, m.from_user.id, "wheel_x10")
    await ach.check_bet(bot, m.from_user.id, mult)
