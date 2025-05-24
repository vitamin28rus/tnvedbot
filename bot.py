import asyncio
import html
from aiogram import Bot, Dispatcher, types, F
from playwright.async_api import async_playwright
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from ai_api import (
    extract_hs_code,
    check_description_sufficiency,
    get_keywords,
    analyze_parsed_results2,
)
from parser2 import (
    parse_ifcg,
    parse_tnved_tree,
    fetch_tks_explanation,
    fetch_examples,
    parse_tks_info2,
)
from database import (
    get_user,
    update_access,
    check_and_update_trial,
    log_query,
    get_analytics_data,
)
from dotenv import load_dotenv
import os
import ast
from database import init_db
import logging

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = ast.literal_eval(os.getenv("ADMIN_IDS", "set()"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

user_context = {}

cache_explanation = {}

PLAYWRIGHT = None
BROWSER = None


# инициализация браузера
async def init_browser():
    global PLAYWRIGHT, BROWSER
    PLAYWRIGHT = await async_playwright().start()
    BROWSER = await PLAYWRIGHT.chromium.launch(headless=True)


# закрытие браузера
async def shutdown_browser():
    await PLAYWRIGHT.stop()


@dp.message(CommandStart())
async def start(message: types.Message):
    user_context.pop(message.from_user.id, None)
    await message.answer(
        "📦 Отправьте описание товара для подбора кода ТН ВЭД:",
        reply_markup=new_query_kb,
    )


@dp.message(Command("analytics"))
async def cmd_analytics(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply("❌ У вас нет доступа к этой команде.")
    data = get_analytics_data()
    text = (
        "📊 *Аналитика*\n\n"
        "🧑‍💻 *Пользователи:*\n"
        f"   • Всего: {data['total_users']}\n"
        f"   • Новых (24 ч): {data['new_users_24h']}\n"
        f"   • Активных (7 дн): {data['active7']}\n\n"
        "🔍 *Запросы:*\n"
        f"   • Подбор кода ТН ВЕД: {data['code_total']}\n"
        f"   • Таможенные сборы: {data['duty_total']}\n"
        f"   • Дерево: {data['tree_total']}\n"
        f"   • Пояснения: {data['explanations_total']}\n"
        f"   • Примеры: {data['examples_total']}"
    )

    await message.reply(text, parse_mode="Markdown")


@dp.message(Command("newrequest"))
async def cmd_newrequest(message: types.Message):
    user_context.pop(message.from_user.id, None)
    await message.answer("📦 Отправьте описание товара для подбора кода ТН ВЭД:")
    return


@dp.message(F.text)
async def handle_description(message: types.Message):
    user_id = message.from_user.id
    description = message.text

    if description == "🔄 Новый запрос":
        user_context.pop(user_id, None)
        await message.answer("📦 Отправьте описание товара для подбора кода ТН ВЭД:")
        return

    if description == "👤 Личный кабинет":
        await user_profile(message)
        return

    msg = await message.answer("⏳ Анализирую описание товара...")

    sufficient, reply = await check_description_sufficiency(
        description, user_context.get(user_id)
    )

    if not sufficient:
        user_context[user_id] = user_context.get(user_id, []) + [
            {"role": "user", "content": description},
            {"role": "assistant", "content": reply},
        ]
        await msg.edit_text(f"❓ {reply}")
        return

    if sufficient:
        await msg.edit_text("✅ Описание полное. Формирую ключевые слова...")

    user_messages = []

    if user_context.get(user_id):
        user_messages = [
            msg["content"] for msg in user_context[user_id] if msg["role"] == "user"
        ]  # Получаем из контекста только сообщения пользователя

    user_messages.append(description)

    full_user_input = " ".join(user_messages)  # Склеиваем их в единый текст

    keywords = await get_keywords(full_user_input)

    logger.info(full_user_input)
    logger.info(keywords)

    await msg.edit_text(
        f"🔎 Выполняю поиск по ключевым словам: {keywords.replace('+', ', ')}"
    )

    while True:
        try:
            parsed_data = await parse_ifcg(keywords)
            # Здесь проверка твоего условия
            if parsed_data:  # замени на нужное условие
                break

            # Удаляем последнее слово
            keywords = "+".join(keywords.split("+")[:-1])
            logger.info(keywords)
            # Если уже нечего удалять — останавливаем
            if not keywords:
                break
        except RuntimeError as e:
            # ошибка запроса, например: не удалось подключиться
            await message.answer("Произошла ошибка запроса, повторите поиск.")
            user_context.pop(user_id, None)  # сбрасываем контекст
        except Exception as e:
            # на крайний случай — неизвестная ошибка
            await message.answer("Произошла непредвиденная ошибка.")
            user_context.pop(user_id, None)  # сбрасываем контекст

    if not parsed_data:
        await message.answer(
            "Не удалось найти данные по запросу, повторите поиск с другим описанием"
        )
        user_context.pop(user_id, None)  # сбрасываем контекст
        return

    await msg.edit_text("🧠 Анализирую полученные результаты...")
    final_result = await analyze_parsed_results2(full_user_input, parsed_data)

    hs_code = extract_hs_code(final_result) if extract_hs_code(final_result) else None

    log_query(message.from_user.id, "code")

    markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📑 Узнать таможенные платежи",
                        callback_data=f"duty:{hs_code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📚 Показать дерево ТН ВЭД",
                        callback_data=f"tree:{hs_code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📘 Пояснения к ТН ВЭД",
                        callback_data=f"explan:{hs_code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📝 Примеры декларирования",
                        callback_data=f"examples:{hs_code}",
                    )
                ],
            ]
        )
        if hs_code
        else None
    )

    user_context.pop(user_id, None)  # сбрасываем контекст после успешного ответа

    await msg.edit_text(f"🎯 Результат:\n{final_result}", reply_markup=markup)


