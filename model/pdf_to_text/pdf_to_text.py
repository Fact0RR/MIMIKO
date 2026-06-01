import requests
import io
import PyPDF2
from typing import Optional
#from cleaner import clean_pdf_text

def extract_text_from_pdf_url(url: str) -> Optional[str]:
    """
    Загружает PDF файл по указанному URL и извлекает из него текст.
    
    Args:
        url: URL-адрес PDF файла
        
    Returns:
        Извлеченный текст или None в случае ошибки
    """
    try:
        # Загружаем PDF файл
        response = requests.get(url, timeout=30)
        response.raise_for_status()  # Проверяем успешность запроса
        
        # Создаем файлоподобный объект из содержимого
        pdf_file = io.BytesIO(response.content)
        
        # Открываем PDF
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        # Извлекаем текст из всех страниц
        text = ""
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text()
        
        return text.strip() if text else None
        
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при загрузке PDF: {e}")
        return None
    except Exception as e:
        print(f"Ошибка при обработке PDF: {e}")
        return None

# dirty = extract_text_from_pdf_url("http://localhost:9000/files/Лекция 5 ТПР 2 курс.pdf")
# print(dirty)
# print("====================================================")
# clean = clean_pdf_text(dirty)
# print(clean)