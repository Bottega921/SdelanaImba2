import os
import time
import random
import logging
import asyncio
import asyncpg
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, Filters, ContextTypes
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from faker import Faker
from pathlib import Path

# Настройка логирования
logging.basicConfig(filename='mamba_bot.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DB_CONFIG = {"dsn": os.getenv("DB_URL")}
PHOTO_DIR = Path("photos")
PROXY_LIST = os.getenv("PROXY_LIST", "").split(",")

fake = Faker('ru_RU')

# 200 шаблонов сообщений (для примера 10, полный список из предыдущего ответа)
SPAM_TEMPLATES = [
    "Привет, {name}! Тут неудобно писать, давай в Telegram: {contact}",
    "Хай, {name}! Лучше продолжим в TG: {contact}",
    "Здравствуй, {name}! Напиши мне в Telegram, тут не очень: {contact}",
    "Приветик, {name}! Давай в Telegram, там проще: {contact}",
    "Добрый день, {name}! Перейдём в TG? Вот контакт: {contact}",
    "Хай, {name}! В Telegram удобнее болтать: {contact}",
    "Привет, {name}! Давай в TG, тут некомфортно: {contact}",
    "Здравствуйте, {name}! Напиши в Telegram: {contact}",
    "Привет, {name}! Лучше продолжим в TG: {contact}",
    "Хай, {name}! Давай общаться в Telegram: {contact}"
]

async def send_log(message: str, context: ContextTypes.DEFAULT_TYPE = None):
    try:
        if context:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Лог: {message}")
        else:
            async with Application.builder().token(TELEGRAM_TOKEN).build() as app:
                await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Лог: {message}")
    except Exception as e:
        logger.error(f"Failed to send log: {e}")

async def check_vak_sms_balance():
    try:
        response = requests.get(f"https://vak-sms.com/api/balance?apiKey={VAK_SMS_API_KEY}", timeout=10)
        response.raise_for_status()
        balance = response.json().get("balance", 0)
        logger.info(f"Vak SMS balance: {balance}")
        await send_log(f"Vak SMS balance: {balance}")
        return balance > 0
    except Exception as e:
        logger.error(f"Logger: Vak SMS balance check failed: {e}")
        await send_log(f"Ошибка Vak SMS: {e}")
        return False

async def get_vak_sms_number():
    try:
        response = requests.get(
            f"https://vak-sms.com/api/getNumber?apiKey={VAK_SMS_API_KEY}&service=ms&country=ru",
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if data.get("tel"):
            await send_log(f"Получен номер: {data['tel']}")
            return data["tel"], data["id"]
        raise Exception("Vak SMS error")
    except Exception as e:
        await send_log(f"Ошибка получения номера: {e}")
        raise

async def get_vak_sms_code(number_id):
    for _ in range(5):
        try:
            response = requests.get(
                f"https://vak-sms.com/api/getCode?apiKey={VAK_SMS_API_KEY}&id={number_id}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code"):
                await send_log(f"Получен код: {data['code']}")
                return data["code"]
            await asyncio.sleep(10)
        except Exception as e:
            await send_log(f"Ошибка получения кода: {e}")
    return None

async def init_db():
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id SERIAL PRIMARY KEY,
                login TEXT,
                password TEXT,
                name TEXT,
                age INTEGER,
                description TEXT,
                status TEXT,
                likes_count INTEGER,
                chats_count INTEGER,
                token TEXT,
                photos TEXT[]
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        await send_log("База данных готова")
        return conn
    except Exception as e:
        await send_log(f"Ошибка базы данных: {e}")
        raise

def get_main_menu():
    keyboard = [
        ["Запустить регистрацию", "Запустить лайкинг"],
        ["Обновить токен", "Загрузить изображения"],
        ["Удалить изображения", "Запустить спам"],
        ["Настройки"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def setup_driver(proxy=None):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"user-agent={fake.user_agent()}")
    if proxy:
        chrome_options.add_argument(f"--proxy-server={proxy}")
    service = Service("/usr/bin/chromium")
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

async def register_profile(driver, conn, settings):
    try:
        name = next((s['value'] for s in settings if s['key'] == 'name'), 'Анна')
        age = int(next((s['value'] for s in settings if s['key'] == 'age'), '25'))
        login = fake.email()
        password = fake.password()
        description = fake.text(max_nb_chars=200)
        
        driver.get("https://www.mamba.ru")
        time.sleep(random.uniform(3, 7))
        if driver.find_elements(By.CLASS_NAME, "captcha-form"):
            await send_log("CAPTCHA при регистрации, пропускаем")
            return None
        driver.find_element(By.ID, "email").send_keys(login)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "name").send_keys(name)
        driver.find_element(By.ID, "age").send_keys(str(age))
        driver.find_element(By.ID, "gender").find_element(By.XPATH, "//option[@value='female']").click()
        driver.find_element(By.ID, "description").send_keys(description)
        number, number_id = await get_vak_sms_number()
        driver.find_element(By.ID, "phone").send_keys(number)
        driver.find_element(By.ID, "submit").click()
        time.sleep(random.uniform(3, 7))
        code = await get_vak_sms_code(number_id)
        if code:
            driver.find_element(By.ID, "code").send_keys(code)
            driver.find_element(By.ID, "verify").click()
            time.sleep(random.uniform(3, 7))
            token = driver.execute_script("return localStorage.getItem('auth_token')")
            photos = random.sample(list(PHOTO_DIR.glob("*.jpg")), k=min(3, len(list(PHOTO_DIR.glob("*.jpg")))))
            profile_id = await conn.fetchval(
                "INSERT INTO profiles (login, password, name, age, description, status, likes_count, chats_count, token, photos) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
                login, password, name, age, description, "active", 0, 0, token, [str(p) for p in photos]
            )
            await upload_photos(driver, profile_id, conn, photos)
            likes = await start_liking(driver, profile_id, conn)
            chats = await count_chats(driver, profile_id, conn)
            await send_log(f"Анкета ID{profile_id}: {likes} лайков, {chats} чатов", context=None)
            return profile_id
    except Exception as e:
        await send_log(f"Ошибка регистрации: {e}")
        return None

async def upload_photos(driver, profile_id: int, conn, photos: list):
    try:
        driver.get("https://www.mamba.ru/profile/photos")
        time.sleep(random.uniform(3, 7))
        if driver.find_elements(By.CLASS_NAME, "captcha-form"):
            await send_log("CAPTCHA при загрузке фото, пропускаем")
            return
        for photo in photos:
            driver.find_element(By.ID, "photo-upload").send_keys(str(photo))
            time.sleep(random.uniform(1, 3))
            driver.find_element(By.ID, "photo-submit").click()
            time.sleep(random.uniform(1, 3))
        await conn.execute("UPDATE profiles SET photos = $1 WHERE id = $2", [str(p) for p in photos], profile_id)
        await send_log(f"Фото загружены для ID{profile_id}")
    except Exception as e:
        await send_log(f"Ошибка загрузки фото ID{profile_id}: {e}")

async def start_liking(driver, profile_id: int, conn):
    try:
        driver.get("https://www.mamba.ru/search")
        time.sleep(random.uniform(3, 7))
        likes_limit = 200 if driver.find_elements(By.CLASS_NAME, "vip-badge") else 2
        likes = 0
        for _ in range(likes_limit):
            if driver.find_elements(By.CLASS_NAME, "captcha-form"):
                await send_log("CAPTCHA при лайкинге, пропускаем")
                break
            like_button = driver.find_element(By.CLASS_NAME, "like-button")
            like_button.click()
            likes += 1
            await conn.execute("UPDATE profiles SET likes_count = likes_count + 1 WHERE id = $1", profile_id)
            time.sleep(random.uniform(5, 10))
        logger.info(f"Profile ID{profile_id}: {likes} likes")
        return likes
    except Exception as e:
        logger.error(f"Liking error: {e}")
        return 0

async def count_chats(driver, profile_id: int, conn):
    try:
        driver.get("https://www.mamba.ru/chats")
        time.sleep(random.uniform(3, 7))
        chats = len(driver.find_elements(By.CLASS_NAME, "chat-item"))
        await conn.execute("UPDATE profiles SET chats_count = $1 WHERE id = $2", chats, profile_id)
        logger.info(f"Profile ID{profile_id}: {chats} chats")
        return chats
    except Exception as e:
        logger.error(f"Chat count error: {e}")
        return 0

async def start_spam(driver, profile_id: int, conn, telegram_username):
    try:
        driver.get("https://www.mamba.ru/chats")
        time.sleep(random.uniform(3, 7))
        chats = driver.find_elements(By.CLASS_NAME, "chat-item")
        messages_sent = 0
        for chat in chats:
            if messages_sent >= 10:
                break
            if driver.find_elements(By.CLASS_NAME, "captcha-form"):
                await send_log("CAPTCHA при спаме, пропускаем")
                break
            name = chat.find_element(By.CLASS_NAME, "chat-name").text
            chat.click()
            time.sleep(random.uniform(1, 3))
            message = random.choice(SPAM_TEMPLATES).format(name=name, contact=telegram_username)
            driver.find_element(By.CLASS_NAME, "message-input").send_keys(message)
            driver.find_element(By.CLASS_NAME, "contacts-button").click()
            driver.find_element(By.ID, "telegram-contact").send_keys(telegram_username)
            driver.find_element(By.CLASS_NAME, "send-message-button").click()
            messages_sent += 1
            time.sleep(random.uniform(3, 7))
        await send_log(f"Спам отправлен для ID{profile_id}: {messages_sent} сообщений")
    except Exception as e:
        await send_log(f"Ошибка спама ID{profile_id}: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        await update.message.reply_text("Доступ запрещён.")
        return
    await update.message.reply_text("Выберите действие:", reply_markup=get_main_menu())
    await send_log("Бот запущен", context)

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("Сколько анкет создать (1–10)?")
    context.user_data['state'] = 'registration_count'

async def process_registration_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'registration_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 10:
            await update.message.reply_text("Введите число от 1 до 10.", reply_markup=get_main_menu())
            return
        if not await check_vak_sms_balance():
            await update.message.reply_text("Недостаточно средств на Vak SMS.", reply_markup=get_main_menu())
            return

        conn = await init_db()
        settings = await conn.fetch("SELECT * FROM settings")
        driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
        try:
            for i in range(count):
                profile_id = await register_profile(driver, conn, settings)
                if profile_id:
                    await update.message.reply_text(f"Анкета ID{profile_id} готова")
        finally:
            driver.quit()
            await conn.close()
        await update.message.reply_text(f"Готово: {count} анкет.", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("Введите число.", reply_markup=get_main_menu())
    finally:
        context.user_data['state'] = None

async def handle_liking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    conn = await init_db()
    profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active'")
    driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
    try:
        for profile in profiles:
            driver.get("https://www.mamba.ru/login")
            time.sleep(random.uniform(3, 7))
            driver.find_element(By.ID, "email").send_keys(profile["login"])
            driver.find_element(By.ID, "password").send_keys(profile["password"])
            driver.find_element(By.ID, "login").click()
            time.sleep(random.uniform(3, 7))
            likes = await start_liking(driver, profile["id"], conn)
            chats = await count_chats(driver, profile["id"], conn)
            await update.message.reply_text(f"Анкета ID{profile['id']}: {likes} лайков, {chats} чатов")
    finally:
        driver.quit()
        await conn.close()
    await update.message.reply_text("Лайкинг завершён.", reply_markup=get_main_menu())

async def handle_update_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    conn = await init_db()
    profiles = await conn.fetch("SELECT id, login, password FROM profiles")
    driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
    try:
        for profile in profiles:
            driver.get("https://www.mamba.ru/login")
            time.sleep(random.uniform(3, 7))
            driver.find_element(By.ID, "email").send_keys(profile["login"])
            driver.find_element(By.ID, "password").send_keys(profile["password"])
            driver.find_element(By.ID, "login").click()
            time.sleep(random.uniform(3, 7))
            token = driver.execute_script("return localStorage.getItem('auth_token')")
            status = "active" if driver.find_elements(By.CLASS_NAME, "profile-active") else "banned"
            await conn.execute("UPDATE profiles SET token = $1, status = $2 WHERE id = $3", token, status, profile["id"])
            await update.message.reply_text(f"Анкета ID{profile['id']}: {status}")
    finally:
        driver.quit()
        await conn.close()
    await update.message.reply_text("Обновление токенов завершено.", reply_markup=get_main_menu())

async def handle_upload_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("Сколько анкет обновить фото (1–10)?")
    context.user_data['state'] = 'upload_photos_count'

async def process_upload_photos_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'upload_photos_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 10:
            await update.message.reply_text("Введите число от 1 до 10.", reply_markup=get_main_menu())
            return
        conn = await init_db()
        profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active' LIMIT $1", count)
        driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
        try:
            for profile in profiles:
                photos = random.sample(list(PHOTO_DIR.glob("*.jpg")), k=min(3, len(list(PHOTO_DIR.glob("*.jpg")))))
                await upload_photos(driver, profile["id"], conn, photos)
        finally:
            driver.quit()
            await conn.close()
        await update.message.reply_text("Загрузка фото завершена.", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("Введите число.", reply_markup=get_main_menu())
    finally:
        context.user_data['state'] = None

async def handle_delete_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    conn = await init_db()
    profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active'")
    driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
    try:
        for profile in profiles:
            driver.get("https://www.mamba.ru/profile/photos")
            time.sleep(random.uniform(3, 7))
            photos = driver.find_elements(By.CLASS_NAME, "photo-item")
            for photo in photos:
                photo.find_element(By.CLASS_NAME, "delete-button").click()
                time.sleep(random.uniform(1, 3))
            await conn.execute("UPDATE profiles SET photos = $1 WHERE id = $2", [], profile["id"])
            await send_log(f"Фото удалены для ID{profile['id']}")
    finally:
        driver.quit()
        await conn.close()
    await update.message.reply_text("Удаление фото завершено.", reply_markup=get_main_menu())

async def handle_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("Сколько анкет использовать для спама (1–10)?")
    context.user_data['state'] = 'spam_count'

async def process_spam_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'spam_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 10:
            await update.message.reply_text("Введите число от 1 до 10.", reply_markup=get_main_menu())
            return
        conn = await init_db()
        telegram_username = await conn.fetchval("SELECT value FROM settings WHERE key = 'telegram_username'") or '@MyBot'
        profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active' LIMIT $1", count)
        driver = setup_driver(random.choice(PROXY_LIST) if PROXY_LIST else None)
        try:
            for profile in profiles:
                driver.get("https://www.mamba.ru/login")
                time.sleep(random.uniform(3, 7))
                driver.find_element(By.ID, "email").send_keys(profile["login"])
                driver.find_element(By.ID, "password").send_keys(profile["password"])
                driver.find_element(By.ID, "login").click()
                time.sleep(random.uniform(3, 7))
                await start_spam(driver, profile["id"], conn, telegram_username)
        finally:
            driver.quit()
            await conn.close()
        await update.message.reply_text("Спам завершён.", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("Введите число.", reply_markup=get_main_menu())
    finally:
        context.user_data['state'] = None

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    keyboard = [["Имя", "Возраст"], ["Telegram"]]
    await update.message.reply_text("Выберите настройку:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    context.user_data['state'] = 'settings'

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'settings':
        return
    text = update.message.text
    if text in ["Имя", "Возраст", "Telegram"]:
        key = {"Имя": "name", "Возраст": "age", "Telegram": "telegram_username"}[text]
        context.user_data['setting_key'] = key
        await update.message.reply_text(f"Введите {text}:", reply_markup=ReplyKeyboardRemove())
        context.user_data['state'] = 'save_setting'
    else:
        await update.message.reply_text("Выберите настройку.", reply_markup=get_main_menu())
        context.user_data['state'] = None

async def save_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'save_setting':
        return
    key = context.user_data.get('setting_key')
    value = update.message.text
    try:
        conn = await init_db()
        await conn.execute("INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2", key, value)
        await conn.close()
        await update.message.reply_text(f"{key} сохранено: {value}", reply_markup=get_main_menu())
        await send_log(f"Настройка {key}: {value}", context)
    except Exception as e:
        await send_log(f"Ошибка настройки: {e}", context)
    finally:
        context.user_data['state'] = None

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('state') == 'registration_count':
        await process_registration_count(update, context)
    elif context.user_data.get('state') == 'upload_photos_count':
        await process_upload_photos_count(update, context)
    elif context.user_data.get('state') == 'spam_count':
        await process_spam_count(update, context)
    elif context.user_data.get('state') == 'settings':
        await handle_settings(update, context)
    elif context.user_data.get('state') == 'save_setting':
        await save_setting(update, context)
    elif text == "Запустить регистрацию":
        await handle_registration(update, context)
    elif text == "Запустить лайкинг":
        await handle_liking(update, context)
    elif text == "Обновить токен":
        await handle_update_token(update, context)
    elif text == "Загрузить изображения":
        await handle_upload_photos(update, context)
    elif text == "Удалить изображения":
        await handle_delete_photos(update, context)
    elif text == "Запустить спам":
        await handle_spam(update, context)
    elif text == "Настройки":
        await settings_menu(update, context)

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(Filters.text & ~Filters.command, message_handler))
    
    for attempt in range(3):
        try:
            await app.run_polling()
            break
        except Exception as e:
            await send_log(f"Ошибка запуска (попытка {attempt + 1}): {e}")
            await asyncio.sleep(10)

if __name__ == '__main__':
    asyncio.run(main())
