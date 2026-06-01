import os
import sys
import subprocess
import requests
import yaml
import shutil
import uuid
import logging
import asyncio
import websockets
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify
from minio import Minio
from minio.error import S3Error
import traceback

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

HALLO2_ROOT_DIR = Path(__file__).parent.resolve() / "hallo2"
PRETRAINED_DIR = HALLO2_ROOT_DIR / "pretrained_models"

# Конфигурация MinIO
MINIO_ENDPOINT = "localhost:9000"
MINIO_ACCESS_KEY = "minio-user"
MINIO_SECRET_KEY = "minio-password"
MINIO_BUCKET = "files"
MINIO_SECURE = False

# WebSocket конфигурация
WS_URI = "ws://localhost:8000/ws"

class WebSocketSender:
    """Класс для отправки сообщений в WebSocket с обработкой ошибок подключения"""
    def __init__(self, ws_uri):
        self.ws_uri = ws_uri
        self.ws_available = False
        self._check_connection()
    
    def _check_connection(self):
        """Проверяет доступность WebSocket сервера"""
        try:
            asyncio.run(self._test_connection())
            self.ws_available = True
            logger.info(f"✅ WebSocket connection available at {self.ws_uri}")
        except Exception as e:
            self.ws_available = False
            logger.warning(f"⚠️ WebSocket server not available at {self.ws_uri}: {e}")
            logger.warning("Continuing without WebSocket logging...")
    
    async def _test_connection(self):
        """Тестирует подключение к WebSocket"""
        try:
            async with websockets.connect(self.ws_uri, close_timeout=2) as websocket:
                await websocket.send(json.dumps({"type": "connection_test", "message": "ping"}))
        except Exception as e:
            raise e
    
    async def send_to_websocket(self, data):
        """Асинхронная отправка данных в WebSocket"""
        if not self.ws_available:
            return
        
        try:
            async with websockets.connect(self.ws_uri, close_timeout=2) as websocket:
                await websocket.send(json.dumps(data))
        except Exception as e:
            logger.error(f"WebSocket send error: {e}")
            # Если ошибка повторяется, отмечаем WebSocket как недоступный
            self.ws_available = False
    
    def send_sync(self, data):
        """Синхронная обертка для вызова из Flask маршрутов"""
        if not self.ws_available:
            return
        
        try:
            asyncio.run(self.send_to_websocket(data))
        except Exception as e:
            logger.error(f"Failed to send to WebSocket: {e}")
            self.ws_available = False

# Инициализация WebSocket отправителя
ws_sender = WebSocketSender(WS_URI)

# Инициализация клиента MinIO
try:
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )
    
    # Проверяем существование бакета, если нет - создаем
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
        logger.info(f"Bucket '{MINIO_BUCKET}' created successfully")
        ws_sender.send_sync({
            "type": "hallo2_log",
            "level": "info",
            "message": f"Bucket '{MINIO_BUCKET}' created successfully"
        })
    else:
        logger.info(f"Bucket '{MINIO_BUCKET}' already exists")
        
except Exception as e:
    logger.error(f"Failed to initialize MinIO client: {e}")
    ws_sender.send_sync({
        "type": "hallo2_log",
        "level": "error",
        "message": f"Failed to initialize MinIO client: {e}"
    })
    raise

def log_to_ws(message, level="info"):
    """Отправка лога в WebSocket и в стандартный логгер"""
    if level == "error":
        logger.error(message)
    else:
        logger.info(message)
    
    ws_sender.send_sync({
        "type": "hallo2_log",
        "level": level,
        "message": message
    })

def clean_url(url: str) -> str:
    """Очищает URL от двойных слешей и других проблем"""
    # Убираем двойные слеши в пути (но не в http://)
    if '://' in url:
        protocol, rest = url.split('://', 1)
        # Заменяем множественные слеши на одиночные в пути
        while '//' in rest:
            rest = rest.replace('//', '/')
        return f"{protocol}://{rest}"
    return url

