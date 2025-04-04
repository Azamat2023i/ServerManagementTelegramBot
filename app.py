import logging
import subprocess
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import platform

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, FSInputFile, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# Конфигурация бота
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not API_TOKEN:
    logger.error("Не указан TELEGRAM_BOT_TOKEN в переменных окружения")
    sys.exit(1)

ADMIN_IDS = [int(id_) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_]
if not ADMIN_IDS:
    logger.error("Не указаны ADMIN_IDS в переменных окружения")
    sys.exit(1)

MAX_MESSAGE_LENGTH = 4000  # Максимальная длина сообщения в Telegram
DEFAULT_DOWNLOAD_DIR = "/tmp/bot_uploads"  # Папка для загрузки файлов по умолчанию

# Инициализация бота
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# Состояния для FSM
class FileStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_file_path = State()
    waiting_for_download_path = State()


# Информация о запуске
start_time = datetime.now()
bot_version = "2.1"

# Маршрутизаторы
admin_router = Router()
file_router = Router()
command_router = Router()
system_router = Router()


# Фильтр для проверки админа
async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# Улучшенная функция для разбивки длинных сообщений
def split_long_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]

    lines = text.split('\n')
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                # Если строка очень длинная (без переносов), разбиваем посимвольно
                chunks.extend([line[i:i + max_length] for i in range(0, len(line), max_length)])
                current_chunk = ""
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# Клавиатура для отмены действий
def get_cancel_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_action")
    return builder.as_markup()


# Функция для выполнения команды shell
async def execute_shell_command(command: str) -> Tuple[str, str, int]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            executable='/bin/bash',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return (
            result.stdout.strip() if result.stdout else "",
            result.stderr.strip() if result.stderr else "",
            result.returncode
        )
    except Exception as e:
        return "", str(e), -1


# Отправка уведомления админам при запуске
async def notify_admins(bot: Bot):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🟢 Бот v{bot_version} запущен!\n"
                f"⏰ Время запуска: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🐍 Python: {sys.version.split()[0]}\n"
                f"💻 Сервер: \n"
                f"  system: {platform.uname().system}\n"
                f"  node: {platform.uname().node}\n"
                f"  version: {platform.uname().version}"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")


# ==================== Команды администратора ====================

@admin_router.message(Command("data"))
@admin_router.message(Command("start"))
async def cmd_start(message: Message):
    if not await is_admin(message.from_user.id):
        return

    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]  # Убираем микросекунды

    await message.answer(
        f"🖥️ <b>Бот управления Linux сервером v{bot_version}</b>\n\n"
        f"⏱ Время работы: <code>{uptime_str}</code>\n"
        f"🆔 Ваш ID: <code>{message.from_user.id}</code>\n\n"
        "📋 <b>Основные команды:</b>\n"
        "/status - Статус сервера\n"
        "/disk - Дисковое пространство\n"
        "/memory - Использование памяти\n"
        "📁 <b>Работа с файлами:</b>\n"
        "/upload - Загрузить файл на сервер\n"
        "/download - Скачать файл с сервера\n\n"
        "⚙️ <b>Другие команды:</b>\n"
        "/execute - Выполнить команду"
    )


# ==================== Команды управления сервером ====================

@system_router.message(Command("status"))
async def cmd_status(message: Message):
    if not await is_admin(message.from_user.id):
        return

    commands = {
        "Время работы": "uptime",
        "Нагрузка": "cat /proc/loadavg",
        "Пользователи": "who",
        "Дата и время": "date",
        "Дисковое пространство": "df -h | grep -v tmpfs"
    }

    results = ["<b>🔄 Статус сервера:</b>"]
    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]
    results.append(f"\n⏱ <b>Время работы бота:</b> <code>{uptime_str}</code>")

    for name, cmd in commands.items():
        stdout, stderr, retcode = await execute_shell_command(cmd)
        if retcode == 0:
            results.append(f"\n<b>🔹 {name}:</b>\n<code>{stdout}</code>")
        else:
            results.append(f"\n<b>🔹 {name}:</b>\n❌ Ошибка: <code>{stderr}</code>")

    full_message = "\n".join(results)
    for chunk in split_long_message(full_message):
        await message.answer(chunk)


@system_router.message(Command("disk"))
async def cmd_disk(message: Message):
    if not await is_admin(message.from_user.id):
        return

    commands = {
        "Дисковое пространство": "df -h",
        "Крупные директории": "du -sh /* 2>/dev/null | sort -hr | head -n 10",
        "Блочные устройства": "lsblk"
    }

    results = ["<b>💾 Информация о дисках:</b>"]

    for name, cmd in commands.items():
        stdout, stderr, retcode = await execute_shell_command(cmd)
        if retcode == 0:
            results.append(f"\n<b>🔹 {name}:</b>\n<code>{stdout}</code>")
        else:
            results.append(f"\n<b>🔹 {name}:</b>\n❌ Ошибка: <code>{stderr}</code>")

    full_message = "\n".join(results)
    for chunk in split_long_message(full_message):
        await message.answer(chunk)


@system_router.message(Command("memory"))
async def cmd_memory(message: Message):
    if not await is_admin(message.from_user.id):
        return

    commands = {
        "Использование памяти": "free -h",
        "Статистика памяти": "vmstat",
        "Детали памяти": "cat /proc/meminfo | grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree'"
    }

    results = ["<b>🧠 Информация о памяти:</b>"]

    for name, cmd in commands.items():
        stdout, stderr, retcode = await execute_shell_command(cmd)
        if retcode == 0:
            results.append(f"\n<b>🔹 {name}:</b>\n<code>{stdout}</code>")
        else:
            results.append(f"\n<b>🔹 {name}:</b>\n❌ Ошибка: <code>{stderr}</code>")

    full_message = "\n".join(results)
    for chunk in split_long_message(full_message):
        await message.answer(chunk)


