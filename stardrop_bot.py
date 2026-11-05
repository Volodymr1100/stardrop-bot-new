import asyncio
import logging
import sqlite3
import os
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9010350150:AAFNZLHSKwL2PUi0G3kNgAiF5F7tmbzvR3o"
OWNER_ID = 8328754114
# =============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= ВЕБ-СЕРВЕР ДЛЯ RENDER & UPTIMEROBOT =================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    
    def log_message(self, format, *args):
        pass  # Отключаем лишний спам в логах от пингов

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()
# ======================================================================

def init_db():
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        referrer_id INTEGER,
        ref_rewarded INTEGER DEFAULT 0,
        refs_total INTEGER DEFAULT 0,
        refs_for_reward INTEGER DEFAULT 0,
        claims_count INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        joined_at TEXT,
        last_active TEXT,
        passed_op INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER
    )''')
    c.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (OWNER_ID, OWNER_ID))
    c.execute('''CREATE TABLE IF NOT EXISTS ads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        url TEXT,
        chat_id TEXT,
        max_users INTEGER DEFAULT 0,
        current_users INTEGER DEFAULT 0,
        expires_at TEXT,
        is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ad_views (
        ad_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (ad_id, user_id)
    )''')
    conn.commit()
    conn.close()

init_db()

def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def is_banned(user_id: int) -> bool:
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

def get_all_admins() -> list:
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM admins')
    res = [r[0] for r in c.fetchall()]
    conn.close()
    return res

def update_activity(user_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (now, user_id))
    conn.commit()
    conn.close()

async def notify_admins(text: str, reply_markup=None):
    for adm_id in get_all_admins():
        try:
            await bot.send_message(adm_id, text, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            pass

async def check_ads_expiration():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT id, title, url, current_users, max_users, expires_at FROM ads WHERE is_active = 1')
    active_ads = c.fetchall()
    
    for ad in active_ads:
        ad_id, title, url, cur_u, max_u, exp = ad
        reason = None
        if exp != "forever" and exp <= now_str:
            reason = "⏰ Истекло время размещения"
        elif max_u > 0 and cur_u >= max_u:
            reason = f"🎯 Выполнен лимит участников ({cur_u}/{max_u})"
            
        if reason:
            c.execute('UPDATE ads SET is_active = 0 WHERE id = ?', (ad_id,))
            conn.commit()
            await notify_admins(
                f"🏁 **Реклама автоматически завершена!**\n\n"
                f"🏷 Кнопка: **{title}** (#{ad_id})\n"
                f"🔗 Ссылка: {url}\n"
                f"📌 Причина: **{reason}**\n"
                f"👥 Всего перешло/подписалось: **{cur_u} чел.**"
            )
    conn.close()

async def get_active_ads():
    await check_ads_expiration()
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT id, title, url, chat_id, max_users, current_users, expires_at FROM ads WHERE is_active = 1')
    ads = c.fetchall()
    conn.close()
    return ads

async def record_ad_join(ad_id: int, user_id: int):
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT 1 FROM ad_views WHERE ad_id = ? AND user_id = ?', (ad_id, user_id))
    if not c.fetchone():
        c.execute('INSERT INTO ad_views (ad_id, user_id) VALUES (?, ?)', (ad_id, user_id))
        c.execute('UPDATE ads SET current_users = current_users + 1 WHERE id = ?', (ad_id,))
        conn.commit()
    conn.close()
    await check_ads_expiration()

async def check_user_subscriptions(user_id: int) -> list:
    ads = await get_active_ads()
    if not ads:
        return []
    unsubscribed = []
    for ad in ads:
        ad_id, title, url, chat_id, max_u, cur_u, exp = ad
        if chat_id and chat_id.lower() != "none":
            try:
                member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    await record_ad_join(ad_id, user_id)
                else:
                    unsubscribed.append(ad)
            except Exception as e:
                logging.error(f"Ошибка проверки канала {chat_id}: {e}")
        else:
            conn = sqlite3.connect('stardrop_bot.db')
            c = conn.cursor()
            c.execute('SELECT 1 FROM ad_views WHERE ad_id = ? AND user_id = ?', (ad_id, user_id))
            clicked = c.fetchone()
            conn.close()
            if not clicked:
                unsubscribed.append(ad)
    return unsubscribed

def reply_main_menu():
    kb = [
        [KeyboardButton(text="⭐️ Заработать звёзды")],
        [KeyboardButton(text="🏆 Топ приглашений"), KeyboardButton(text="👤 Мой профиль")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def op_keyboard(ads_list):
    kb = []
    for ad in ads_list:
        ad_id, title, url, chat_id, _, _, _ = ad
        if chat_id and chat_id.lower() != "none":
            kb.append([InlineKeyboardButton(text=f"📢 Подписаться: {title}", url=url)])
        else:
            kb.append([InlineKeyboardButton(text=f"📢 Подписаться: {title}", callback_data=f"click_ad_{ad_id}")])
    kb.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subs")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

class AdminStates(StatesGroup):
    ad_title = State()
    ad_url = State()
    ad_chat_id = State()
    ad_hours = State()
    ad_max_users = State()
    add_admin_id = State()
    broadcast_msg = State()
    search_user_id = State()

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("⛔️ Вы заблокированы в этом боте.")
        return

    username = message.from_user.username or "Без юзернейма"
    args = message.text.split()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    exists = c.fetchone()

    if not exists:
        ref_id = None
        if len(args) > 1 and args[1].startswith('ref_'):
            try:
                potential_ref = int(args[1].split('_')[1])
                if potential_ref != user_id:
                    ref_id = potential_ref
            except ValueError:
                pass
        c.execute('''INSERT INTO users (user_id, username, referrer_id, joined_at, last_active)
                     VALUES (?, ?, ?, ?, ?)''', (user_id, username, ref_id, now_str, now_str))
        conn.commit()
    else:
        c.execute('UPDATE users SET username = ? WHERE user_id = ?', (username, user_id))
        conn.commit()
    conn.close()
    update_activity(user_id)

    unsub = await check_user_subscriptions(user_id)
    if unsub:
        await message.answer(
            "🔒 **Для доступа подпишитесь на каналы**\n\n"
            "После подписки нажмите «✅ Проверить подписку».",
            reply_markup=op_keyboard(unsub),
            parse_mode="Markdown"
        )
    else:
        await grant_access(user_id, message)

async def grant_access(user_id: int, message_or_call):
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT referrer_id, ref_rewarded FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    
    if row:
        ref_id, rewarded = row
        c.execute('UPDATE users SET passed_op = 1 WHERE user_id = ?', (user_id,))
        if ref_id and rewarded == 0:
            c.execute('UPDATE users SET ref_rewarded = 1 WHERE user_id = ?', (user_id,))
            c.execute('''UPDATE users SET refs_total = refs_total + 1, 
                            refs_for_reward = refs_for_reward + 1 WHERE user_id = ?''', (ref_id,))
            conn.commit()
            try:
                await bot.send_message(ref_id, "🎉 **Ваш друг прошел проверку подписки!** Вам засчитан +1 реферал.", parse_mode="Markdown")
            except Exception:
                pass
        conn.commit()
    conn.close()

    text = "✨ **Главное меню**\n\nВыберите действие 👇"
    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=reply_main_menu(), parse_mode="Markdown")
    else:
        try:
            await message_or_call.message.delete()
        except Exception:
            pass
        await message_or_call.message.answer(text, reply_markup=reply_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("click_ad_"))
async def handle_ad_click(callback: types.CallbackQuery):
    ad_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT title, url FROM ads WHERE id = ?', (ad_id,))
    ad = c.fetchone()
    conn.close()
    
    if ad:
        await record_ad_join(ad_id, user_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👉 Перейти: {ad[0]}", url=ad[1])],
            [InlineKeyboardButton(text="🔙 Назад к проверке", callback_data="back_to_op")]
        ])
        await callback.message.edit_text(
            f"Нажмите на кнопку ниже, чтобы перейти в **{ad[0]}**, затем вернитесь и нажмите «Проверить подписку»:",
            reply_markup=kb, parse_mode="Markdown"
        )
    await callback.answer()

@dp.callback_query(F.data == "back_to_op")
async def back_to_op(callback: types.CallbackQuery):
    unsub = await check_user_subscriptions(callback.from_user.id)
    if unsub:
        await callback.message.edit_text(
            "🔒 **Для доступа подпишитесь на каналы**\n\nПосле подписки нажмите «✅ Проверить подписку».",
            reply_markup=op_keyboard(unsub), parse_mode="Markdown"
        )
    else:
        await grant_access(callback.from_user.id, callback)

@dp.callback_query(F.data == "check_subs")
async def check_subs_callback(callback: types.CallbackQuery):
    update_activity(callback.from_user.id)
    unsub = await check_user_subscriptions(callback.from_user.id)
    if unsub:
        await callback.answer("❌ Вы подписались не на все каналы!", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=op_keyboard(unsub))
        except Exception:
            pass
    else:
        await callback.answer("✅ Подписка подтверждена!")
        await grant_access(callback.from_user.id, callback)

async def show_earn_stars(user_id: int, message_obj, is_edit=False):
    if is_banned(user_id):
        return
    update_activity(user_id)
    unsub = await check_user_subscriptions(user_id)
    if unsub:
        text = "🔒 Для доступа подпишитесь на каналы\n\nПосле подписки нажмите «✅ Проверить подписку»."
        if is_edit:
            await message_obj.edit_text(text, reply_markup=op_keyboard(unsub))
        else:
            await message_obj.answer(text, reply_markup=op_keyboard(unsub))
        return

    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT refs_for_reward, refs_total FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    refs_reward = row[0] if row else 0
    refs_total = row[1] if row else 0
    current_goal = refs_reward % 10
    available_claims = refs_reward // 10

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    text = (
        f"⭐️ Заработать звёзды\n\n"
        f"🎁 Каждые 10 рефералов — 200 ⭐️\n"
        f"Приглашайте друзей по персональной ссылке. Реферал засчитывается, когда новый пользователь впервые заходит в бота и проходит проверку подписки.\n\n"
        f"🔗 Ваша ссылка:\n{ref_link}\n\n"
        f"👥 Засчитано для наград: {refs_reward}\n"
        f"📈 Всего засчитано за всё время: {refs_total}\n"
        f"🎯 Текущая цель: {current_goal}/10\n"
        f"✅ Доступно заявок: {available_claims} (по 200 ⭐️)\n\n"
        f"ℹ️ До выплаты рефералы должны оставаться подписчиками обязательных каналов."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Получить звёзды", callback_data="claim_stars")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="close_stars")]
    ])

    if is_edit:
        await message_obj.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    else:
        await message_obj.answer(text, reply_markup=kb, disable_web_page_preview=True)

@dp.message(F.text == "⭐️ Заработать звёзды")
async def btn_earn_stars(message: types.Message):
    await show_earn_stars(message.from_user.id, message, is_edit=False)

@dp.message(F.text == "🏆 Топ приглашений")
async def btn_top_refs(message: types.Message):
    if is_banned(message.from_user.id):
        return
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT username, refs_total FROM users WHERE refs_total > 0 ORDER BY refs_total DESC LIMIT 10')
    top = c.fetchall()
    conn.close()

    text = "🏆 Топ-10 лучших по приглашениям:\n\n"
    if not top:
        text += "Пока никто не пригласил друзей. Стань первым!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, (uname, r_total) in enumerate(top, 1):
            icon = medals[idx-1] if idx <= 3 else f"🔹 {idx}."
            display_name = f"@{uname[:3]}***" if uname != "Без юзернейма" else "Скрытый юзер"
            text += f"{icon} {display_name} — {r_total} реф.\n"
    await message.answer(text)

@dp.message(F.text == "👤 Мой профиль")
async def btn_my_profile(message: types.Message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT refs_total, refs_for_reward, claims_count, joined_at FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    if row:
        r_tot, r_rew, claims, j_at = row
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        text = (
            f"👤 Ваш профиль\n\n"
            f"🆔 Ваш ID: {user_id}\n"
            f"📅 Дата регистрации: {j_at}\n\n"
            f"👥 Всего приглашено: {r_tot} чел.\n"
            f"⏳ Заявок в обработке: {claims} (на {claims * 200} ⭐️)\n\n"
            f"🔗 Ваша реферальная ссылка:\n{ref_link}"
        )
        await message.answer(text, disable_web_page_preview=True)

@dp.callback_query(F.data == "close_stars")
async def close_stars_cb(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "claim_stars")
async def claim_stars_cb(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT refs_for_reward FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    
    if not row or row[0] < 10:
        conn.close()
        await callback.answer("❌ У вас недостаточно рефералов! Нужно минимум 10 друзей.", show_alert=True)
        return

    c.execute('UPDATE users SET refs_for_reward = refs_for_reward - 10, claims_count = claims_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

    ticket_num = random.randint(10400, 99800)
    await callback.answer("✅ Заявка успешно создана!", show_alert=False)
    await show_earn_stars(user_id, callback.message, is_edit=True)
    await callback.message.answer(
        f"✅ Заявка #{ticket_num} на вывод 200 ⭐️ принята в обработку!\n\n"
        f"⏳ Статус: В очереди на проверку подписок (до 48 часов).\n"
        f"⚠️ Друзья не должны отписываться от спонсорских каналов."
    )

def admin_menu_kb():
    kb = [
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="adm_stats")],
        [InlineKeyboardButton(text="➕ Поставить рекламу", callback_data="adm_add_ad"),
         InlineKeyboardButton(text="📋 Активная реклама", callback_data="adm_list_ads")],
        [InlineKeyboardButton(text="🔍 Пробить / Изменить юзера", callback_data="adm_search_user")],
        [InlineKeyboardButton(text="👑 Управление админами", callback_data="adm_manage_admins"),
         InlineKeyboardButton(text="📥 Скачать базу", callback_data="adm_export_db")],
        [InlineKeyboardButton(text="✉️ Рассылка всем", callback_data="adm_broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(Command("admins", "admin"))
async def cmd_admins(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🛠 Панель администратора\nВыберите нужный раздел:", reply_markup=admin_menu_kb())

@dp.callback_query(F.data == "adm_back")
async def adm_back_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠 Панель администратора\nВыберите нужный раздел:", reply_markup=admin_menu_kb())

@dp.callback_query(F.data == "adm_stats")
async def adm_stats_cb(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    day_ago_str = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE passed_op = 1')
    passed_op = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE joined_at LIKE ?', (f"{today_str}%",))
    joined_today = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE last_active >= ?', (day_ago_str,))
    active_24h = c.fetchone()[0]
    c.execute('SELECT SUM(refs_total), SUM(claims_count) FROM users')
    sums = c.fetchone()
    total_refs = sums[0] or 0
    total_claims = sums[1] or 0
    conn.close()

    text = (
        f"📊 Полная статистика бота\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Прошли ОП: {passed_op}\n"
        f"🔗 Рефералов: {total_refs}\n"
        f"⏳ Заявок создано: {total_claims}\n\n"
        f"➕ Новых за сегодня: {joined_today}\n"
        f"⚡️ Активных за 24ч: {active_24h}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "adm_add_ad")
async def adm_add_ad_start(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await callback.message.edit_text("📢 Шаг 1/5: Название кнопки\n\nНапишите текст для кнопки:", reply_markup=kb)
    await state.set_state(AdminStates.ad_title)

@dp.message(AdminStates.ad_title)
async def adm_ad_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await message.answer("🔗 Шаг 2/5: Ссылка\n\nОтправьте ссылку (начинается с https://):", reply_markup=kb)
    await state.set_state(AdminStates.ad_url)

@dp.message(AdminStates.ad_url)
async def adm_ad_url(message: types.Message, state: FSMContext):
    if not message.text.startswith("http"):
        await message.answer("❌ Ссылка должна начинаться с http:// или https://:")
        return
    await state.update_data(url=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await message.answer("🆔 Шаг 3/5: ID канала\n\nОтправьте ID канала (например -100...) или 0 если это реф. ссылка:", reply_markup=kb)
    await state.set_state(AdminStates.ad_chat_id)

@dp.message(AdminStates.ad_chat_id)
async def adm_ad_chat_id(message: types.Message, state: FSMContext):
    chat_id = "none" if message.text.strip() == "0" else message.text.strip()
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await message.answer("⏳ Шаг 4/5: Срок в часах\n\nВведите часы (например 12, 13) или 0 для бессрочной:", reply_markup=kb)
    await state.set_state(AdminStates.ad_hours)

@dp.message(AdminStates.ad_hours)
async def adm_ad_hours(message: types.Message, state: FSMContext):
    try:
        hours = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число:")
        return
    await state.update_data(hours=hours)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await message.answer("👥 Шаг 5/5: Лимит участников\n\nВведите лимит переходов (например 500) или 0 без лимита:", reply_markup=kb)
    await state.set_state(AdminStates.ad_max_users)

@dp.message(AdminStates.ad_max_users)
async def adm_ad_finish(message: types.Message, state: FSMContext):
    try:
        max_users = int(message.text)
    except ValueError:
        await message.answer("❌ Введите целое число:")
        return

    data = await state.get_data()
    hours = data['hours']
    if hours <= 0:
        expires_at = "forever"
    else:
        exp_time = datetime.now() + timedelta(hours=hours)
        expires_at = exp_time.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO ads (title, url, chat_id, max_users, expires_at, is_active)
                 VALUES (?, ?, ?, ?, ?, 1)''', (data['title'], data['url'], data['chat_id'], max_users, expires_at))
    conn.commit()
    conn.close()
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="adm_back")]])
    await message.answer("✅ Реклама успешно запущена!", reply_markup=kb)

