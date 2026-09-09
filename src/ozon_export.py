import csv
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from requests import Response
from requests.exceptions import RequestException


load_dotenv()



CLIENT_ID = os.getenv("OZON_CLIENT_ID")
API_KEY = os.getenv("OZON_API_KEY")

BASE_URL = os.getenv(
    "OZON_API_BASE_URL",
    "https://api-seller.ozon.ru",
)

PAGE_SIZE = int(os.getenv("OZON_PAGE_SIZE", "100"))
BATCH_SIZE = int(os.getenv("OZON_BATCH_SIZE", "100"))

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_FILE = OUTPUT_DIR / "products.csv"

LIST_URL = f"{BASE_URL}/v3/product/list"
INFO_URL = f"{BASE_URL}/v3/product/info/list"

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


def validate_config() -> None:
    if not CLIENT_ID:
        raise ValueError("Не задан OZON_CLIENT_ID")

    if not API_KEY:
        raise ValueError("Не задан OZON_API_KEY")

    if PAGE_SIZE <= 0:
        raise ValueError("OZON_PAGE_SIZE должен быть больше 0")

    if BATCH_SIZE <= 0:
        raise ValueError("OZON_BATCH_SIZE должен быть больше 0")


def get_headers() -> dict[str, str]:
    if CLIENT_ID is None or API_KEY is None:
        raise ValueError("OZON_CLIENT_ID и OZON_API_KEY должны быть заданы")
    
    return {
        "Client-Id": CLIENT_ID,
        "Api-Key": API_KEY,
        "Content-Type": "application/json",
    }


def get_retry_delay(response: Response | None, attempt: int) -> float:
    """
    Определяет задержку перед повторной попыткой.

    Если API вернул Retry-After, используем его.
    Иначе - экспоненциальная задержка.
    """

    if response is not None:
        retry_after = response.headers.get("Retry-After")

        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass

    return 2 ** attempt


def post_request(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Выполняет POST-запрос с повторными попытками.

    Повторяем запросы при: HTTP 429, HTTP 5xx, сетевых ошибках.
    Остальные HTTP-ошибки сразу передаём вызывающему коду.
    """

    for attempt in range(MAX_RETRIES + 1):
        response: Response | None = None

        try:
            response = requests.post(
                url,
                headers=get_headers(),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == MAX_RETRIES:
                    response.raise_for_status()

                delay = get_retry_delay(response, attempt)

                logger.warning(
                    "HTTP %s. Повторная попытка %s/%s через %.1f сек.",
                    response.status_code,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response.json()

        except RequestException as exc:
            if attempt == MAX_RETRIES:
                raise

            delay = get_retry_delay(response, attempt)

            logger.warning(
                "Ошибка запроса: %s. "
                "Повторная попытка %s/%s через %.1f сек.",
                exc,
                attempt + 1,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError("Не удалось выполнить запрос")


def get_all_products() -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    last_id = ""
    page_number = 0

    while True:
        payload = {
            "filter": {
                "visibility": "ALL",
            },
            "limit": PAGE_SIZE,
            "last_id": last_id,
        }
        data = post_request(LIST_URL, payload)

        result = data["result"]
        items = result["items"]

        page_number += 1

        logger.info(
            "Получена страница %s. "
            "Товаров на странице: %s. "
            "Всего получено: %s.",
            page_number,
            len(items),
            len(products) + len(items),
        )

        products.extend(items)
        
        if not items:
            logger.info("Пагинация завершена.")
            break

        next_last_id = result.get("last_id", "")

        if not next_last_id:
            logger.info("Пагинация завершена: курсор отсутствует.")
            break

        last_id = next_last_id

    return products


def get_product_details(product_ids: list[int]) -> list[dict[str, Any]]:
    all_details: list[dict[str, Any]] = []
    total = len(product_ids)

    for start in range(0, total, BATCH_SIZE):
        batch = product_ids[start:start + BATCH_SIZE]

        payload = {"product_id": batch}

        data = post_request(INFO_URL, payload)
        items = data.get("items", [])
        all_details.extend(items)

        logger.info(
            "Получены детали: %s/%s.",
            len(all_details),
            total,
        )

    return all_details


def extract_stock(product: dict[str, Any]) -> int:
    stocks_data = product.get("stocks") or {}
    stocks = stocks_data.get("stocks") or []

    total_stock = 0

    for stock in stocks:
        raw_present = stock.get("present", 0)

        try:
            total_stock += int(raw_present or 0)
        except (TypeError, ValueError):
            logger.warning(
                "Некорректный остаток в ответе API: %r",
                raw_present,
            )

    return total_stock


def extract_price(product: dict[str, Any]) -> str:
    price_info =  product.get("price", "")

    if isinstance(price_info, dict):
        price = price_info.get("price")
    else:
        price = price_info

    if price is None:
        return ""

    return str(price)


def prepare_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Преобразует ответ API в структуру для CSV.
    """

    export_date = date.today().isoformat()
    return [
        {
            "export_date": export_date,
            "offer_id": product.get("offer_id", ""),
            "name": product.get("name", ""),
            "price": extract_price(product),
            "stock": extract_stock(product),
        }
        for product in products
    ]


def save_to_csv(products: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "export_date",
        "offer_id",
        "name",
        "price",
        "stock",
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(products)

    logger.info(
        "CSV сохранён: %s. Строк: %s.",
        OUTPUT_FILE,
        len(products),
    )


def main() -> None:
    validate_config()

    logger.info("Начало выгрузки товаров.")

    products = get_all_products()
    if not products:
        logger.info("Товары не найдены.")
        return

    product_ids = [product["product_id"] for product in products if product.get("product_id") is not None]

    details = get_product_details(product_ids)
    prepared_products = prepare_products(details)
    save_to_csv(prepared_products)

    logger.info("Выгрузка завершена.")


if __name__ == "__main__":
    main()
