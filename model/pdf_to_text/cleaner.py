import requests
import json
import re
from typing import Optional, Dict, Any
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PDFTextCleaner:
    """Класс для очистки PDF текста с помощью Qwen через Ollama"""
    
    def __init__(self, 
                 ollama_url: str = "http://localhost:11434",
                 model_name: str = "qwen2.5:7b",
                 temperature: float = 0.1,
                 timeout: int = 120):
        """
        Инициализация клинера
        
        Args:
            ollama_url: URL Ollama сервера
            model_name: Название модели Qwen
            temperature: Температура генерации (ниже = более стабильный вывод)
            timeout: Таймаут запроса в секундах
        """
        self.ollama_url = ollama_url
        self.model_name = model_name
        self.temperature = temperature
        self.timeout = timeout
        self.api_endpoint = f"{ollama_url}/api/generate"
        
    def pull_model(self) -> bool:
        """Загрузка модели Qwen в Ollama"""
        try:
            logger.info(f"Загружаем модель {self.model_name}...")
            response = requests.post(
                f"{self.ollama_url}/api/pull",
                json={"name": self.model_name},
                timeout=300
            )
            if response.status_code == 200:
                logger.info(f"Модель {self.model_name} успешно загружена")
                return True
            else:
                logger.error(f"Ошибка загрузки модели: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Ошибка подключения к Ollama: {e}")
            return False
    
    def clean_with_qwen(self, text: str, custom_prompt: Optional[str] = None) -> str:
        """
        Очистка текста с помощью Qwen
        
        Args:
            text: Исходный текст из PDF
            custom_prompt: Пользовательский промпт (опционально)
            
        Returns:
            Очищенный текст
        """
        if not text or not text.strip():
            logger.warning("Получен пустой текст")
            return ""
        
        # Стандартный промпт для очистки PDF текста
        if custom_prompt is None:
            prompt = f"""Ты - ассистент для подгтовки доклада из лекции в PDF документе.:

1. Удали колонтитулы, номера страниц и водяные знаки
2. Все формулы превратить в предложения
3. Сами примеры игнорировать
4. Все определения сохранить
5. Ничего нового не добавлять
6. Ответ составить в одну строку


Текст для очистки:
{text}

Выведи ТОЛЬКО очищенный текст, без пояснений:"""
        
        try:
            # Отправляем запрос к Ollama
            response = requests.post(
                self.api_endpoint,
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": min(len(text) * 2, 4096)  # Лимит токенов
                    }
                },
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                cleaned_text = result.get("response", "").strip()
                logger.info(f"Текст успешно очищен. Длина: {len(cleaned_text)} символов")
                return cleaned_text
            else:
                logger.error(f"Ошибка API Ollama: {response.status_code} - {response.text}")
                return text  # Возвращаем исходный текст в случае ошибки
                
        except requests.exceptions.Timeout:
            logger.error("Таймаут запроса к Ollama")
            return text
        except Exception as e:
            logger.error(f"Ошибка при очистке текста: {e}")
            return text
    
    def basic_clean(self, text: str) -> str:
        """Базовая предварительная очистка текста"""
        # Удаляем множественные пробелы
        text = re.sub(r'\s+', ' ', text)
        
        # Удаляем номера страниц (паттерн: цифры в начале или конце строки)
        text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
        
        # Пытаемся объединить слова с переносами
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)
        
        return text.strip()
    
    def process_pdf_text(self, text: str, use_basic_clean: bool = True) -> str:
        """
        Полный процесс очистки PDF текста
        
        Args:
            text: Исходный текст
            use_basic_clean: Применять базовую очистку перед Qwen
            
        Returns:
            Очищенный текст
        """
        if use_basic_clean:
            text = self.basic_clean(text)
            logger.info("Базовая очистка выполнена")
        
        # Если текст короткий, можно пропустить Qwen
        if len(text) < 100:
            logger.info("Текст короткий, Qwen не используется")
            return text
        
        # Разбиваем на чанки, если текст слишком длинный
        max_chunk_size = 2000  # Максимальный размер чанка
        if len(text) > max_chunk_size:
            chunks = self._split_text(text, max_chunk_size)
            cleaned_chunks = []
            
            for i, chunk in enumerate(chunks):
                logger.info(f"Обработка чанка {i+1}/{len(chunks)}")
                cleaned_chunk = self.clean_with_qwen(chunk)
                cleaned_chunks.append(cleaned_chunk)
            
            return '\n\n'.join(cleaned_chunks)
        else:
            return self.clean_with_qwen(text)
    
    def _split_text(self, text: str, max_size: int) -> list:
        """Разбиение текста на чанки по абзацам"""
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            if len(current_chunk) + len(paragraph) < max_size:
                current_chunk += paragraph + '\n\n'
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph + '\n\n'
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks


# Основная функция для использования
def clean_pdf_text(text: str, 
                   ollama_url: str = "http://localhost:11434",
                   model: str = "qwen2.5:7b",
                   auto_pull: bool = False) -> str:
    """
    Функция для очистки PDF текста
    
    Args:
        text: Неочищенный текст из PDF
        ollama_url: URL Ollama сервера
        model: Модель Qwen (qwen2.5:7b, qwen2.5:14b и т.д.)
        auto_pull: Автоматически загружать модель при отсутствии
        
    Returns:
        Очищенный текст
    """
    cleaner = PDFTextCleaner(ollama_url=ollama_url, model_name=model)
    
    # Проверяем доступность модели
    if auto_pull:
        cleaner.pull_model()
    
    return cleaner.process_pdf_text(text)
