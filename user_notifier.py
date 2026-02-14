import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, errors

# --- LOAD CONFIGURATION ---
load_dotenv()

API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
SESSION_NAME = os.getenv('SESSION_NAME', 'gym_admin_account')
TWO_STEP_PASSWORD = os.getenv('TWO_STEP_PASSWORD')
MAIN_ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

PLANS = {
    'daily_no_coach': {'name': 'Har kuni', 'price': 320000},
    'daily_coach': {'name': 'Har kuni + Murabbiy', 'price': 500000},
    'threeday_no_coach': {'name': 'Haftada 3 kun', 'price': 200000},
    'threeday_coach': {'name': 'Haftada 3 kun + Murabbiy', 'price': 370000}
}

# Tracking for the final report
report_data = {
    "success_list": [],
    "failed_list": [],
    "unpaid_total": 0
}


async def send_single_notification(client, name, phone, plan_key):
    plan_info = PLANS.get(plan_key, {'name': 'A\'zolik', 'price': 0})

    message = (
        f"Assalomu alaykum {name}! 👋\n\n"
        f"Sizning sport zalimizga a'zoligingiz bo'yicha eslatma: {plan_info['name']}.\n"
        f"To'lov miqdori: {plan_info['price']:,} so'm.\n"
        "Mashg'ulotlarda uzilish bo'lmasligi uchun to'lovni amalga oshirishingizni so'raymiz. Rahmat!"
    )

    try:
        # Normalize phone number
        target = phone if str(phone).startswith('+') else f"+{phone}"
        await client.send_message(target, message)
        report_data["success_list"].append(f"✅ {name} ({phone})")
    except errors.FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await client.send_message(target, message)
        report_data["success_list"].append(f"✅ {name} ({phone})")
    except Exception as e:
        report_data["failed_list"].append(f"❌ {name} ({phone}): {str(e)}")


async def run_notifier():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    print("Connecting to Telegram...")
    await client.start(password=lambda: TWO_STEP_PASSWORD)

    if not await client.is_user_authorized():
        print("Authorization failed.")
        return

    print("Fetching unpaid users from database...")
    try:
        conn = sqlite3.connect('gym.db')
        cursor = conn.cursor()
        # Find members with is_paid = 0
        cursor.execute("SELECT fullname, phone, plan_key FROM users WHERE is_paid = 0")
        unpaid_users = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Database Error: {e}")
        return

    report_data["unpaid_total"] = len(unpaid_users)

    if not unpaid_users:
        summary = "📢 To'lamagan foydalanuvchilar topilmadi. Xabar yuborish bekor qilindi."
        print(summary)
        if MAIN_ADMIN_ID:
            await client.send_message(MAIN_ADMIN_ID, summary)
        return

    print(f"Sending notifications to {len(unpaid_users)} members...")

    for user in unpaid_users:
        await send_single_notification(client, *user)
        await asyncio.sleep(1.5)  # Safe delay to prevent spam triggers

    # --- GENERATE AND SEND REPORT ---
    success_count = len(report_data["success_list"])
    failed_count = len(report_data["failed_list"])

    report_text = (
        f"📊 *Xabarnoma Hisoboti*\n"
        f"Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"👥 Jami qarzdorlar: {report_data['unpaid_total']}\n"
        f"✅ Yuborildi: {success_count}\n"
        f"❌ Xatolik: {failed_count}\n\n"
    )

    if success_count > 0:
        report_text += "*Muvaffaqiyatli:*\n" + "\n".join(report_data["success_list"][:15]) + "\n"
        if success_count > 15: report_text += f"...va yana {success_count - 15} kishi\n"

    if failed_count > 0:
        report_text += "\n*Xatoliklar:*\n" + "\n".join(report_data["failed_list"][:10])

    print(report_text)

    # Send report to Admin
    if MAIN_ADMIN_ID:
        try:
            await client.send_message(MAIN_ADMIN_ID, report_text, parse_mode='markdown')
            print("✅ Report sent to Admin.")
        except Exception as e:
            print(f"Could not send report to Admin: {e}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_notifier())