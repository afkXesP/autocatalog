import csv
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "output" / "products.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_MESSAGE_LIMIT = 3500


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Не задана обязательная переменная окружения: {name}"
        )

    return value


def get_low_stock_threshold() -> int:
    raw_value = get_required_env("LOW_STOCK_THRESHOLD")

    try:
        threshold = int(raw_value)
    except ValueError as e:
        raise RuntimeError("LOW_STOCK_THRESHOLD должен быть целым числом") from e

    if threshold < 0:
        raise RuntimeError(
            "LOW_STOCK_THRESHOLD не может быть отрицательным"
        )

    return threshold


def load_products(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Файл с товарами не найден: {csv_path}")

    with csv_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        products = list(reader)

    if not products:
        raise RuntimeError(f"Файл {csv_path} не содержит товаров")

    required_columns = {
        "export_date",
        "offer_id",
        "name",
        "price",
        "stock",
    }

    actual_columns = set(reader.fieldnames or [])
    missing_columns = required_columns - actual_columns

    if missing_columns:
        raise RuntimeError(
            "В CSV отсутствуют обязательные столбцы: "
            + ", ".join(sorted(missing_columns))
        )

    return products


def format_product(product: dict[str, str], low_stock_threshold: int) -> str:
    name = product.get("name", "").strip() or "Без названия"
    price = product.get("price", "").strip() or "0.00"
    stock_raw = product.get("stock", "").strip() or "0"

    try:
        stock = int(float(stock_raw))
    except ValueError:
        logger.warning(
            "Некорректный остаток у товара %s: %s",
            name,
            stock_raw,
        )
        stock = 0

    stock_status = ""

    if stock < low_stock_threshold:
        stock_status = " - заканчивается"

    return (
        f"{name}\n"
        f"Цена: {price} ₽\n"
        f"Остаток: {stock} шт.{stock_status}"
    )


def build_report(products: list[dict[str, str]], low_stock_threshold: int) -> str:
    """
    Формирует полное сообщение для Telegram.
    """
    product_lines = [
        format_product(
            product=product,
            low_stock_threshold=low_stock_threshold,
        )
        for product in products
    ]

    return "Утренняя сводка по товарам\n\n"+"\n\n".join(product_lines)


def split_message(message: str, max_length: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """
    Разбивает сообщение на части, чтобы каждая часть не превышала max_length символов.
    """
    if max_length <= 0:
        raise ValueError("max_length должен быть больше 0")
    
    if  len(message) <= max_length:
        return [message]

    chunks: list[str] = []
    curr_chunk = ""

    for line in message.splitlines():
        line_with_separator = f"{line}\n"

        if len(line_with_separator) <= max_length:
            if curr_chunk and len(curr_chunk) + len(line_with_separator) > max_length:
                chunks.append(curr_chunk.rstrip())
                curr_chunk = ""

            curr_chunk += line_with_separator
            continue

        # Если отдельная строка длиннее лимита,
        # разбиваем её принудительно.
        if curr_chunk:
            chunks.append(curr_chunk.rstrip())
            curr_chunk = ""

        for start in range(0, len(line), max_length):
            chunks.append(line[start:start + max_length])

    if curr_chunk:
        chunks.append(curr_chunk.rstrip())

    return chunks or [""]


def send_telegram_message(bot_token: str, chat_id: str, message: str) -> None:
    url = (
        f"https://api.telegram.org/bot"
        f"{bot_token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        raise RuntimeError("Не удалось подключиться к Telegram API") from e

    if response.status_code != 200:
        raise RuntimeError(
            "Telegram API вернул ошибку "
            f"{response.status_code}: {response.text}"
        )

    response_data = response.json()

    if not response_data.get("ok"):
        raise RuntimeError(
            "Telegram API не подтвердил отправку сообщения: "
            f"{response.text}"
        )

    logger.info("Telegram-сводка успешно отправлена")


def main() -> None:
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_required_env("TELEGRAM_CHAT_ID")
    low_stock_threshold = get_low_stock_threshold()

    products = load_products(CSV_PATH)

    report = build_report(
        products=products,
        low_stock_threshold=low_stock_threshold,
    )

    logger.info(
        "Загружено товаров для сводки: %s",
        len(products),
    )

    messages = split_message(report)

    for index, message in enumerate(messages, start=1):
        send_telegram_message(
            bot_token=bot_token,
            chat_id=chat_id,
            message=message,
        )

        logger.info(
            "Отправлена часть сводки %s/%s",
            index,
            len(messages),
        )


if __name__ == "__main__":
    main()
