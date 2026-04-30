# -*- coding: utf-8 -*-

import datetime
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import markdown
import requests
import streamlit as st

from openai import OpenAI

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


# =========================
# НАСТРОЙКИ
# =========================

LLM_PROXY_URL = "https://llm-proxy.t-tech.team/v1"
MODEL_NAME = "openai/gpt-4.1"

DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9222

CHROMEDRIVER_PATH = r"C:\Users\e.kaganova\1 парсинг\0 core parse\chromedriver-win64\chromedriver-win64\chromedriver.exe"

BASE_OUTPUT_FOLDER = "banks_compare_output"
MAX_TEXT_LENGTH = 120000

BANKS = [
    {
        "name": "Сбер",
        "url": "https://www.sberbank.ru/ru/person/credits/money/credit_zalog",
    },
    {
        "name": "ВТБ",
        "url": "https://www.vtb.ru/personal/ipoteka/ipoteka-pod-zalog-nedvizhimosti/",
    },
    {
        "name": "Совкомбанк",
        "url": "https://sovcombank.ru/credits/cash/alternativa",
    },
    {
        "name": "МТС Банк",
        "url": "https://www.mtsbank.ru/chastnim-licam/ipoteka/kredit-pod-zalog/",
    },
    {
        "name": "Газпромбанк",
        "url": "https://www.gazprombank.ru/personal/bail/pod-zalog/",
    },
    {
        "name": "Альфа-Банк",
        "url": "https://alfabank.ru/get-money/credit/pod-zalog/",
    },
]

PRODUCT_BANK_URLS = {
    "КНЗ: кредит под залог недвижимости": [
        {"name": "Сбер", "url": "https://www.sberbank.ru/ru/person/credits/money/credit_zalog"},
        {"name": "ВТБ", "url": "https://www.vtb.ru/personal/ipoteka/ipoteka-pod-zalog-nedvizhimosti/"},
        {"name": "Совкомбанк", "url": "https://sovcombank.ru/credits/cash/alternativa"},
        {"name": "МТС Банк", "url": "https://www.mtsbank.ru/chastnim-licam/ipoteka/kredit-pod-zalog/"},
        {"name": "Газпромбанк", "url": "https://www.gazprombank.ru/personal/bail/pod-zalog/"},
        {"name": "Альфа-Банк", "url": "https://alfabank.ru/get-money/credit/pod-zalog/"},
    ],
    "КНА: кредит под залог автомобиля": [
        {"name": "Т-Банк", "url": "https://www.tbank.ru/loans/cash-loan/auto/"},
        {"name": "Совкомбанк", "url": "https://sovcombank.ru/credits/cash/pod-zalog-avto-"},
        {"name": "ВТБ", "url": "https://www.vtb.ru/personal/kredit/pod-zalog-avto/"},
    ],
}

# =========================
# STREAMLIT UI
# =========================

st.set_page_config(
    page_title="Парсер баттл-карт",
    layout="wide"
)

st.title("Парсер баттл-карт")

mode = st.radio(
    "Режим работы",
    options=[
        "Продакт: сравнение банков",
        "Кабинетник: расширенный режим"
    ],
    horizontal=True
)


# =========================
# ЛОГИ
# =========================

log_box = st.empty()


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{ts}] {msg}"
    print(full_msg)

    if "logs" not in st.session_state:
        st.session_state.logs = []

    st.session_state.logs.append(full_msg)
    log_box.text("\n".join(st.session_state.logs[-50:]))


# =========================
# СЛУЖЕБНЫЕ ФУНКЦИИ
# =========================

def ensure_folder(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)


def make_slug(text: str) -> str:
    text = text.lower().strip()

    replacements = {
        "сбер": "sber",
        "втб": "vtb",
        "совкомбанк": "sovcombank",
        "мтс банк": "mtsbank",
        "газпромбанк": "gazprombank",
        "альфа-банк": "alfabank",
        "альфа банк": "alfabank",
    }

    if text in replacements:
        return replacements[text]

    text = re.sub(r"[^a-zа-я0-9]+", "_", text, flags=re.IGNORECASE)
    text = text.strip("_")
    return text or "source"


