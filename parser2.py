import aiohttp
import asyncio
import logging
import re
import textwrap
import socket
import random
from bs4 import BeautifulSoup
from collections import defaultdict
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
from pprint import pprint

logger = logging.getLogger(__name__)


async def get_customs_duty(hs_code):
    url = f"https://www.alta.ru/tnved/code/{hs_code}/"

    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)
        soup = BeautifulSoup(html, "html.parser")
        duty = {}

        # Импорт
        import_tag = soup.select_one("fieldset.pTnved_customs b.black")
        duty["import"] = import_tag.text.strip() if import_tag else "не указано"

        # Экспорт
        export_rows = soup.select("fieldset.pTnved_customs tr.pTnved_item")
        export_text = "не указано"
        excise_text = "не указано"

        for row in export_rows:
            label = row.select_one("td:first-child b")
            value = row.select_one("td:nth-child(2)")
            if not label or not value:
                continue

            label_text = label.text.strip().lower()
            value_text = value.text.strip()

            if "экспорт" in label_text or "пошлин" in label_text:
                export_text = value_text
            elif "акциз" in label_text:
                excise_text = value_text

        duty["export"] = export_text
        duty["excise"] = excise_text

        return duty if duty else "Не удалось найти ставку пошлины."


async def fetch_official_description(session, code):
    url = f"https://www.ifcg.ru/kb/tnved/{code}"
    html = await fetch_html(session, url)
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.select_one(".subtitle")
    return tag.text.strip() if tag else "Описание не найдено"


def extract_rows_from_soup(soup):
    result = defaultdict(set)  # code -> set of examples
    results = soup.select(".row.row-in.mt10")
    if len(results) < 5:
        return []  # Возвращаем пустой список, если элементов меньше 5

    for res in results[:15]:
        code_tag = res.select_one(".col-xs-12.col-md-4.col-lg-2.mt10")
        desc_tag = res.select_one(".col-xs-12.col-md-8.col-lg-10.mt10")
        if code_tag and desc_tag:
            code = code_tag.text.strip().replace("\xa0", "").replace(" ", "")
            example = desc_tag.text.strip()
            if re.fullmatch(r"\d{10}", code):  # проверка: ровно 10 цифр
                result[code].add(example)
    return result


async def parse_ifcg(keywords):
    url = f"https://www.ifcg.ru/kb/tnved/search/?q={keywords}&g="
    all_examples = defaultdict(set)

    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)
        soup = BeautifulSoup(html, "html.parser")

        # обрезаем всё выше result--stat
        result_stat = soup.find(id="result--stat")
        if not result_stat:
            return []

        # current = result_stat
        # while current.previous_sibling:
        #     prev = current.previous_sibling
        #     if prev:
        #         prev.extract()
        #     else:
        #         break

        # основная страница
        result_part = extract_rows_from_soup(soup)
        if not result_part:
            return []

        for code, descs in result_part.items():
            all_examples[code].update(descs)

        # доп. страницы
        link_tags = soup.select(".row.row-in.mt20.tac > .btn")
        for link_tag in link_tags[:2]:
            if link_tag.has_attr("href"):
                link = "https://www.ifcg.ru" + link_tag["href"]
                html = await fetch_html(session, link)
                sub_soup = BeautifulSoup(html, "html.parser")
                result_part = extract_rows_from_soup(sub_soup)
                for code, descs in result_part.items():
                    all_examples[code].update(descs)

        # Получаем официальные описания
        codes = list(all_examples.keys())
        tasks = [fetch_official_description(session, code) for code in codes]
        official_descriptions = await asyncio.gather(*tasks)

        # Собираем итоговый массив
        structured_data = []
        for code, official in zip(codes, official_descriptions):
            structured_data.append(
                {
                    "code": code,
                    "official": official,
                    "examples": list(all_examples[code]),
                }
            )
        # pprint(structured_data)
        return structured_data


async def fetch_html(session, url, retries=3, delay=5):
    """
    Загружает HTML с повторными попытками при сбоях подключения.
    :param session: aiohttp-сессия
    :param url: URL-адрес
    :param retries: Кол-во попыток
    :param delay: Задержка между попытками в секундах
    """
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url) as resp:
                return await resp.text()
        except aiohttp.ClientConnectorError as e:
            logger.warning(
                f"[{attempt}] Ошибка соединения: {e}. Повтор через {delay}с..."
            )
        except aiohttp.ClientError as e:
            logger.warning(f"[{attempt}] Ошибка клиента: {e}. Повтор через {delay}с...")
        except Exception as e:
            logger.warning(
                f"[{attempt}] Непредвиденная ошибка: {e}. Повтор через {delay}с..."
            )

        await asyncio.sleep(delay)

    raise RuntimeError(f"Не удалось подключиться к {url} после {retries} попыток.")


