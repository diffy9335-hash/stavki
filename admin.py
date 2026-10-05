import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS, WEEKLY_PRIZES, MONTHLY_PRIZES, TOP_SIZE
from database import (get_user, add_balance, set_banned, create_promo, list_promos,
                      delete_promo, add_match, get_match, list_matches, update_coefs,
                      update_time, delete_match, search_users, admin_stats,
                      top_profit, all_user_ids, match_exists, get_user_bets)
from keyboards import btn
import services

admin_router = Router()

CANCEL_WORDS = {"отмена", "cancel", "стоп", "/cancel"}


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[btn("❌ Отмена", "ui:cancel")]])


@admin_router.message(F.text.lower().in_(CANCEL_WORDS), F.from_user.id.in_(ADMIN_IDS))
async def admin_cancel_input(m: Message, state: FSMContext):
    """«отмена» в любой админ-форме — выход в админ-меню (только для админов)."""
    if await state.get_state():
        await state.clear()
        await m.answer("❌ Действие отменено.", reply_markup=admin_menu_kb())


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


class AStates(StatesGroup):
    give = State()
    give2 = State()
    ban = State()
    unban = State()
    promo_new = State()
    match_teams = State()
    match_coefs = State()
    match_time = State()
    match_coefs_edit = State()
    match_time_edit = State()
    fin_result = State()
    fin_btts = State()
    res_result = State()
    res_btts = State()
    broadcast = State()
    search = State()


def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("💰 Выдать/забрать монеты", "adm:give"), btn("🔍 Поиск игрока", "adm:search")],
        [btn("🔨 Забанить", "adm:ban"), btn("✅ Разбанить", "adm:unban")],
        [btn("🎟 Промокоды", "adm:promos")],
        [btn("⚽ Матчи", "adm:matches"), btn("➕ Добавить матч", "adm:add")],
        [btn("📡 Матчи из API", "adm:api")],
        [btn("📣 Рассылка", "adm:bc"), btn("📊 Статистика", "adm:stats")],
        [btn("🏆 Призы недели", "adm:pw"), btn("🏆 Призы месяца", "adm:pm")],
        [btn("💾 Бэкап БД", "adm:backup")],
    ])