def save_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append_text_file(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def normalize_block(block: str) -> str:
    lines = [normalize_line(line) for line in block.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def split_into_blocks(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n\s*\n+", text) if block.strip()]


def deduplicate_blocks(text: str) -> str:
    seen = set()
    unique_blocks = []

    for block in split_into_blocks(text):
        normalized = normalize_block(block)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_blocks.append(block)

    return "\n\n".join(unique_blocks)


def deduplicate_lines(text: str) -> str:
    seen = set()
    result = []

    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue

        if line not in seen:
            seen.add(line)
            result.append(line)

    return "\n".join(result)


def clean_and_deduplicate_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = deduplicate_blocks(text)
    text = deduplicate_lines(text)

    rebuilt = []

    for line in text.splitlines():
        if line.startswith("===") and rebuilt:
            rebuilt.append("")
        rebuilt.append(line)

    result = "\n".join(rebuilt)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    return result


def truncate_text(text: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    if len(text) <= max_length:
        return text

    log(f"⚠️ Текст обрезан до {max_length} символов")
    return text[:max_length]


def safe_filename_from_url(url: str, fallback: str = "file") -> str:
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)

    if not filename:
        filename = fallback

    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

    return filename


def get_body_text(driver) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception as e:
        return f"[ОШИБКА] Не удалось получить body.text: {e}"


def append_section(path: str, title: str, content: str) -> None:
    append_text_file(
        path,
        f"\n\n=== {title} ===\n{str(content).strip()}\n"
    )


def parse_urls_from_text(urls_text: str) -> list[str]:
    result = []

    for line in urls_text.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("http://") or line.startswith("https://"):
            result.append(line)

    return result


def source_name_from_url(index: int, url: str) -> str:
    try:
        domain = urlparse(url).netloc
        domain = domain.replace("www.", "")
        return f"Источник {index + 1}: {domain}"
    except Exception:
        return f"Источник {index + 1}"


# =========================
# SELENIUM
# =========================

def build_driver():
    options = Options()
    options.debugger_address = f"{DEBUG_HOST}:{DEBUG_PORT}"

    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)

    return driver


def scroll_and_js_click(driver, elem) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            elem
        )
        time.sleep(0.3)

        try:
            elem.click()
        except Exception:
            driver.execute_script("arguments[0].click();", elem)

        time.sleep(0.8)
        return True

    except Exception:
        return False


def wait_for_content_update(driver, previous_text: str, timeout: int = 4) -> None:
    started_at = time.time()

    while time.time() - started_at < timeout:
        try:
            current_text = get_body_text(driver)
            if current_text != previous_text:
                time.sleep(0.5)
                return
        except Exception:
            pass

        time.sleep(0.3)


def close_popups(driver, raw_text_path: str) -> None:
    popup_xpaths = [
        "//button[contains(., 'Принять')]",
        "//button[contains(., 'Согласен')]",
        "//button[contains(., 'Согласиться')]",
        "//button[contains(., 'Понятно')]",
        "//button[contains(., 'Хорошо')]",
        "//button[contains(., 'Закрыть')]",
        "//button[contains(., 'ОК')]",
        "//button[contains(., 'Ok')]",
        "//button[contains(., 'Accept')]",
        "//button[contains(., 'Allow')]",
    ]

    for xp in popup_xpaths:
        try:
            buttons = driver.find_elements(By.XPATH, xp)

            for btn in buttons[:3]:
                try:
                    if btn.is_displayed():
                        clicked = scroll_and_js_click(driver, btn)
                        if clicked:
                            append_section(
                                raw_text_path,
                                "ПОПАП / COOKIE",
                                f"Клик по элементу: {xp}"
                            )
                except Exception:
                    pass

        except Exception:
            pass


def scroll_page(driver, raw_text_path: str, source_name: str) -> None:
    log(f"{source_name}: прокрутка страницы")

    for _ in range(10):
        driver.execute_script(
            "window.scrollBy(0, Math.floor(window.innerHeight * 0.8));"
        )
        time.sleep(0.7)

    append_section(
        raw_text_path,
        "ПОСЛЕ ПРОКРУТКИ",
        get_body_text(driver)
    )


def click_relevant_elements(driver, raw_text_path: str, source_name: str) -> None:
    log(f"{source_name}: раскрываем вкладки, FAQ и аккордеоны")

    relevant_words = [
        "условия",
        "тариф",
        "ставк",
        "требован",
        "документ",
        "вопрос",
        "ответ",
        "как получить",
        "подробнее",
        "о кредите",
        "заемщик",
        "заёмщик",
        "залог",
        "страхован",
        "погашен",
        "оформ",
        "комисс",
        "срок",
        "сумм",
        "недвиж",
        "получить",
        "подать",
        "анкета",
        "справк",
        "паспорт",
        "цена",
        "стоимость",
        "функции",
        "возможности",
        "преимущества",
        "подключить",
        "как работает",
        "faq",
    ]

    xpath_clickables = (
        "//button | "
        "//a | "
        "//*[@role='button'] | "
        "//*[@role='tab'] | "
        "//*[contains(@class, 'accordion')] | "
        "//*[contains(@class, 'Accordion')] | "
        "//*[contains(@class, 'faq')] | "
        "//*[contains(@class, 'Faq')]"
    )

    clicked_signatures = set()
    clicked_count = 0
    max_clicks = 60

    try:
        elements = driver.find_elements(By.XPATH, xpath_clickables)
    except Exception as e:
        append_section(raw_text_path, "ОШИБКА ПОИСКА КЛИКАБЕЛЬНЫХ ЭЛЕМЕНТОВ", str(e))
        return

    for index in range(len(elements)):
        if clicked_count >= max_clicks:
            break

        try:
            elements = driver.find_elements(By.XPATH, xpath_clickables)

            if index >= len(elements):
                break

            elem = elements[index]

            if not elem.is_displayed():
                continue

            text = normalize_line(elem.text)
            text_lower = text.lower()

            if not text_lower:
                continue

            if not any(word in text_lower for word in relevant_words):
                continue

            signature = text_lower[:160]

            if signature in clicked_signatures:
                continue

            clicked_signatures.add(signature)

            previous_text = get_body_text(driver)
            clicked = scroll_and_js_click(driver, elem)

            if not clicked:
                continue

            wait_for_content_update(driver, previous_text, timeout=4)

            clicked_count += 1

            append_section(
                raw_text_path,
                f"КЛИК ПО ЭЛЕМЕНТУ #{clicked_count}: {text[:120]}",
                get_body_text(driver)
            )

        except Exception as e:
            append_section(raw_text_path, f"ОШИБКА КЛИКА #{index + 1}", str(e))


def click_generic_accordions(driver, raw_text_path: str, source_name: str) -> None:
    log(f"{source_name}: пробуем раскрыть скрытые аккордеоны")

    selectors = [
        "[aria-expanded='false']",
        "[data-testid*='accordion']",
        "[data-test-id*='accordion']",
        "[data-testid*='Accordion']",
        "[data-test-id*='Accordion']",
        "[class*='accordion']",
        "[class*='Accordion']",
        "[class*='faq']",
        "[class*='Faq']",
        "[class*='collapse']",
        "[class*='Collapse']",
    ]

    clicked_count = 0
    max_clicks = 50

    for selector in selectors:
        if clicked_count >= max_clicks:
            break

        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            for index in range(len(elements)):
                if clicked_count >= max_clicks:
                    break

                elements = driver.find_elements(By.CSS_SELECTOR, selector)

                if index >= len(elements):
                    break

                elem = elements[index]

                try:
                    if not elem.is_displayed():
                        continue

                    previous_text = get_body_text(driver)
                    clicked = scroll_and_js_click(driver, elem)

                    if not clicked:
                        continue

                    wait_for_content_update(driver, previous_text, timeout=3)
                    clicked_count += 1

                except Exception:
                    pass

            append_section(
                raw_text_path,
                f"АККОРДЕОНЫ ПО СЕЛЕКТОРУ: {selector}",
                get_body_text(driver)
            )

        except Exception as e:
            append_section(
                raw_text_path,
                f"ОШИБКА АККОРДЕОНОВ: {selector}",
                str(e)
            )


def download_files_from_page(
    driver,
    page_url: str,
    files_folder: str,
    raw_text_path: str
) -> None:
    log("Поиск и скачивание документов")

    try:
        links = driver.find_elements(By.TAG_NAME, "a")
    except Exception as e:
        append_section(raw_text_path, "ОШИБКА ПОИСКА ССЫЛОК", str(e))
        return

    downloaded_urls = set()
    downloaded_count = 0

    for link in links:
        try:
            href = link.get_attribute("href")

            if not href:
                continue

            absolute_url = urljoin(page_url, href)
            lower_url = absolute_url.lower()

            is_document = (
                ".pdf" in lower_url
                or ".doc" in lower_url
                or ".docx" in lower_url
                or ".xls" in lower_url
                or ".xlsx" in lower_url
            )

            if not is_document:
                continue

            if absolute_url in downloaded_urls:
                continue

            downloaded_urls.add(absolute_url)

            filename = safe_filename_from_url(
                absolute_url,
                fallback=f"file_{downloaded_count + 1}"
            )

            local_path = os.path.join(files_folder, filename)

            response = requests.get(absolute_url, timeout=60)
            response.raise_for_status()

            with open(local_path, "wb") as f:
                f.write(response.content)

            downloaded_count += 1

            append_section(
                raw_text_path,
                "СКАЧАННЫЙ ФАЙЛ",
                f"{filename}\n{absolute_url}"
            )

        except Exception as e:
            append_section(raw_text_path, "ОШИБКА СКАЧИВАНИЯ ФАЙЛА", str(e))

    if downloaded_count == 0:
        append_section(raw_text_path, "СКАЧАННЫЕ ФАЙЛЫ", "Файлы не найдены.")


# =========================
# УНИВЕРСАЛЬНЫЙ ПАРСЕР
# =========================

def parse_universal_source(source: dict, output_subfolder: str = "sources") -> dict:
    source_name = source["name"]
    source_url = source["url"]
    source_slug = make_slug(source_name)

    folder = os.path.join(BASE_OUTPUT_FOLDER, output_subfolder, source_slug)
    ensure_folder(folder)

    files_folder = os.path.join(folder, "files")
    ensure_folder(files_folder)

    raw_text_path = os.path.join(folder, "parsed_text_raw.txt")
    clean_text_path = os.path.join(folder, "parsed_text_clean.txt")

    save_text_file(raw_text_path, "")

    driver = build_driver()

    try:
        log(f"Открываем страницу: {source_name}")
        driver.get(source_url)
        time.sleep(7)

        append_section(
            raw_text_path,
            "ГЛАВНАЯ СТРАНИЦА",
            get_body_text(driver)
        )

        close_popups(driver, raw_text_path)
        scroll_page(driver, raw_text_path, source_name)
        click_relevant_elements(driver, raw_text_path, source_name)
        click_generic_accordions(driver, raw_text_path, source_name)
        scroll_page(driver, raw_text_path, source_name)

        download_files_from_page(
            driver=driver,
            page_url=source_url,
            files_folder=files_folder,
            raw_text_path=raw_text_path
        )

    finally:
        driver.quit()

    raw_text = Path(raw_text_path).read_text(encoding="utf-8")

    clean_text = clean_and_deduplicate_text(raw_text)
    clean_text = truncate_text(clean_text, MAX_TEXT_LENGTH)

    save_text_file(clean_text_path, clean_text)

    log(f"✅ {source_name}: парсинг завершён")

    return {
        "name": source_name,
        "bank_name": source_name,
        "url": source_url,
        "folder": folder,
        "raw_text_path": raw_text_path,
        "clean_text_path": clean_text_path,
        "text": clean_text,
    }


def parse_universal_bank(bank: dict) -> dict:
    return parse_universal_source(bank, output_subfolder="banks")


# =========================
# LLM
# =========================

def check_llm(token_value: str) -> OpenAI:
    log("Проверка подключения к LLM API")

    http_client = httpx.Client(
        base_url=LLM_PROXY_URL,
        headers={
            "Authorization": f"Bearer {token_value}",
            "Content-Type": "application/json"
        },
        verify=False,
        timeout=120.0,
    )

    client = OpenAI(
        base_url=LLM_PROXY_URL,
        api_key=token_value,
        http_client=http_client
    )

    response = client.responses.create(
        model=MODEL_NAME,
        input="Привет. Ответь одним словом: работает."
    )

    text = response.output[0].content[0].text
    log(f"✅ Ответ от LLM: {text[:100]}")

    return client


def suggest_comparison_params(
    client: OpenAI,
    product_name: str,
    urls_text: str
) -> str:
    log("LLM предлагает параметры сравнения")

    prompt = (
        f"Продукт / категория для кабинетного исследования: {product_name}\n\n"
        f"Ссылки, которые пользователь планирует анализировать:\n{urls_text}\n\n"
        "Предложи параметры для сравнительной таблицы по этому продукту.\n"
        "Параметры должны помогать понять, как устроены предложения / продукты / сервисы у разных игроков.\n"
        "Не анализируй сами ссылки, потому что их содержимое ещё не спарсено.\n"
        "Не делай выводов.\n"
        "Верни только список параметров, каждый с новой строки.\n"
        "Формат строго такой:\n"
        "- Название параметра\n"
        "- Название параметра\n"
        "- Название параметра\n"
    )

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt
    )

    result = response.output[0].content[0].text
    log("✅ Параметры сравнения получены")

    return result


