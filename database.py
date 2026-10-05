import json
import aiosqlite
from datetime import datetime, timedelta
from config import (START_BALANCE, REF_BONUS_REFERRER, REF_BONUS_NEW,
                    BONUS_MIN, BONUS_MAX, BONUS_STREAK_STEP, BONUS_STREAK_MAX,
                    BONUS_MIN_GAP_HOURS, GIFT_MIN, GIFT_COMMISSION, ACHIEVEMENTS)

import os
# Абсолютный путь рядом с файлом базы: на хостинге рабочая директория иная,
# относительный путь привёл бы к «потерянной» базе при первом запуске.
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT DEFAULT '',
  balance INTEGER DEFAULT 0,
  banned INTEGER DEFAULT 0,
  last_bonus TEXT DEFAULT '',
  bonus_date TEXT DEFAULT '',
  streak INTEGER DEFAULT 0,
  referrer_id INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS promocodes(
  code TEXT PRIMARY KEY,
  amount INTEGER,
  max_activations INTEGER,
  used INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS promo_uses(
  code TEXT,
  user_id INTEGER,
  PRIMARY KEY(code, user_id)
);
CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team1 TEXT, team2 TEXT,
  coef1 REAL, coefx REAL, coef2 REAL,
  coef_btts_yes REAL, coef_btts_no REAL,
  start_time TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  result TEXT DEFAULT '',
  btts_result TEXT DEFAULT '',
  settled INTEGER DEFAULT 0,
  notified INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  match_id INTEGER,
  game TEXT DEFAULT 'match',
  bet_type TEXT,
  amount INTEGER,
  coefficient REAL,
  potential_win INTEGER,
  status TEXT DEFAULT 'pending',
  created_at TEXT,
  settled_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS active_games(
  user_id INTEGER PRIMARY KEY,
  game TEXT,
  state TEXT,
  bet INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS achievements(
  user_id INTEGER,
  code TEXT,
  awarded_at TEXT,
  PRIMARY KEY(user_id, code)
);
CREATE TABLE IF NOT EXISTS coupons(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  amount INTEGER DEFAULT 0,
  coef REAL DEFAULT 1,
  potential_win INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending',
  created_at TEXT,
  settled_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS coupon_legs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coupon_id INTEGER,
  match_id INTEGER,
  bet_type TEXT,
  coef REAL
);
CREATE TABLE IF NOT EXISTS gifts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id INTEGER,
  to_id INTEGER,
  amount INTEGER,
  commission INTEGER,
  created_at TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript(SCHEMA)
        # --- миграции для старых баз ---
        cur = await db.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in await cur.fetchall()]
        if "referrer_id" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT 0")
        if "bonus_date" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN bonus_date TEXT DEFAULT ''")
        if "streak" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN streak INTEGER DEFAULT 0")
        cur = await db.execute("PRAGMA table_info(matches)")
        cols = [r[1] for r in await cur.fetchall()]
        if "notified" not in cols:
            await db.execute("ALTER TABLE matches ADD COLUMN notified INTEGER DEFAULT 0")
        await db.commit()


# ---------------- ПОЛЬЗОВАТЕЛИ ----------------

async def register_user(user_id: int, username: str = "", referrer_id: int = 0):
    """Возвращает (is_new, ref_rewarded)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username, balance, created_at) VALUES(?,?,?,?)",
            (user_id, username or "", START_BALANCE, now()))
        is_new = cur.rowcount > 0
        rewarded = False
        if not is_new:
            await db.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", user_id))
        elif referrer_id and referrer_id != user_id:
            cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,))
            if await cur.fetchone():
                await db.execute("UPDATE users SET referrer_id=?, balance=balance+? WHERE user_id=?",
                                 (referrer_id, REF_BONUS_NEW, user_id))
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?",
                                 (REF_BONUS_REFERRER, referrer_id))
                rewarded = True
        await db.commit()
        return is_new, rewarded


async def get_ref_stats(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (user_id,))
        cnt = (await cur.fetchone())[0]
        return {"count": cnt, "earned": cnt * REF_BONUS_REFERRER}


async def top_referrers(limit: int = 10, exclude_ids=()):
    """Глобальный топ по количеству приглашённых друзей."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        xcond = (f"AND u2.user_id NOT IN ({','.join('?' * len(exclude_ids))})"
                 if exclude_ids else "")
        cur = await db.execute(
            f"SELECT u2.user_id, u2.username, COUNT(*) AS count "
            f"FROM users u1 JOIN users u2 ON u2.user_id=u1.referrer_id "
            f"WHERE u1.referrer_id!=0 AND u2.banned=0 {xcond} "
            f"GROUP BY u1.referrer_id ORDER BY count DESC LIMIT ?",
            (*exclude_ids, limit))
        return [dict(r) for r in await cur.fetchall()]


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0