def match_detail_kb(mt: dict):
    mid = mt["id"]
    kb = []
    if mt["status"] == "open":
        kb.append([btn("✏️ Изменить кэфы", f"adm:cef:{mid}"),
                   btn("🕒 Изменить время", f"adm:tim:{mid}")])
        kb.append([btn("🏁 Завершить матч", f"adm:fin:{mid}")])
    else:
        kb.append([btn("✏️ Изменить результат", f"adm:res:{mid}")])
    kb.append([btn("🗑 Удалить матч", f"adm:del:{mid}")])
    kb.append([btn("⬅️ Назад", "adm:matches")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def match_text(mt: dict) -> str:
    return (f"⚽ <b>#{mt['id']} {mt['team1']} — {mt['team2']}</b>\n"
            f"🕒 {mt['start_time']} | Статус: {mt['status']}\n"
            f"П1: {mt['coef1']} | X: {mt['coefx']} | П2: {mt['coef2']}\n"
            f"Обе забьют: Да {mt['coef_btts_yes']} | Нет {mt['coef_btts_no']}\n"
            f"🏁 Результат: {mt['result'] or '—'} | Обе забьют: {mt['btts_result'] or '—'}")


def parse_coefs(text: str):
    import re
    text = re.sub(r"<[^>]*>", " ", text or "")
    parts = re.findall(r"\d+(?:[.,]\d+)?", text)
    if len(parts) != 5:
        return None
    coefs = [float(x.replace(",", ".")) for x in parts]
    if any(c <= 1.0 for c in coefs):
        return None
    return coefs


def parse_result(text: str):
    t = text.strip().upper()
    if t in ("1", "X", "2", "Х"):
        return "X" if t == "Х" else t
    return None


def parse_btts(text: str):
    t = text.strip().lower()
    if t in ("да", "yes", "д"):
        return "yes"
    if t in ("нет", "no", "н"):
        return "no"
    return None


# ---------------- ВХОД ----------------

@admin_router.message(Command("admin"))
async def admin_cmd(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🛠 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_menu_kb())


@admin_router.callback_query(F.data == "adm:menu")
async def adm_menu(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("Нет доступа.", show_alert=True)
    await c.message.edit_text("🛠 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_menu_kb())
    await c.answer()


# ---------------- МОНЕТЫ / БАН ----------------

@admin_router.callback_query(F.data == "adm:give")
async def adm_give(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите: <code>user_id сумма</code>\n(сумма отрицательная — забрать монеты)", reply_markup=cancel_kb())
    await state.set_state(AStates.give)
    await c.answer()


@admin_router.message(AStates.give)
async def give_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        uid, amount = map(int, m.text.split())
    except ValueError:
        return await m.answer("Неверный формат. Пример: <code>123456789 500</code>")
    u = await get_user(uid)
    if not u:
        return await m.answer("Пользователь не найден.")
    await add_balance(uid, amount)
    await m.answer(f"✅ Готово. Новый баланс {uid}: {u['balance'] + amount}")
    await state.clear()


@admin_router.callback_query(F.data.startswith("adm:give2:"))
async def adm_give2(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(":")[2])
    await state.update_data(uid=uid)
    await state.set_state(AStates.give2)
    await c.message.answer(f"Введите сумму для {uid} (отрицательная — забрать):", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.give2)
async def give2_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    try:
        amount = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите число.")
    u = await get_user(data["uid"])
    if not u:
        return await m.answer("Пользователь не найден.")
    await add_balance(data["uid"], amount)
    await m.answer(f"✅ Готово. Новый баланс {data['uid']}: {u['balance'] + amount}")
    await state.clear()


@admin_router.callback_query(F.data.in_({"adm:ban", "adm:unban"}))
async def adm_ban_ask(c: CallbackQuery, state: FSMContext):
    await state.set_state(AStates.ban if c.data == "adm:ban" else AStates.unban)
    await c.message.edit_text("Введите user_id:", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.ban)
async def ban_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        uid = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите число.")
    if not await get_user(uid):
        return await m.answer("Пользователь не найден.")
    await set_banned(uid, True)
    await m.answer(f"🔨 Пользователь {uid} забанен.")
    await state.clear()


@admin_router.message(AStates.unban)
async def unban_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        uid = int(m.text.strip())
    except ValueError:
        return await m.answer("Введите число.")
    await set_banned(uid, False)
    await m.answer(f"✅ Пользователь {uid} разбанен.")
    await state.clear()


# ---------------- ПОИСК ИГРОКА ----------------

@admin_router.callback_query(F.data == "adm:search")
async def adm_search(c: CallbackQuery, state: FSMContext):
    await state.set_state(AStates.search)
    await c.message.edit_text("Введите ID или ник игрока:", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.search)
async def search_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    rows = await search_users(m.text)
    await state.clear()
    if not rows:
        return await m.answer("Никого не найдено.")
    for u in rows[:5]:
        bets = await get_user_bets(u["user_id"], limit=5)
        bets_str = "\n".join(
            f"  • {b['game']} {b['bet_type']} | {b['amount']} | {b['status']}" for b in bets) or "  нет ставок"
        await m.answer(
            f"👤 <b>{u['user_id']}</b> @{u['username'] or '—'}\n"
            f"💰 Баланс: {u['balance']} | {'🔨 ЗАБАНЕН' if u['banned'] else 'активен'}\n"
            f"📅 Регистрация: {u['created_at']}\n"
            f"Последние ставки:\n{bets_str}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn("💰 Выдать", f"adm:give2:{u['user_id']}"),
                 btn("🔨 Бан", f"adm:ban2:{u['user_id']}")],
            ]))


@admin_router.callback_query(F.data.startswith("adm:ban2:"))
async def ban2(c: CallbackQuery):
    uid = int(c.data.split(":")[2])
    await set_banned(uid, True)
    await c.answer(f"🔨 {uid} забанен.")
    await c.message.answer(f"🔨 Пользователь {uid} забанен.")


# ---------------- ПРОМОКОДЫ ----------------

@admin_router.callback_query(F.data == "adm:promos")
async def adm_promos(c: CallbackQuery):
    rows = await list_promos()
    lines = ["🎟 <b>Промокоды:</b>"]
    kb = [[btn("➕ Создать промокод", "adm:pnew")]]
    for r in rows:
        lines.append(f"• <code>{r['code']}</code> — {r['amount']} монет, активаций {r['used']}/{r['max_activations']}"
                     + ("" if r["active"] else " (выключен)"))
        kb.append([btn(f"🗑 {r['code']}", f"adm:pdel:{r['code']}")])
    kb.append([btn("⬅️ Назад", "adm:menu")])
    if len(lines) == 1:
        lines.append("пусто")
    await c.message.edit_text("\n".join(lines), parse_mode="HTML",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()


@admin_router.callback_query(F.data == "adm:pnew")
async def promo_new(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите: <code>код сумма количество_активаций</code>\nПример: <code>BONUS500 500 10</code>", reply_markup=cancel_kb())
    await state.set_state(AStates.promo_new)
    await c.answer()


@admin_router.message(AStates.promo_new)
async def promo_new_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        code, amount, n = m.text.split()
        amount, n = int(amount), int(n)
    except ValueError:
        return await m.answer("Неверный формат. Пример: <code>BONUS500 500 10</code>")
    await create_promo(code, amount, n)
    await m.answer(f"✅ Промокод {code.upper()} создан: {amount} монет, {n} активаций.")
    await state.clear()


@admin_router.callback_query(F.data.startswith("adm:pdel:"))
async def promo_del(c: CallbackQuery):
    code = c.data.split(":", 2)[2]
    await delete_promo(code)
    await c.answer(f"Промокод {code} удалён.")
    await adm_promos(c)


# ---------------- РАССЫЛКА ----------------

@admin_router.callback_query(F.data == "adm:bc")
async def adm_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(AStates.broadcast)
    await c.message.edit_text("📣 Введите текст рассылки (поддерживается HTML):", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.broadcast)
async def bc_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    ids = await all_user_ids()
    sent, failed = 0, 0
    status = await m.answer(f"📣 Рассылка {len(ids)} пользователям...")
    for uid in ids:
        try:
            await m.bot.send_message(uid, m.text, parse_mode="HTML", disable_web_page_preview=True)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}")


# ---------------- СТАТИСТИКА ----------------

@admin_router.callback_query(F.data == "adm:stats")
async def adm_stats(c: CallbackQuery):
    s = await admin_stats()
    await c.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Игроков: <b>{s['players']}</b> (+{s['new_today']} за день)\n"
        f"🎲 Идёт игр сейчас: {s['games_now']}\n"
        f"💸 Оборот (поставлено): <b>{s['wagered']}</b> монет\n"
        f"💰 Выплачено выигрышей: <b>{s['paid']}</b> монет\n"
        f"🏦 Прибыль «казино»: <b>{s['wagered'] - s['paid']}</b> монет\n"
        f"⏳ Активных ставок: {s['pending_bets']}\n"
        f"🧾 Активных экспрессов: {s['active_coupons']}\n"
        f"⚽ Открытых матчей: {s['open_matches']}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔄 Обновить", "adm:stats"), btn("⬅️ Назад", "adm:menu")]]))
    await c.answer()


