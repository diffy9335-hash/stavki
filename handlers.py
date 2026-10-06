from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import html
from config import (MIN_BET, ADMIN_IDS, REF_BONUS_REFERRER, REF_BONUS_NEW,
                    TOP_SIZE, TOP_MIN_COEF, CHANNEL_ID, ACHIEVEMENTS)
from database import (register_user, get_user, get_stats, activate_promo,
                      claim_bonus, list_matches, get_match, get_balance,
                      take_balance, add_balance, record_bet, get_user_bets,
                      get_ref_stats, top_balance, top_day_wins, top_profit,
                      top_referrers, get_user_coupons,
                      send_gift, user_achievements,
                      get_pending_coupon, add_coupon_leg, clear_coupon,
                      place_coupon)
from keyboards import main_menu, games_kb, btn
import achievements as ach

user_router = Router()

CANCEL_WORDS = {"отмена", "cancel", "стоп", "/cancel"}


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[btn("❌ Отмена", "ui:cancel")]])


@user_router.message(F.text.lower().in_(CANCEL_WORDS))
async def cancel_input(m: Message, state: FSMContext):
    """Слово «отмена» в любом запросе ввода — выход в главное меню."""
    if await state.get_state():
        await state.clear()
        await m.answer("❌ Действие отменено.", reply_markup=main_menu())


@user_router.callback_query(F.data == "ui:cancel")
async def cancel_cb(c: CallbackQuery, state: FSMContext):
    """Кнопка «Отмена» под запросами ввода (из любого модуля)."""
    await state.clear()
    try:
        await c.message.edit_text("❌ Отменено.")
    except Exception:
        pass
    await c.answer()


BET_NAMES = {"1": "П1", "X": "Ничья", "2": "П2",
             "btts_yes": "Обе забьют: Да", "btts_no": "Обе забьют: Нет"}


class PromoState(StatesGroup):
    code = State()


class BetState(StatesGroup):
    amount = State()


class ExprState(StatesGroup):
    amount = State()


class GiftState(StatesGroup):
    data = State()


def fmt(v):
    return f"+{v}" if v >= 0 else str(v)


async def is_subscribed(bot: Bot, uid: int) -> bool:
    if not CHANNEL_ID:
        return True
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return True  # канал недоступен — не блокируем


# ---------------- СТАРТ ----------------

@user_router.message(CommandStart())
async def start(m: Message, command: CommandObject, bot: Bot):
    ref_id = 0
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id = int(command.args[4:])
        except ValueError:
            ref_id = 0
    is_new, rewarded = await register_user(m.from_user.id, m.from_user.username, ref_id)
    await m.answer(
        "🎲 <b>Добро пожаловать в ставочного бота!</b>\n\n"
        "• ⚽ <b>События</b> — ставки на матчи и экспрессы\n"
        "• 🎮 <b>Игры</b> — мины, краш, слоты, блэкджек...\n"
        "• 🎁 <b>Бонус</b> — каждый день, серия растёт!\n"
        "• 🏆 <b>Топ</b> — лучшие игроки и призы\n"
        "• 👥 <b>Рефералы</b> — приглашай друзей за монеты\n"
        "• 👤 <b>Профиль</b> — статистика, промокоды, переводы",
        reply_markup=main_menu(), parse_mode="HTML")
    if rewarded:
        await m.answer(f"🎉 Вы пришли по приглашению! Бонус: <b>+{REF_BONUS_NEW} монет</b>.",
                       parse_mode="HTML")
        await ach.grant(bot, ref_id, "ref_1")
        refs = await get_ref_stats(ref_id)
        if refs["count"] >= 10:
            await ach.grant(bot, ref_id, "ref_10")
        try:
            await bot.send_message(
                ref_id,
                f"👥 По вашей ссылке зарегистрировался новый игрок!\n"
                f"Бонус: <b>+{REF_BONUS_REFERRER} монет</b>.", parse_mode="HTML")
        except Exception:
            pass


# ---------------- РЕФЕРАЛЫ ----------------

