import os
import time
import random
import logging
import asyncio
import asyncpg
import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from faker import Faker
from pathlib import Path
from dotenv import load_dotenv
from webdriver_manager.chrome import ChromeDriverManager  # Добавлено для автоматической установки ChromeDriver
import re

# Загрузка переменных из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(
    filename='mamba_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация из .env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DB_CONFIG = {"dsn": os.getenv("DB_URL")}
PHOTO_DIR = Path("photos")
PROXY_LIST = os.getenv("PROXY_LIST", "").split(",")

# Проверка переменных окружения
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN не указан в .env")
    raise ValueError("TELEGRAM_TOKEN не указан в .env")
if not VAK_SMS_API_KEY:
    logger.error("VAK_SMS_API_KEY не указан в .env")
    raise ValueError("VAK_SMS_API_KEY не указан в .env")
if not ADMIN_CHAT_ID:
    logger.error("ADMIN_CHAT_ID не указан в .env")
    raise ValueError("ADMIN_CHAT_ID не указан в .env")
if not DB_CONFIG["dsn"]:
    logger.error("DB_URL не указан в .env")
    raise ValueError("DB_URL не указан в .env")

logger.info("Переменные окружения загружены успешно")

fake = Faker('ru_RU')

# Варианты уменьшительно-ласкательных имён
NAME_VARIATIONS = {
    "Анастасия": ["Настя", "Настенька", "Настасья", "Ася"],
    "Екатерина": ["Катя", "Катерина", "Катюша", "Екатерина"],
    "Мария": ["Маша", "Машенька", "Маришка", "Маруся"],
}

# 10 шаблонов сообщений без "Telegram"
SPAM_TEMPLATES = [
    "Привет, {name}! 📩 Тут неудобно писать, давай в другой чат: [контакт]",
    "Хай, {name}! 📲 Лучше продолжим в другом месте: [контакт]",
    "Здравствуй, {name}! ✉️ Напиши мне в другой чат, тут не очень: [контакт]",
    "Приветик, {name}! 🌐 Давай в другом месте, там проще: [контакт]",
    "Добрый день, {name}! 🔗 Перейдём в другой чат? Вот контакт: [контакт]",
    "Хай, {name}! 💬 В другом месте удобнее болтать: [контакт]",
    "Привет, {name}! 📱 Давай в другом месте, тут некомфортно: [контакт]",
    "Здравствуйте, {name}! 📧 Напиши в другой чат: [контакт]",
    "Привет, {name}! 🌍 Лучше продолжим в другом месте: [контакт]",
    "Хай, {name}! 💌 Давай общаться в другом чате: [контакт]"
]

