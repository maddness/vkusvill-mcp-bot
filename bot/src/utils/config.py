"""Configuration loader"""
import yaml
from pathlib import Path
from typing import List


class Config:
    """Bot configuration"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load configuration from YAML file"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    @property
    def telegram_bot_token(self) -> str:
        return self._config['telegram']['bot_token']
    
    @property
    def admin_ids(self) -> List[int]:
        return self._config['telegram'].get('admin_ids') or []
    
    @property
    def llm_model(self) -> str:
        return self._config['llm']['model']
    
    @property
    def llm_api_key(self) -> str:
        return self._config['llm']['api_key']
    
    @property
    def llm_api_base(self) -> str:
        return self._config['llm']['api_base']
    
    @property
    def mcp_url(self) -> str:
        return self._config['mcp']['url']
    
    @property
    def whisper_api_url(self) -> str:
        return self._config.get('whisper', {}).get('api_url', '')
    
    @property
    def whisper_api_key(self) -> str:
        return self._config.get('whisper', {}).get('api_key', '')
    
    @property
    def whisper_model(self) -> str:
        return self._config.get('whisper', {}).get('model', 'whisper-1')
    
    @property
    def whisper_max_file_size_mb(self) -> int:
        return self._config.get('whisper', {}).get('max_file_size_mb', 20)
    
    @property
    def whisper_max_duration_seconds(self) -> int:
        return self._config.get('whisper', {}).get('max_duration_seconds', 180)
    
    @property
    def max_history_messages(self) -> int:
        return self._config['bot']['max_history_messages']
    
    @property
    def stream_update_interval(self) -> float:
        return self._config['bot']['stream_update_interval']
    
    @property
    def stream_min_chars(self) -> int:
        return self._config['bot']['stream_min_chars']
    
    @property
    def max_turns(self) -> int:
        return self._config['bot'].get('max_turns', 10)

    @property
    def langfuse_secret_key(self) -> str:
        return self._config.get('langfuse', {}).get('secret_key', '')

    @property
    def langfuse_public_key(self) -> str:
        return self._config.get('langfuse', {}).get('public_key', '')

    @property
    def langfuse_base_url(self) -> str:
        return self._config.get('langfuse', {}).get('base_url', 'https://cloud.langfuse.com')

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_secret_key and self.langfuse_public_key)


# Global config instance
config = Config()


