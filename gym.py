import os
import logging
import sqlite3
import asyncio
import subprocess
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButtonRequestUser,
    WebAppInfo
)

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')
try:
    MAIN_ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
except ValueError:
    MAIN_ADMIN_ID = 0

if not API_TOKEN:
    print("Xatolik: ..env faylida BOT_TOKEN topilmadi!")
    sys.exit(1)

# --- CONFIGURATION ---
GYM_NAME = "Lion Gym"
WEB_APP_URL = "https://liongymm.netlify.app/"

# Membership Data with Durations (in days)
PLANS = {
    'test_1min': {'name': '1 Minute Test', 'price': 0, 'days': 0.0007},  # ~1 minute
    'daily_no_coach': {'name': 'Har kuni', 'price': 320000, 'days': 30},
    'daily_coach': {'name': 'Har kuni + Murabbiy', 'price': 500000, 'days': 30},
    'threeday_no_coach': {'name': 'Haftada 3 kun', 'price': 200000, 'days': 30},
    'threeday_coach': {'name': 'Haftada 3 kun + Murabbiy', 'price': 370000, 'days': 30}
}


# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('gym.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            fullname TEXT,
            phone TEXT,
            plan_key TEXT,
            is_paid INTEGER DEFAULT 0,
            reg_date TEXT,
            expiry_date TEXT,
            notified_expiry INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            admin_id INTEGER PRIMARY KEY
        )
    ''')
    if MAIN_ADMIN_ID != 0:
        cursor.execute("INSERT OR IGNORE INTO admins (admin_id) VALUES (?)", (MAIN_ADMIN_ID,))
    conn.commit()
    conn.close()


def get_admins():
    conn = sqlite3.connect('gym.db')
    admins = [row[0] for row in conn.execute("SELECT admin_id FROM admins").fetchall()]
    conn.close()
    return admins


# --- EXPIRY CHECKER TASK ---
async def check_expiries():
    """Background task to check for expired memberships every minute"""
    while True:
        try:
            now = datetime.now()
            conn = sqlite3.connect('gym.db')
            expired_users = conn.execute(
                "SELECT user_id, fullname, plan_key FROM users WHERE notified_expiry = 0 AND expiry_date <= ?",
                (now.strftime("%Y-%m-%d %H:%M:%S"),)
            ).fetchall()

            for uid, name, pk in expired_users:
                plan_name = PLANS.get(pk, {'name': 'Noma\'lum'})['name']
                try:
                    await bot.send_message(
                        uid,
                        f"⚠️ *A'zolik muddati tugadi!*\n\nHurmatli {name}, sizning '{plan_name}' tarifingiz muddati tugadi. "
                        f"Mashg'ulotlarni davom ettirish uchun to'lovni amalga oshirishingizni so'raymiz.",
                        parse_mode="Markdown"
                    )
                    conn.execute("UPDATE users SET notified_expiry = 1, is_paid = 0 WHERE user_id = ?", (uid,))
                    conn.commit()
                except Exception as e:
                    logging.error(f"Failed to notify {uid}: {e}")

            conn.close()
        except Exception as e:
            logging.error(f"Error in expiry checker: {e}")

        await asyncio.sleep(60)


# --- STATES ---
class AdminCreateUser(StatesGroup):
    user_id = State()
    name = State()
    phone = State()
    plan = State()


class AdminSearchUser(StatesGroup):
    query = State()


class AdminAddAdmin(StatesGroup):
    user_id = State()


# --- BOT INITIALIZATION ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()


# --- KEYBOARDS ---
def get_main_kb(user_id):
    admins = get_admins()
    if user_id in admins:
        kb_list = [
            [KeyboardButton(text="➕ Foydalanuvchi qo'shish"), KeyboardButton(text="📊 Foyda va Statistika")],
            [KeyboardButton(text="🔍 A'zoni qidirish")],
            [KeyboardButton(text="👥 Azolarni boshqarish"), KeyboardButton(text="📢 To'lamaganlarga xabar yuborish")],
            [KeyboardButton(text="🌐 Ochish: Web Ilova", web_app=WebAppInfo(url=WEB_APP_URL))]
        ]
        if user_id == MAIN_ADMIN_ID:
            kb_list.append([KeyboardButton(text="🔑 Admin qo'shish")])
        return ReplyKeyboardMarkup(keyboard=kb_list, resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="👤 Mening profilim")],
            [KeyboardButton(text="🌐 Ochish: Web Ilova", web_app=WebAppInfo(url=WEB_APP_URL))]
        ], resize_keyboard=True)


def get_plans_kb():
    buttons = [[InlineKeyboardButton(text=f"{info['name']} ({info['price']:,} so'm)", callback_data=f"setplan_{key}")]
               for key, info in PLANS.items()]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- SEARCH LOGIC ---
@dp.message(F.text == "🔍 A'zoni qidirish")
async def cmd_search_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_admins(): return
    await message.answer(
        "🔎 Qidirish uchun foydalanuvchi ismini yoki telefon raqami oxirgi 4 ta raqamini kiriting:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
    )
    await state.set_state(AdminSearchUser.query)


@dp.message(AdminSearchUser.query)
async def process_search_query(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_admins(): return
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Qidiruv bekor qilindi.", reply_markup=get_main_kb(message.from_user.id))

    search_query = message.text.strip().lower()
    conn = sqlite3.connect('gym.db')
    users = conn.execute(
        "SELECT user_id, fullname, phone, is_paid, expiry_date FROM users WHERE LOWER(fullname) LIKE ? OR phone LIKE ?",
        (f'%{search_query}%', f'%{search_query}')
    ).fetchall()
    conn.close()

    if not users:
        return await message.answer("❌ Hech kim topilmadi. Qaytadan urinib ko'ring yoki bekor qiling:")

    await message.answer(f"🔍 {len(users)} ta natija topildi:", reply_markup=get_main_kb(message.from_user.id))

    for uid, name, phone, paid, expiry in users:
        status = "✅ Faol" if paid else "❌ Muddati tugagan"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 To'lov holatini o'zgartirish", callback_data=f"admintoggle_{uid}")]
        ])
        await message.answer(
            f"👤 *Foydalanuvchi:* {name}\n"
            f"📞 Tel: {phone}\n"
            f"📅 Tugaydi: {expiry}\n"
            f"📊 Holat: {status}",
            parse_mode="Markdown",
            reply_markup=kb
        )
    await state.clear()


# --- PAGINATED USER LIST ---
async def show_member_page(message: types.Message, page: int = 0, edit: bool = False):
    limit = 10
    offset = page * limit
    conn = sqlite3.connect('gym.db')
    users = conn.execute("SELECT user_id, fullname, is_paid FROM users LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    if not users:
        if edit:
            await message.edit_text("A'zolar ro'yxati bo'sh.")
        else:
            await message.answer("A'zolar ro'yxati bo'sh.")
        return

    text = f"👥 *A'zolar ro'yxati* (Jami: {total})\n_Sahifa: {page + 1}_"
    kb_list = []
    for uid, name, paid in users:
        icon = "✅" if paid else "❌"
        kb_list.append([InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"viewuser_{uid}")])

    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"page_{page - 1}"))
    if offset + limit < total: nav_row.append(InlineKeyboardButton(text="Oldinga ➡️", callback_data=f"page_{page + 1}"))
    if nav_row: kb_list.append(nav_row)

    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    except TelegramBadRequest:
        pass


@dp.message(F.text == "👥 Azolarni boshqarish")
async def cmd_manage_members(message: types.Message):
    if message.from_user.id not in get_admins(): return
    await show_member_page(message, 0, edit=False)


@dp.callback_query(F.data.startswith("page_"))
async def process_pagination(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    await show_member_page(callback.message, page, edit=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("viewuser_"))
async def view_user_detail(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    conn = sqlite3.connect('gym.db')
    user = conn.execute("SELECT fullname, phone, plan_key, is_paid, reg_date, expiry_date FROM users WHERE user_id=?",
                        (uid,)).fetchone()
    conn.close()

    if user:
        name, phone, plan, paid, rdate, edate = user
        status = "✅ Faol" if paid else "❌ Muddati tugagan"
        plan_name = PLANS.get(plan, {'name': plan})['name']
        msg = f"👤 *A'zo ma'lumotlari*\n\nIsm: {name}\nTel: {phone}\nTarif: {plan_name}\nRo'yxat: {rdate}\nTugash: {edate}\nHolat: {status}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Holatni o'zgartirish", callback_data=f"admintoggle_{uid}")],
            [InlineKeyboardButton(text="⬅️ Ro'yxatga qaytish", callback_data="page_0")]
        ])
        try:
            await callback.message.edit_text(msg, reply_markup=kb, parse_mode="Markdown")
        except TelegramBadRequest:
            pass
    await callback.answer()


# --- ADMIN TOGGLE LOGIC ---
@dp.callback_query(F.data.startswith("admintoggle_"))
async def admin_toggle(callback: types.CallbackQuery):
    if callback.from_user.id not in get_admins(): return
    user_id = int(callback.data.split("_")[1])
    conn = sqlite3.connect('gym.db')
    row = conn.execute("SELECT is_paid FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return await callback.answer("Foydalanuvchi topilmadi.")

    current = row[0]
    new_status = 1 - current
    conn.execute("UPDATE users SET is_paid=?, notified_expiry=0 WHERE user_id=?", (new_status, user_id))
    conn.commit()
    conn.close()

    await callback.answer("Holat yangilandi!")
    if callback.message.text and "A'zo ma'lumotlari" in callback.message.text:
        await view_user_detail(callback)
    else:
        try:
            lines = callback.message.text.split('\n')
            new_status_text = "✅ Faol" if new_status else "❌ Muddati tugagan"
            new_lines = [f"📊 Holat: {new_status_text}" if "📊 Holat:" in line else line for line in lines]
            await callback.message.edit_text("\n".join(new_lines), reply_markup=callback.message.reply_markup,
                                             parse_mode="Markdown")
        except Exception:
            pass


# --- START & USER STATUS ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    init_db()
    await message.answer(f"{GYM_NAME} boshqaruv tizimiga xush kelibsiz.",
                         reply_markup=get_main_kb(message.from_user.id))


@dp.message(F.text == "👤 Mening profilim")
async def user_status(message: types.Message):
    conn = sqlite3.connect('gym.db')
    user = conn.execute("SELECT fullname, plan_key, is_paid, expiry_date FROM users WHERE user_id=?",
                        (message.from_user.id,)).fetchone()
    conn.close()
    if not user:
        await message.answer("Ro'yxatda yo'qsiz.")
        return
    name, plan_key, is_paid, expiry = user
    status = "✅ Faol" if is_paid == 1 else "❌ Muddati tugagan/To'lanmagan"
    await message.answer(
        f"👤 Ism: {name}\n🏋️ Tarif: {PLANS.get(plan_key, {'name': plan_key})['name']}\n📅 Tugash sanasi: {expiry}\n💳 Holat: {status}")


# --- ADD USER LOGIC ---
@dp.message(F.text == "➕ Foydalanuvchi qo'shish")
async def admin_add_user(message: types.Message, state: FSMContext):
    if message.from_user.id not in get_admins(): return
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Kontaktni tanlash",
                                                       request_user=KeyboardButtonRequestUser(request_id=1,
                                                                                              user_is_bot=False))],
                                       [KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
    await message.answer("Kontaktni tanlang:", reply_markup=kb)
    await state.set_state(AdminCreateUser.user_id)


@dp.message(AdminCreateUser.user_id, F.user_shared)
async def process_user_shared(message: types.Message, state: FSMContext):
    await state.update_data(target_id=message.user_shared.user_id)
    await message.answer("To'liq ismini kiriting:",
                         reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
                                                          resize_keyboard=True))
    await state.set_state(AdminCreateUser.name)


@dp.message(AdminCreateUser.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(fullname=message.text)
    await message.answer("Telefon raqami:")
    await state.set_state(AdminCreateUser.phone)


@dp.message(AdminCreateUser.phone)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer("Tarifni tanlang:", reply_markup=get_plans_kb())
    await state.set_state(AdminCreateUser.plan)


@dp.callback_query(AdminCreateUser.plan, F.data.startswith("setplan_"))
async def finish_creation(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.split("_", 1)[1]
    data = await state.get_data()

    if plan_key not in PLANS:
        await callback.answer("Xatolik: Tarif topilmadi.")
        return

    plan_days = PLANS[plan_key]['days']
    now = datetime.now()
    expiry = now + timedelta(days=plan_days)

    conn = sqlite3.connect('gym.db')
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, fullname, phone, plan_key, reg_date, expiry_date, notified_expiry, is_paid) VALUES (?, ?, ?, ?, ?, ?, 0, 1)",
        (data['target_id'], data['fullname'], data['phone'], plan_key, now.strftime("%Y-%m-%d"),
         expiry.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    await callback.message.answer(f"✅ Qo'shildi!\n⏳ Tugash vaqti: {expiry.strftime('%Y-%m-%d %H:%M:%S')}",
                                  reply_markup=get_main_kb(callback.from_user.id))
    await state.clear()


# --- ADMIN STATS & ADMIN MANAGEMENT ---
@dp.message(F.text == "🔑 Admin qo'shish")
async def admin_add_admin_start(message: types.Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID: return
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Kontaktni tanlash",
                                                       request_user=KeyboardButtonRequestUser(request_id=2,
                                                                                              user_is_bot=False))],
                                       [KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
    await message.answer("Yangi admin uchun kontaktni tanlang:", reply_markup=kb)
    await state.set_state(AdminAddAdmin.user_id)


@dp.message(AdminAddAdmin.user_id, F.user_shared)
async def process_admin_shared(message: types.Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID: return
    new_id = message.user_shared.user_id
    conn = sqlite3.connect('gym.db')
    conn.execute("INSERT OR IGNORE INTO admins (admin_id) VALUES (?)", (new_id,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Yangi admin qo'shildi (ID: {new_id})", reply_markup=get_main_kb(message.from_user.id))
    await state.clear()


@dp.message(F.text == "📊 Foyda va Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id not in get_admins(): return
    conn = sqlite3.connect('gym.db')
    users = conn.execute("SELECT plan_key, is_paid FROM users").fetchall()
    conn.close()
    actual = unpaid = 0
    for pk, paid in users:
        p = PLANS.get(pk, {'price': 0})['price']
        if paid:
            actual += p
        else:
            unpaid += 1
    await message.answer(
        f"📊 *Statistika*\n\n👥 Azolar: {len(users)}\n🔴 Muddati tugaganlar: {unpaid}\n💰 Jami tushum: {actual:,} so'm",
        parse_mode="Markdown")


@dp.message(F.text == "📢 To'lamaganlarga xabar yuborish")
async def notify_all(message: types.Message):
    if message.from_user.id not in get_admins(): return
    await message.answer("🚀 Xabarlar yuborilmoqda...")
    subprocess.Popen([sys.executable, "user_notifier.py"])


@dp.message(F.text == "❌ Bekor qilish")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Amal bekor qilindi.", reply_markup=get_main_kb(message.from_user.id))


async def main():
    init_db()
    asyncio.create_task(check_expiries())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())