def download_file(url: str, dest_path: Path) -> None:
    """Скачивает файл по URL в указанный путь."""
    # Очищаем URL от двойных слешей
    clean_url_str = clean_url(url)
    log_to_ws(f"Downloading {clean_url_str} to {dest_path}...")
    
    try:
        with requests.get(clean_url_str, stream=True, timeout=30) as r:
            r.raise_for_status()
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        file_size = dest_path.stat().st_size
        log_to_ws(f"Download complete. Size: {file_size / 1024:.1f} KB")
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to download {clean_url_str}: {e}"
        log_to_ws(error_msg, "error")
        raise RuntimeError(error_msg)

def run_command(command: list, cwd: Path = None) -> None:
    """Выполняет системную команду с выводом в реальном времени."""
    if cwd is None:
        cwd = HALLO2_ROOT_DIR
    log_to_ws(f"Running command: {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        error_msg = f"Command failed with return code {result.returncode}: {result.stderr}"
        log_to_ws(error_msg, "error")
        raise RuntimeError(error_msg)
    log_to_ws(f"Command completed successfully")

def upload_to_minio(file_path: Path, object_name: str) -> str:
    """Загружает файл в MinIO и возвращает URL."""
    try:
        file_size = file_path.stat().st_size
        log_to_ws(f"Uploading {file_path.name} to MinIO bucket '{MINIO_BUCKET}' as '{object_name}' (size: {file_size / 1024:.1f} KB)")
        
        # Определяем content-type
        content_type = "video/mp4"
        if file_path.suffix == '.avi':
            content_type = "video/x-msvideo"
        elif file_path.suffix == '.mov':
            content_type = "video/quicktime"
        
        # Загружаем файл
        minio_client.fput_object(
            MINIO_BUCKET,
            object_name,
            str(file_path),
            content_type=content_type
        )
        
        # Формируем URL (без двойных слешей)
        url = f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{object_name}"
        log_to_ws(f"File uploaded successfully. URL: {url}")
        return url
        
    except S3Error as e:
        error_msg = f"MinIO upload error: {e}"
        log_to_ws(error_msg, "error")
        raise RuntimeError(error_msg)

def clear_outputs_directory():
    """Удаляет папку outputs если она существует."""
    outputs_dir = HALLO2_ROOT_DIR / "outputs"
    if outputs_dir.exists():
        log_to_ws(f"Removing existing outputs directory: {outputs_dir}")
        shutil.rmtree(outputs_dir)
    # Создаем чистую папку
    outputs_dir.mkdir(parents=True, exist_ok=True)
    log_to_ws("Outputs directory cleaned")

def generate_talking_video(image_url: str, audio_url: str, session_id: str = None) -> str:
    """
    Генерирует анимированное говорящее видео из фото и аудио, используя Hallo2.
    
    Args:
        image_url: URL к .jpg изображению портрета.
        audio_url: URL к .wav аудиофайлу (английская речь).
        session_id: ID сессии пользователя.
    
    Returns:
        URL к загруженному в MinIO видеофайлу.
    """
    # Очищаем URL от двойных слешей
    clean_image_url = clean_url(image_url)
    clean_audio_url = clean_url(audio_url)
    
    log_to_ws("=" * 50)
    log_to_ws("🎬 НАЧАЛО ГЕНЕРАЦИИ ВИДЕО HALLO2")
    if session_id:
        log_to_ws(f"👤 Session ID: {session_id}")
    log_to_ws(f"📸 Image URL: {clean_image_url}")
    log_to_ws(f"🔊 Audio URL: {clean_audio_url}")
    
    # Проверяем URL на корректность
    if '//' in clean_audio_url.replace('://', ''):
        log_to_ws("⚠️ Обнаружены проблемы в URL (исправлены автоматически)", "warning")
    
    # 0. Очистка папки outputs перед каждым запуском
    log_to_ws("[0/6] Cleaning outputs directory...")
    clear_outputs_directory()
    
    # 1. Проверка наличия моделей
    log_to_ws("[1/6] Checking pretrained models...")
    if not PRETRAINED_DIR.exists():
        error_msg = (
            f"Pretrained models directory not found at {PRETRAINED_DIR}. "
            f"Please download them using: huggingface-cli download fudan-generative-ai/hallo2 --local-dir {PRETRAINED_DIR}"
        )
        log_to_ws(error_msg, "error")
        raise FileNotFoundError(error_msg)
    log_to_ws(f"✅ Pretrained models found at {PRETRAINED_DIR}")
    
    # 2. Проверка существования скрипта
    log_to_ws("[2/6] Checking inference script...")
    inference_script = HALLO2_ROOT_DIR / "scripts" / "inference_long.py"
    if not inference_script.exists():
        error_msg = f"Inference script not found at {inference_script}"
        log_to_ws(error_msg, "error")
        raise FileNotFoundError(error_msg)
    log_to_ws(f"✅ Inference script found: {inference_script}")
    
    # 3. Создание папки для входных данных
    log_to_ws("[3/6] Preparing input data...")
    input_data_dir = HALLO2_ROOT_DIR / "temp_inference_inputs"
    input_data_dir.mkdir(exist_ok=True)
    
    try:
        # 4. Скачивание файлов (с очищенными URL)
        log_to_ws("[4/6] Downloading input files...")
        image_path = input_data_dir / "source_image.jpg"
        download_file(clean_image_url, image_path)
        
        audio_path = input_data_dir / "driving_audio.wav"
        download_file(clean_audio_url, audio_path)
        
        # 5. Создаем конфигурацию на основе стандартной
        log_to_ws("[5/6] Creating inference configuration...")
        config_dir = HALLO2_ROOT_DIR / "configs" / "inference"
        default_config_path = config_dir / "long.yaml"
        
        if not default_config_path.exists():
            error_msg = f"Default config not found at {default_config_path}"
            log_to_ws(error_msg, "error")
            raise FileNotFoundError(error_msg)
        
        # Читаем стандартный конфиг
        with open(default_config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Создаем уникальную папку для сохранения
        save_dir = HALLO2_ROOT_DIR / "outputs" / "custom_inference"
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # Обновляем пути в конфигурации
        config['source_image'] = str(image_path)
        config['driving_audio'] = str(audio_path)
        config['save_path'] = str(save_dir)
        config['cache_path'] = str(HALLO2_ROOT_DIR / ".cache" / "custom_inference")
        
        # Записываем временный конфиг
        temp_config_path = config_dir / "temp_custom_config.yaml"
        with open(temp_config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
        
        log_to_ws(f"✅ Configuration created: {temp_config_path}")
        
        # 6. Запускаем инференс
        log_to_ws("[6/6] Running Hallo2 inference...")
        log_to_ws("⏳ This may take several minutes...")
        
        command = [
            sys.executable,
            str(inference_script),
            "--config", str(temp_config_path)
        ]
        
        # Запускаем из корня hallo2
        run_command(command, cwd=HALLO2_ROOT_DIR)
        
        log_to_ws("✅ Inference completed, searching for output video...")
        
        # 7. Поиск результата
        generated_files = []
        for ext in ['*.mp4', '*.avi', '*.mov']:
            generated_files.extend(list(save_dir.glob(ext)))
            # Также ищем в поддиректориях
            generated_files.extend(list(save_dir.glob(f"**/{ext}")))
        
        if not generated_files:
            error_msg = (
                f"Output video not found in {save_dir}. "
                f"Contents: {list(save_dir.iterdir())}"
            )
            log_to_ws(error_msg, "error")
            raise RuntimeError(error_msg)
        
        # Берем самый новый файл
        final_output = max(generated_files, key=os.path.getctime)
        file_size = final_output.stat().st_size
        log_to_ws(f"✅ Output video found: {final_output.name} (size: {file_size / 1024 / 1024:.1f} MB)")
        
        # 8. Загружаем в MinIO
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if session_id:
            object_name = f"generated_videos/{session_id}/video_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
        else:
            object_name = f"generated_videos/video_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
        
        log_to_ws(f"☁️ Uploading to MinIO as: {object_name}")
        minio_url = upload_to_minio(final_output, object_name)
        
        log_to_ws("=" * 50)
        log_to_ws(f"🎉 VIDEO GENERATION COMPLETED!")
        log_to_ws(f"📺 URL: {minio_url}")
        log_to_ws("=" * 50)
        
        return minio_url
        
    finally:
        # Очистка временных файлов в любом случае
        log_to_ws("🧹 Cleaning up temporary files...")
        if input_data_dir.exists():
            shutil.rmtree(input_data_dir)
            log_to_ws("✅ Temporary input files cleaned")
        
        if 'temp_config_path' in locals() and Path(temp_config_path).exists():
            os.remove(temp_config_path)
            log_to_ws("✅ Temporary config cleaned")

@app.route('/generate_video', methods=['POST'])
def generate_video_endpoint():
    """
    Flask endpoint для генерации видео.
    
    Ожидает JSON:
    {
        "image_url": "http://example.com/image.jpg",
        "audio_url": "http://example.com/audio.wav",
        "session_id": "optional_session_id"
    }
    
    Возвращает JSON:
    {
        "status": "finish",
        "url": "http://localhost:9000/files/generated_videos/video_20260503_172246.mp4"
    }
    или при ошибке:
    {
        "status": "failed",
        "reason": "Описание ошибки"
    }
    """
    try:
        # Проверяем входные данные
        data = request.get_json()
        if not data:
            ws_sender.send_sync({
                "type": "hallo2_log",
                "level": "error",
                "message": "No JSON data provided"
            })
            return jsonify({
                "status": "failed",
                "reason": "No JSON data provided"
            }), 400
        
        image_url = data.get('image_url')
        audio_url = data.get('audio_url')
        session_id = data.get('session_id')
        
        if not image_url or not audio_url:
            ws_sender.send_sync({
                "type": "hallo2_log",
                "level": "error",
                "message": "Both 'image_url' and 'audio_url' are required"
            })
            return jsonify({
                "status": "failed",
                "reason": "Both 'image_url' and 'audio_url' are required"
            }), 400
        
        # Отправляем статус о принятии запроса
        ws_sender.send_sync({
            "info": "Hallo2 video generation request accepted!",
            "image_url": clean_url(image_url),
            "audio_url": clean_url(audio_url),
            "session_id": session_id
        })
        
        logger.info(f"Generating video from image: {image_url} and audio: {audio_url}")
        
        # Генерируем видео (с очисткой URL)
        video_url = generate_talking_video(image_url, audio_url, session_id)
        
        logger.info(f"Video generated successfully: {video_url}")
        
        # Отправляем результат через WebSocket
        ws_sender.send_sync({
            "url": video_url,
            "status": "finish"
        })
        
        return jsonify({
            "status": "finish",
            "url": video_url,
            "session_id": session_id
        }), 200
        
    except FileNotFoundError as e:
        error_msg = f"Required file or model not found: {str(e)}"
        logger.error(f"File not found error: {e}")
        ws_sender.send_sync({
            "type": "hallo2_log",
            "level": "error",
            "message": error_msg,
            "error": str(e)
        })
        return jsonify({
            "status": "failed",
            "reason": error_msg
        }), 500
        
    except RuntimeError as e:
        error_msg = f"Processing error: {str(e)}"
        logger.error(f"Runtime error: {e}")
        ws_sender.send_sync({
            "type": "hallo2_log",
            "level": "error",
            "message": error_msg,
            "error": str(e)
        })
        return jsonify({
            "status": "failed",
            "reason": error_msg
        }), 500
        
    except Exception as e:
        error_msg = f"Internal server error: {str(e)}"
        logger.error(f"Unexpected error: {e}")
        logger.error(traceback.format_exc())
        ws_sender.send_sync({
            "type": "hallo2_log",
            "level": "error",
            "message": error_msg,
            "error": str(e),
            "traceback": traceback.format_exc()
        })
        return jsonify({
            "status": "failed",
            "reason": error_msg
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint для проверки здоровья сервиса."""
    health_status = {
        "status": "healthy",
        "minio_connected": True,
        "models_available": PRETRAINED_DIR.exists(),
        "websocket_uri": WS_URI,
        "websocket_available": ws_sender.ws_available
    }
    
    return jsonify(health_status), 200

if __name__ == '__main__':
    log_to_ws("🚀 Starting Hallo2 Video Generation Service...")
    log_to_ws(f"📁 Hallo2 root directory: {HALLO2_ROOT_DIR}")
    log_to_ws(f"🤖 Pretrained models: {PRETRAINED_DIR}")
    log_to_ws(f"☁️ MinIO endpoint: {MINIO_ENDPOINT}")
    log_to_ws(f"📦 MinIO bucket: {MINIO_BUCKET}")
    log_to_ws(f"🔌 WebSocket: {WS_URI}")
    log_to_ws(f"📡 WebSocket available: {ws_sender.ws_available}")
    
    app.run(host='0.0.0.0', port=5001, debug=True)