# ---------------- ПРИЗЫ ----------------

async def award_prizes(m_or_c, days: int, prizes: list, label: str):
    top = await top_profit(days, len(prizes), tuple(ADMIN_IDS))
    if not top:
        return await m_or_c.answer("Нет данных для расчёта.")
    lines = [f"🏆 <b>{label}</b> — призы:"]
    for i, r in enumerate(top):
        prize = prizes[i]
        await add_balance(r["user_id"], prize)
        lines.append(f"🥇 {r['user_id']} (@{r['username'] or '—'}) — {r['profit']:+d} → <b>+{prize}</b>")
        try:
            if hasattr(m_or_c, "bot"):
                await m_or_c.bot.send_message(r["user_id"],
                                              f"🏆 Вы заняли {i + 1}-е место в {label}! Награда: +{prize} монет!")
            else:
                await m_or_c.message.bot.send_message(r["user_id"],
                                                      f"🏆 Вы заняли {i + 1}-е место в {label}! Награда: +{prize} монет!")
        except Exception:
            pass
    text = "\n".join(lines) if len(lines) > 1 else "Нет данных."
    if hasattr(m_or_c, "message"):
        await m_or_c.message.answer(text, parse_mode="HTML")
    else:
        await m_or_c.answer(text, parse_mode="HTML")


@admin_router.callback_query(F.data == "adm:pw")
async def prizes_week(c: CallbackQuery):
    await award_prizes(c, 7, WEEKLY_PRIZES, "топе недели")
    await c.answer()


@admin_router.callback_query(F.data == "adm:pm")
async def prizes_month(c: CallbackQuery):
    await award_prizes(c, 30, MONTHLY_PRIZES, "топе месяца")
    await c.answer()


# ---------------- БЭКАП ----------------

@admin_router.callback_query(F.data == "adm:backup")
async def adm_backup(c: CallbackQuery):
    from database import DB_NAME
    try:
        await c.message.answer_document(FSInputFile(DB_NAME), caption="💾 Бэкап базы")
        await c.answer("Отправлено.")
    except Exception as e:
        await c.answer(f"Ошибка: {e}", show_alert=True)