def get_product_prompt_structure(battle_card_type: str) -> str:
    if battle_card_type == "КНА: кредит под залог автомобиля":
        return """
## Основные параметры кредита
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название продукта | |
| Тип кредита | |
| Процентная ставка | |
| Полная стоимость кредита / ПСК | |
| Максимальная сумма кредита | |
| Минимальная сумма кредита | |
| Максимальный срок кредитования | |
| Минимальный срок кредитования | |
| Валюта кредита | |
| График платежей | |
| Целевое / нецелевое использование средств | |

## Залоговое обеспечение
| Параметр | Содержание |
|---|---|
| Требуется ли залог автомобиля | |
| Какие транспортные средства принимаются в залог | |
| Легковые автомобили | |
| Коммерческий транспорт | |
| Мототехника | |
| Иностранные / отечественные автомобили | |
| Максимальный возраст автомобиля | |
| Требования к техническому состоянию | |
| Требования к регистрации автомобиля | |
| Требования к собственнику автомобиля | |
| Возможность залога автомобиля третьего лица | |
| Максимальный процент от оценочной стоимости / LTV | |
| Необходимость оценки автомобиля | |
| Способ оценки автомобиля | |
| Ограничения на использование автомобиля во время кредита | |

## ПТС / ЭПТС и обременение
| Параметр | Содержание |
|---|---|
| Требуется ли передача ПТС | |
| Работа с электронным ПТС / ЭПТС | |
| Накладывается ли обременение / запрет регистрационных действий | |
| Возможность пользоваться автомобилем во время кредита | |
| Возможность продажи автомобиля до погашения кредита | |
| Условия снятия обременения после погашения | |

## Требования к заемщику
| Параметр | Содержание |
|---|---|
| Возрастные ограничения | |
| Гражданство / резидентство | |
| Регистрация | |
| Требования к доходу | |
| Условия трудоустройства | |
| Минимальный стаж работы | |
| Требования к кредитной истории | |
| Возможность привлечения созаемщиков | |
| Требования к собственнику залога, если он не заемщик | |

## Оформление и получение денег
| Параметр | Содержание |
|---|---|
| Способы подачи заявки | |
| Возможность онлайн-заявки | |
| Возможность заполнения через Госуслуги | |
| Срок рассмотрения заявки | |
| Необходимые документы заемщика | |
| Документы на автомобиль | |
| Требуется ли подтверждение дохода | |
| Требуется ли осмотр автомобиля | |
| Требуется ли фотографирование автомобиля | |
| Необходимость визита в офис | |
| Возможность встречи с представителем | |
| Способы получения средств | |
| Скорость получения денег после одобрения | |

## Страхование и дополнительные услуги
| Параметр | Содержание |
|---|---|
| Требуется ли каско | |
| Влияние каско на ставку | |
| ОСАГО | |
| Страхование жизни и здоровья | |
| Финансовая защита | |
| Дополнительные услуги и пакеты | |
| Возможность отказаться от дополнительных услуг | |

## Комиссии, расходы и санкции
| Параметр | Содержание |
|---|---|
| Комиссия за выдачу кредита | |
| Комиссия за оценку автомобиля | |
| Комиссия за перевод / снятие денег | |
| Обслуживание счета | |
| Досрочное погашение | |
| Частичное досрочное погашение | |
| Штрафы / пени за просрочку | |
| Иные комиссии и расходы | |

## Гибкость и специальные условия
| Параметр | Содержание |
|---|---|
| Условия для зарплатных клиентов | |
| Условия для действующих клиентов | |
| Программы лояльности | |
| Персональные предложения | |
| Возможность рефинансирования | |
| Акции и временные предложения | |
"""

    if battle_card_type == "КНЗ: кредит под залог недвижимости":
        return """
## Основные параметры кредита
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название продукта | |
| Процентная ставка | |
| Максимальная сумма кредита | |
| Минимальная сумма кредита | |
| Максимальный срок кредитования | |
| Минимальный срок кредитования | |
| График платежей | |
| Целевое использование средств | |
| Особые условия для рефинансирования | |
| Условия снятия/перевода денег без комиссии | |

## Залоговое обеспечение
| Параметр | Содержание |
|---|---|
| Виды принимаемой недвижимости | |
| Отношение к объектам под реновацией | |
| Другие условия для залоговой недвижимости | |
| Максимальный процент от оценочной стоимости (LTV) | |
| Возрастные ограничения для имущества | |
| Требования к расположению недвижимости | |
| Ограничения на использование залога | |

## Требования к заемщику
| Параметр | Содержание |
|---|---|
| Возрастные ограничения | |
| Требования к доходу | |
| Условия трудоустройства | |
| Требования к кредитной истории | |
| Возможность привлечения созаемщиков | |
| Требования к гражданству/резидентству | |

## Оформление и скорость получения
| Параметр | Содержание |
|---|---|
| Возможность заполнения через Госуслуги | |
| Способы подачи заявки | |
| Срок рассмотрения | |
| Необходимые документы при подаче | |
| Требуемые документы по недвижимости | |
| Требуемые документы о заемщике | |
| Документы для оформления | |
| Необходимость оценки недвижимости | |
| Наличие осмотра квартиры / фотографирования | |
| Необходимость согласия супруга | |
| Необходимость нотариального заверения договора | |
| Необходимость ездить в офис / встречаться с представителем | |
| Способы получения средств | |
| Скорость получения после одобрения | |

## Комиссии и дополнительные расходы
| Параметр | Содержание |
|---|---|
| Комиссия за выдачу кредита | |
| Условия досрочного погашения | |
| Штрафы за просрочку | |
| Обслуживание счета | |
| Дополнительные услуги и их стоимость | |

## Страхование
| Параметр | Содержание |
|---|---|
| Страхование недвижимости | |
| Ограничения по страховым компаниям | |
| Страхование жизни и здоровья заемщика | |

## Гибкость условий
| Параметр | Содержание |
|---|---|
| Возможность изменения графика платежей | |
| Условия для зарплатных клиентов | |
| Программы лояльности | |
| Партнерские программы | |
| Возможность рефинансирования | |
| Налоговый вычет | |
"""

    if battle_card_type == "Автокредит":
        return """
## Основные параметры автокредита
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название продукта | |
| Тип автокредита | |
| Процентная ставка | |
| Полная стоимость кредита / ПСК | |
| Максимальная сумма кредита | |
| Минимальная сумма кредита | |
| Максимальный срок кредитования | |
| Минимальный срок кредитования | |
| Первоначальный взнос | |
| Валюта кредита | |
| График платежей | |
| Целевое использование средств | |

## Требования к автомобилю
| Параметр | Содержание |
|---|---|
| Новые автомобили | |
| Автомобили с пробегом | |
| Марки / категории автомобилей | |
| Максимальный возраст автомобиля | |
| Требования к продавцу | |
| Покупка у дилера | |
| Покупка у физического лица | |
| Возможность покупки коммерческого транспорта | |
| Возможность покупки мототехники | |
| Требования к регистрации автомобиля | |

## Залог и ПТС
| Параметр | Содержание |
|---|---|
| Требуется ли залог автомобиля | |
| Передача ПТС / ЭПТС | |
| Ограничения на распоряжение автомобилем | |
| Требования к оценке автомобиля | |
| Максимальный LTV / доля от стоимости авто | |

## Требования к заемщику
| Параметр | Содержание |
|---|---|
| Возрастные ограничения | |
| Гражданство / резидентство | |
| Регистрация | |
| Требования к доходу | |
| Условия трудоустройства | |
| Стаж работы | |
| Требования к кредитной истории | |
| Возможность привлечения созаемщиков | |

## Оформление и получение
| Параметр | Содержание |
|---|---|
| Способы подачи заявки | |
| Возможность онлайн-заявки | |
| Возможность оформления у дилера | |
| Срок рассмотрения заявки | |
| Необходимые документы | |
| Требуется ли подтверждение дохода | |
| Требуется ли водительское удостоверение | |
| Способы получения средств | |
| Скорость получения кредита после одобрения | |

## Страхование и дополнительные продукты
| Параметр | Содержание |
|---|---|
| Требуется ли каско | |
| Влияние каско на ставку | |
| ОСАГО | |
| Страхование жизни и здоровья | |
| Финансовая защита | |
| Дополнительные услуги и пакеты | |

## Комиссии, погашение и санкции
| Параметр | Содержание |
|---|---|
| Комиссия за выдачу кредита | |
| Досрочное погашение | |
| Частичное досрочное погашение | |
| Штрафы / пени за просрочку | |
| Обслуживание счета | |
| Иные комиссии | |

## Гибкость и специальные условия
| Параметр | Содержание |
|---|---|
| Условия для зарплатных клиентов | |
| Скидки от партнеров / дилеров | |
| Госпрограммы / субсидии | |
| Программы trade-in | |
| Возможность рефинансирования автокредита | |
| Акции и временные предложения | |
"""

    if battle_card_type == "Кредит наличными":
        return """
## Основные параметры кредита
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название продукта | |
| Тип кредита | |
| Процентная ставка | |
| Полная стоимость кредита / ПСК | |
| Максимальная сумма кредита | |
| Минимальная сумма кредита | |
| Максимальный срок кредитования | |
| Минимальный срок кредитования | |
| Валюта кредита | |
| График платежей | |
| Целевое / нецелевое использование | |

## Требования к заемщику
| Параметр | Содержание |
|---|---|
| Возрастные ограничения | |
| Гражданство / резидентство | |
| Регистрация | |
| Требования к доходу | |
| Условия трудоустройства | |
| Минимальный стаж работы | |
| Требования к кредитной истории | |
| Возможность привлечения созаемщиков | |
| Требования к зарплатному клиенту | |

## Оформление и получение денег
| Параметр | Содержание |
|---|---|
| Способы подачи заявки | |
| Возможность онлайн-заявки | |
| Возможность заполнения через Госуслуги | |
| Срок рассмотрения заявки | |
| Необходимые документы | |
| Требуется ли подтверждение дохода | |
| Требуется ли справка 2-НДФЛ / выписка | |
| Необходимость визита в офис | |
| Возможность встречи с представителем | |
| Способы получения средств | |
| Скорость получения денег после одобрения | |

## Комиссии и расходы
| Параметр | Содержание |
|---|---|
| Комиссия за выдачу кредита | |
| Комиссия за перевод / снятие денег | |
| Обслуживание счета | |
| Платные дополнительные услуги | |
| Стоимость уведомлений / сервисных пакетов | |

## Страхование и дополнительные услуги
| Параметр | Содержание |
|---|---|
| Страхование жизни и здоровья | |
| Финансовая защита | |
| Влияние страховки на ставку | |
| Возможность отказаться от страховки | |
| Дополнительные подписки / пакеты услуг | |

## Погашение и санкции
| Параметр | Содержание |
|---|---|
| Досрочное погашение | |
| Частичное досрочное погашение | |
| Штрафы / пени за просрочку | |
| Изменение даты платежа | |
| Кредитные каникулы / отсрочка | |

## Гибкость и специальные условия
| Параметр | Содержание |
|---|---|
| Условия для зарплатных клиентов | |
| Условия для действующих клиентов | |
| Программы лояльности | |
| Персональные предложения | |
| Возможность рефинансирования | |
| Акции и временные предложения | |
"""

    if battle_card_type == "Ипотека":
        return """
## Основные параметры ипотеки
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название ипотечной программы | |
| Тип ипотеки | |
| Процентная ставка | |
| Полная стоимость кредита / ПСК | |
| Максимальная сумма кредита | |
| Минимальная сумма кредита | |
| Максимальный срок кредитования | |
| Минимальный срок кредитования | |
| Первоначальный взнос | |
| Валюта кредита | |
| График платежей | |

## Объект недвижимости
| Параметр | Содержание |
|---|---|
| Первичный рынок / новостройка | |
| Вторичный рынок | |
| Дом / таунхаус / ИЖС | |
| Апартаменты | |
| Коммерческая недвижимость | |
| Земельный участок | |
| Требования к объекту недвижимости | |
| Требования к застройщику / продавцу | |
| Требования к расположению недвижимости | |
| Ограничения по объекту | |

## Первоначальный взнос и LTV
| Параметр | Содержание |
|---|---|
| Минимальный первоначальный взнос | |
| Максимальный LTV | |
| Использование материнского капитала | |
| Использование субсидий | |
| Собственные средства заемщика | |

## Требования к заемщику
| Параметр | Содержание |
|---|---|
| Возрастные ограничения | |
| Гражданство / резидентство | |
| Регистрация | |
| Требования к доходу | |
| Условия трудоустройства | |
| Минимальный стаж работы | |
| Требования к кредитной истории | |
| Возможность привлечения созаемщиков | |
| Требования к супругу / супруге | |

## Оформление и документы
| Параметр | Содержание |
|---|---|
| Способы подачи заявки | |
| Возможность онлайн-заявки | |
| Возможность заполнения через Госуслуги | |
| Срок рассмотрения заявки | |
| Документы заемщика | |
| Документы по объекту недвижимости | |
| Подтверждение дохода | |
| Необходимость оценки недвижимости | |
| Электронная регистрация сделки | |
| Необходимость нотариального заверения | |
| Необходимость визита в офис / МФЦ / Росреестр | |
| Способ выдачи кредита | |
| Скорость получения после одобрения | |

## Страхование
| Параметр | Содержание |
|---|---|
| Страхование недвижимости | |
| Страхование жизни и здоровья | |
| Титульное страхование | |
| Влияние страхования на ставку | |
| Ограничения по страховым компаниям | |

## Льготные и специальные программы
| Параметр | Содержание |
|---|---|
| Семейная ипотека | |
| IT-ипотека | |
| Дальневосточная / Арктическая ипотека | |
| Военная ипотека | |
| Ипотека с господдержкой | |
| Партнерские программы с застройщиками | |
| Скидки для зарплатных клиентов | |
| Акции и временные предложения | |

## Комиссии, погашение и санкции
| Параметр | Содержание |
|---|---|
| Комиссия за выдачу кредита | |
| Досрочное погашение | |
| Частичное досрочное погашение | |
| Штрафы / пени за просрочку | |
| Обслуживание счета | |
| Дополнительные услуги и расходы | |

## Гибкость условий
| Параметр | Содержание |
|---|---|
| Возможность рефинансирования | |
| Изменение графика платежей | |
| Кредитные каникулы / отсрочка | |
| Налоговый вычет | |
| Возможность досрочного снятия обременения | |
"""

    return """
## Параметры сравнения
| Параметр | Содержание |
|---|---|
| Название банка | |
| URL источника | |
| Название продукта | |
| Процентная ставка | |
| Максимальная сумма | |
| Минимальная сумма | |
| Максимальный срок | |
| Минимальный срок | |
| Требования к заемщику | |
| Способы оформления | |
| Необходимые документы | |
| Комиссии | |
| Страхование | |
| Специальные условия | |
"""


