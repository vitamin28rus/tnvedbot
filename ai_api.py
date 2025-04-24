# import openai
from openai import OpenAI, DefaultHttpxClient, OpenAIError
from config import OPENAI_API_KEY, IO_API_KEY
import re
import logging
import openai
import asyncio

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Ты — эксперт-аналитик по классификации товаров по коду ТН ВЭД (товарная номенклатура внешнеэкономической деятельности).

Твоя задача — максимально точно и однозначно подобрать 10-значный код ТН ВЭД на основе описания товара, предоставленного пользователем.

Выполняй строго следующие этапы анализа:

1. Внимательно изучи описание товара от пользователя.
2. Сформируй предварительный код ТН ВЭД (без вывода ответа).
3. Проведи тщательный анализ выбранного кода (без вывода ответа):
    - Проверь возможные двусмысленности или неточности.
    - Проверь соответствие выбранного кода официальным справочникам.
    - Проверь актуальность кода на текущую дату
4. Если есть хоть малейшая неопределенность или недостаточность информации, обязательно сформулируй и задавай пользователю уточняющие вопросы, прежде чем выдавать окончательный код.
5. Только после полного устранения неопределенностей предоставь итоговый код ТН ВЭД и краткое обоснование выбора.
6. В итоговом ответе строго укажи:
   - Точный 10-значный код ТН ВЭД.
   - Краткое обоснование, почему выбран именно этот код.
   - Уровень уверенности в процентах от 0 до 100.