@user_router.message(F.text == "👥 Рефералы")
async def referrals(m: Message, bot: Bot):
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=ref_{m.from_user.id}"
    st = await get_ref_stats(m.from_user.id)
    await m.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей по своей ссылке:\n<code>{link}</code>\n\n"
        f"🎁 Вы получаете: <b>+{REF_BONUS_REFERRER}</b> монет за каждого друга\n"
        f"🎁 Друг получает: <b>+{REF_BONUS_NEW}</b> монет к стартовому балансу\n\n"
        f"📊 Приглашено: <b>{st['count']}</b>\n"
        f"💰 Заработано: <b>{st['earned']}</b> монет",
        parse_mode="HTML", disable_web_page_preview=True)


# ---------------- ТОПЫ ----------------

def _name(row) -> str:
    if row.get("username"):
        return "@" + html.escape(row["username"])
    return f"ID {str(row['user_id'])[-4:]}"


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _event_title(r) -> str:
    if r["game"] == "mines":
        return "🧨 Мины"
    if r["game"] == "wheel":
        return "🎡 Колесо"
    if r["game"] == "crash":
        return "🚀 Crash"
    if r["game"] == "coin":
        return "🪙 Монетка"
    if r["game"] == "dice":
        return "🎲 Кости"
    if r["game"] == "slots":
        return "🎰 Слоты"
    if r["game"] == "bj":
        return "🃏 Блэкджек"
    t = BET_NAMES.get(r["bet_type"], r["bet_type"])
    return f"⚽ {html.escape(r['team1'] or '?')} — {html.escape(r['team2'] or '?')} ({t})"


@user_router.message(F.text == "🏆 Топ")
async def top_menu(m: Message):
    ex = tuple(ADMIN_IDS)
    rich = await top_balance(TOP_SIZE, ex)
    lines = [f"🏆 <b>Топ-{TOP_SIZE} по балансу</b>\n"]
    if not rich:
        lines.append("Пока пусто.")
    for i, r in enumerate(rich, 1):
        lines.append(f"{MEDALS.get(i, f'{i}.')} {_name(r)} — <b>{r['balance']}</b> монет")

    wins = await top_day_wins(TOP_SIZE, TOP_MIN_COEF, ex)
    lines.append(f"\n🔥 <b>Топ дня — большие кэфы</b> (за 24 ч, от x{TOP_MIN_COEF:g})\n")
    if not wins:
        lines.append("Пока никто не поймал большой кэф.")
    for i, r in enumerate(wins, 1):
        lines.append(
            f"{MEDALS.get(i, f'{i}.')} {_name(r)} — <b>x{r['coefficient']:.2f}</b>\n"
            f"    {_event_title(r)}\n"
            f"    Ставка {r['amount']} → выигрыш <b>{r['potential_win']}</b>")

    week = await top_profit(7, 3, ex)
    if week:
        lines.append("\n📅 <b>Топ недели по прибыли</b> (призы выдаёт админ)\n")
        for i, r in enumerate(week, 1):
            lines.append(f"{MEDALS.get(i, f'{i}.')} {_name(r)} — {fmt(r['profit'])} монет")

    month = await top_profit(30, 3, ex)
    if month:
        lines.append("\n🗓 <b>Топ месяца по прибыли</b>\n")
        for i, r in enumerate(month, 1):
            lines.append(f"{MEDALS.get(i, f'{i}.')} {_name(r)} — {fmt(r['profit'])} монет")

    refs = await top_referrers(TOP_SIZE, ex)
    lines.append("\n👥 <b>Топ рефералов</b> (приглашено друзей)\n")
    if not refs:
        lines.append("Пока никого не пригласили.")
    for i, r in enumerate(refs, 1):
        lines.append(f"{MEDALS.get(i, f'{i}.')} {_name(r)} — <b>{r['count']}</b> 👥")
    await m.answer("\n".join(lines), parse_mode="HTML")


# ---------------- ПРОФИЛЬ ----------------