def get_cancel_keyboard():
    keyboard = [["Отменить действие 🚫"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def send_log(message: str, context: ContextTypes.DEFAULT_TYPE = None):
    logger.info(message)
    if context and context.bot:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Лог: {message}")
        except Exception as e:
            logger.error(f"Не удалось отправить лог в Telegram: {e}")

async def check_vak_sms_balance():
    try:
        # Попробуем v1
        v1_response = requests.get(f"https://vak-sms.com/api/balance?apiKey={VAK_SMS_API_KEY}", timeout=10)
        v1_response.raise_for_status()
        v1_data = v1_response.json()
        v1_balance = float(v1_data.get("balance", 0))
        await send_log(f"Vak SMS v1 balance: {v1_balance} рублей")
        if v1_balance >= 10:
            return True

        # Если v1 не работает или баланс < 10, попробуем v2
        v2_response = requests.get(f"https://api.vak-sms.com/v2/balance?apiKey={VAK_SMS_API_KEY}", timeout=10)
        v2_response.raise_for_status()
        v2_data = v2_response.json()
        v2_balance = float(v2_data.get("balance", 0))
        await send_log(f"Vak SMS v2 balance: {v2_balance} рублей")
        if v2_balance >= 10:
            return True

        await send_log(f"Баланс Vak SMS ({v1_balance} или {v2_balance} рублей) недостаточен для покупки номера (нужно минимум 10 рублей).")
        return False
    except Exception as e:
        await send_log(f"Ошибка проверки баланса Vak SMS: {e}. Ответ v1: {v1_response.text if 'v1_response' in locals() else 'нет ответа'}, Ответ v2: {v2_response.text if 'v2_response' in locals() else 'нет ответа'}")
        return False

async def get_vak_sms_number():
    try:
        # Попробуем v1
        v1_response = requests.get(
            f"https://vak-sms.com/api/getNumber?apiKey={VAK_SMS_API_KEY}&service=ms&country=ru",
            timeout=10
        )
        v1_response.raise_for_status()
        v1_data = v1_response.json()
        if v1_data.get("tel"):
            await send_log(f"Получен номер через v1: {v1_data['tel']}")
            return v1_data["tel"], v1_data["id"], "v1"

        # Если v1 не работает, попробуем v2
        v2_response = requests.get(
            f"https://api.vak-sms.com/v2/getNumber?apiKey={VAK_SMS_API_KEY}&service=ms&country=ru",
            timeout=10
        )
        v2_response.raise_for_status()
        v2_data = v2_response.json()
        if v2_data.get("tel"):
            await send_log(f"Получен номер через v2: {v2_data['tel']}")
            return v2_data["tel"], v2_data["id"], "v2"

        raise Exception("Vak SMS error: не удалось получить номер через v1 или v2")
    except Exception as e:
        await send_log(f"Ошибка получения номера: {e}. Ответ v1: {v1_response.text if 'v1_response' in locals() else 'нет ответа'}, Ответ v2: {v2_response.text if 'v2_response' in locals() else 'нет ответа'}")
        raise

async def get_vak_sms_code(number_id, api_version):
    for _ in range(5):
        try:
            # Выбираем версию API в зависимости от того, через какую версию был получен номер
            base_url = "https://vak-sms.com/api" if api_version == "v1" else "https://api.vak-sms.com/v2"
            response = requests.get(
                f"{base_url}/getCode?apiKey={VAK_SMS_API_KEY}&id={number_id}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code"):
                await send_log(f"Получен код через {api_version}: {data['code']}")
                return data["code"]
            time.sleep(10)
        except Exception as e:
            await send_log(f"Ошибка получения кода через {api_version}: {e}. Ответ: {response.text if 'response' in locals() else 'нет ответа'}")
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
        ["Запустить регистрацию 📝", "Запустить лайкинг 👍"],
        ["Обновить токен 🔑", "Добавить изображения 🖼️"],
        ["Удалить изображения ❌", "Запустить спам 💬"],
        ["Статистика 📊", "Настройки ⚙️"]
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
    service = Service(ChromeDriverManager().install())  # Изменено на ChromeDriverManager
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

async def register_profile(driver, conn, settings):
    try:
        base_name = next((s['value'] for s in settings if s['key'] == 'name'), 'Анастасия')
        age = int(next((s['value'] for s in settings if s['key'] == 'age'), '25'))
        login = f"{fake.first_name().lower()}{fake.random_int(100, 999)}@gmail.com"
        password = fake.password()
        description = fake.text(max_nb_chars=200)
        
        driver.get("https://www.mamba.ru")
        time.sleep(random.uniform(3, 7))
        if driver.find_elements(By.CLASS_NAME, "captcha-form"):
            await send_log("CAPTCHA при регистрации, пропускаем")
            return None
        
        # Покупка номера через Vak SMS (попробуем обе версии API)
        number, number_id, api_version = await get_vak_sms_number()
        if not number:
            await send_log("Не удалось получить номер для регистрации")
            return None
        
        # Заполнение формы регистрации
        driver.find_element(By.ID, "email").send_keys(login)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "name").send_keys(base_name)
        driver.find_element(By.ID, "age").send_keys(str(age))
        driver.find_element(By.ID, "gender").find_element(By.XPATH, "//option[@value='female']").click()
        driver.find_element(By.ID, "description").send_keys(description)
        driver.find_element(By.ID, "phone").send_keys(number)
        driver.find_element(By.ID, "submit").click()
        time.sleep(random.uniform(3, 7))
        
        # Получение кода верификации (используем ту же версию API, через которую получили номер)
        code = await get_vak_sms_code(number_id, api_version)
        if code:
            driver.find_element(By.ID, "code").send_keys(code)
            driver.find_element(By.ID, "verify").click()
            time.sleep(random.uniform(3, 7))
            token = driver.execute_script("return localStorage.getItem('auth_token')")
            photos = random.sample(list(PHOTO_DIR.glob("*.jpg")), k=min(3, len(list(PHOTO_DIR.glob("*.jpg")))))
            profile_id = await conn.fetchval(
                "INSERT INTO profiles (login, password, name, age, description, status, likes_count, chats_count, token, photos) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
                login, password, base_name, age, description, "active", 0, 0, token, [str(p) for p in photos]
            )
            await upload_photos(driver, profile_id, conn, photos)
            await send_log(f"Анкета ID{profile_id} успешно зарегистрирована")
            return profile_id
        else:
            await send_log("Не удалось получить код верификации")
            return None
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

async def start_liking(driver, profile_id: int, conn, context):
    try:
        driver.get("https://www.mamba.ru/login")
        time.sleep(random.uniform(3, 7))
        conn_profile = await conn.fetchrow("SELECT login, password FROM profiles WHERE id = $1", profile_id)
        driver.find_element(By.ID, "email").send_keys(conn_profile["login"])
        driver.find_element(By.ID, "password").send_keys(conn_profile["password"])
        driver.find_element(By.ID, "login").click()
        time.sleep(random.uniform(3, 7))
        
        driver.get("https://www.mamba.ru/search")
        time.sleep(random.uniform(3, 7))
        likes_limit = 200
        likes = 0
        while likes < likes_limit:
            if driver.find_elements(By.CLASS_NAME, "captcha-form"):
                await send_log(f"CAPTCHA при лайкинге для ID{profile_id}, пропускаем", context)
                break
            like_button = driver.find_element(By.CLASS_NAME, "like-button")
            like_button.click()
            likes += 1
            await conn.execute("UPDATE profiles SET likes_count = likes_count + 1 WHERE id = $1", profile_id)
            chats = await count_chats(driver, profile_id, conn)
            await conn.execute("UPDATE profiles SET chats_count = $1 WHERE id = $2", chats, profile_id)
            if likes % 50 == 0:
                await update_message(context, profile_id, likes, chats)
            time.sleep(random.uniform(5, 10))
        await update_message(context, profile_id, likes, chats)
        if likes >= likes_limit:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🎉 Аккаунт ID{profile_id} достиг лимита 200 лайков!")
        return likes
    except Exception as e:
        logger.error(f"Liking error for ID{profile_id}: {e}")
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
        logger.error(f"Chat count error for ID{profile_id}: {e}")
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
            message = random.choice(SPAM_TEMPLATES).format(name=name, contact="[контакт]")
            driver.find_element(By.CLASS_NAME, "message-input").send_keys(message)
            contact_button = driver.find_element(By.CLASS_NAME, "attach-button")
            contact_button.click()
            time.sleep(random.uniform(1, 2))
            driver.find_element(By.CLASS_NAME, "contact-option").click()
            driver.find_element(By.ID, "contact-input").send_keys(telegram_username)
            driver.find_element(By.ID, "send-contact").click()
            messages_sent += 1
            time.sleep(random.uniform(3, 7))
        await send_log(f"Спам отправлен для ID{profile_id}: {messages_sent} сообщений")
    except Exception as e:
        await send_log(f"Ошибка спама ID{profile_id}: {e}")

async def update_message(context, profile_id, likes, chats):
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📊 Статистика ID{profile_id}: {likes} лайков, {chats} чатов")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Получена команда /start от chat_id: {update.message.chat_id}")
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        await update.message.reply_text("🚫 Доступ запрещён.")
        logger.info(f"Доступ запрещён для chat_id: {update.message.chat_id}")
        return
    await update.message.reply_text("📋 Выберите действие:", reply_markup=get_main_menu())
    await send_log("Бот запущен", context)

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("📥 Сколько анкет создать (1–500)?", reply_markup=get_cancel_keyboard())
    context.user_data['state'] = 'registration_count'
    logger.info("Запрошено количество анкет для регистрации")