Если точный код определить невозможно, сообщи об этом явно и запроси дополнительные детали.
Отвечай максимально кратко и ёмко.
"""

client = OpenAI(
    api_key=OPENAI_API_KEY,
    http_client=DefaultHttpxClient(
        proxy="http://aXhqcPR1:NumDnqiQ@192.177.45.85:62896",
    ),
)

clientIO = openai.OpenAI(
    api_key=IO_API_KEY,
    base_url="https://api.intelligence.io.solutions/api/v1/",
)


# Сначала проверяем, достаточно ли данных
async def check_description_sufficiency(
    description: str, context: list = None, max_retries: int = 3
):
    # SYSTEM_PROMPT_CHECK = """
    # Ты эксперт по ТН ВЭД. Проверь, достаточно ли информации для точного определения кода ТН ВЭД.
    # Если нет — задай максимум уточняющих вопросов кратко и ёмко.
    # Если достаточно — ответь только "ДА".
    # """
    SYSTEM_PROMPT_CHECK = """
    Ты эксперт по подбору кода ТН ВЭД и помогаешь пользователю правильно описать товар, чтобы по этой информации оператор смог точно определить код ТН ВЭД.
    Если информации недостаточно — задай КОНКРЕТНЫЕ, ЧЁТКИЕ И СТРУКТУРИРОВАННЫЕ вопросы

    Правила формирования уточняющих вопросов:

    1. Твоя задача — задавать ТОЛЬКО те уточняющие вопросы, от которых напрямую зависит выбор кода ТН ВЭД.
    — Не задавай сразу все вопросы списком.
    — Начинай с самого критичного параметра.
    — Дожидайся ответа и задавай следующий уточняющий вопрос, на основе предыдущего.

    2. ЗАПРЕЩЕНО задавать вопросы, которые не влияют напрямую на код ТН ВЭД, например:
    - Цвет товара (если цвет не влияет на химический состав или назначение).
    - Бренд или производитель.
    - Страна происхождения товара.
    - Размер упаковки, количество в упаковке.
    - Очевидные функции (например, наличие камеры и интернета в современных смартфонах, очевидное назначение спортивного инвентаря).

    3. РАЗРЕШЕНО задавать только вопросы, напрямую влияющие на классификацию, например:
    - Материал товара (если это существенно для кода).
    - Специфическое назначение (если код зависит от назначения товара).
    - Технические или конструктивные особенности, влияющие на классификацию (например, наличие или отсутствие мотора, тип двигателя, наличие специальных компонентов, химический состав).

    4. Если информации достаточно для точного определения кода ТН ВЭД, ответь только: "ДА".
    """

    SYSTEM_PROMPT_CHECK2 = """
    Ты эксперт по подбору кода ТН ВЭД и помогаешь пользователю правильно описать товар, чтобы по этой информации оператор смог точно определить код ТН ВЭД.

    Твоя задача — задавать ТОЛЬКО те уточняющие вопросы, от которых напрямую зависит выбор кода ТН ВЭД.

    Ты НЕ должен пытаться угадать код ТН ВЭД!

    Правила формирования уточняющих вопросов:

    1. ЗАПРЕЩЕНО задавать вопросы, которые не влияют напрямую на код ТН ВЭД, например:
    - Цвет товара (если цвет не влияет на химический состав или назначение).
    - Бренд или производитель.
    - Страна происхождения товара.
    - Размер упаковки, количество в упаковке.
    - Очевидные функции (например, наличие камеры и интернета в современных смартфонах, очевидное назначение спортивного инвентаря).

    2. РАЗРЕШЕНО задавать только вопросы, напрямую влияющие на классификацию, например:
    - Материал товара (если это существенно для кода).
    - Специфическое назначение (если код зависит от назначения товара).
    - Новый это товар или бывший в употреблении (если это характерно для товара)
    - Технические или конструктивные особенности, влияющие на классификацию (например, наличие или отсутствие мотора, тип двигателя, наличие специальных компонентов, химический состав).

    3. Если информации достаточно для точного определения кода ТН ВЭД, ответь только: "ДА".

    Твоя цель — минимальное количество уточнений, максимальная эффективность и точность вопросов.
    """

    messages = [{"role": "system", "content": SYSTEM_PROMPT_CHECK2}]
    if context:
        messages.extend(context)
    messages.append({"role": "user", "content": description})

    # response = client.chat.completions.create(
    #     model="gpt-4o-mini", messages=messages, temperature=0.0
    # )

    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(  # убрать или добавить IO для досутпа к chatgpt и китайской ИИ!
                model="gpt-4.1-mini",  # использовать модель gpt-4o или deepseek-ai/DeepSeek-R1-Distill-Llama-70B
                messages=messages,
                temperature=0,
                stream=False,
            )
            reply = response.choices[0].message.content.strip()
            if "</think>" in reply:
                reply = reply.split("</think>")[1].strip()
            reply_upper = reply.upper().strip()
            if reply_upper.startswith("ДА") or reply_upper.endswith("ДА"):
                return True, "ДА"

            return False, reply

        except OpenAIError as e:
            logger.warning(f"[Attempt {attempt}] OpenAIError: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[Attempt {attempt}] Timeout waiting for model response")
        except Exception as e:
            logger.error(f"[Attempt {attempt}] Unexpected error: {e}", exc_info=True)

        # Если не последний повтор — ждём экспоненциально растущий backoff
        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2

    # Все попытки исчерпаны
    err_msg = "⚠️ Не удалось получить ответ от модели. Попробуйте позже."
    logger.error(err_msg)
    return err_msg


# Формируем ключевые слова для поиска
async def get_keywords(description: str, max_retries: int = 3):
    SYSTEM_PROMPT_KEYWORDS = """
    Проанализируй описание товара, сформируй  от 3 до 5 наиболее значимых и подходящих ключевых слов,
    которые однозначно характеризуют данный товар для поиска кода ТН ВЭД. Количество общих слов должно быть не больше 5.
    Все слова раздели знаком "+" без пробелов.
    """

    # response = client.chat.completions.create(
    #     model="gpt-4o-mini",
    #     messages=[
    #         {"role": "system", "content": SYSTEM_PROMPT_KEYWORDS},
    #         {"role": "user", "content": description},
    #     ],
    #     temperature=0.0,
    # )

    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(  # убрать или добавить IO для досутпа к chatgpt и китайской ИИ!
                model="gpt-4.1-mini",  # использовать модель gpt-4o или deepseek-ai/DeepSeek-R1-Distill-Llama-70B
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_KEYWORDS},
                    {
                        "role": "user",
                        "content": description,
                    },
                ],
                temperature=0,
                stream=False,
            )
            text = response.choices[0].message.content.strip()

            # Если в ответе ожидается разделитель </think>, но его нет — возвращаем полный текст
            if "</think>" in text:
                return text.split("</think>")[1].strip()
            return text

        except OpenAIError as e:
            logger.warning(f"[Attempt {attempt}] OpenAIError: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[Attempt {attempt}] Timeout waiting for model response")
        except Exception as e:
            logger.error(f"[Attempt {attempt}] Unexpected error: {e}", exc_info=True)

        # Если не последний повтор — ждём экспоненциально растущий backoff
        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2

    # Все попытки исчерпаны
    err_msg = "⚠️ Не удалось получить ответ от модели. Попробуйте позже."
    logger.error(err_msg)
    return err_msg


