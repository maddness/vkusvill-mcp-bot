"""Voice message transcription using Whisper API"""
import httpx
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class VoiceTranscriber:
    """Transcribe voice messages using Whisper API"""
    
    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str = "whisper-1",
        max_file_size_mb: int = 20,
        max_duration_seconds: int = 180
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_file_size_mb = max_file_size_mb
        self.max_duration_seconds = max_duration_seconds
    
    async def transcribe(
        self,
        audio_file: bytes,
        filename: str = "audio.ogg",
        language: str = "ru"
    ) -> Optional[str]:
        """
        Transcribe audio file to text
        
        Args:
            audio_file: Audio file content in bytes
            filename: Original filename
            language: Language code (default: ru)
        
        Returns:
            Transcribed text or None if failed
        """
        # Check file size
        file_size_mb = len(audio_file) / (1024 * 1024)
        if file_size_mb > self.max_file_size_mb:
            log.warning(f"File too large: {file_size_mb:.2f} MB (max: {self.max_file_size_mb} MB)")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {
                    'file': (filename, audio_file, 'audio/ogg')
                }
                data = {
                    'model': self.model,
                    'response_format': 'json',
                    'temperature': 0,
                    'language': language
                }
                headers = {
                    'Authorization': f'Bearer {self.api_key}'
                }
                
                log.info(f"🎤 Транскрибирую голосовое сообщение ({file_size_mb:.2f} MB)...")
                
                response = await client.post(
                    self.api_url,
                    files=files,
                    data=data,
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get('text', '').strip()
                    duration = result.get('duration', 0)
                    
                    # Check duration
                    if duration > self.max_duration_seconds:
                        log.warning(f"Audio too long: {duration}s (max: {self.max_duration_seconds}s)")
                        return None
                    
                    log.info(f"✅ Транскрибировано: {len(text)} символов, {duration:.1f}с")
                    return text
                else:
                    log.error(f"❌ Ошибка транскрибации: {response.status_code} - {response.text}")
                    return None
        
        except Exception as e:
            log.error(f"❌ Ошибка при транскрибации: {e}")
            return None