async def parse_tnved_tree(hs_code):
    url = f"https://www.alta.ru/tnved/code/{hs_code}/"
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)

    soup = BeautifulSoup(html, "html.parser")

    first_tree_block = soup.select_one("ul.pTnved_position.reset")
    if not first_tree_block:
        return "⚠️ Не удалось получить данные по дереву ТН ВЭД."

    items = first_tree_block.select("li.pTnved_item")
    if not items:
        return "⚠️ Не найдено элементов в дереве ТН ВЭД."

    tree_text = "🗂️ *Дерево ТН ВЭД:*\n\n"
    indent = ""

    for item in items:
        code = item.select_one("div > b").text.strip()
        description = item.select("div")[1].text.strip()

        # Определяем уровень по длине стиля ширины (60px = уровень 1, 90px = уровень 2 и т.д.)
        width_style = item.select_one("div").get("style")
        width_px = int(re.search(r"width:(\d+)px", width_style).group(1))
        level = (
            width_px - 60
        ) // 30  # Уровень отступа (60px - базовый, каждый новый уровень +30px)

        indent = "    " * level
        tree_text += f"{indent}▫️ *{code}* — {description}\n"
    return tree_text


async def fetch_tks_explanation(hs_code):
    url = f"https://www.tks.ru/db/tnved/prim_2017/c{hs_code}/"

    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)

    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#prim_issue_content")

    if not content:
        return ["Пояснение к данному коду не найдено."]

    result_parts = []
    temp_table_lines = []

    for child in content.children:
        if isinstance(child, str):
            continue

        # если это "табличная строка", у которой есть <td>
        if child.name == "table" or child.find("td"):
            rows = child.select("tr")
            for i, row in enumerate(rows):
                cells = [
                    cell.get_text(strip=True, separator=" ")
                    for cell in row.select("td")
                ]

                # Пропускаем пустые строки
                if not any(cells):
                    continue

                # Первый ряд — заголовок таблицы
                if i == 0 and len(cells) >= 2:
                    code = cells[0]
                    desc = cells[1]
                    temp_table_lines.append(f"📘 {code}\n{desc}")

                # Обычные строки: подпозиции
                elif len(cells) == 2:
                    temp_table_lines.append(f"• {cells[0]} {cells[1]}")
                elif len(cells) == 3:
                    temp_table_lines.append(f"• {cells[1]} {cells[2]}")
                elif len(cells) == 1:
                    temp_table_lines.append(f"{cells[0]}")

        # Параграф
        elif child.name == "p":
            text = child.get_text(strip=True, separator=" ")
            if text:
                # если были накоплены строки таблицы — добавим их блоком
                if temp_table_lines:
                    result_parts.append("\n".join(temp_table_lines))
                    temp_table_lines = []
                result_parts.append(text)

    # на случай, если таблица была в конце
    if temp_table_lines:
        result_parts.append("\n".join(temp_table_lines))

    # объединяем всё и разбиваем по лимиту
    full_text = "\n\n".join(result_parts)
    pages = textwrap.wrap(
        full_text, width=4000, break_long_words=False, replace_whitespace=False
    )

    return pages or ["Пояснение отсутствует"]


async def fetch_examples(hs_code):
    url = f"https://www.ifcg.ru/kb/tnved/{hs_code}/"
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, url)

    soup = BeautifulSoup(html, "html.parser")

    examples = []
    for sample in soup.select(".row.row-in.tnv-samples"):
        example_text = sample.select_one(".col-md-8")
        if example_text:
            text = example_text.get_text(strip=True).replace("\xa0", " ")
            examples.append(f"• {text}")

    if not examples:
        return ["Примеры декларирования отсутствуют"]

    # Собираем столько примеров, сколько помещается в одно сообщение Telegram
    current_text = "📝 <b>Примеры декларирования:</b>\n\n"
    for example in examples:
        if len(current_text) + len(example) + 2 > 4000:
            break
        current_text += example + "\n\n"

    return current_text.strip()


