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
    async def search_products(query: str) -> str:
        """Поиск товаров ВкусВилл по названию. Возвращает список товаров с xml_id, названием, ценой и рейтингом."""
        log.info(f"🔍 Поиск: {query}")
        result = await mcp.call("vkusvill_products_search", {"q": query})
        
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
            
            # Filter only necessary fields
            filtered = []
            for p in products[:10]:  # Take up to 10 products for better search coverage
                rating = p.get("rating", {})
                filtered.append({
                    "xml_id": p.get("xml_id"),
                    "name": p.get("name", "")[:50],  # Truncate name
                    "price": p.get("price"),
                    "rating": rating.get("average") if rating else None
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
    async def get_product_link(xml_id: int) -> str:
        """Получить прямую ссылку на товар ВкусВилл по xml_id. Возвращает URL на страницу товара."""
        log.info(f"🔗 Получаю ссылку на товар: {xml_id}")
        result = await mcp.call("vkusvill_product_link", {"xml_id": xml_id})
        
        content = result.get("content", [])
        if content:
            link = content[0].get("text", "")
            if link:
                log.info(f"✅ Ссылка получена: {link}")
                return link
        
        log.warning(f"⚠️ Не удалось получить ссылку, создаю через корзину")
        # Fallback: создаём корзину с одним товаром
        cart_result = await mcp.call("vkusvill_cart_link_create", {"products": [{"xml_id": xml_id, "q": 1}]})
        cart_content = cart_result.get("content", [])
        if cart_content:
            return f"Ссылка через корзину: {cart_content[0].get('text', '')}"
        return f"Ошибка получения ссылки для товара {xml_id}"
    
    return [search_products, create_cart, get_product_link]