@command_router.message(Command("execute"))
async def cmd_execute(message: Message, command: CommandObject):
    if not await is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("ℹ️ Укажите команду для выполнения. Пример: /execute ls -la")
        return

    cmd = command.args
    await message.answer(f"🔄 Выполняю команду: <code>{cmd}</code>")

    try:
        stdout, stderr, retcode = await execute_shell_command(cmd)

        if retcode != 0:
            raise Exception(stderr if stderr else f"Команда вернула код {retcode}")

        output = stdout if stdout else "✅ Команда выполнена успешно, вывод отсутствует."

        for chunk in split_long_message(output):
            await message.answer(f"<pre>{chunk}</pre>")

    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении команды:\n<pre>{str(e)}</pre>"
        await message.answer(error_msg)


# ==================== Работа с файлами ====================

@file_router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    await message.answer(
        "📤 Отправьте файл для загрузки на сервер\n"
        "Или нажмите ❌ Отмена для прерывания",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(FileStates.waiting_for_file)


@file_router.message(FileStates.waiting_for_file, F.document)
async def handle_file_upload(message: Message, state: FSMContext):
    if not message.document:
        await message.answer("ℹ️ Пожалуйста, отправьте файл")
        return

    await state.update_data(file_id=message.document.file_id, file_name=message.document.file_name)
    await message.answer(
        "📁 Укажите путь для сохранения файла (например: /home/user/uploads/)\n"
        f"По умолчанию: <code>{DEFAULT_DOWNLOAD_DIR}</code>\n\n"
        "Или нажмите ❌ Отмена для прерывания",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(FileStates.waiting_for_file_path)


@file_router.message(FileStates.waiting_for_file_path)
async def handle_file_path(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = data.get('file_id')
    original_name = data.get('file_name')

    if not file_id or not original_name:
        await message.answer("❌ Ошибка: данные о файле не найдены")
        await state.clear()
        return

    # Обработка пути для сохранения
    save_path = message.text.strip() if message.text else ""
    if not save_path or save_path.lower() == "cancel":
        save_path = DEFAULT_DOWNLOAD_DIR

    # Создаем директорию, если ее нет
    try:
        Path(save_path).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await message.answer(f"❌ Ошибка создания директории: {str(e)}")
        await state.clear()
        return

    full_path = Path(save_path) / original_name

    try:
        file = await bot.get_file(file_id)
        file_path = file.file_path

        # Скачиваем файл
        await bot.download_file(file_path, str(full_path))

        # Проверяем размер файла
        file_size = os.path.getsize(full_path)
        human_size = f"{file_size / 1024:.2f} KB" if file_size < 1024 * 1024 else f"{file_size / 1024 / 1024:.2f} MB"

        await message.answer(
            f"✅ Файл успешно сохранен:\n"
            f"📄 Имя: <code>{original_name}</code>\n"
            f"📂 Путь: <code>{full_path}</code>\n"
            f"📏 Размер: {human_size}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при загрузке файла: {str(e)}")
    finally:
        await state.clear()


@file_router.message(Command("download"))
async def cmd_download(message: Message, state: FSMContext, command: CommandObject):
    if not await is_admin(message.from_user.id):
        return

    if command.args:
        # Если путь указан сразу в команде
        await handle_download_request(message, command.args)
    else:
        await message.answer(
            "📥 Укажите путь к файлу для скачивания (например: /var/log/syslog)\n"
            "Или нажмите ❌ Отмена для прерывания",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(FileStates.waiting_for_download_path)


@file_router.message(FileStates.waiting_for_download_path)
async def handle_download_path(message: Message, state: FSMContext):
    file_path = message.text.strip() if message.text else ""
    await handle_download_request(message, file_path)
    await state.clear()


async def handle_download_request(message: Message, file_path: str):
    if not file_path or file_path.lower() == "cancel":
        await message.answer("❌ Загрузка отменена", reply_markup=get_cancel_keyboard())
        return

    try:
        path = Path(file_path)
        if not path.exists():
            await message.answer("❌ Файл не найден")
            return

        if path.is_dir():
            await message.answer("❌ Указанный путь является директорией")
            return

        file_size = path.stat().st_size
        if file_size > 20 * 1024 * 1024:  # 20MB - лимит Telegram
            await message.answer("❌ Файл слишком большой (максимум 20MB)")
            return

        with open(path, 'rb') as file:
            await message.answer_document(
                BufferedInputFile(file.read(), filename=path.name),
                caption=f"📥 Файл: <code>{file_path}</code>"
            )
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке файла: {str(e)}")


# ==================== Обработка отмены ====================

@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено")
    await callback.answer()


# ==================== Запуск бота ====================

async def on_startup(bot: Bot):
    logger.info("Бот запускается...")
    await notify_admins(bot)


async def on_shutdown(bot: Bot):
    logger.info("Бот выключается...")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "🛑 Бот выключается...")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {str(e)}")


def main():
    # Подключаем роутеры
    dp.include_router(admin_router)
    dp.include_router(file_router)
    dp.include_router(command_router)
    dp.include_router(system_router)

    # Настройка обработчиков
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        logger.info("Запуск бота...")
        dp.run_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
    finally:
        logger.info("Бот остановлен")


if __name__ == '__main__':
    # Создаем директорию для загрузок по умолчанию
    Path(DEFAULT_DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    main()