async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
        await db.commit()


async def take_balance(user_id: int, amount: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
            (amount, user_id, amount))
        await db.commit()
        return cur.rowcount > 0


async def set_banned(user_id: int, banned: bool):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if banned else 0, user_id))
        await db.commit()


async def search_users(query: str, limit: int = 10):
    q = query.strip().lstrip("@")
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if q.isdigit():
            cur = await db.execute(
                "SELECT user_id, username, balance, banned, created_at FROM users WHERE user_id=? OR username LIKE ? LIMIT ?",
                (int(q), f"%{q}%", limit))
        else:
            cur = await db.execute(
                "SELECT user_id, username, balance, banned, created_at FROM users WHERE username LIKE ? LIMIT ?",
                (f"%{q}%", limit))
        return [dict(r) for r in await cur.fetchall()]


async def all_user_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


# ---------------- СТАТИСТИКА / ТОПЫ ----------------

async def top_balance(limit: int = 10, exclude_ids=()):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        xcond = (f"AND user_id NOT IN ({','.join('?' * len(exclude_ids))})"
                 if exclude_ids else "")
        cur = await db.execute(
            f"SELECT user_id, username, balance FROM users "
            f"WHERE banned=0 {xcond} ORDER BY balance DESC LIMIT ?",
            (*exclude_ids, limit))
        return [dict(r) for r in await cur.fetchall()]


async def top_day_wins(limit: int = 10, min_coef: float = 2.0, exclude_ids=()):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        xcond = (f"AND b.user_id NOT IN ({','.join('?' * len(exclude_ids))})"
                 if exclude_ids else "")
        cur = await db.execute(
            f"SELECT b.user_id, u.username, b.game, b.bet_type, b.amount, "
            f"b.coefficient, b.potential_win, m.team1, m.team2 "
            f"FROM bets b JOIN users u ON u.user_id=b.user_id "
            f"LEFT JOIN matches m ON m.id=b.match_id "
            f"WHERE b.status='win' AND b.coefficient>=? "
            f"AND b.settled_at >= datetime('now','-1 day') "
            f"{xcond} AND u.banned=0 "
            f"ORDER BY b.coefficient DESC, b.potential_win DESC LIMIT ?",
            (min_coef, *exclude_ids, limit))
        return [dict(r) for r in await cur.fetchall()]


async def top_profit(days: int, limit: int = 10, exclude_ids=()):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        xcond = (f"AND b.user_id NOT IN ({','.join('?' * len(exclude_ids))})"
                 if exclude_ids else "")
        cur = await db.execute(
            f"SELECT b.user_id, u.username, "
            f"COALESCE(SUM(CASE WHEN b.status='win' THEN b.potential_win ELSE 0 END),0)"
            f" - COALESCE(SUM(b.amount),0) AS profit "
            f"FROM bets b JOIN users u ON u.user_id=b.user_id "
            f"WHERE b.status IN ('win','lose') AND b.settled_at >= datetime('now', ?) "
            f"AND u.banned=0 {xcond} "
            f"GROUP BY b.user_id ORDER BY profit DESC LIMIT ?",
            (f"-{days} days", *exclude_ids, limit))
        return [dict(r) for r in await cur.fetchall()]