async def analyze_parsed_results2(description, parsed_data, max_retries: int = 3):
    SYSTEM_PROMPT_FINAL = """
    Ты эксперт по ТН ВЭД.

    Перед тобой — список кодов ТН ВЭД, каждый из которых содержит:
    - 10-значный код,
    - официальное описание,
    - реальные примеры товаров, которые подпадают под данный код.

    Твоя задача — на основе описания товара от пользователя выбрать **наиболее подходящий код** из списка.

    Обращай внимание на:
    - технические характеристики,
    - материал изделия,
    - назначение,
    - форму обработки и упаковки.

    Если описание товара недостаточно подробное — выбери наиболее близкий по смыслу код из представленного списка.

    Ответ должен содержать:
    - Точный 10-значный код ТН ВЭД.
    - Краткое обоснование и характеристика выбранного кода, без сравнения.
    """
    formatted_data = format_parsed_data(parsed_data)

    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(  # убрать или добавить IO для досутпа к chatgpt и китайской ИИ!
                model="gpt-4.1-mini",  # использовать модель gpt-4o или deepseek-ai/DeepSeek-R1-Distill-Llama-70B
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_FINAL},
                    {
                        "role": "user",
                        "content": f"Описание товара пользователя:\n{description}\n\nСписок кодов:\n\n{formatted_data}",
                    },
                ],
                temperature=0,
                stream=False,
            )
            text = response.choices[0].message.content.strip()

            # Если в ответе ожидается разделитель </think>, но его нет — возвращаем полный текст
            if "</think>" in text:
                return text.split("</think>")[1].strip()
            return text

        except OpenAIError as e:
            logger.warning(f"[Attempt {attempt}] OpenAIError: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[Attempt {attempt}] Timeout waiting for model response")
        except Exception as e:
            logger.error(f"[Attempt {attempt}] Unexpected error: {e}", exc_info=True)

        # Если не последний повтор — ждём экспоненциально растущий backoff
        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2

    # Все попытки исчерпаны
    err_msg = "⚠️ Не удалось получить ответ от модели. Попробуйте позже."
    logger.error(err_msg)
    return err_msg


def format_parsed_data(data):
    formatted = ""
    for item in data:
        formatted += f"КОД: {item['code']}\n"
        formatted += f"ОФИЦИАЛЬНОЕ ОПИСАНИЕ: {item['official']}\n"
        formatted += "ПРИМЕРЫ ДЕКЛАРИРОВАНИЯ:\n"
        for example in item["examples"]:
            formatted += f" - {example.strip()}\n"
        formatted += "\n"
    return formatted.strip()


# Функция извлечения уровня уверенности из ответа
def extract_confidence(response_text):
    match = re.search(r"Уровень уверенности.*?(\d+)%", response_text)
    if match:
        return int(match.group(1))
    return 0  # По умолчанию при отсутствии информации считаем уверенность низкой


def extract_hs_code(text):
    match = re.search(r"((?:\d\s*){10})", text)
    if match:
        raw_digits = match.group(1)
        clean_digits = re.sub(r"\s+", "", raw_digits)  # Удаляем все пробелы
        return clean_digits
    else:
        return None
