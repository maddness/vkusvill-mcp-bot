"""AI Agent Runner"""
import time
import html
import logging
import litellm
from pathlib import Path
from datetime import datetime
from agents import Agent, Runner, ModelSettings
from typing import Callable, Optional

from ..utils.config import config
from ..mcp.tools import create_mcp_tools, set_cart_storage

log = logging.getLogger(__name__)


def load_prompt(filename: str) -> str:
    """Load prompt from file"""
    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / filename
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read().strip()


# Load prompts from files
SYSTEM_PROMPT = load_prompt("system_prompt.txt")
USER_INITIAL_PROMPT_TEMPLATE = load_prompt("user_initial_prompt.txt")


class SessionData:
    """Session data with metadata"""
    def __init__(self):
        self.messages = []
        self.last_tokens = None
        self.tools_used = []
        self.cart_products: dict[str, int] = {}  # name -> id mapping for quick lookup


class AgentRunner:
    """AI Agent runner with streaming support"""
    
    def __init__(self):
        self.sessions: dict[str, SessionData] = {}  # "user_id:thread_id" -> SessionData
        self.tools = create_mcp_tools(config.mcp_url)
    
    async def run(
        self,
        user_id: int,
        username: str,
        user_message: str,
        send_progress: Callable,
        stream_callback: Optional[Callable] = None,
        thread_id: int = 0
    ) -> str:
        """Run agent with user message"""
        log.info(f"👤 {username} ({user_id}, топик: {thread_id}): {user_message}")
        
        session_key = f"{user_id}:{thread_id}"
        if session_key not in self.sessions:
            self.sessions[session_key] = SessionData()
        
        session = self.sessions[session_key]

        # Reset tools tracking for this run
        session.tools_used = []

        # Set up cart storage for this session
        set_cart_storage(session.cart_products)

        # Build cart context if we have products
        cart_context = ""
        if session.cart_products:
            items = [f"{name}(id:{pid})" for name, pid in session.cart_products.items()]
            cart_context = f"\n\n[Товары в корзине: {', '.join(items)}]"
            log.info(f"📦 Контекст корзины: {len(session.cart_products)} товаров")

        # Format user message with template if it's the first message
        if len(session.messages) == 0:
            current_date = datetime.now().strftime("%d.%m.%Y")
            formatted_message = USER_INITIAL_PROMPT_TEMPLATE.format(
                current_date=current_date,
                task=user_message
            )
            session.messages.append({"role": "user", "content": formatted_message + cart_context})
        else:
            session.messages.append({"role": "user", "content": user_message + cart_context})
        
        if len(session.messages) > config.max_history_messages:
            session.messages = session.messages[-config.max_history_messages:]
        
        settings = ModelSettings(include_usage=True)
        
        agent = Agent(
            name="VkusVill Assistant",
            model=config.llm_model,
            instructions=SYSTEM_PROMPT,
            tools=self.tools,
            model_settings=settings,
        )
        
        # Ограничиваем количество шагов (tool calls) за один запрос
        # Это защищает от злоупотреблений и зацикливания
        max_turns = config.max_turns
        log.info(f"🔄 Максимум шагов: {max_turns}")
        result = Runner.run_streamed(agent, session.messages, max_turns=max_turns)
        
        # Track tool calls
        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                item = event.item
                if hasattr(item, 'raw_item') and hasattr(item.raw_item, 'name'):
                    tool_name = item.raw_item.name
                    tool_args = getattr(item.raw_item, 'arguments', None) or getattr(item.raw_item, 'input', None) or ''
                    log.info(f"🔧 Tool call: {tool_name}({tool_args})")
                    session.tools_used.append(tool_name)
                    if "search" in tool_name:
                        await send_progress("🔍 Ищу товары...")
                    elif "cart" in tool_name:
                        await send_progress("🛒 Собираю корзину...")
        
        final = result.final_output
        
        # Log output
        log.info(f"🔍 Raw output (первые 500 симв.): {repr(final[:500]) if final else 'empty'}")
        
        # Log token usage and save to session
        try:
            usage = result.context_wrapper.usage
            session.last_tokens = {
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "total": usage.total_tokens
            }
            cache_info = ""
            if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
                cache_info += f", cache_write={usage.cache_creation_input_tokens}"
                session.last_tokens["cache_write"] = usage.cache_creation_input_tokens
            if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
                cache_info += f", cache_read={usage.cache_read_input_tokens}"
                session.last_tokens["cache_read"] = usage.cache_read_input_tokens
            log.info(f"📊 Токены: input={usage.input_tokens}, output={usage.output_tokens}, total={usage.total_tokens}{cache_info}")
        except:
            pass
        
        # Remove thinking tags
        if "<think>" in final:
            think_end = final.find("</think>")
            if think_end > 0:
                think_content = final[final.find("<think>")+7:think_end]
                log.info(f"🧠 Thinking ({len(think_content)} симв.): {think_content[:200]}...")
                final = final[think_end+8:].strip()
        
        # Stream response if callback provided
        # Проверяем, есть ли multimodal сообщения в истории (они содержат списки в content)
        has_multimodal = any(
            isinstance(msg.get("content"), list) 
            for msg in session.messages 
            if isinstance(msg, dict)
        )
        
        if stream_callback and final and not has_multimodal:
            try:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                messages.extend(session.messages)
                
                accumulated = ""
                last_update_len = 0
                last_update_time = 0
                
                response = await litellm.acompletion(
                    model=config.llm_model.replace("litellm/", ""),
                    messages=messages,
                    stream=True,
                    api_base=config.llm_api_base,
                    api_key=config.llm_api_key,
                )
                
                async for chunk in response:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            content = html.unescape(delta.content)
                            accumulated += content
                            
                            current_time = time.time()
                            # Update every N chars OR every second
                            if (len(accumulated) - last_update_len >= config.stream_min_chars) or \
                               (current_time - last_update_time >= config.stream_update_interval):
                                display_text = accumulated
                                if "<think>" in display_text:
                                    think_end = display_text.find("</think>")
                                    if think_end > 0:
                                        display_text = display_text[think_end+8:].strip()
                                
                                if display_text:
                                    await stream_callback(display_text)
                                    last_update_len = len(accumulated)
                                    last_update_time = current_time
                
                # Final update
                if accumulated:
                    display_text = accumulated
                    if "<think>" in display_text:
                        think_end = display_text.find("</think>")
                        if think_end > 0:
                            display_text = display_text[think_end+8:].strip()
                    if display_text:
                        await stream_callback(display_text)
            
            except Exception as e:
                log.error(f"❌ Ошибка стриминга: {e}")
                if final:
                    await stream_callback(final)
        
        session.messages.append({"role": "assistant", "content": final})
        log.info(f"✅ Ответ готов ({len(final)} символов)")
        return final
    
    async def run_with_image(
        self,
        user_id: int,
        username: str,
        user_message: str,
        image_base64: str,
        send_progress: Callable,
        stream_callback: Optional[Callable] = None,
        thread_id: int = 0
    ) -> str:
        """Run agent with user message and image"""
        log.info(f"👤 {username} ({user_id}, топик: {thread_id}): [PHOTO] {user_message[:100]}")
        
        session_key = f"{user_id}:{thread_id}"
        if session_key not in self.sessions:
            self.sessions[session_key] = SessionData()
        
        session = self.sessions[session_key]

        # Reset tools tracking for this run
        session.tools_used = []

        # Set up cart storage for this session
        set_cart_storage(session.cart_products)

        # Build cart context if we have products
        cart_context = ""
        if session.cart_products:
            items = [f"{name}(id:{pid})" for name, pid in session.cart_products.items()]
            cart_context = f"\n\n[Товары в корзине: {', '.join(items)}]"

        # Format message with image (используем input_text и input_image для OpenAI Agents SDK)
        message_content = [
            {"type": "input_text", "text": user_message + cart_context},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_base64}"}
        ]

        # Format user message with template if it's the first message
        if len(session.messages) == 0:
            current_date = datetime.now().strftime("%d.%m.%Y")
            formatted_text = USER_INITIAL_PROMPT_TEMPLATE.format(
                current_date=current_date,
                task=user_message
            )
            message_content[0]["text"] = formatted_text + cart_context

        session.messages.append({"role": "user", "content": message_content})
        
        if len(session.messages) > config.max_history_messages:
            session.messages = session.messages[-config.max_history_messages:]
        
        settings = ModelSettings(include_usage=True)
        
        agent = Agent(
            name="VkusVill Assistant",
            model=config.llm_model,
            instructions=SYSTEM_PROMPT,
            tools=self.tools,
            model_settings=settings,
        )
        
        # Ограничиваем количество шагов (tool calls) за один запрос
        max_turns = config.max_turns
        log.info(f"🔄 Максимум шагов: {max_turns}")
        result = Runner.run_streamed(agent, session.messages, max_turns=max_turns)
        
        # Track tool calls
        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                item = event.item
                if hasattr(item, 'raw_item') and hasattr(item.raw_item, 'name'):
                    tool_name = item.raw_item.name
                    tool_args = getattr(item.raw_item, 'arguments', None) or getattr(item.raw_item, 'input', None) or ''
                    log.info(f"🔧 Tool call: {tool_name}({tool_args})")
                    session.tools_used.append(tool_name)
                    if "search" in tool_name:
                        await send_progress("🔍 Ищу товары...")
                    elif "cart" in tool_name:
                        await send_progress("🛒 Собираю корзину...")
        
        final = result.final_output
        
        # Log output
        log.info(f"🔍 Raw output (первые 500 симв.): {repr(final[:500]) if final else 'empty'}")
        
        # Log token usage and save to session
        try:
            usage = result.context_wrapper.usage
            session.last_tokens = {
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "total": usage.total_tokens
            }
            cache_info = ""
            if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
                cache_info += f", cache_write={usage.cache_creation_input_tokens}"
                session.last_tokens["cache_write"] = usage.cache_creation_input_tokens
            if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
                cache_info += f", cache_read={usage.cache_read_input_tokens}"
                session.last_tokens["cache_read"] = usage.cache_read_input_tokens
            log.info(f"📊 Токены: input={usage.input_tokens}, output={usage.output_tokens}, total={usage.total_tokens}{cache_info}")
        except:
            pass
        
        # Remove thinking tags
        if "<think>" in final:
            think_end = final.find("</think>")
            if think_end > 0:
                think_content = final[final.find("<think>")+7:think_end]
                log.info(f"🧠 Thinking ({len(think_content)} симв.): {think_content[:200]}...")
                final = final[think_end+8:].strip()
        
        # Stream response if callback provided
        # Note: Стриминг для multimodal сообщений отключен, т.к. litellm не поддерживает
        # формат input_text/input_image. Для текстовых сообщений стриминг работает в run()
        if stream_callback and final and False:  # Отключаем стриминг для multimodal
            try:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                messages.extend(session.messages)
                
                accumulated = ""
                last_update_len = 0
                last_update_time = 0
                
                response = await litellm.acompletion(
                    model=config.llm_model.replace("litellm/", ""),
                    messages=messages,
                    stream=True,
                    api_base=config.llm_api_base,
                    api_key=config.llm_api_key
                )
                
                async for chunk in response:
                    if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            accumulated += delta.content
                            
                            current_time = time.time()
                            chars_added = len(accumulated) - last_update_len
                            time_elapsed = current_time - last_update_time
                            
                            should_update = (
                                chars_added >= config.stream_min_chars or
                                time_elapsed >= config.stream_update_interval
                            )
                            
                            if should_update:
                                decoded_text = html.unescape(accumulated)
                                await stream_callback(decoded_text)
                                last_update_len = len(accumulated)
                                last_update_time = current_time
                
                if accumulated:
                    final = html.unescape(accumulated)
            
            except Exception as e:
                log.error(f"❌ Ошибка стриминга: {e}")
        
        session.messages.append({"role": "assistant", "content": final})
        
        log.info(f"✅ Ответ готов ({len(final)} символов)")
        return final
    
    def reset_session(self, user_id: int, thread_id: int = 0):
        """Reset user session"""
        session_key = f"{user_id}:{thread_id}"
        self.sessions.pop(session_key, None)