async def admin_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async def one(sql, p=()):
            cur = await db.execute(sql, p)
            return (await cur.fetchone())[0]
        return {
            "players": await one("SELECT COUNT(*) FROM users"),
            "new_today": await one("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-1 day')"),
            "wagered": await one("SELECT COALESCE(SUM(amount),0) FROM bets WHERE status IN ('win','lose')"),
            "paid": await one("SELECT COALESCE(SUM(potential_win),0) FROM bets WHERE status='win'"),
            "pending_bets": await one("SELECT COUNT(*) FROM bets WHERE status='pending'"),
            "active_coupons": await one("SELECT COUNT(*) FROM coupons WHERE status='active'"),
            "open_matches": await one("SELECT COUNT(*) FROM matches WHERE status='open'"),
            "games_now": await one("SELECT COUNT(*) FROM active_games"),
        }


# ---------------- ПРОМОКОДЫ ----------------

async def create_promo(code: str, amount: int, max_activations: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO promocodes(code, amount, max_activations, used, active) VALUES(?,?,?,0,1)",
            (code.upper(), amount, max_activations))
        await db.commit()


async def list_promos():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM promocodes ORDER BY code")
        return [dict(r) for r in await cur.fetchall()]


async def delete_promo(code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM promocodes WHERE code=?", (code.upper(),))
        await db.execute("DELETE FROM promo_uses WHERE code=?", (code.upper(),))
        await db.commit()


async def activate_promo(user_id: int, code: str):
    code = code.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT * FROM promocodes WHERE code=?", (code,))
        row = await cur.fetchone()
        if not row:
            return False, "Промокод не найден."
        _, amount, maxact, used, active = row
        if not active:
            return False, "Промокод недействителен."
        if used >= maxact:
            return False, "Промокод исчерпал лимит активаций."
        cur = await db.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id))
        if await cur.fetchone():
            return False, "Вы уже использовали этот промокод."
        await db.execute("UPDATE promocodes SET used=used+1 WHERE code=?", (code,))
        await db.execute("INSERT INTO promo_uses VALUES(?,?)", (code, user_id))
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
        await db.commit()
        return True, f"Промокод активирован: +{amount} монет!"


# ---------------- БОНУС (со серией дней) ----------------

async def claim_bonus(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT last_bonus, bonus_date, streak FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False, "Сначала нажмите /start.", 0
        lb, bdate, streak = row
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if bdate == today:
            return False, "Вы уже получили бонус сегодня. Приходите завтра!", streak or 0
        if lb:
            last = datetime.strptime(lb, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() - last < timedelta(hours=BONUS_MIN_GAP_HOURS):
                left = timedelta(hours=BONUS_MIN_GAP_HOURS) - (datetime.utcnow() - last)
                h, rem = divmod(int(left.total_seconds()), 3600)
                return False, f"Следующий бонус через {h}ч {rem // 60}мин.", streak or 0
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        streak = (streak or 0) + 1 if bdate == yesterday else 1
        import random
        amount = random.randint(BONUS_MIN, BONUS_MAX) + (streak - 1) * BONUS_STREAK_STEP
        amount = min(amount, BONUS_STREAK_MAX)
        await db.execute(
            "UPDATE users SET balance=balance+?, last_bonus=?, bonus_date=?, streak=? WHERE user_id=?",
            (amount, now(), today, streak, user_id))
        await db.commit()
        return True, amount, streak


# ---------------- МАТЧИ ----------------

async def add_match(team1, team2, c1, cx, c2, cy, cn, start_time):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO matches(team1, team2, coef1, coefx, coef2, coef_btts_yes, coef_btts_no, start_time) "
            "VALUES(?,?,?,?,?,?,?,?)", (team1, team2, c1, cx, c2, cy, cn, start_time))
        await db.commit()
        return cur.lastrowid


async def get_match(match_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def match_exists(team1, team2, start_time):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT 1 FROM matches WHERE team1=? AND team2=? AND start_time=?",
            (team1, team2, start_time))
        return await cur.fetchone() is not None


async def list_matches(open_only=False):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        sql = "SELECT * FROM matches" + (" WHERE status='open'" if open_only else "") + " ORDER BY id DESC"
        cur = await db.execute(sql)
        return [dict(r) for r in await cur.fetchall()]


async def update_coefs(match_id, c1, cx, c2, cy, cn):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE matches SET coef1=?, coefx=?, coef2=?, coef_btts_yes=?, coef_btts_no=? WHERE id=?",
            (c1, cx, c2, cy, cn, match_id))
        await db.commit()