@user_router.message(F.text == "👤 Профиль")
async def profile(m: Message, bot: Bot):
    u = await get_user(m.from_user.id)
    s = await get_stats(m.from_user.id)
    if u["balance"] >= 1000:
        await ach.grant(bot, m.from_user.id, "rich")
    await m.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{m.from_user.id}</code>\n"
        f"💰 Баланс: <b>{u['balance']}</b> монет\n"
        f"📊 Всего поставлено: <b>{s['wagered']}</b> монет\n"
        f"🔥 Серия бонусов: <b>{u.get('streak') or 0}</b> дн.\n\n"
        f"📈 Прибыль/убыток:\n"
        f"  • За день: <b>{fmt(s['day'])}</b>\n"
        f"  • За неделю: <b>{fmt(s['week'])}</b>\n"
        f"  • За всё время: <b>{fmt(s['total'])}</b>\n\n"
        f"🏆 Винрейт: <b>{s['winrate']}%</b> ({s['wins']}/{s['bets']})",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("🎟 Промокод", "pr:promo"), btn("🎁 Перевод", "gf:start")],
            [btn("🏅 Достижения", "ach:view")],
        ]))


# ---------------- ДОСТИЖЕНИЯ ----------------

@user_router.callback_query(F.data == "ach:view")
async def ach_view(c: CallbackQuery):
    mine = await user_achievements(c.from_user.id)
    lines = ["🏅 <b>Достижения:</b>"]
    for code, (name, reward) in ACHIEVEMENTS.items():
        mark = "✅" if code in mine else "⬜"
        lines.append(f"{mark} {name} — {reward} монет")
    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()


# ---------------- ПЕРЕВОДЫ / ПОДАРКИ ----------------

@user_router.callback_query(F.data == "gf:start")
async def gift_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(GiftState.data)
    await c.message.answer(
        "🎁 Перевод монет другому игроку\nВведите: <code>user_id сумма</code>\n"
        "(комиссия 10%, минимум 50)", reply_markup=cancel_kb())
    await c.answer()


@user_router.message(GiftState.data)
async def gift_done(m: Message, state: FSMContext):
    try:
        to_id, amount = map(int, m.text.split())
    except ValueError:
        return await m.answer("Неверный формат. Пример: <code>123456789 100</code>", reply_markup=cancel_kb())
    ok, msg = await send_gift(m.from_user.id, to_id, amount)
    await state.clear()
    if ok:
        await m.answer(msg)
        try:
            await m.bot.send_message(to_id, f"🎁 Игрок {m.from_user.id} отправил вам {amount} монет!")
        except Exception:
            pass
    else:
        await m.answer(f"❌ {msg}")


# ---------------- ПРОМОКОДЫ ----------------

@user_router.callback_query(F.data == "pr:promo")
async def promo_enter(c: CallbackQuery, state: FSMContext):
    await state.set_state(PromoState.code)
    await c.message.answer("🎟 Введите промокод (или напишите «отмена»):",
                         reply_markup=cancel_kb())
    await c.answer()


@user_router.message(PromoState.code)
async def promo_done(m: Message, state: FSMContext):
    ok, msg = await activate_promo(m.from_user.id, m.text)
    await state.clear()
    if ok:
        await m.answer(f"✅ {msg}")
    else:
        await m.answer(f"❌ {msg}")


# ---------------- БОНУС (с проверкой подписки и серией) ----------------