def analyze_bank_to_table(
    client: OpenAI,
    bank_name: str,
    url: str,
    text: str,
    battle_card_type: str
) -> str:
    log(f"Отправка текста в LLM для банка: {bank_name}")

    structure = get_product_prompt_structure(battle_card_type)

    prompt = (
        f"Тип баттл-карты: {battle_card_type}\n"
        f"Цель: составить максимально полную таблицу условий продукта для банка {bank_name}.\n\n"
        f"Источник: {url}\n\n"
        "Работай только на основании предоставленного текста.\n"
        "Не делай предположений.\n"
        "Не используй знания из интернета или памяти модели.\n"
        "Если информации нет — пиши: Не указано.\n"
        "Если данные косвенные — помечай: упоминается косвенно.\n"
        "Если в тексте есть противоречие — укажи оба значения и пометь: есть противоречие в источнике.\n"
        "Не удаляй важные числовые условия: ставки, суммы, сроки, проценты, комиссии, возраст, LTV, первоначальный взнос, ПСК.\n"
        "Верни результат строго в Markdown.\n\n"
        "Заполни таблицу строго по этой структуре:\n\n"
        f"{structure}\n\n"
        f"Текст для анализа:\n\n{text}"
    )

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt
    )

    result = response.output[0].content[0].text

    log(f"✅ Таблица по банку {bank_name} получена")

    return result

