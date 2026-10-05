import json
import random
import sqlite3

DB = "agent.db"
GOAL = 5000  # цель, k€
START_MONEY = 100

FIRST = ["Артём", "Лука", "Матео", "Кайл", "Диего", "Ян", "Рафа", "Тимо", "Илья", "Амин",
         "Бруно", "Серж", "Лео", "Данте", "Эмре", "Влад", "Нуно", "Хави", "Марк", "Олег"]
LAST = ["Смирнов", "Росси", "Мюллер", "Силва", "Кабрал", "Дюпон", "Оздемир", "Ковач",
        "Нильсен", "Агирре", "Фернандес", "Волков", "Мбаппе-Лу", "Хансен", "Петров", "Диаш"]
POS = ["GK", "DEF", "MID", "FWD"]
CLUBS = {
    1: ["Динамо Брест", "Лилль-Б", "Хетафе-Б", "Аустрия-2"],
    2: ["Ростов", "Брага", "Мец", "Утрехт"],
    3: ["Бенфика", "Аякс", "Зенит", "Лион"],
    4: ["Атлетико", "Наполи", "Лейпциг", "Милан"],
    5: ["Реал Мадрид", "Манчестер Сити", "Бавария", "ПСЖ"],
}
EVENTS = [
    ("📰 Клиент попал в скандал в соцсетях. Репутация падает.", "rep", -3),
    ("🔥 Блогеры хвалят вашего клиента — рост стоимости!", "val", 1.05),
    ("🤕 Травма у одного из клиентов. Стоимость падает.", "val", 0.9),
    ("🎁 Спонсор дал грант вашему агентству: +€20k.", "money", 20),
    ("🦈 Конкурент переманил клиента!", "lose", 0),
]


def fmt(k):
    k = int(k)
    return f"€{k / 1000:.1f}M" if abs(k) >= 1000 else f"€{k}k"


def pvalue(p):
    eff = p["ovr"] if p["age"] >= 25 else (p["ovr"] * 2 + p["pot"]) / 3
    age_f = 1 + max(0, 24 - p["age"]) * 0.04 - max(0, p["age"] - 30) * 0.08
    return max(20, int(100 * 1.7 ** ((eff - 50) / 4) * age_f))


def gen_player(st, q=0):
    st["next_id"] += 1
    age = random.randint(17, 31)
    ovr = random.randint(48, 66) + q * 2 + random.randint(0, 4)
    pot = min(94, ovr + max(0, 25 - age) * random.randint(1, 3))
    p = {"id": st["next_id"], "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
         "age": age, "pos": random.choice(POS), "ovr": ovr, "pot": pot,
         "club": random.choice(CLUBS[1] + CLUBS[2])}
    p["value"] = pvalue(p)
    return p


def new_game(name):
    st = {"name": name, "week": 1, "money": START_MONEY, "rep": 5, "office": 1,
          "next_id": 0, "clients": [], "offers": [], "cands": [], "over": None}
    st["clients"].append(gen_player(st))
    return st


def capacity(st):
    return 2 + st["office"] * 2


def scout_cost(st):
    return 10 + 8 * st["office"]


def upgrade_cost(st):
    return 80 * st["office"] ** 2


def commission_rate(st):
    return 0.08 + st["rep"] / 1000


def scout(st):
    st["money"] -= scout_cost(st)
    st["cands"] = [gen_player(st, st["office"] - 1) for _ in range(3)]
    for c in st["cands"]:
        c["ask"] = max(5, int(c["value"] * 0.08))
        c["rep_req"] = max(0, (c["ovr"] - 58) * 2)


def sign(st, i):
    c = st["cands"][i]
    if len(st["clients"]) >= capacity(st):
        return "❌ Нет свободных мест в офисе."
    if st["rep"] < c["rep_req"]:
        return f"❌ Игрок не доверяет новичку: нужна репутация {c['rep_req']}."
    if st["money"] < c["ask"]:
        return "❌ Не хватает денег на бонус за подпись."
    st["money"] -= c["ask"]
    st["clients"].append(st["cands"].pop(i))
    return f"✅ {c['name']} теперь ваш клиент!"