@user_router.message(F.text == "🎁 Бонус")
async def bonus(m: Message, bot: Bot):
    if not await is_subscribed(bot, m.from_user.id):
        rows = []
        if str(CHANNEL_ID).startswith("@"):
            rows.append([btn("📢 Подписаться", f"https://t.me/{CHANNEL_ID.lstrip('@')}")])
        rows.append([btn("✅ Я подписался", "bn:check")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        return await m.answer(
            f"🎁 Чтобы получать бонус, подпишитесь на канал {CHANNEL_ID}:",
            reply_markup=kb)
    await _do_bonus(m, bot)


@user_router.callback_query(F.data == "sub:check")
async def sub_check(c: CallbackQuery, bot: Bot):
    """Подтверждение обязательной подписки (кнопка из middleware)."""
    if await is_subscribed(bot, c.from_user.id):
        await c.message.edit_text("✅ Подписка подтверждена! Пользуйтесь ботом:")
        await c.answer("✅ Готово!")
    else:
        await c.answer("❌ Вы ещё не подписаны на @StavkiRofl!", show_alert=True)


@user_router.callback_query(F.data == "bn:check")
async def bonus_check(c: CallbackQuery, bot: Bot):
    if not await is_subscribed(bot, c.from_user.id):
        return await c.answer("❌ Вы ещё не подписаны!", show_alert=True)
    await _do_bonus(c.message, bot)


async def _do_bonus(msg_or_call_msg, bot: Bot):
    uid = msg_or_call_msg.chat.id
    ok, res, streak = await claim_bonus(uid)
    if ok:
        await bot.send_message(
            uid, f"🎁 Ежедневный бонус: <b>+{res} монет</b>!\n"
                 f"🔥 Серия: {streak} дн. (каждый день бонус растёт)", parse_mode="HTML")
    else:
        await bot.send_message(uid, f"⏳ {res}")


# ---------------- ИГРЫ ----------------

@user_router.message(F.text == "🎮 Игры")
async def games(m: Message):
    await m.answer("🎮 <b>Игры:</b>", parse_mode="HTML", reply_markup=games_kb())


# ---------------- СТАВКИ (мои) ----------------

@user_router.message(F.text == "💸 Ставки")
async def my_bets(m: Message):
    bets = await get_user_bets(m.from_user.id, status="pending", limit=20)
    coupon = await get_pending_coupon(m.from_user.id)
    lines = []
    if coupon and coupon["legs"]:
        legs_str = "\n".join(
            f"  • {l['team1']} — {l['team2']}: {BET_NAMES.get(l['bet_type'], l['bet_type'])} ({l['coef']})"
            for l in coupon["legs"])
        lines.append(f"🧾 <b>Экспресс (собирается):</b>\n{legs_str}")
    if not bets and not lines:
        text = "💸 Активных ставок нет."
    else:
        if bets:
            lines.append("💸 <b>Ваши активные ставки:</b>")
            for b in bets:
                name = BET_NAMES.get(b["bet_type"], b["bet_type"])
                if b["game"] == "match":
                    title = f"{b['team1']} — {b['team2']}"
                else:
                    title = {"mines": "🧨 Мины", "wheel": "🎡 Колесо", "crash": "🚀 Crash",
                             "coin": "🪙 Монетка", "dice": "🎲 Кости", "slots": "🎰 Слоты",
                             "bj": "🃏 Блэкджек"}.get(b["game"], b["game"])
                lines.append(f"• {title}\n  {name} | Ставка: {b['amount']} | Возм. выигрыш: {b['potential_win']}")
        text = "\n".join(lines)
    kb = [[btn("📜 История ставок", "bt:hist")]]
    if coupon and coupon["legs"]:
        kb.insert(0, [btn("💰 Поставить экспресс", "xp:stake"),
                      btn("🗑 Очистить", "xp:clear")])
    await m.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@user_router.callback_query(F.data == "bt:hist")
async def bet_history(c: CallbackQuery):
    bets = await get_user_bets(c.from_user.id, limit=20)
    if not bets:
        return await c.answer("История пуста.", show_alert=True)
    lines = ["📜 <b>История ставок:</b>"]
    for b in bets:
        name = BET_NAMES.get(b["bet_type"], b["bet_type"])
        title = {"mines": "🧨 Мины", "wheel": "🎡 Колесо", "crash": "🚀 Crash",
                 "coin": "🪙 Монетка", "dice": "🎲 Кости", "slots": "🎰 Слоты",
                 "bj": "🃏 Блэкджек"}.get(b["game"])
        if title is None:
            title = f"⚽ {b['team1']} — {b['team2']}"
        icon = {"win": "✅", "lose": "❌", "pending": "⏳"}.get(b["status"], "❔")
        lines.append(
            f"{icon} {title} | {name} | {b['amount']} x{b['coefficient']:.2f}"
            + (f" → <b>+{b['potential_win']}</b>" if b["status"] == "win" else ""))
    coupons = await get_user_coupons(c.from_user.id, limit=5)
    if coupons:
        lines.append("\n🧾 <b>Экспрессы:</b>")
        for cp in coupons:
            icon = {"win": "✅", "lose": "❌", "active": "⏳",
                    "canceled": "🚫"}.get(cp["status"], "❔")
            legs_str = " + ".join(
                f"{html.escape(l['team1'] or '?')}—{html.escape(l['team2'] or '?')} "
                f"{BET_NAMES.get(l['bet_type'], l['bet_type'])}"
                for l in cp["legs"][:3])
            more = " …" if len(cp["legs"]) > 3 else ""
            lines.append(
                f"{icon} x{cp['coef']:.2f} | {cp['amount']} → {cp['potential_win']}\n"
                f"    {legs_str}{more}")
    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()


# ---------------- СОБЫТИЯ ----------------

@user_router.message(F.text == "⚽ События")
async def events(m: Message):
    matches = await list_matches(open_only=True)
    if not matches:
        return await m.answer("⚽ Сейчас нет доступных матчей.")
    for mt in matches:
        await m.answer(
            f"⚽ <b>#{mt['id']} {mt['team1']} — {mt['team2']}</b>\n"
            f"🕒 {mt['start_time']}\n"
            f"П1: {mt['coef1']} | Ничья: {mt['coefx']} | П2: {mt['coef2']}\n"
            f"Обе забьют: Да {mt['coef_btts_yes']} | Нет {mt['coef_btts_no']}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn("🎯 Ставка", f"ev:bet:{mt['id']}"),
                 btn("🧾 В экспресс", f"ev:xadd:{mt['id']}")]
            ]))