def analyze_source_by_custom_params(
    client: OpenAI,
    product_name: str,
    source_name: str,
    url: str,
    text: str,
    comparison_params: str
) -> str:
    log(f"Отправка текста в LLM для источника: {source_name}")

    prompt = (
        f"Продукт / категория: {product_name}\n"
        f"Источник: {source_name}\n"
        f"URL: {url}\n\n"
        "Нужно заполнить сравнительную таблицу по заданным пользователем параметрам.\n"
        "Работай только на основании предоставленного текста.\n"
        "Не делай предположений.\n"
        "Не используй знания из интернета или памяти модели.\n"
        "Если информации нет — пиши: Не указано.\n"
        "Если данные косвенные — помечай: упоминается косвенно.\n"
        "Если есть противоречие — укажи оба значения и пометь: есть противоречие в источнике.\n"
        "Сохраняй важные числа, условия, ограничения, сроки, цены, тарифы, комиссии, если они есть в тексте.\n\n"

        "Параметры сравнения:\n"
        f"{comparison_params}\n\n"

        "Верни результат строго в Markdown.\n"
        "Формат строго такой:\n\n"
        "## Параметры сравнения\n"
        "| Параметр | Содержание |\n"
        "|---|---|\n"
        "| Название источника | |\n"
        "| URL источника | |\n"
        "| Название продукта / предложения | |\n"
        "| ... | ... |\n\n"

        "В таблицу обязательно включи все параметры из списка пользователя.\n\n"

        f"Текст для анализа:\n\n{text}"
    )

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt
    )

    result = response.output[0].content[0].text

    log(f"✅ Таблица по источнику {source_name} получена")

    return result