async def parse_tks_info(hs_code: str):
    content = await fetch_tks_info(hs_code)

    soup = BeautifulSoup(content, "html.parser")

    # Парсим секции Импорт и Экспорт
    info_sections = soup.select(".product-info__section")
    result = ""

    for section in info_sections:
        section_title = section.select_one(".product-info__section-title").text.strip()
        result += f"\n*{section_title}*\n\n"

        for row in section.select("table.product-info__table tr"):
            cols = row.find_all("td")
            if len(cols) >= 2:
                label = cols[0].text.strip()
                value = cols[1].text.strip()
                result += f"*{label}* {value}\n"

    return result


async def fetch_tks_info(
    hs_code: str, max_retries: int = 3, base_delay: float = 2.0
) -> str:
    """
    Достаём HTML блока с подробностями по коду через AJAX POST,
    с рефором и CSRF-токеном и с retry при сбоях.
    """
    base_url = "https://www.tks.ru/db/tnved/tree/"
    info_url = base_url + "info/"

    timeout = aiohttp.ClientTimeout(total=30)
    for attempt in range(1, max_retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 1) GET, чтобы получить csrftoken в куках
                async with session.get(base_url) as resp_get:
                    resp_get.raise_for_status()
                    # вытаскиваем токен из куки
                    csrf_token = session.cookie_jar.filter_cookies(base_url)[
                        "csrftoken"
                    ].value

                # 2) Заголовки и form-data
                headers = {
                    "Referer": base_url,
                    "X-CSRFToken": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                }
                data = {
                    "code": hs_code,
                    "csrfmiddlewaretoken": csrf_token,
                }

                # 3) POST запроса
                async with session.post(
                    info_url, data=data, headers=headers
                ) as resp_post:
                    resp_post.raise_for_status()  # бросит ClientResponseError, если код !=200
                    result = await resp_post.text()
                    return result

        except (aiohttp.ClientConnectorError, aiohttp.ClientResponseError) as e:
            # сетевые ошибки или HTTP != 2xx
            if attempt == max_retries:
                raise RuntimeError(f"Не удалось получить данные: {e}") from e
            # логировать можно так:
            print(f"[TKS] Попытка {attempt}/{max_retries} не удалась: {e}")
        except Exception as e:
            # любые другие ошибки
            if attempt == max_retries:
                raise RuntimeError(f"Ошибка при запросе TKS: {e}") from e
            print(f"[TKS] Непредвиденная ошибка, retry {attempt}: {e}")

        # ожидание перед следующей попыткой: экспоненциальный бэкофф + jitter
        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(delay)


async def parse_tks_info2(browser, hs_code: str) -> str:
    # 1. Проверка DNS — чтобы не ждать таймаут Playwright, если домен не резолвится
    try:
        socket.gethostbyname("www.tks.ru")
    except socket.gaierror:
        return "⚠️ Сервер не отвечает, попробуйте позже."

    max_retries = 5
    backoff = 2.0  # секунды

    for attempt in range(1, max_retries + 1):
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_navigation_timeout(15_000)
        page.set_default_timeout(10_000)
        try:
            await page.goto(
                "https://www.tks.ru/db/tnved/tree/", wait_until="domcontentloaded"
            )
            await page.fill("#tnved-search__input", hs_code)
            await page.click("#tnved-search__submit")
            await page.wait_for_selector(".tree-list__code", timeout=10_000)
            await page.click(f".tree-list__code:text('{hs_code}')")
            await page.wait_for_selector("#code_info", timeout=10_000)
            content = await page.content()
            # 6. Парсим BeautifulSoup’ом
            soup = BeautifulSoup(content, "html.parser")
            info_sections = soup.select("#code_info .product-info__section")
            if not info_sections:
                return "ℹ️ По заданному коду не найдено информации."

            result = ""
            for section in info_sections:
                title = section.select_one(".product-info__section-title").text.strip()
                result += f"\n*{title}*\n"
                for row in section.select("table.product-info__table tr"):
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        label = cols[0].text.strip()
                        value = cols[1].text.strip()
                        result += f"• *{label}*: {value}\n"
            return result

        except PlaywrightTimeoutError:
            if attempt == max_retries:
                return "⚠️ Таймаут при получении данных."
        except Exception as e:
            if attempt == max_retries:
                return f"⚠️ Ошибка доступа: {e}"
        finally:
            await page.close()
            await context.close()

        await asyncio.sleep(backoff + random.random() * 0.5)
        backoff *= 2

    # На всякий случай
    return "⚠️ Не удалось получить данные. Попробуйте позже."


# asyncio.run(parse_ifcg("Шины+Легковые+Резина"))