# ---------------- МАТЧИ ИЗ API ----------------

@admin_router.callback_query(F.data == "adm:api")
async def adm_api(c: CallbackQuery):
    await c.answer("Загружаю матчи...")
    try:
        from api_matches import fetch_matches
        matches = await fetch_matches()
    except Exception as e:
        return await c.message.answer(f"❌ Ошибка загрузки: {e}")
    if not matches:
        return await c.message.answer(
            "❌ Нет источника матчей. Задайте ODDS_API_KEY в окружении "
            "или создайте файл matches.json (см. api_matches.py).")
    added, skipped = 0, 0
    for mt in matches:
        if await match_exists(mt["team1"], mt["team2"], mt.get("start_time", "")):
            skipped += 1
            continue
        await add_match(mt["team1"], mt["team2"], mt["coef1"], mt.get("coefx", 3.4),
                        mt["coef2"], mt.get("coef_btts_yes", 1.8), mt.get("coef_btts_no", 1.95),
                        mt.get("start_time", ""))
        added += 1
    await c.message.answer(f"✅ Добавлено матчей: {added}\n⏭ Уже было: {skipped}")


# ---------------- МАТЧИ ----------------

@admin_router.callback_query(F.data == "adm:matches")
async def adm_matches(c: CallbackQuery):
    rows = await list_matches()
    lines = ["⚽ <b>Матчи:</b>"]
    kb = []
    icons = {"open": "🟢", "closed": "⏸", "finished": "🏁"}
    for r in rows:
        lines.append(f"{icons.get(r['status'], '❔')} #{r['id']} {r['team1']} — {r['team2']} | {r['start_time']} | {r['status']}")
        kb.append([btn(f"⚙️ #{r['id']} {r['team1']}—{r['team2']}", f"adm:m:{r['id']}")])
    if len(lines) == 1:
        lines.append("нет матчей")
    kb.append([btn("➕ Добавить матч", "adm:add")])
    kb.append([btn("⬅️ Назад", "adm:menu")])
    await c.message.edit_text("\n".join(lines), parse_mode="HTML",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()


@admin_router.callback_query(F.data == "adm:add")
async def match_add(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите матч в формате:\n<code>Команда1 - Команда2</code>", reply_markup=cancel_kb())
    await state.set_state(AStates.match_teams)
    await c.answer()


@admin_router.message(AStates.match_teams)
async def match_teams_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if "-" not in m.text:
        return await m.answer("Используйте формат: <code>Команда1 - Команда2</code>")
    team1, team2 = [t.strip() for t in m.text.split("-", 1)]
    if not team1 or not team2:
        return await m.answer("Обе команды должны быть указаны.")
    await state.update_data(team1=team1, team2=team2)
    await state.set_state(AStates.match_coefs)
    await m.answer("Введите 5 коэффициентов через пробел:\nкф_П1 кф_Х кф_П2 кф_обе_да кф_обе_нет\nПример: <code>2.10 3.40 3.60 1.80 1.95</code>", reply_markup=cancel_kb())


@admin_router.message(AStates.match_coefs)
async def match_coefs_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    coefs = parse_coefs(m.text)
    if not coefs:
        return await m.answer("Нужно 5 чисел через пробел. Пример: 2.10 3.40 3.60 1.80 1.95")
    await state.update_data(coefs=coefs)
    await state.set_state(AStates.match_time)
    await m.answer("Введите время матча:\nПример: <code>2026-10-06 21:00</code>", reply_markup=cancel_kb())


@admin_router.message(AStates.match_time)
async def match_time_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    c1, cx, c2, cy, cn = data["coefs"]
    mid = await add_match(data["team1"], data["team2"], c1, cx, c2, cy, cn, m.text.strip())
    await state.clear()
    await m.answer(f"✅ Матч #{mid} добавлен: {data['team1']} — {data['team2']}")


@admin_router.callback_query(F.data.startswith("adm:m:"))
async def match_detail(c: CallbackQuery):
    mid = int(c.data.split(":")[2])
    mt = await get_match(mid)
    if not mt:
        return await c.answer("Матч не найден.", show_alert=True)
    await c.message.edit_text(match_text(mt), parse_mode="HTML", reply_markup=match_detail_kb(mt))
    await c.answer()


@admin_router.callback_query(F.data.startswith("adm:cef:"))
async def match_coefs_edit(c: CallbackQuery, state: FSMContext):
    mid = int(c.data.split(":")[2])
    await state.update_data(mid=mid)
    await state.set_state(AStates.match_coefs_edit)
    await c.message.edit_text("Введите новые 5 коэффициентов через пробел:\n<code>кф_П1 кф_Х кф_П2 кф_обе_да кф_обе_нет</code>", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.match_coefs_edit)
async def match_coefs_edit_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    coefs = parse_coefs(m.text)
    if not coefs:
        return await m.answer("Нужно 5 чисел через пробел.")
    data = await state.get_data()
    await update_coefs(data["mid"], *coefs)
    await state.clear()
    mt = await get_match(data["mid"])
    await m.answer(f"✅ Кэфы матча #{data['mid']} обновлены.\n{match_text(mt)}", parse_mode="HTML")


@admin_router.callback_query(F.data.startswith("adm:tim:"))
async def match_time_edit(c: CallbackQuery, state: FSMContext):
    mid = int(c.data.split(":")[2])
    await state.update_data(mid=mid)
    await state.set_state(AStates.match_time_edit)
    await c.message.edit_text("Введите новое время матча (например <code>2026-10-06 21:00</code>):", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.match_time_edit)
async def match_time_edit_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    await update_time(data["mid"], m.text.strip())
    await state.clear()
    await m.answer(f"✅ Время матча #{data['mid']} обновлено.")


@admin_router.callback_query(F.data.startswith("adm:del:"))
async def match_del(c: CallbackQuery):
    mid = int(c.data.split(":")[2])
    await delete_match(mid)
    await c.answer(f"Матч #{mid} удалён (ставки и экспрессы возвращены).")
    await adm_matches(c)


# ---------------- ЗАВЕРШЕНИЕ / РЕЗУЛЬТАТ ----------------

@admin_router.callback_query(F.data.startswith("adm:fin:"))
async def match_finish(c: CallbackQuery, state: FSMContext):
    mid = int(c.data.split(":")[2])
    await state.update_data(mid=mid)
    await state.set_state(AStates.fin_result)
    await c.message.edit_text("Введите результат матча: <code>1</code>, <code>X</code> или <code>2</code>:", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.fin_result)
async def finish_result_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    res = parse_result(m.text)
    if not res:
        return await m.answer("Введите 1, X или 2.")
    await state.update_data(result=res)
    await state.set_state(AStates.fin_btts)
    await m.answer("Обе забьют? <code>да/нет</code>:", reply_markup=cancel_kb())


@admin_router.message(AStates.fin_btts)
async def finish_btts_done(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    btts = parse_btts(m.text)
    if not btts:
        return await m.answer("Введите да или нет.")
    data = await state.get_data()
    await services.settle_match_full(bot, data["mid"], data["result"], btts)
    await state.clear()
    await m.answer(f"🏁 Матч #{data['mid']} завершён: {data['result']}, обе забьют: {btts}.\n"
                   f"Все выигрыши выплачены, игрокам отправлены уведомления.")


@admin_router.callback_query(F.data.startswith("adm:res:"))
async def match_result_edit(c: CallbackQuery, state: FSMContext):
    mid = int(c.data.split(":")[2])
    mt = await get_match(mid)
    await state.update_data(mid=mid, old_result=mt["result"], old_btts=mt["btts_result"])
    await state.set_state(AStates.res_result)
    await c.message.edit_text(f"Текущий результат: {mt['result']} / ОЗ: {mt['btts_result']}\n"
                              "Введите новый результат: <code>1</code>, <code>X</code> или <code>2</code>:", reply_markup=cancel_kb())
    await c.answer()


@admin_router.message(AStates.res_result)
async def result_edit_done(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    res = parse_result(m.text)
    if not res:
        return await m.answer("Введите 1, X или 2.")
    await state.update_data(result=res)
    await state.set_state(AStates.res_btts)
    await m.answer("Обе забьют? <code>да/нет</code>:", reply_markup=cancel_kb())


@admin_router.message(AStates.res_btts)
async def result_btts_done(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    btts = parse_btts(m.text)
    if not btts:
        return await m.answer("Введите да или нет.")
    data = await state.get_data()
    await services.resettle_match_full(bot, data["mid"], data["result"], btts)
    await state.clear()
    await m.answer(f"✅ Результат матча #{data['mid']} изменён: {data['result']}, обе забьют: {btts}.\n"
                   f"Балансы и экспрессы пересчитаны (было {data['old_result']}/{data['old_btts']}).")