async def process_registration_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'registration_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 500:
            await update.message.reply_text("❌ Введите число от 1 до 500.", reply_markup=get_main_menu())
            return
        if not await check_vak_sms_balance():
            await update.message.reply_text("💸 Недостаточно средств на Vak SMS.", reply_markup=get_main_menu())
            return

        conn = await init_db()
        settings = await conn.fetch("SELECT * FROM settings")
        extra_names = next((s['value'].split(',') for s in settings if s['key'] == 'extra_names'), [])
        name_variations = [next((s['value'] for s in settings if s['key'] == 'name'), 'Анастасия')] + extra_names
        driver = setup_driver()
        successful_registrations = 0
        try:
            for i in range(count):
                profile_id = await register_profile(driver, conn, settings)
                if profile_id:
                    successful_registrations += 1
        finally:
            driver.quit()
            await conn.close()
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🎉 Успешно зарегистрировано {successful_registrations} аккаунтов из {count}!")
        await update.message.reply_text("✅ Регистрация завершена. Выберите следующее действие.", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("❌ Введите число.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в process_registration_count: {e}", context)
    finally:
        context.user_data['state'] = None

async def handle_liking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    try:
        conn = await init_db()
        profiles = await conn.fetch("SELECT id FROM profiles WHERE status = 'active' AND likes_count < 200")
        driver = None  # Инициализируем driver как None
        try:
            driver = setup_driver()
            for profile in profiles:
                profile_id = profile["id"]
                try:
                    likes = await start_liking(driver, profile_id, conn, context)
                except Exception as e:
                    await send_log(f"Ошибка лайкинга для ID{profile_id}: {e}", context)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    await send_log(f"Ошибка при закрытии драйвера: {e}", context)
            await conn.close()
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="✅ Лайкинг завершен для всех аккаунтов.")
    except Exception as e:
        await send_log(f"Ошибка в handle_liking: {e}", context)
        await update.message.reply_text("❌ Ошибка при выполнении лайкинга.", reply_markup=get_main_menu())

