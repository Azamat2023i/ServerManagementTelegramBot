import logging
import subprocess
import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import platform

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BufferedInputFile, CallbackQuery
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

MAX_MESSAGE_LENGTH = 4000
DEFAULT_DOWNLOAD_DIR = "/tmp/bot_uploads"
COMMANDS_FILE = "commands.json"

# Инициализация бота
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# Состояния для FSM
class FileStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_file_path = State()
    waiting_for_download_path = State()


# Загрузка команд из JSON
def load_commands() -> Dict[str, Any]:
    try:
        with open(COMMANDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки команд: {e}")
        return {}


# Информация о запуске
start_time = datetime.now()
bot_version = "3.0"
commands_config = load_commands()

# Маршрутизаторы
admin_router = Router()
file_router = Router()
command_router = Router()


# Фильтр для проверки админа
async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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
                chunks.extend([line[i:i + max_length] for i in range(0, len(line), max_length)])
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def get_cancel_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_action")
    return builder.as_markup()


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


# ==================== Основные команды ====================

@admin_router.message(Command("data"))
@admin_router.message(Command("start"))
async def cmd_start_data(message: Message):
    if not await is_admin(message.from_user.id):
        return

    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]

    help_text = (
        f"🖥️ <b>Бот управления сервером v{bot_version}</b>\n\n"
        f"⏱ Время работы: <code>{uptime_str}</code>\n"
        f"🆔 Ваш ID: <code>{message.from_user.id}</code>\n\n"
        "📋 <b>Основные команды:</b>\n"
        "/status - Статус сервера\n"
        "/disk - Дисковое пространство\n"
        "/memory - Использование памяти\n\n"
        "📁 <b>Работа с файлами:</b>\n"
        "/upload - Загрузить файл на сервер\n"
        "/download - Скачать файл с сервера\n\n"
        "⚙️ <b>Другие команды:</b>\n"
        "/reboot - Перезагрузка системы\n"
        "/execute - Выполнить команду"
    )
    # Добавляем команды из конфига
    if commands_config:
        help_text += "\n\n🛠️ <b>Дополнительные команды:</b>"
        for cmd in commands_config.keys():
            help_text += f"\n/{cmd} - {commands_config[cmd]['description']}"

    await message.answer(help_text)


# Обработчик для команд из JSON
async def handle_config_command(message: Message, command_name: str):
    if command_name not in commands_config:
        await message.answer("❌ Команда не найдена")
        return

    cmd_config = commands_config[command_name]
    results = [cmd_config.get("title", f"<b>🔹 {command_name}:</b>")]

    for name, cmd in cmd_config.items():
        if name == "title" or name == "description":
            continue

        stdout, stderr, retcode = await execute_shell_command(cmd)
        if retcode == 0:
            results.append(f"\n<b>• {name}:</b>\n<code>{stdout}</code>")
        else:
            results.append(f"\n<b>• {name}:</b>\n❌ Ошибка: <code>{stderr}</code>")

    full_message = "\n".join(results)
    for chunk in split_long_message(full_message):
        await message.answer(chunk)


# Динамическая регистрация команд из JSON
for cmd_name in commands_config.keys():
    @admin_router.message(Command(cmd_name))
    async def dynamic_command_handler(message: Message, command: CommandObject):
        if not await is_admin(message.from_user.id):
            return
        await handle_config_command(message, command.command)


# ==================== Стандартные команды ====================

@command_router.message(Command("status"))
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
            results.append(f"\n<b>• {name}:</b>\n<code>{stdout}</code>")
        else:
            results.append(f"\n<b>• {name}:</b>\n❌ Ошибка: <code>{stderr}</code>")

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


# Обработчик команды reboot
@command_router.message(Command("reboot"))
async def cmd_reboot(message: Message):
    if not await is_admin(message.from_user.id):
        return

    # Простое подтверждение перед перезагрузкой
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="confirm_reboot")
    builder.button(text="❌ Нет", callback_data="cancel_action")

    await message.answer(
        "⚠️ <b>Вы уверены, что хотите перезагрузить сервер?</b>\n\n"
        "Будет выполнена команда: <code>sudo reboot</code>",
        reply_markup=builder.as_markup()
    )


# Подтверждение перезагрузки
@dp.callback_query(F.data == "confirm_reboot")
async def confirm_reboot(callback: CallbackQuery):
    try:
        # Просто выполняем команду перезагрузки
        subprocess.run("sudo reboot", shell=True)

        await callback.message.answer("🔄 Выполняю перезагрузку сервера...")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при перезагрузке: {str(e)}")

    await callback.answer()


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

    save_path = message.text.strip() if message.text else ""
    if not save_path or save_path.lower() == "cancel":
        save_path = DEFAULT_DOWNLOAD_DIR

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

        await bot.download_file(file_path, str(full_path))

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
        await message.answer("❌ Загрузка отменена")
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
        if file_size > 20 * 1024 * 1024:
            await message.answer("❌ Файл слишком большой (максимум 20MB)")
            return

        with open(path, 'rb') as file:
            await message.answer_document(
                BufferedInputFile(file.read(), filename=path.name),
                caption=f"📥 Файл: <code>{file_path}</code>"
            )
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке файла: {str(e)}")


@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено")
    await callback.answer()


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
    dp.include_router(admin_router)
    dp.include_router(file_router)
    dp.include_router(command_router)

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
    Path(DEFAULT_DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    main()