# =========================
# MARKDOWN TABLE PARSER
# =========================

def parse_markdown_tables(md_text: str) -> dict:
    lines = md_text.splitlines()

    sections = {}
    current_section = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("## "):
            current_section = line[3:].strip()
            if current_section not in sections:
                sections[current_section] = {}
            i += 1
            continue

        if current_section and line.startswith("|") and i + 1 < len(lines):
            next_line = lines[i + 1].strip()

            clean_separator = (
                next_line
                .replace("|", "")
                .replace("-", "")
                .replace(":", "")
                .replace(" ", "")
            )

            if next_line.startswith("|") and clean_separator == "":
                i += 2

                while i < len(lines):
                    row = lines[i].strip()

                    if not row.startswith("|"):
                        break

                    parts = [part.strip() for part in row.strip("|").split("|")]

                    if len(parts) >= 2:
                        param = parts[0]
                        value = parts[1]

                        if param and param.lower() != "параметр":
                            sections[current_section][param] = value

                    i += 1

                continue

        i += 1

    return sections


def escape_markdown_cell(value: str) -> str:
    value = str(value)
    value = value.replace("\n", "<br>")
    value = value.replace("|", "\\|")
    return value

def highlight_best_worst_conditions(
    client: OpenAI,
    comparison_table: str,
    product_type: str
) -> str:
    log("LLM размечает лучшие и худшие условия")

    prompt = (
        f"Тип продукта / баттл-карты: {product_type}\n\n"
        "Ниже сравнительная таблица условий.\n"
        "Нужно вернуть HTML-таблицы с цветовой подсветкой лучших и худших условий.\n\n"
        "Правила оценки:\n"
        "- Лучшие условия подсвечивай светло-зелёным цветом: #d9fdd3.\n"
        "- Худшие условия подсвечивай светло-красным цветом: #ffd6d6.\n"
        "- Если сравнить нельзя, данных нет или параметр не является оценочным — не подсвечивай.\n"
        "- Не выдумывай оценки, если данных недостаточно.\n"
        "- Если меньше — лучше, например ставка, комиссия, первоначальный взнос, срок рассмотрения, штрафы, подсвечивай минимальное значение как лучшее.\n"
        "- Если больше — лучше, например максимальная сумма, срок кредита, LTV, количество способов оформления, подсвечивай максимальное значение как лучшее.\n"
        "- Если условия качественные, оценивай только при явном преимуществе в тексте.\n"
        "- Верни только валидный HTML без markdown-блоков.\n\n"
        f"Сравнительная таблица:\n\n{comparison_table}"
    )

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt
    )

    result = response.output[0].content[0].text
    log("✅ Подсветка условий готова")

    return result

