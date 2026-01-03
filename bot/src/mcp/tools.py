"""MCP Tools for VkusVill"""
import json
import logging
from contextvars import ContextVar
from agents import function_tool
from .client import MCPClient

log = logging.getLogger(__name__)

# Context-local storage for cart products (thread/async-safe)
_cart_storage_var: ContextVar[dict[str, int]] = ContextVar('cart_storage', default={})


def set_cart_storage(storage: dict[str, int]):
    """Set the cart storage dict for current async context"""
    _cart_storage_var.set(storage)


def get_cart_storage() -> dict[str, int]:
    """Get the cart storage dict for current async context"""
    return _cart_storage_var.get()


def create_mcp_tools(mcp_url: str):
    """Create MCP tools for agent"""
    mcp = MCPClient(mcp_url)

    @function_tool
    async def search_products(query: str, page: int = 1) -> str:
        """Поиск товаров ВкусВилл по названию. Возвращает список товаров с id, xml_id, названием, ценой и рейтингом. page - номер страницы (10 товаров на страницу)."""
        log.info(f"🔍 Поиск: {query} (страница {page})")
        result = await mcp.call("vkusvill_products_search", {"q": query, "page": page, "sort": "popularity"})

        content = result.get("content", [])
        if not content:
            return "Товары не найдены"

        text = content[0].get("text", "")
        if not text:
            return "Товары не найдены"

        try:
            data = json.loads(text)
            products = data.get("data", {}).get("items", [])
            if not products:
                products = data if isinstance(data, list) else []

            # Return more fields including id for vkusvill_product_details
            filtered = []
            for p in products:
                rating = p.get("rating", {})
                product_id = p.get("id")
                product_name = p.get("name", "")

                # Store first product in cart storage for quick lookup
                if product_id and product_name and page == 1:
                    # Normalize name for lookup (lowercase, first significant words)
                    name_key = product_name.lower().split(",")[0].strip()
                    cart_storage = get_cart_storage()
                    if name_key not in cart_storage:
                        cart_storage[name_key] = product_id
                        log.debug(f"📦 Сохранён товар: {name_key} -> {product_id}")

                filtered.append({
                    "id": product_id,  # Для vkusvill_product_details
                    "xml_id": p.get("xml_id"),  # Для корзины
                    "name": product_name,
                    "price": p.get("price"),
                    "rating": rating.get("average") if rating else None,
                    "url": p.get("url", "")  # Возможно есть прямая ссылка
                })
            log.info(f"✅ Найдено {len(filtered)} товаров")
            return json.dumps(filtered, ensure_ascii=False) if filtered else "Товары не найдены"
        except Exception as e:
            log.error(f"❌ Ошибка парсинга: {e}")
            return text[:500]  # Fallback
    
    @function_tool
    async def create_cart(products_json: str) -> str:
        """Создаёт ссылку на корзину ВкусВилл. products_json: JSON строка вида [{"xml_id": 123, "q": 1}, ...]"""
        try:
            products = json.loads(products_json)
        except:
            log.error("❌ Неверный JSON для корзины")
            return "Ошибка: неверный формат JSON"

        log.info(f"🛒 Создаю корзину: {len(products)} товаров")
        result = await mcp.call("vkusvill_cart_link_create", {"products": products})

        content = result.get("content", [])
        if content:
            return content[0].get("text", "Ошибка создания корзины")
        return "Ошибка создания корзины"

    @function_tool
    async def get_product_details(product_id: int) -> str:
        """Получает детальную информацию о товаре по его id: состав, КБЖУ, срок годности, условия хранения, изготовитель."""
        log.info(f"📋 Детали товара: {product_id}")
        result = await mcp.call("vkusvill_product_details", {"id": product_id})

        content = result.get("content", [])
        if not content:
            return "Информация о товаре не найдена"

        text = content[0].get("text", "")
        if not text:
            return "Информация о товаре не найдена"

        try:
            data = json.loads(text)
            product = data.get("data", data)

            # Извлекаем основную информацию
            info = {
                "name": product.get("name", "").replace("&nbsp;", " "),
                "price": product.get("price", {}).get("current"),
                "brand": product.get("brand"),
                "rating": product.get("rating", {}).get("average"),
                "url": product.get("url")  # Ссылка на страницу товара
            }

            # Добавляем первое фото (large размер)
            images = product.get("images", [])
            if images and len(images) > 0:
                info["image_url"] = images[0].get("large") or images[0].get("medium")

            # Извлекаем свойства (КБЖУ, состав, срок годности и т.д.)
            properties = product.get("properties", [])
            for prop in properties:
                name = prop.get("name", "").lower()
                value = prop.get("value", "")
                if "пищевая" in name or "энергетическая" in name:
                    info["nutrition"] = value
                elif "состав" in name:
                    info["composition"] = value[:200]  # Ограничиваем длину
                elif "срок годности" in name:
                    info["shelf_life"] = value
                elif "условия хранения" in name:
                    info["storage"] = value
                elif "изготовитель" in name:
                    info["manufacturer"] = value[:150]  # Ограничиваем длину
                elif "страна" in name:
                    info["country"] = value

            log.info(f"✅ Получены детали товара: {info.get('name', product_id)}")
            return json.dumps(info, ensure_ascii=False)
        except Exception as e:
            log.error(f"❌ Ошибка парсинга деталей: {e}")
            return text[:500]

    return [search_products, create_cart, get_product_details]


