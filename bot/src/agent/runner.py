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
from ..mcp.tools import create_mcp_tools

log = logging.getLogger(__name__)


def load_prompt(filename: str) -> str:
    """Load prompt from file"""
    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / filename
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read().strip()


# Load prompts from files
SYSTEM_PROMPT = load_prompt("system_prompt.txt")
USER_INITIAL_PROMPT_TEMPLATE = load_prompt("user_initial_prompt.txt")


class AgentRunner:
    """AI Agent runner with streaming support"""
    
    def __init__(self):
        self.sessions: dict[tuple[int, int], list] = {}  # (user_id, thread_id) -> messages
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
        
        session_key = (user_id, thread_id)
        if session_key not in self.sessions:
            self.sessions[session_key] = []
        
        self.sessions[session_key].append({"role": "user", "content": user_message})
        
        if len(self.sessions[session_key]) > config.max_history_messages:
            self.sessions[session_key] = self.sessions[session_key][-config.max_history_messages:]
        
        settings = ModelSettings(include_usage=True)
        
        agent = Agent(
            name="VkusVill Assistant",
            model=config.llm_model,
            instructions=SYSTEM_PROMPT,
            tools=self.tools,
            model_settings=settings,
        )
        
        result = Runner.run_streamed(agent, self.sessions[session_key])
        
        # Track tool calls
        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                item = event.item
                if hasattr(item, 'raw_item') and hasattr(item.raw_item, 'name'):
                    tool_name = item.raw_item.name
                    if "search" in tool_name:
                        await send_progress("🔍 Ищу товары...")
                    elif "cart" in tool_name:
                        await send_progress("🛒 Собираю корзину...")
        
        final = result.final_output
        
        # Log output
        log.info(f"🔍 Raw output (первые 500 симв.): {repr(final[:500]) if final else 'empty'}")
        
        # Log token usage
        try:
            usage = result.context_wrapper.usage
            cache_info = ""
            if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
                cache_info += f", cache_write={usage.cache_creation_input_tokens}"
            if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
                cache_info += f", cache_read={usage.cache_read_input_tokens}"
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
        if stream_callback and final:
            try:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                messages.extend(self.sessions[session_key])
                
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
        
        self.sessions[session_key].append({"role": "assistant", "content": final})
        log.info(f"✅ Ответ готов ({len(final)} символов)")
        return final
    
    def reset_session(self, user_id: int, thread_id: int = 0):
        """Reset user session"""
        session_key = (user_id, thread_id)
        self.sessions.pop(session_key, None)

