from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)


def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎁 Бонус")],
        [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="💸 Ставки"), KeyboardButton(text="⚽ События")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="👥 Рефералы")],
    ], resize_keyboard=True)


def games_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧨 Мины", callback_data="gm:mines"),
         InlineKeyboardButton(text="🎡 Колесо", callback_data="gm:wheel"),
         InlineKeyboardButton(text="🚀 Crash", callback_data="gm:crash")],
        [InlineKeyboardButton(text="🪙 Монетка", callback_data="gm:coin"),
         InlineKeyboardButton(text="🎲 Кости", callback_data="gm:dice"),
         InlineKeyboardButton(text="🎰 Слоты", callback_data="gm:slots")],
        [InlineKeyboardButton(text="🃏 Блэкджек", callback_data="gm:bj")],
    ])


def btn(text, cb):
    return InlineKeyboardButton(text=text, callback_data=cb)
