"""Загрузка матчей из внешнего API.

Поддерживаются два источника:
1) The Odds API (https://the-odds-api.com) — задайте ключ в переменной окружения ODDS_API_KEY
2) Локальный файл matches.json рядом с ботом (если ключа нет):

[
  {
    "team1": "ЦСКА", "team2": "Спартак",
    "coef1": 2.10, "coefx": 3.40, "coef2": 3.60,
    "coef_btts_yes": 1.80, "coef_btts_no": 1.95,
    "start_time": "2026-10-06 19:00"
  }
]
"""
import os
import json
import aiohttp

SPORT = os.getenv("ODDS_SPORT", "soccer")  # для The Odds API
# Кэфы "обе забьют", если API их не отдаёт
DEFAULT_BTTS_YES = 1.80
DEFAULT_BTTS_NO = 1.95


async def _from_json_file():
    if not os.path.exists("matches.json"):
        return None
    with open("matches.json", encoding="utf-8") as f:
        return json.load(f)


async def _from_odds_api():
    key = os.getenv("ODDS_API_KEY", "")
    if not key:
        return None
    url = (f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/"
           f"?apiKey={key}&regions=eu&markets=h2h&oddsFormat=decimal")
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=15) as r:
            r.raise_for_status()
            data = await r.json()
    out = []
    for ev in data:
        home, away = ev.get("home_team"), ev.get("away_team")
        if not home or not away:
            continue
        c1 = cx = c2 = None
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") == "h2h":
                    for o in mk.get("outcomes", []):
                        if o["name"] == home:
                            c1 = o["price"]
                        elif o["name"] == away:
                            c2 = o["price"]
                        elif o["name"].lower() in ("draw", "tie"):
                            cx = o["price"]
            if c1 and c2:
                break
        if c1 and c2:
            out.append({
                "team1": home, "team2": away,
                "coef1": c1, "coefx": cx or 3.4, "coef2": c2,
                "coef_btts_yes": DEFAULT_BTTS_YES, "coef_btts_no": DEFAULT_BTTS_NO,
                "start_time": ev.get("commence_time", "")[:16].replace("T", " "),
            })
    return out


async def fetch_matches():
    """Возвращает список матчей или None (нет источника), или кидает исключение."""
    try:
        res = await _from_odds_api()
        if res:
            return res
    except Exception:
        pass
    return await _from_json_file()