# --- одиночная ставка ---

@user_router.callback_query(F.data.startswith("ev:bet:"))
async def bet_choose(c: CallbackQuery):
    mid = int(c.data.split(":")[2])
    mt = await get_match(mid)
    if not mt or mt["status"] != "open":
        return await c.answer("Матч недоступен.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"П1 ({mt['coef1']})", f"ev:out:{mid}:1"),
         btn(f"Ничья ({mt['coefx']})", f"ev:out:{mid}:X"),
         btn(f"П2 ({mt['coef2']})", f"ev:out:{mid}:2")],
        [btn(f"Обе забьют: Да ({mt['coef_btts_yes']})", f"ev:out:{mid}:btts_yes"),
         btn(f"Нет ({mt['coef_btts_no']})", f"ev:out:{mid}:btts_no")],
    ])
    await c.message.edit_text(
        f"🎯 <b>{mt['team1']} — {mt['team2']}</b>\nВыберите исход:", parse_mode="HTML", reply_markup=kb)
    await c.answer()


@user_router.callback_query(F.data.startswith("ev:out:"))
async def bet_amount(c: CallbackQuery, state: FSMContext):
    _, _, mid, bt = c.data.split(":")
    mt = await get_match(int(mid))
    if not mt or mt["status"] != "open":
        return await c.answer("Матч недоступен.", show_alert=True)
    coef = {"1": mt["coef1"], "X": mt["coefx"], "2": mt["coef2"],
            "btts_yes": mt["coef_btts_yes"], "btts_no": mt["coef_btts_no"]}[bt]
    await state.update_data(match_id=int(mid), bet_type=bt, coef=coef)
    await state.set_state(BetState.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.edit_text(
        f"🎯 {mt['team1']} — {mt['team2']}\n"
        f"Исход: <b>{BET_NAMES[bt]}</b> (кэф {coef})\n\n"
        f"Введите сумму ставки (мин. {MIN_BET}, баланс: {bal}):", parse_mode="HTML",
        reply_markup=cancel_kb())
    await c.answer()


@user_router.message(BetState.amount)
async def bet_place(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    mt = await get_match(data["match_id"])
    if not mt or mt["status"] != "open":
        await state.clear()
        return await m.answer("⛔ Матч уже недоступен для ставок (приём закрыт или матч удалён).")
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    pot = await record_bet(m.from_user.id, data["match_id"], "match",
                           data["bet_type"], amount, data["coef"])
    await state.clear()
    await m.answer(
        f"✅ Ставка принята!\n{BET_NAMES[data['bet_type']]} | {amount} монет\n"
        f"💰 Возможный выигрыш: {pot}")
    await ach.check_bet(bot, m.from_user.id, data["coef"])


# --- экспресс ---

@user_router.callback_query(F.data.startswith("ev:xadd:"))
async def expr_choose(c: CallbackQuery):
    mid = int(c.data.split(":")[2])
    mt = await get_match(mid)
    if not mt or mt["status"] != "open":
        return await c.answer("Матч недоступен.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"П1 ({mt['coef1']})", f"ev:xout:{mid}:1"),
         btn(f"Ничья ({mt['coefx']})", f"ev:xout:{mid}:X"),
         btn(f"П2 ({mt['coef2']})", f"ev:xout:{mid}:2")],
        [btn(f"Обе забьют: Да ({mt['coef_btts_yes']})", f"ev:xout:{mid}:btts_yes"),
         btn(f"Нет ({mt['coef_btts_no']})", f"ev:xout:{mid}:btts_no")],
    ])
    await c.message.edit_text(
        f"🧾 <b>{mt['team1']} — {mt['team2']}</b>\nВыберите исход для экспресса:",
        parse_mode="HTML", reply_markup=kb)
    await c.answer()