@dp.callback_query(F.data.startswith("duty:"))
async def show_duty(call: types.CallbackQuery):
    hs_code = call.data.split(":")[1]

    has_access, attempts_left = check_and_update_trial(
        call.from_user.id, call.from_user.username
    )

    if not has_access:
        await call.answer(
            "⚠️ Доступ закрыт. Приобретите полный доступ в личном кабинете.",
            show_alert=True,
        )
        return

    try:
        await call.answer()
    except Exception:
        pass

    wait_msg = await call.message.reply(
        "⏳ Пожалуйста, подождите..."
    )  # Показываем "Ждите"
    duty = await parse_tks_info2(BROWSER, hs_code)
    await wait_msg.edit_text(
        f"📌 *Код ТН ВЭД:* {hs_code}\n{duty}",
        parse_mode="Markdown",
    )

    log_query(call.from_user.id, "duty")


@dp.callback_query(F.data.startswith("tree:"))
async def show_tnved_tree(call: types.CallbackQuery):
    hs_code = call.data.split(":")[1]

    has_access, attempts_left = check_and_update_trial(
        call.from_user.id, call.from_user.username
    )

    if not has_access:
        await call.answer(
            "⚠️ Доступ закрыт. Приобретите полный доступ в личном кабинете.",
            show_alert=True,
        )
        return

    tree_text = await parse_tnved_tree(hs_code)

    await call.message.reply(
        tree_text, reply_markup=new_query_kb, parse_mode="Markdown"
    )

    await call.answer()  # Убираем "часики" у кнопки
    log_query(call.from_user.id, "tree")


@dp.callback_query(F.data.startswith("explan:"))
async def show_tnved_explanation(call: types.CallbackQuery):
    hs_code = call.data.split(":")[1]

    has_access, attempts_left = check_and_update_trial(
        call.from_user.id, call.from_user.username
    )

    if not has_access:
        await call.answer(
            "⚠️ Доступ закрыт. Приобретите полный доступ в личном кабинете.",
            show_alert=True,
        )
        return

    pages = await fetch_tks_explanation(hs_code)
    if not pages:
        await call.message.reply("Не удалось получить пояснение.")
        return

    cache_explanation[call.from_user.id] = {"code": hs_code, "pages": pages}

    await send_page(call, hs_code, pages, 0)

    await call.answer()  # Убираем "часики" у кнопки
    log_query(call.from_user.id, "explanations")