@dp.callback_query(F.data == "adm_list_ads")
async def adm_list_ads_cb(callback: types.CallbackQuery):
    ads = await get_active_ads()
    if not ads:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]])
        await callback.message.edit_text("📋 Сейчас нет активной рекламы.", reply_markup=kb)
        return

    text = "📋 Активная реклама сейчас:\n\n"
    kb = []
    for ad in ads:
        ad_id, title, url, chat_id, max_u, cur_u, exp = ad
        limit_str = f"{cur_u}/{max_u} чел." if max_u > 0 else f"{cur_u} чел. (без лимита)"
        exp_str = "Навсегда" if exp == "forever" else f"до {exp}"
        text += f"🔹 #{ad_id} {title}\n├ Залито: {limit_str}\n├ Стоит: {exp_str}\n└ Ссылка: {url}\n\n"
        kb.append([InlineKeyboardButton(text=f"❌ Удалить #{ad_id} ({title})", callback_data=f"del_ad_{ad_id}")])

    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("del_ad_"))
async def del_ad_cb(callback: types.CallbackQuery):
    ad_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('UPDATE ads SET is_active = 0 WHERE id = ?', (ad_id,))
    conn.commit()
    conn.close()
    await callback.answer("Реклама отключена!")
    await adm_list_ads_cb(callback)