async def handle_update_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    try:
        conn = await init_db()
        profiles = await conn.fetch("SELECT id, login, password FROM profiles")
        driver = setup_driver()
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
                await update_message(context, profile["id"], 0, 0)
        finally:
            driver.quit()
            await conn.close()
        await update.message.reply_text("🔄 Обновление токенов завершено.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в handle_update_token: {e}", context)

async def handle_upload_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("📸 Сколько добавить изображений?", reply_markup=get_cancel_keyboard())
    context.user_data['state'] = 'upload_photos_count'

async def process_upload_photos_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'upload_photos_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 500:
            await update.message.reply_text("❌ Введите число от 1 до 500.", reply_markup=get_main_menu())
            return
        await update.message.reply_text(f"📤 Отправьте {count} фото (сжатые или оригинальные, по одному на анкету). Используйте /finish_upload после отправки всех фото.")
        context.user_data['state'] = 'upload_photos_files'
        context.user_data['upload_count'] = count
        context.user_data['photos_received'] = 0
        context.user_data['photos'] = []
    except ValueError:
        await update.message.reply_text("❌ Введите число.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в process_upload_photos_count: {e}", context)

async def process_upload_photos_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'upload_photos_files':
        return
    try:
        count = context.user_data.get('upload_count', 0)
        photos_received = context.user_data.get('photos_received', 0)
        photos = context.user_data.get('photos', [])
        if update.message.photo:
            if photos_received >= count:
                await update.message.reply_text("❌ Достигнут лимит фото.", reply_markup=get_main_menu())
                context.user_data.clear()
                return
            photo_file = await update.message.photo[-1].get_file()
            photo_path = f"photos/uploaded_{update.message.photo[-1].file_id}.jpg"
            await photo_file.download_to_drive(photo_path)
            photos.append(photo_path)
            photos_received += 1
            context.user_data['photos_received'] = photos_received
            context.user_data['photos'] = photos
            await update.message.reply_text(f"📸 Фото {photos_received}/{count} добавлено (сохранено как {photo_path}). Отправьте ещё или используйте /finish_upload.")
        elif update.message.text == "/finish_upload":
            if photos_received == 0:
                await update.message.reply_text("❌ Нет загруженных фото.", reply_markup=get_main_menu())
                context.user_data.clear()
                return
            if photos_received < count:
                await update.message.reply_text(f"⚠️ Загружено {photos_received} из {count} фото. Завершить?")
                return
            conn = await init_db()
            profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active' LIMIT $1", count)
            driver = setup_driver()
            try:
                for i, profile in enumerate(profiles):
                    if i < len(photos):
                        await upload_photos(driver, profile["id"], conn, [photos[i]])
                    else:
                        await upload_photos(driver, profile["id"], conn, [])
            finally:
                driver.quit()
                await conn.close()
            await update.message.reply_text("🖼️ Добавление изображений завершено.", reply_markup=get_main_menu())
            context.user_data.clear()
        else:
            await update.message.reply_text(f"📤 Отправьте фото или используйте /finish_upload (получено {photos_received}/{count}).")
    except Exception as e:
        await send_log(f"Ошибка в process_upload_photos_files: {e}", context)

async def handle_delete_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    try:
        conn = await init_db()
        profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active'")
        driver = setup_driver()
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
        await update.message.reply_text("❌ Удаление фото завершено.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в handle_delete_photos: {e}", context)

async def handle_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("💬 Сколько анкет использовать для спама (1–500)?", reply_markup=get_cancel_keyboard())
    context.user_data['state'] = 'spam_count'

