"""MCP Tools for VkusVill"""
import json
import logging
from agents import function_tool
from .client import MCPClient

log = logging.getLogger(__name__)


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
                filtered.append({
                    "id": p.get("id"),  # Для vkusvill_product_details
                    "xml_id": p.get("xml_id"),  # Для корзины
                    "name": p.get("name", ""),
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
        """Получить детальную информацию о товаре ВкусВилл по id (не xml_id!). Возвращает состав, КБЖУ, фото, рейтинг, цену и URL товара."""
        log.info(f"📋 Получаю детали товара: {product_id}")
        result = await mcp.call("vkusvill_product_details", {"id": product_id})
        
        content = result.get("content", [])
        if content:
            details = content[0].get("text", "")
            if details:
                log.info(f"✅ Детали получены")
                return details
        
        log.warning(f"⚠️ Не удалось получить детали товара {product_id}")
        return f"Ошибка получения деталей для товара {product_id}"
    
    return [search_products, create_cart, get_product_details]