async def update_time(match_id, start_time):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE matches SET start_time=? WHERE id=?", (start_time, match_id))
        await db.commit()


async def delete_match(match_id):
    """Удаляет матч, возвращает ставки и отменяет экспрессы с этим матчем."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id, amount FROM bets WHERE match_id=? AND status='pending'", (match_id,))
        for uid, amount in await cur.fetchall():
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
        # отмена активных экспрессов с этим матчем
        cur = await db.execute(
            "SELECT DISTINCT c.id, c.user_id, c.amount FROM coupons c "
            "JOIN coupon_legs l ON l.coupon_id=c.id WHERE c.status='active' AND l.match_id=?", (match_id,))
        for cid, uid, amount in await cur.fetchall():
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
            await db.execute("UPDATE coupons SET status='canceled', settled_at=? WHERE id=?", (now(), cid))
        await db.execute("DELETE FROM bets WHERE match_id=?", (match_id,))
        await db.execute("DELETE FROM matches WHERE id=?", (match_id,))
        await db.commit()


# --- автозакрытие / уведомления ---

async def close_due_matches():
    """Закрывает приём ставок на матчи, у которых наступило start_time."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT id FROM matches WHERE status='open' AND start_time!='' AND start_time<=?", (now(),))
        ids = [r[0] for r in await cur.fetchall()]
        if ids:
            await db.execute(
                f"UPDATE matches SET status='closed' WHERE id IN ({','.join('?' * len(ids))})", ids)
            await db.commit()
        return ids


async def matches_starting_soon(minutes: int = 5):
    """Возвращает матчи, которые начинаются в ближайшие N минут (и ещё не уведомлены)."""
    t_from = now()
    t_to = (datetime.utcnow() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM matches WHERE notified=0 AND status IN ('open','closed') "
            "AND start_time!='' AND start_time>? AND start_time<=?", (t_from, t_to))
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            ids = [r["id"] for r in rows]
            await db.execute(
                f"UPDATE matches SET notified=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
            await db.commit()
        return rows


async def match_bettors(match_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT DISTINCT user_id FROM bets WHERE match_id=?", (match_id,))
        return [r[0] for r in await cur.fetchall()]


# ---------------- АКТИВНЫЕ ИГРЫ (в БД, а не в памяти) ----------------

async def save_game(user_id, game: str, state: dict, bet: int = 0):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_games(user_id, game, state, bet, created_at) VALUES(?,?,?,?,?)",
            (user_id, game, json.dumps(state, default=list), bet, now()))
        await db.commit()


async def get_game(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM active_games WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["state"] = json.loads(d["state"])
        return d


async def delete_game(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM active_games WHERE user_id=?", (user_id,))
        await db.commit()


async def refund_crashed_games():
    """После перезапуска возвращает ставки незавершённых crash-игр."""
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT user_id, bet FROM active_games WHERE game='crash'")
        rows = await cur.fetchall()
        for uid, bet in rows:
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (bet, uid))
        if rows:
            await db.execute("DELETE FROM active_games WHERE game='crash'")
            await db.commit()
        return len(rows)


# ---------------- ДОСТИЖЕНИЯ ----------------

async def award_achievement(user_id, code) -> int:
    """Возвращает награду, 0 если достижение уже есть или неизвестно."""
    if code not in ACHIEVEMENTS:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO achievements VALUES(?,?,?)", (user_id, code, now()))
        except aiosqlite.IntegrityError:
            return 0
        reward = ACHIEVEMENTS[code][1]
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, user_id))
        await db.commit()
        return reward


async def user_achievements(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT code FROM achievements WHERE user_id=?", (user_id,))
        return {r[0] for r in await cur.fetchall()}


# ---------------- ПОДАРКИ / ПЕРЕВОДЫ ----------------

async def send_gift(from_id, to_id, amount):
    if amount < GIFT_MIN:
        return False, f"Минимальная сумма перевода — {GIFT_MIN} монет."
    if to_id == from_id:
        return False, "Нельзя перевести монеты самому себе."
    if not await get_user(to_id):
        return False, "Получатель не найден (он должен хоть раз нажать /start)."
    commission = int(amount * GIFT_COMMISSION)
    total = amount + commission
    if not await take_balance(from_id, total):
        return False, f"Недостаточно монет (нужно {amount} + комиссия {commission})."
    await add_balance(to_id, amount)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO gifts(from_id, to_id, amount, commission, created_at) VALUES(?,?,?,?,?)",
                         (from_id, to_id, amount, commission, now()))
        await db.commit()
    return True, f"✅ Отправлено {amount} монет игроку {to_id} (комиссия {commission})."


