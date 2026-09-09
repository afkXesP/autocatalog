import csv
import logging
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_PATH = BASE_DIR / "input" / "catalog_raw.csv"
if not INPUT_PATH.exists():
    INPUT_PATH = BASE_DIR / "catalog_raw.csv"

OUTPUT_PATH = BASE_DIR / "output" / "catalog_clean.csv"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


OUTPUT_FIELDS = [
    "offer_id",
    "brand",
    "oem",
    "name",
    "quantity",
    "price",
    "stock",
]

BRAND_MAP = {
    "mavico": "Mavico",
    "dba": "DBA",
    "деталиус": "Деталиус",
}

_BRAND_PATTERN = re.compile(
    rf"\b({'|'.join(re.escape(b) for b in BRAND_MAP)})\b",
    re.IGNORECASE,
)


def normalize_offer_id(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def parse_price(value: str) -> float | None:
    if not value:
        return None

    normalized = value.strip().lower()
    normalized = normalized.replace("\xa0", " ")

    numbers = re.findall(r"\d+(?:[.,]\d+)?", normalized)

    if not numbers:
        return None

    number = numbers[-1].replace(",", ".")

    try:
        return float(number)
    except ValueError:
        return None


def parse_stock(value: str) -> int:
    """
    Преобразует остаток в целое число.
    Пустой остаток считается равным нулю.
    """

    if not value:
        return 0

    try:
        return int(float(value.replace(",", ".")))
    except ValueError:
        return 0


def normalize_text(value: str) -> str:
    """
    Убирает лишние пробелы и пробелы перед знаками препинания.
    """

    value = re.sub(r"\s+", " ", value.strip())
    value = re.sub(r"\s+([,.;])", r"\1", value)
    value = value.strip(" ,.;")

    return value


def extract_brand(name: str) -> str:
    match = _BRAND_PATTERN.search(name)
    if match:
        return BRAND_MAP.get(match.group().lower(), "")
    return ""


def extract_oem(offer_id: str, name: str) -> str:
    """
    Извлекает OEM/артикул из названия.

    Если отдельный OEM не найден, используется offer_id.
    """

    name_upper = name.upper()
    explicit_oem = re.search(r"\bOEM\s*[:#]?\s*([A-ZА-Я0-9-]+)", name_upper)

    if explicit_oem:
        return explicit_oem.group(1).replace("-", "")

    article_pattern = (r"\b[A-ZА-Я]{1,5}(?:[- ]?\d{3,6})[A-ZА-Я]?\b")
    article_match = re.search(article_pattern, name_upper)

    if article_match:
        return article_match.group(0).replace("-", "").replace(" ", "")

    return offer_id


def extract_quantity(name: str) -> int:
    """
    Извлекает количество из названия.

    Если количество не указано, возвращается 1.
    """

    quantity_match = re.search(
        r"(?:комплект|компл|к-т|набор)?\s*(\d+)\s*шт\.?",
        name.lower(),
    )

    if quantity_match:
        return int(quantity_match.group(1))

    if re.search(r"\bпара\b", name.lower()):
        return 2

    return 1


def clean_name(name: str, brand: str, oem: str) -> str:
    result = name

    if brand:
        result = re.sub(
            rf"\b{re.escape(brand)}\b",
            "",
            result,
            flags=re.IGNORECASE,
        )

    # Удаляем основной артикул offer_id и его варианты с пробелом/дефисом.
    oem_pattern = re.escape(oem)
    oem_pattern = oem_pattern.replace(r"\-", "[- ]?")

    result = re.sub(
        rf"\b{oem_pattern}\b",
        "",
        result,
        flags=re.IGNORECASE,
    )

    # Удаляем технические обозначения количества.
    result = re.sub(
        r"\b(?:комплект|компл|к-т|набор)\b",
        "",
        result,
        flags=re.IGNORECASE,
    )

    result = re.sub(
        r"\b\d+\s*шт\.?\b",
        "",
        result,
        flags=re.IGNORECASE,
    )

    result = re.sub(
        r"\bпара\b",
        "",
        result,
        flags=re.IGNORECASE,
    )

    result = normalize_text(result)

    return result


def process_row(row: dict[str, str]) -> dict[str, str] | None:
    raw_offer_id = (row.get("offer_id") or "").strip()
    raw_name = (row.get("name") or "").strip()
    raw_price = (row.get("price") or "").strip()
    raw_stock = (row.get("stock") or "").strip()

    if not raw_offer_id:
        logger.warning("Строка пропущена: отсутствует offer_id")
        return None

    if not raw_name:
        logger.warning(
            "Товар %s пропущен: отсутствует название",
            raw_offer_id,
        )
        return None

    price = parse_price(raw_price)

    if price is None:
        logger.warning(
            "Товар %s пропущен: некорректная цена %r",
            raw_offer_id,
            raw_price,
        )
        return None

    offer_id = normalize_offer_id(raw_offer_id)
    brand = extract_brand(raw_name)
    oem = extract_oem(offer_id, raw_name)
    quantity = extract_quantity(raw_name)
    name = clean_name(raw_name, brand, oem)
    stock = parse_stock(raw_stock)

    return {
        "offer_id": offer_id,
        "brand": brand,
        "oem": oem,
        "name": name,
        "quantity": str(quantity),
        "price": f"{price:.2f}",
        "stock": str(stock),
    }


def read_catalog(input_path: Path) -> list[dict[str, str]]:
    if not input_path.exists():
        raise FileNotFoundError(f"Исходный файл не найден: {input_path}")

    with input_path.open(mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)


def clean_catalog(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned_rows: list[dict[str, str]] = []
    seen_offer_ids: set[str] = set()

    for row in rows:
        cleaned_row = process_row(row)

        if cleaned_row is None:
            continue

        offer_id = cleaned_row["offer_id"]

        if offer_id in seen_offer_ids:
            logger.info(
                "Дубликат пропущен: %s",
                offer_id,
            )
            continue

        seen_offer_ids.add(offer_id)
        cleaned_rows.append(cleaned_row)

    return cleaned_rows


def save_catalog(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(mode="w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)

        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = read_catalog(INPUT_PATH)
    cleaned_rows = clean_catalog(rows)
    save_catalog(cleaned_rows, OUTPUT_PATH)

    logger.info("Исходных строк: %s", len(rows))
    logger.info("Очищенных строк: %s", len(cleaned_rows))
    logger.info("Каталог сохранён: %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()