async def process_spam_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'spam_count':
        return
    try:
        count = int(update.message.text)
        if count < 1 or count > 500:
            await update.message.reply_text("❌ Введите число от 1 до 500.", reply_markup=get_main_menu())
            return
        conn = await init_db()
        telegram_username = await conn.fetchval("SELECT value FROM settings WHERE key = 'telegram_username'") or '@MyBot'
        profiles = await conn.fetch("SELECT id, login, password FROM profiles WHERE status = 'active' LIMIT $1", count)
        driver = setup_driver()
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
        await update.message.reply_text("📨 Спам завершён.", reply_markup=get_main_menu())
    except ValueError:
        await update.message.reply_text("❌ Введите число.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в process_spam_count: {e}", context)
    finally:
        context.user_data['state'] = None

async def handle_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    try:
        conn = await init_db()
        profiles = await conn.fetch("SELECT id, likes_count, chats_count FROM profiles WHERE status = 'active'")
        stats_message = "📊 Статистика аккаунтов:\n"
        for profile in profiles:
            stats_message += f"ID{profile['id']}: {profile['likes_count']} лайков, {profile['chats_count']} чатов\n"
        await update.message.reply_text(stats_message or "📊 Нет данных о профилях.", reply_markup=get_main_menu())
    except Exception as e:
        await send_log(f"Ошибка в handle_statistics: {e}", context)

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_CHAT_ID:
        return
    keyboard = [["Имя 📛", "Возраст 🎂"], ["Telegram 💬", "Доп. имена ➕"], ["Отменить действие 🚫"]]
    await update.message.reply_text("⚙️ Выберите настройку:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    context.user_data['state'] = 'settings'

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') != 'settings':
        return
    text = update.message.text
    if text in ["Имя 📛", "Возраст 🎂", "Telegram 💬", "Доп. имена ➕"]:
        key = {"Имя 📛": "name", "Возраст 🎂": "age", "Telegram 💬": "telegram_username", "Доп. имена ➕": "extra_names"}[text]
        context.user_data['setting_key'] = key
        await update.message.reply_text(f"📝 Введите {text.split()[0]}:", reply_markup=ReplyKeyboardRemove())
        context.user_data['state'] = 'save_setting'
    else:
        await update.message.reply_text("⚙️ Выберите настройку.", reply_markup=get_main_menu())
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
        await update.message.reply_text(f"✅ {key} сохранено: {value}", reply_markup=get_main_menu())
        await send_log(f"Настройка {key}: {value}", context)
    except Exception as e:
        await send_log(f"Ошибка настройки: {e}", context)
    finally:
        context.user_data['state'] = None

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info(f"Получено сообщение: {text}")
    if text == "Отменить действие 🚫":
        context.user_data.clear()
        await update.message.reply_text("🚫 Действие отменено.", reply_markup=get_main_menu())
        return
    if context.user_data.get('state') == 'registration_count':
        await process_registration_count(update, context)
    elif context.user_data.get('state') == 'upload_photos_count':
        await process_upload_photos_count(update, context)
    elif context.user_data.get('state') == 'upload_photos_files':
        await process_upload_photos_files(update, context)
    elif context.user_data.get('state') == 'spam_count':
        await process_spam_count(update, context)
    elif context.user_data.get('state') == 'settings':
        await handle_settings(update, context)
    elif context.user_data.get('state') == 'save_setting':
        await save_setting(update, context)
    elif text == "Запустить регистрацию 📝":
        await handle_registration(update, context)
    elif text == "Запустить лайкинг 👍":
        await handle_liking(update, context)
    elif text == "Обновить токен 🔑":
        await handle_update_token(update, context)
    elif text == "Добавить изображения 🖼️":
        await handle_upload_photos(update, context)
    elif text == "Удалить изображения ❌":
        await handle_delete_photos(update, context)
    elif text == "Запустить спам 💬":
        await handle_spam(update, context)
    elif text == "Статистика 📊":
        await handle_statistics(update, context)
    elif text == "Настройки ⚙️":
        await settings_menu(update, context)

def main():
    try:
        logger.info("Инициализация бота...")
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        logger.info("Бот инициализирован")
        
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(MessageHandler(filters.PHOTO, process_upload_photos_files))
        application.add_handler(MessageHandler(filters.Text() & ~filters.Command(), message_handler))
        application.add_handler(CommandHandler("finish_upload", process_upload_photos_files))
        logger.info("Обработчики добавлены")
        
        logger.info("Запуск polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Polling запущен")
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        raise

if __name__ == '__main__':
    logger.info("Запуск приложения...")
    main()