# ---------------- СТАВКИ ----------------

def bet_won(bet_type: str, result: str, btts: str) -> bool:
    if bet_type in ("1", "X", "2"):
        return bet_type == result
    if bet_type == "btts_yes":
        return btts == "yes"
    if bet_type == "btts_no":
        return btts == "no"
    return False


async def record_bet(user_id, match_id, game, bet_type, amount, coef, status="pending"):
    potential = int(amount * coef)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO bets(user_id, match_id, game, bet_type, amount, coefficient, potential_win, status, created_at, settled_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, match_id, game, bet_type, amount, coef, potential, status, now(),
             now() if status != "pending" else ""))
        await db.commit()
    return potential


async def settle_match(match_id, result, btts):
    """Рассчитывает матч. Возвращает [(user_id, won, potential, coef)] для уведомлений."""
    payouts = []
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT id, user_id, bet_type, potential_win, coefficient FROM bets WHERE match_id=? AND status='pending'",
            (match_id,))
        for bet_id, uid, bt, pot, coef in await cur.fetchall():
            won = bet_won(bt, result, btts)
            payouts.append((uid, won, pot, coef))
            if won:
                await db.execute("UPDATE bets SET status='win', settled_at=? WHERE id=?", (now(), bet_id))
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (pot, uid))
            else:
                await db.execute("UPDATE bets SET status='lose', settled_at=? WHERE id=?", (now(), bet_id))
        await db.execute(
            "UPDATE matches SET result=?, btts_result=?, status='finished', settled=1 WHERE id=?",
            (result, btts, match_id))
        await db.commit()
    return payouts


async def resettle_match(match_id, new_result, new_btts):
    """Меняет результат завершённого матча, пересчитывает балансы. Возвращает [(uid, new_won, pot, coef)]."""
    changes = []
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT id, user_id, bet_type, potential_win, status, coefficient FROM bets WHERE match_id=? AND status IN ('win','lose')",
            (match_id,))
        for bet_id, uid, bt, pot, st, coef in await cur.fetchall():
            old_won = 1 if st == "win" else 0
            new_won = 1 if bet_won(bt, new_result, new_btts) else 0
            if old_won != new_won:
                delta = (new_won - old_won) * pot
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, uid))
                await db.execute("UPDATE bets SET status=? WHERE id=?",
                                 ("win" if new_won else "lose", bet_id))
                changes.append((uid, bool(new_won), pot, coef))
        await db.execute("UPDATE matches SET result=?, btts_result=? WHERE id=?",
                         (new_result, new_btts, match_id))
        await db.commit()
    return changes


async def get_user_bets(user_id, status=None, limit=20):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        sql = ("SELECT b.*, m.team1, m.team2 FROM bets b "
               "LEFT JOIN matches m ON b.match_id=m.id WHERE b.user_id=?")
        params = [user_id]
        if status:
            sql += " AND b.status=?"
            params.append(status)
        sql += " ORDER BY b.id DESC LIMIT ?"
        params.append(limit)
        cur = await db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


async def get_stats(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async def one(sql, params):
            cur = await db.execute(sql, params)
            return (await cur.fetchone())[0]

        total_wagered = await one(
            "SELECT COALESCE(SUM(amount),0) FROM bets WHERE user_id=?", (user_id,))
        base = ("SELECT COALESCE(SUM(CASE WHEN status='win' THEN potential_win ELSE 0 END),0)"
                " - COALESCE(SUM(amount),0) FROM bets WHERE user_id=? AND status IN ('win','lose')")
        day = await one(base + " AND settled_at >= datetime('now','-1 day')", (user_id,))
        week = await one(base + " AND settled_at >= datetime('now','-7 days')", (user_id,))
        total = await one(base, (user_id,))
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status='win' THEN 1 ELSE 0 END),0) "
            "FROM bets WHERE user_id=? AND status IN ('win','lose')", (user_id,))
        cnt, wins = await cur.fetchone()
        winrate = round(wins * 100 / cnt) if cnt else 0
        return {"wagered": total_wagered, "day": day, "week": week, "total": total,
                "winrate": winrate, "bets": cnt, "wins": wins}