def merge_many_tables(source_tables: list[dict]) -> str:
    parsed = []

    for item in source_tables:
        parsed.append(
            {
                "source_name": item["source_name"],
                "data": parse_markdown_tables(item["md"])
            }
        )

    all_sections = []

    for item in parsed:
        for section in item["data"].keys():
            if section not in all_sections:
                all_sections.append(section)

    result_parts = []

    for section in all_sections:
        all_params = []

        for item in parsed:
            params = item["data"].get(section, {})

            for param in params.keys():
                if param not in all_params:
                    all_params.append(param)

        header = "| Параметр | " + " | ".join(item["source_name"] for item in parsed) + " |"
        separator = "|---|" + "|".join(["---"] * len(parsed)) + "|"

        result_parts.append(f"## {section}\n")
        result_parts.append(header)
        result_parts.append(separator)

        for param in all_params:
            row_values = []

            for item in parsed:
                value = item["data"].get(section, {}).get(param, "Не указано")
                row_values.append(escape_markdown_cell(value))

            result_parts.append(
                f"| {escape_markdown_cell(param)} | " + " | ".join(row_values) + " |"
            )

        result_parts.append("")

    return "\n".join(result_parts)


# =========================
# HTML
# =========================

def save_comparison_html(
    source_tables: list[dict],
    comparison_table: str,
    output_path: str,
    title: str,
    subtitle: str
) -> str:
    source_blocks = []

    for item in source_tables:
        source_blocks.append(
            f"""
            <h2>{item["source_name"]}</h2>
            {markdown.markdown(item["md"], extensions=["tables"])}
            """
        )

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 40px;
            line-height: 1.5;
        }}
        h1 {{
            margin-bottom: 8px;
        }}
        .subtitle {{
            margin-bottom: 32px;
            color: #555;
        }}
        h2 {{
            margin-top: 40px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 8px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin-bottom: 32px;
            font-size: 13px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            vertical-align: top;
        }}
        th {{
            background: #f3f3f3;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div class="subtitle">{subtitle}</div>

    <h2>Сравнительная таблица</h2>
    {markdown.markdown(comparison_table, extensions=["tables"])}

    {''.join(source_blocks)}
</body>
</html>
"""

    Path(output_path).write_text(html, encoding="utf-8")

    return output_path


# =========================
# РЕЖИМ ПРОДАКТА
# =========================

if mode == "Продакт: сравнение банков":
    st.write(
        "Режим для продуктовой баттл-карты: пользователь выбирает банки и тип сравнения, "
        "приложение парсит зашитые ссылки и собирает сравнительную таблицу."
    )

    token = st.text_input("Вставь LLM-токен", type="password", key="product_token")



    battle_card_type = st.selectbox(
        "Выбери тип баттл-карты",
        options=[
            "КНЗ: кредит под залог недвижимости",
            "Автокредит",
            "Кредит наличными",
            "Ипотека",
            "КНА: кредит под залог автомобиля",
        ],
        index=0
    )
    # ← ВОТ ЭТО ОБЯЗАТЕЛЬНО СРАЗУ ПОСЛЕ
    available_banks = PRODUCT_BANK_URLS.get(battle_card_type, [])

    selected_bank_names = st.multiselect(
            "Выбери банки для анализа",
            options=[bank["name"] for bank in available_banks],
            default=[bank["name"] for bank in available_banks],
            key=f"product_bank_multiselect_{battle_card_type}",
        )
    st.warning(
        "Перед запуском Chrome должен быть открыт в режиме remote debugging на порту 9222. "
        "Иначе Selenium не подключится."
    )

    run_product_button = st.button("Запустить анализ", key="run_product_button")

    if run_product_button:
        if not token:
            st.error("Сначала вставь LLM-токен.")
            st.stop()

        selected_banks = [
            bank for bank in available_banks
            if bank["name"] in selected_bank_names
        ]

        if not selected_banks:
            st.error("Выбери хотя бы один банк.")
            st.stop()

        ensure_folder(BASE_OUTPUT_FOLDER)
        st.session_state.logs = []

        try:
            with st.spinner("Проверяем LLM..."):
                client = check_llm(token)

            parsed_banks = []

            for bank in selected_banks:
                with st.spinner(f"Парсим {bank['name']}..."):
                    data = parse_universal_bank(bank)
                    parsed_banks.append(data)

            source_tables = []

            for bank_data in parsed_banks:
                with st.spinner(f"Анализируем {bank_data['bank_name']} через LLM..."):
                    table_md = analyze_bank_to_table(
                        client=client,
                        bank_name=bank_data["bank_name"],
                        url=bank_data["url"],
                        text=bank_data["text"],
                        battle_card_type=battle_card_type
                    )

                md_path = os.path.join(bank_data["folder"], "llm_table.md")
                Path(md_path).write_text(table_md, encoding="utf-8")

                source_tables.append(
                    {
                        "source_name": bank_data["bank_name"],
                        "folder": bank_data["folder"],
                        "md": table_md,
                    }
                )

            comparison_table = merge_many_tables(source_tables)

            comparison_md_path = os.path.join(BASE_OUTPUT_FOLDER, "product_comparison_table.md")
            highlighted_html = highlight_best_worst_conditions(
                client=client,
                comparison_table=comparison_table,
                product_type=battle_card_type
            )
            Path(comparison_md_path).write_text(comparison_table, encoding="utf-8")

            comparison_html_path = os.path.join(BASE_OUTPUT_FOLDER, "product_comparison_result.html")

            final_html_path = save_comparison_html(
                source_tables=source_tables,
                comparison_table=comparison_table,
                output_path=comparison_html_path,
                title="Сравнение банковских условий",
                subtitle=f"Тип баттл-карты: {battle_card_type}"
            )

            st.success("Готово.")

            tab_names = ["Сравнительная таблица"] + [
                item["source_name"] for item in source_tables
            ]

            tabs = st.tabs(tab_names)

            with tabs[0]:
                st.subheader("Сравнительная таблица")
                st.caption(f"Тип баттл-карты: {battle_card_type}")
                st.markdown(highlighted_html, unsafe_allow_html=True)

            for i, item in enumerate(source_tables, start=1):
                with tabs[i]:
                    st.subheader(item["source_name"])
                    st.markdown(item["md"])

            st.divider()

            st.subheader("Скачать результаты")

            st.download_button(
                label="Скачать сравнительную таблицу Markdown",
                data=comparison_table,
                file_name="product_comparison_table.md",
                mime="text/markdown"
            )

            html_data = Path(final_html_path).read_text(encoding="utf-8")

            st.download_button(
                label="Скачать итоговый HTML",
                data=html_data,
                file_name="product_comparison_result.html",
                mime="text/html"
            )

            for item in source_tables:
                safe_name = make_slug(item["source_name"])

                st.download_button(
                    label=f"Скачать таблицу {item['source_name']} Markdown",
                    data=item["md"],
                    file_name=f"{safe_name}_table.md",
                    mime="text/markdown"
                )

        except Exception as e:
            st.error("Произошла ошибка.")
            st.exception(e)


# =========================
# РЕЖИМ КАБИНЕТНИКА
# =========================

if mode == "Кабинетник: расширенный режим":
    st.write(
        "Режим для кабинетного исследования: пользователь задаёт продукт, "
        "добавляет ссылки, получает предложенные параметры сравнения, "
        "редактирует их и запускает сбор сравнительной таблицы."
    )

    token = st.text_input("Вставь LLM-токен", type="password", key="desk_token")

    product_name = st.text_input(
        "Название продукта / категории для исследования",
        placeholder="Например: TMS-системы, BNPL, онлайн-аптеки, сервисы телемедицины",
        key="desk_product_name"
    )

    urls_text = st.text_area(
        "Вставь ссылки для парсинга, каждая с новой строки",
        height=180,
        placeholder="https://...\nhttps://...\nhttps://...",
        key="desk_urls_text"
    )

    if "desk_params" not in st.session_state:
        st.session_state.desk_params = ""

    if "desk_comparison_params" not in st.session_state:
        st.session_state.desk_comparison_params = ""

    suggest_params_button = st.button(
        "Предложить параметры сравнения",
        key="suggest_params_button"
    )

    if suggest_params_button:
        if not token:
            st.error("Сначала вставь LLM-токен.")
            st.stop()

        if not product_name.strip():
            st.error("Сначала введи название продукта.")
            st.stop()

        if not urls_text.strip():
            st.error("Сначала вставь ссылки.")
            st.stop()

        parsed_urls = parse_urls_from_text(urls_text)

        if not parsed_urls:
            st.error("Не найдено валидных ссылок. Каждая ссылка должна начинаться с http:// или https://.")
            st.stop()

        try:
            with st.spinner("Проверяем LLM..."):
                client = check_llm(token)

            with st.spinner("LLM предлагает параметры сравнения..."):
                suggested_params = suggest_comparison_params(
                    client=client,
                    product_name=product_name,
                    urls_text=urls_text
                )

            st.session_state.desk_params = suggested_params
            st.session_state.desk_comparison_params = suggested_params

            st.success("Параметры предложены. Их можно отредактировать ниже.")

        except Exception as e:
            st.error("Произошла ошибка при генерации параметров.")
            st.exception(e)

    comparison_params = st.text_area(
        "Параметры сравнения. Можно отредактировать перед запуском анализа.",
        key="desk_comparison_params",
        height=280
    )

    st.warning(
        "Перед запуском Chrome должен быть открыт в режиме remote debugging на порту 9222. "
        "Иначе Selenium не подключится."
    )

    run_desk_button = st.button(
        "Запустить кабинетное исследование",
        key="run_desk_button"
    )

    if run_desk_button:
        if not token:
            st.error("Сначала вставь LLM-токен.")
            st.stop()

        if not product_name.strip():
            st.error("Сначала введи название продукта.")
            st.stop()

        if not urls_text.strip():
            st.error("Сначала вставь ссылки.")
            st.stop()

        if not comparison_params.strip():
            st.error("Сначала сформируй или введи параметры сравнения.")
            st.stop()

        urls = parse_urls_from_text(urls_text)

        if not urls:
            st.error("Не найдено валидных ссылок. Каждая ссылка должна начинаться с http:// или https://.")
            st.stop()

        ensure_folder(BASE_OUTPUT_FOLDER)
        st.session_state.logs = []

        try:
            with st.spinner("Проверяем LLM..."):
                client = check_llm(token)

            desk_sources = [
                {
                    "name": source_name_from_url(i, url),
                    "url": url
                }
                for i, url in enumerate(urls)
            ]

            parsed_sources = []

            for source in desk_sources:
                with st.spinner(f"Парсим {source['name']}..."):
                    parsed = parse_universal_source(
                        source=source,
                        output_subfolder="desk"
                    )
                    parsed_sources.append(parsed)

            source_tables = []

            for source_data in parsed_sources:
                with st.spinner(f"Анализируем {source_data['name']} через LLM..."):
                    table_md = analyze_source_by_custom_params(
                        client=client,
                        product_name=product_name,
                        source_name=source_data["name"],
                        url=source_data["url"],
                        text=source_data["text"],
                        comparison_params=comparison_params
                    )

                md_path = os.path.join(source_data["folder"], "llm_table.md")
                Path(md_path).write_text(table_md, encoding="utf-8")

                source_tables.append(
                    {
                        "source_name": source_data["name"],
                        "folder": source_data["folder"],
                        "md": table_md,
                    }
                )

            comparison_table = merge_many_tables(source_tables)

            desk_folder = os.path.join(BASE_OUTPUT_FOLDER, "desk")
            ensure_folder(desk_folder)

            comparison_md_path = os.path.join(desk_folder, "desk_comparison_table.md")
            Path(comparison_md_path).write_text(comparison_table, encoding="utf-8")

            comparison_html_path = os.path.join(desk_folder, "desk_comparison_result.html")

            final_html_path = save_comparison_html(
                source_tables=source_tables,
                comparison_table=comparison_table,
                output_path=comparison_html_path,
                title="Кабинетное сравнение источников",
                subtitle=f"Продукт / категория: {product_name}"
            )

            st.success("Готово.")

            tab_names = ["Сравнительная таблица"] + [
                item["source_name"] for item in source_tables
            ]

            tabs = st.tabs(tab_names)

            with tabs[0]:
                st.subheader("Сравнительная таблица")
                st.caption(f"Продукт / категория: {product_name}")
                st.markdown(highlighted_html, unsafe_allow_html=True)

            for i, item in enumerate(source_tables, start=1):
                with tabs[i]:
                    st.subheader(item["source_name"])
                    st.markdown(item["md"])

            st.divider()

            st.subheader("Скачать результаты")

            st.download_button(
                label="Скачать сравнительную таблицу Markdown",
                data=comparison_table,
                file_name="desk_comparison_table.md",
                mime="text/markdown"
            )

            html_data = Path(final_html_path).read_text(encoding="utf-8")

            st.download_button(
                label="Скачать итоговый HTML",
                data=html_data,
                file_name="desk_comparison_result.html",
                mime="text/html"
            )

            for item in source_tables:
                safe_name = make_slug(item["source_name"])

                st.download_button(
                    label=f"Скачать таблицу {item['source_name']} Markdown",
                    data=item["md"],
                    file_name=f"{safe_name}_table.md",
                    mime="text/markdown"
                )

        except Exception as e:
            st.error("Произошла ошибка.")
            st.exception(e)