@dp.callback_query(F.data == "adm_search_user")
async def adm_search_user_start(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await callback.message.edit_text("🔍 Отправьте Telegram ID пользователя:", reply_markup=kb)
    await state.set_state(AdminStates.search_user_id)

async def render_user_card(target_id: int, message_or_call, is_edit=False):
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT username, referrer_id, refs_total, refs_for_reward, claims_count, is_banned, joined_at FROM users WHERE user_id = ?', (target_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]])
        text = "❌ Пользователь не найден."
        if is_edit:
            await message_or_call.edit_text(text, reply_markup=kb)
        else:
            await message_or_call.answer(text, reply_markup=kb)
        return

    uname, ref_by, r_tot, r_rew, claims, banned, j_at = row
    status_str = "⛔️ Забанен" if banned else "✅ Активен"
    ban_btn_text = "🟢 Разбанить" if banned else "🔨 Забанить"

    text = f"👤 Юзер {target_id}\n🏷 @{uname}\n📌 Статус: {status_str}\n👥 Рефов: {r_tot}\n🎯 Доступно: {r_rew}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ 10 рефов", callback_data=f"u_add10_{target_id}"), InlineKeyboardButton(text="➖ 10 рефов", callback_data=f"u_sub10_{target_id}")],
        [InlineKeyboardButton(text=ban_btn_text, callback_data=f"u_ban_{target_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]
    ])
    if is_edit:
        await message_or_call.edit_text(text, reply_markup=kb)
    else:
        await message_or_call.answer(text, reply_markup=kb)