@user_router.callback_query(F.data.startswith("ev:xout:"))
async def expr_add(c: CallbackQuery):
    _, _, mid, bt = c.data.split(":")
    mt = await get_match(int(mid))
    if not mt or mt["status"] != "open":
        return await c.answer("Матч недоступен.", show_alert=True)
    coef = {"1": mt["coef1"], "X": mt["coefx"], "2": mt["coef2"],
            "btts_yes": mt["coef_btts_yes"], "btts_no": mt["coef_btts_no"]}[bt]
    count = await add_coupon_leg(c.from_user.id, int(mid), bt, coef)
    if count is None:
        return await c.answer("Максимум 5 исходов в экспрессе.", show_alert=True)
    await c.answer(f"Добавлено ({count}/5)")
    coupon = await get_pending_coupon(c.from_user.id)
    total_coef = 1.0
    lines = ["🧾 <b>Ваш экспресс:</b>"]
    for l in coupon["legs"]:
        total_coef *= l["coef"]
        lines.append(f"• {l['team1']} — {l['team2']}: {BET_NAMES.get(l['bet_type'], l['bet_type'])} ({l['coef']})")
    lines.append(f"\nОбщий кэф: <b>x{total_coef:.2f}</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("💰 Указать сумму", "xp:stake"), btn("🗑 Очистить", "xp:clear")],
    ])
    await c.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@user_router.callback_query(F.data == "xp:clear")
async def expr_clear(c: CallbackQuery):
    await clear_coupon(c.from_user.id)
    await c.answer("Экспресс очищен.")
    await c.message.edit_text("🧾 Экспресс очищен.")


@user_router.callback_query(F.data == "xp:stake")
async def expr_stake(c: CallbackQuery, state: FSMContext):
    coupon = await get_pending_coupon(c.from_user.id)
    if not coupon or len(coupon["legs"]) < 2:
        return await c.answer("Нужно минимум 2 исхода!", show_alert=True)
    await state.set_state(ExprState.amount)
    bal = await get_balance(c.from_user.id)
    await c.message.answer(f"🧾 Введите сумму ставки на экспресс (мин. {MIN_BET}, баланс: {bal}):",
                           reply_markup=cancel_kb())
    await c.answer()


@user_router.message(ExprState.amount)
async def expr_place(m: Message, state: FSMContext, bot: Bot):
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите целое число.", reply_markup=cancel_kb())
    if amount < MIN_BET:
        return await m.answer(f"Минимальная ставка — {MIN_BET} монет.", reply_markup=cancel_kb())
    coupon = await get_pending_coupon(m.from_user.id)
    if not coupon or len(coupon["legs"]) < 2:
        await state.clear()
        return await m.answer("Экспресс не найден (нужно минимум 2 исхода).")
    if any(l["mstatus"] != "open" for l in coupon["legs"]):
        await clear_coupon(m.from_user.id)
        await state.clear()
        return await m.answer("⛔ Один из матчей уже недоступен — экспресс расформирован, ничего не списано.")
    if not await take_balance(m.from_user.id, amount):
        return await m.answer("❌ Недостаточно монет.", reply_markup=cancel_kb())
    res = await place_coupon(m.from_user.id, amount)
    await state.clear()
    if not res:
        await add_balance(m.from_user.id, amount)  # возврат: деньги не должны теряться
        return await m.answer("Экспресс не найден. Сумма ставки возвращена на баланс.")
    await m.answer(
        f"✅ Экспресс принят! Кэф x{res['coef']:.2f}\n"
        f"💰 Возможный выигрыш: {res['potential']} монет")
    await ach.check_bet(bot, m.from_user.id, res["coef"])