def _club_for(p):
    tier = (p["ovr"] - 40) // 9 + random.choice([-1, 0, 0, 1])
    tier = min(5, max(1, tier))
    return random.choice([c for c in CLUBS[tier] if c != p["club"]])


def sell(st, i):
    o = st["offers"].pop(i)
    p = next(x for x in st["clients"] if x["id"] == o["pid"])
    com = int(o["fee"] * commission_rate(st))
    st["money"] += com
    st["rep"] = min(100, st["rep"] + 1 + o["fee"] // 2000)
    p["club"] = o["club"]
    p["value"] = pvalue(p)
    st["offers"] = [x for x in st["offers"] if x["pid"] != p["id"]]
    return f"🤝 {p['name']} → {o['club']} за {fmt(o['fee'])}. Ваша комиссия: {fmt(com)}!"


def negotiate(st, i):
    o = st["offers"][i]
    if o.get("neg"):
        return "Вы уже торговались по этому офферу."
    o["neg"] = True
    if random.random() < 0.5 + st["rep"] / 400:
        o["fee"] = int(o["fee"] * 1.15)
        return f"📈 Клуб согласился поднять цену до {fmt(o['fee'])}."
    st["offers"].pop(i)
    return "📉 Клуб обиделся и отозвал предложение."


def next_week(st):
    log = []
    st["week"] += 1
    st["cands"] = []
    inc = sum(int(p["value"] * 0.005) for p in st["clients"])
    rent = 3 + 3 * st["office"]
    st["money"] += inc - rent
    log.append(f"💰 Комиссии: +{fmt(inc)}, аренда: −{fmt(rent)}")
    for p in st["clients"]:
        if p["age"] < 25 and p["ovr"] < p["pot"] and random.random() < 0.15:
            p["ovr"] += 1
            log.append(f"📈 {p['name']} прогрессирует ({p['ovr']})")
        elif p["age"] > 31 and random.random() < 0.12:
            p["ovr"] -= 1
            log.append(f"📉 {p['name']} теряет форму ({p['ovr']})")
        p["value"] = pvalue(p)
    st["offers"] = []
    for p in st["clients"]:
        if random.random() < 0.3:
            fee = int(p["value"] * random.uniform(0.8, 1.3))
            st["offers"].append({"pid": p["id"], "club": _club_for(p), "fee": fee})
    if st["offers"]:
        log.append(f"📨 Новых офферов: {len(st['offers'])}")
    if random.random() < 0.2 and st["clients"]:
        text, kind, val = random.choice(EVENTS)
        p = random.choice(st["clients"])
        if kind == "rep":
            st["rep"] = max(0, st["rep"] + val)
        elif kind == "val":
            p["value"] = int(p["value"] * val)
        elif kind == "money":
            st["money"] += val
        elif kind == "lose" and len(st["clients"]) > 1:
            st["clients"].remove(p)
            st["offers"] = [o for o in st["offers"] if o["pid"] != p["id"]]
            text += f" Ушёл: {p['name']}."
        else:
            text = None
        if text:
            log.append(text)
    if st["week"] % 52 == 1:
        for p in st["clients"]:
            p["age"] += 1
            p["value"] = pvalue(p)
        log.append("🎂 Новый сезон! Все клиенты стали на год старше.")
    if st["money"] >= GOAL:
        st["over"] = "win"
    elif st["money"] < 0:
        st["over"] = "lose"
    return log


def load(uid):
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, data TEXT)")
        r = c.execute("SELECT data FROM users WHERE id=?", (uid,)).fetchone()
    return json.loads(r[0]) if r else None


def save(uid, st):
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, data TEXT)")
        c.execute("REPLACE INTO users VALUES (?, ?)", (uid, json.dumps(st, ensure_ascii=False)))