@dp.message(AdminStates.search_user_id)
async def adm_search_user_finish(message: types.Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите ID цифрами:")
        return
    await state.clear()
    await render_user_card(target_id, message, is_edit=False)

@dp.callback_query(F.data.startswith("u_add10_"))
async def u_add10_cb(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET refs_for_reward = refs_for_reward + 10, refs_total = refs_total + 10 WHERE user_id = ?', (target_id,))
    conn.commit()
    conn.close()
    await callback.answer("Начислено!")
    await render_user_card(target_id, callback.message, is_edit=True)

@dp.callback_query(F.data.startswith("u_sub10_"))
async def u_sub10_cb(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET refs_for_reward = MAX(0, refs_for_reward - 10) WHERE user_id = ?', (target_id,))
    conn.commit()
    conn.close()
    await callback.answer("Списано!")
    await render_user_card(target_id, callback.message, is_edit=True)

@dp.callback_query(F.data.startswith("u_ban_"))
async def u_ban_cb(callback: types.CallbackQuery):
    target_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT is_banned FROM users WHERE user_id = ?', (target_id,))
    cur = c.fetchone()[0]
    new_val = 0 if cur == 1 else 1
    c.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (new_val, target_id))
    conn.commit()
    conn.close()
    await callback.answer("Изменено!")
    await render_user_card(target_id, callback.message, is_edit=True)

@dp.callback_query(F.data == "adm_export_db")
async def adm_export_db_cb(callback: types.CallbackQuery):
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username, refs_total FROM users')
    rows = c.fetchall()
    conn.close()
    filename = "users_stardrop.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("ID | Username | Refs Total\n")
        for r in rows:
            f.write(f"{r[0]} | @{r[1]} | {r[2]}\n")
    await callback.message.answer_document(FSInputFile(filename), caption=f"📥 База ({len(rows)} чел.)")
    os.remove(filename)
    await callback.answer()

@dp.callback_query(F.data == "adm_manage_admins")
async def adm_manage_admins_cb(callback: types.CallbackQuery):
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM admins')
    admins = c.fetchall()
    conn.close()
    text = "👑 Администраторы:\n\n"
    kb = []
    for adm in admins:
        adm_id = adm[0]
        role = " (Владелец)" if adm_id == OWNER_ID else ""
        text += f"• {adm_id}{role}\n"
        if callback.from_user.id == OWNER_ID and adm_id != OWNER_ID:
            kb.append([InlineKeyboardButton(text=f"🗑 Снять {adm_id}", callback_data=f"rm_adm_{adm_id}")])
    if callback.from_user.id == OWNER_ID:
        kb.append([InlineKeyboardButton(text="➕ Добавить админа", callback_data="adm_add_admin")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "adm_add_admin")
async def adm_add_admin_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Только для владельца!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await callback.message.edit_text("Отправьте ID нового админа:", reply_markup=kb)
    await state.set_state(AdminStates.add_admin_id)

@dp.message(AdminStates.add_admin_id)
async def adm_add_admin_finish(message: types.Message, state: FSMContext):
    try:
        new_adm_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите ID цифрами:")
        return
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (new_adm_id, message.from_user.id))
    conn.commit()
    conn.close()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 К админам", callback_data="adm_manage_admins")]])
    await message.answer(f"✅ Админ {new_adm_id} добавлен!", reply_markup=kb)

@dp.callback_query(F.data.startswith("rm_adm_"))
async def rm_adm_cb(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    adm_id = int(callback.data.split("_")[2])
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('DELETE FROM admins WHERE user_id = ?', (adm_id,))
    conn.commit()
    conn.close()
    await callback.answer("Удален!")
    await adm_manage_admins_cb(callback)

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_back")]])
    await callback.message.edit_text("✉️ Отправьте пост для рассылки всем пользователям:", reply_markup=kb)
    await state.set_state(AdminStates.broadcast_msg)

@dp.message(AdminStates.broadcast_msg)
async def adm_broadcast_finish(message: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('stardrop_bot.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = c.fetchall()
    conn.close()

    sent = 0
    await message.answer(f"⏳ Рассылка на {len(users)} пользователей запущена...")
    for u in users:
        try:
            await message.copy_to(u[0])
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="adm_back")]])
    await message.answer(f"✅ Рассылка завершена! Доставлено: {sent}/{len(users)}.", reply_markup=kb)

async def background_expiration_checker():
    while True:
        await asyncio.sleep(20)
        await check_ads_expiration()

async def main():
    # Запускаем встроенный веб-сервер в фоновом потоке для Render и UptimeRobot
    threading.Thread(target=run_server, daemon=True).start()
    
    asyncio.create_task(background_expiration_checker())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())