@dp.callback_query(F.data.startswith("examples:"))
async def show_examles(call: types.CallbackQuery):
    hs_code = call.data.split(":")[1]

    has_access, attempts_left = check_and_update_trial(
        call.from_user.id, call.from_user.username
    )

    if not has_access:
        await call.answer(
            "⚠️ Доступ закрыт. Приобретите полный доступ в личном кабинете.",
            show_alert=True,
        )
        return

    examples_text = await fetch_examples(hs_code)
    safe_text = html.escape(examples_text)
    # Если нужно оставить какие‑то теги (например, <b>), можно заменить их обратно:
    safe_text = safe_text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    if not examples_text:
        await call.message.reply(
            "Не удалось получить примеры декларирования.",
            reply_markup=new_query_kb,
        )
        return
    await call.message.reply(safe_text, reply_markup=new_query_kb, parse_mode="HTML")

    await call.answer()  # Убираем "часики" у кнопки
    log_query(call.from_user.id, "examples")


async def send_page(message_or_cb, code, pages, index, get=True):
    total = len(pages)
    page_text = f"📄 Страница {index + 1}/{total}\n\n{pages[index]}"

    buttons = []
    if index > 0:
        buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад", callback_data=f"tnved:{code}:{index - 1}"
            )
        )
    if index < total - 1:
        buttons.append(
            InlineKeyboardButton(
                text="▶️ Далее", callback_data=f"tnved:{code}:{index + 1}"
            )
        )

    markup = InlineKeyboardMarkup(inline_keyboard=[buttons] if buttons else [])

    if isinstance(message_or_cb, types.CallbackQuery):
        if get:
            await message_or_cb.message.reply(page_text, reply_markup=markup)
        else:
            await message_or_cb.message.edit_text(page_text, reply_markup=markup)
        await message_or_cb.answer()
    else:
        await message_or_cb.answer(page_text, reply_markup=markup)


@dp.callback_query(F.data.startswith("tnved:"))
async def tnved_pagination(call: types.CallbackQuery):
    _, code, index_str = call.data.split(":")
    index = int(index_str)
    pages = cache_explanation.get(call.from_user.id, {}).get("pages")

    if not pages:
        await call.answer("❗ Данные устарели. Повторите команду.")
        return

    await send_page(call, code, pages, index, False)


@dp.callback_query(F.data == "buy_full_access")
async def buy_full_access(call: types.CallbackQuery):
    update_access(call.from_user.id, True)
    await call.message.edit_text("🎉 Поздравляем! Полный доступ успешно активирован.")


new_query_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔄 Новый запрос")],
        # [KeyboardButton(text="👤 Личный кабинет")],
    ],
    resize_keyboard=True,
)


async def user_profile(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    full_access = "✅ Открыт" if user[2] else "❌ Закрыт"
    trial_attempts = user[3]

    markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Купить полный доступ", callback_data="buy_full_access"
                    )
                ]
            ]
        )
        if not user[2]
        else None
    )

    await message.answer(
        f"👤 *Личный кабинет:*\n\n"
        f"📌 *Полный доступ:* {full_access}\n"
        f"🎟️ *Пробный доступ :* {trial_attempts}",
        reply_markup=markup,
        parse_mode="Markdown",
    )


async def set_bot_commands(bot):
    commands = [
        BotCommand(command="newrequest", description="Новый запрос"),
    ]
    await bot.set_my_commands(commands)


async def main():
    init_db()  # инициализируем базу данных
    await init_browser()  # инициализируем браузер
    await set_bot_commands(bot)
    try:
        # Пойдём в вечный polling
        await dp.start_polling(bot)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        # Корректно завершаем в случае CTRL+C или перезагрузки
        pass
    finally:
        await shutdown_browser()  # очищаем ресурсы
        await bot.session.close()  # закрываем сессию aiohttp внутри Bot


if __name__ == "__main__":
    asyncio.run(main())