# ---------------- ЭКСПРЕССЫ ----------------

async def get_pending_coupon(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM coupons WHERE user_id=? AND status='pending'", (user_id,))
        c = await cur.fetchone()
        if not c:
            return None
        c = dict(c)
        cur = await db.execute(
            "SELECT l.*, m.team1, m.team2, m.status AS mstatus FROM coupon_legs l "
            "JOIN matches m ON m.id=l.match_id WHERE l.coupon_id=?", (c["id"],))
        c["legs"] = [dict(r) for r in await cur.fetchall()]
        return c


async def add_coupon_leg(user_id, match_id, bet_type, coef):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT id FROM coupons WHERE user_id=? AND status='pending'", (user_id,))
        row = await cur.fetchone()
        if row:
            cid = row[0]
        else:
            cur = await db.execute(
                "INSERT INTO coupons(user_id, created_at) VALUES(?,?)", (user_id, now()))
            cid = cur.lastrowid
        cur = await db.execute("SELECT COUNT(*) FROM coupon_legs WHERE coupon_id=?", (cid,))
        count = (await cur.fetchone())[0]
        cur = await db.execute("SELECT id FROM coupon_legs WHERE coupon_id=? AND match_id=?", (cid, match_id))
        existing = await cur.fetchone()
        if existing:
            await db.execute("UPDATE coupon_legs SET bet_type=?, coef=? WHERE id=?",
                             (bet_type, coef, existing[0]))
        else:
            if count >= 5:
                await db.commit()
                return None  # лимис исходов
            await db.execute("INSERT INTO coupon_legs(coupon_id, match_id, bet_type, coef) VALUES(?,?,?,?)",
                             (cid, match_id, bet_type, coef))
            count += 1
        await db.commit()
        return count


async def clear_coupon(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT id FROM coupons WHERE user_id=? AND status='pending'", (user_id,))
        row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM coupon_legs WHERE coupon_id=?", (row[0],))
            await db.execute("DELETE FROM coupons WHERE id=?", (row[0],))
            await db.commit()


async def place_coupon(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT * FROM coupons WHERE user_id=? AND status='pending'", (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        cid = row[0]
        cur = await db.execute("SELECT coef FROM coupon_legs WHERE coupon_id=?", (cid,))
        coef = 1.0
        for (c,) in await cur.fetchall():
            coef *= c
        potential = int(amount * coef)
        await db.execute(
            "UPDATE coupons SET amount=?, coef=?, potential_win=?, status='active' WHERE id=?",
            (amount, coef, potential, cid))
        await db.commit()
        return {"id": cid, "coef": coef, "potential": potential}


async def coupons_with_match(match_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT DISTINCT c.* FROM coupons c JOIN coupon_legs l ON l.coupon_id=c.id "
            "WHERE c.status='active' AND l.match_id=?", (match_id,))
        return [dict(r) for r in await cur.fetchall()]


async def coupon_legs_full(coupon_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT l.*, m.team1, m.team2, m.status AS mstatus, m.result, m.btts_result "
            "FROM coupon_legs l JOIN matches m ON m.id=l.match_id WHERE l.coupon_id=?", (coupon_id,))
        return [dict(r) for r in await cur.fetchall()]


async def get_user_coupons(user_id, limit: int = 5):
    """Последние экспрессы игрока (кроме недособранных) с их плечами."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM coupons WHERE user_id=? AND status!='pending' "
            "ORDER BY id DESC LIMIT ?", (user_id, limit))
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            cur2 = await db.execute(
                "SELECT l.*, m.team1, m.team2 FROM coupon_legs l "
                "JOIN matches m ON m.id=l.match_id WHERE l.coupon_id=?", (d["id"],))
            d["legs"] = [dict(x) for x in await cur2.fetchall()]
            out.append(d)
        return out


async def set_coupon_status(coupon_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE coupons SET status=?, settled_at=? WHERE id=?",
                         (status, now(), coupon_id))
        await db.commit()
