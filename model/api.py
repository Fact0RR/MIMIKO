# api.py

from flask import Flask, request
import asyncio
import websockets
import json
from pdf_to_text.pdf_to_text import extract_text_from_pdf_url
import os
from datetime import datetime
import requests
import torch
import gc

# Импортируем TTS класс, но НЕ инициализируем его глобально
from tts.tts import TTSProcessor

s3_host = "http://localhost:9000"
s3_bucket = "files"

# MinIO credentials
MINIO_ACCESS_KEY = "minio-user"
MINIO_SECRET_KEY = "minio-password"

# URL Hallo2 сервиса
HALLO2_SERVICE_URL = "http://localhost:5001"

app = Flask(__name__)

WS_URI = "ws://localhost:8000/ws"

# Глобальные переменные для процессоров (None = не инициализированы)
tts_processor = None

def make_s3_url(filename):
    """
    Формирует корректный URL для S3 файла без двойных слешей.
    
    Args:
        filename: имя файла в S3 бакете
    
    Returns:
        str: полный URL к файлу
    """
    host = s3_host.rstrip('/')
    bucket = s3_bucket.strip('/')
    return f"{host}/{bucket}/{filename}"

class WebSocketSender:
    def __init__(self, ws_uri):
        self.ws_uri = ws_uri
        self.ws_available = False
        self._check_connection()
    
    def _check_connection(self):
        """Проверяет доступность WebSocket сервера"""
        try:
            asyncio.run(self._test_connection())
            self.ws_available = True
            print(f"✅ WebSocket connection available at {self.ws_uri}")
        except Exception as e:
            self.ws_available = False
            print(f"⚠️ WebSocket server not available at {self.ws_uri}: {e}")
            print("Continuing without WebSocket logging...")
    
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
            print(f"WebSocket send error: {e}")
            self.ws_available = False
    
    def send_sync(self, data):
        """Синхронная обертка для вызова из Flask маршрутов"""
        if not self.ws_available:
            return
        
        try:
            asyncio.run(self.send_to_websocket(data))
        except Exception as e:
            print(f"Failed to send to WebSocket: {e}")
            self.ws_available = False

# Создаем экземпляр объекта WebSocket (всегда нужен)
ws_sender = WebSocketSender(WS_URI)

def free_gpu_memory():
    """Освобождает память GPU"""
    print("🧹 Freeing GPU memory...")
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        
        # Логируем состояние памяти
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        allocated = torch.cuda.memory_allocated(0) / 1024**3
        free = total - allocated
        print(f"GPU Memory: {free:.2f} GB free / {total:.2f} GB total")

def get_tts_processor():
    """
    Ленивая инициализация TTSProcessor.
    Создает новый экземпляр только если он еще не создан.
    """
    global tts_processor
    
    if tts_processor is None:
        print("🤖 Initializing TTS Processor...")
        ws_sender.send_sync({
            "info": "🤖 Initializing TTS Processor...",
            "step": "tts_init"
        })
        
        tts_processor = TTSProcessor(
            ws_sender=ws_sender,
            s3_host=s3_host,
            s3_bucket=s3_bucket,
            s3_access_key=MINIO_ACCESS_KEY,
            s3_secret_key=MINIO_SECRET_KEY
        )
        
        ws_sender.send_sync({
            "info": "✅ TTS Processor initialized",
            "step": "tts_init_complete"
        })
    
    return tts_processor

def release_tts_processor():
    """
    Освобождает ресурсы TTS процессора.
    Выгружает модели из памяти GPU.
    """
    global tts_processor
    
    if tts_processor is not None:
        print("🗑️ Releasing TTS Processor resources...")
        ws_sender.send_sync({
            "info": "🗑️ Releasing TTS Processor resources...",
            "step": "tts_cleanup"
        })
        
        # Явно удаляем модели из памяти
        if hasattr(tts_processor, 'whisper_model') and tts_processor.whisper_model is not None:
            del tts_processor.whisper_model
            tts_processor.whisper_model = None
        
        if hasattr(tts_processor, 'tts_model') and tts_processor.tts_model is not None:
            del tts_processor.tts_model
            tts_processor.tts_model = None
        
        # Удаляем сам процессор
        del tts_processor
        tts_processor = None
        
        # Очищаем GPU память
        free_gpu_memory()
        
        ws_sender.send_sync({
            "info": "✅ TTS Processor resources released",
            "step": "tts_cleanup_complete"
        })
        print("✅ TTS Processor resources released")

@app.route('/send_status', methods=['POST'])
def send_status():
    """Отправка статуса в WebSocket"""
    data = request.get_json()
    ws_sender.send_sync(data)
    return {"status": "ok"}, 200

@app.route('/generate', methods=['POST'])
def generate():
    """
    Основной endpoint для генерации TTS и видео.
    Загружает модели по мере необходимости и освобождает ресурсы между этапами.
    
    Ожидает JSON:
    {
        "jpg_filename": "user_photo.jpg",
        "pdf_filename": "document.pdf",
        "session_id": "user_session_id",
        "wav_filename": "voice_sample.wav"
    }
    """
    data = request.get_json()
    
    # Отправляем статус о начале обработки
    ws_sender.send_sync({
        "info": "🚀 Generation request accepted!",
        "session_id": data.get("session_id")
    })
    
    # Логируем входящие данные
    print(f"📥 Request data: {json.dumps(data, indent=2)}")
    ws_sender.send_sync({
        "info": "Request data received",
        "data": data
    })

    # Проверяем обязательные поля
    required_fields = ['jpg_filename', 'pdf_filename', 'wav_filename']
    missing_fields = [field for field in required_fields if not data.get(field)]
    
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        ws_sender.send_sync({"error": error_msg})
        return {"status": "error", "message": error_msg}, 400

    jpg_filename = data.get("jpg_filename")
    pdf_filename = data.get("pdf_filename")
    wav_filename = data.get("wav_filename")
    session_id = data.get("session_id", "unknown")

    try:
        # ============ ЭТАП 1: ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ PDF ============
        print("📄 Phase 1: Extracting text from PDF")
        ws_sender.send_sync({
            "info": "📄 Phase 1: Extracting text from PDF",
            "step": "phase1_start"
        })
        
        pdf_url = make_s3_url(pdf_filename)
        text = extract_text_from_pdf_url(pdf_url)
        text_length = len(text)
        
        print(f"✅ Text extracted: {text_length} characters")
        ws_sender.send_sync({
            "info": f"✅ Text extracted: {text_length} characters",
            "step": "phase1_complete",
            "text_length": text_length
        })
        
        # ============ ЭТАП 2: TTS ГЕНЕРАЦИЯ ============
        print("🎤 Phase 2: TTS Generation")
        ws_sender.send_sync({
            "info": "🎤 Phase 2: Starting TTS Generation",
            "step": "phase2_start"
        })
        
        # Инициализируем TTS только когда он нужен
        processor = get_tts_processor()
        
        wav_url = make_s3_url(wav_filename)
        base_name = pdf_filename.replace('.pdf', '')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tts_output_filename = f"generated_speech/{session_id}/{base_name}_{timestamp}.wav"
        
        ws_sender.send_sync({
            "info": "Generating speech...",
            "step": "tts_generation",
            "output_filename": tts_output_filename
        })
        
        # Генерируем речь
        tts_result = processor.generate_and_upload_to_s3(
            ref_audio_url=wav_url,
            text_to_synthesize=text,
            s3_filename=tts_output_filename
        )
        
        print(f"✅ TTS completed: {tts_result['url']}")
        ws_sender.send_sync({
            "info": "✅ TTS generation completed!",
            "step": "phase2_complete",
            "result": tts_result
        })
        
        # ============ ВАЖНО: ОСВОБОЖДАЕМ TTS РЕСУРСЫ ============
        print("🗑️ Phase 2.5: Releasing TTS resources for Hallo2...")
        ws_sender.send_sync({
            "info": "🗑️ Releasing TTS resources to free GPU memory for Hallo2...",
            "step": "phase2_cleanup"
        })
        
        # Выгружаем модели TTS из памяти
        release_tts_processor()
        
        # Даем время на освобождение памяти
        import time
        time.sleep(2)
        
        # Показываем состояние памяти после очистки
        if torch.cuda.is_available():
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            allocated = torch.cuda.memory_allocated(0) / 1024**3
            free = total - allocated
            memory_msg = f"GPU Memory after TTS cleanup: {free:.2f} GB free / {total:.2f} GB total"
            print(memory_msg)
            ws_sender.send_sync({"info": memory_msg, "step": "memory_status"})
        
        # ============ ЭТАП 3: HALLO2 ГЕНЕРАЦИЯ ВИДЕО ============
        print("🎬 Phase 3: Hallo2 Video Generation")
        ws_sender.send_sync({
            "info": "🎬 Phase 3: Starting Hallo2 Video Generation",
            "step": "phase3_start"
        })
        
        # Формируем URL для Hallo2
        image_url = make_s3_url(jpg_filename)
        audio_url = tts_result['url']
        
        print(f"📸 Image URL: {image_url}")
        print(f"🔊 Audio URL: {audio_url}")
        
        ws_sender.send_sync({
            "info": "Calling Hallo2 service...",
            "step": "hallo2_calling",
            "image_url": image_url,
            "audio_url": audio_url,
            "session_id": session_id
        })
        
        # Отправляем запрос в Hallo2 сервис
        hallo2_payload = {
            "image_url": image_url,
            "audio_url": audio_url,
            "session_id": session_id
        }
        
        try:
            print(f"📤 Sending request to Hallo2: {HALLO2_SERVICE_URL}/generate_video")
            
            hallo2_response = requests.post(
                f"{HALLO2_SERVICE_URL}/generate_video",
                json=hallo2_payload,
                timeout=600  # 10 минут таймаут
            )
            
            hallo2_data = hallo2_response.json()
            print(f"📥 Hallo2 response status: {hallo2_response.status_code}")
            
            if hallo2_response.status_code == 200 and hallo2_data.get("status") == "finish":
                video_url = hallo2_data.get("url")
                
                print(f"✅ Video generated: {video_url}")
                ws_sender.send_sync({
                    "info": "🎉 Full pipeline completed successfully!",
                    "step": "complete",
                    "session_id": session_id,
                    "result": {
                        "tts_url": tts_result['url'],
                        "video_url": video_url
                    }
                })
                
                return {
                    "status": "ok",
                    "session_id": session_id,
                    "generated_audio": {
                        "url": tts_result['url'],
                        "filename": tts_result['filename'],
                        "duration": tts_result.get('duration', 0),
                        "size_kb": tts_result.get('size_kb', 0)
                    },
                    "generated_video": {
                        "url": video_url,
                        "status": "finish"
                    }
                }, 200
                
            else:
                # Ошибка от Hallo2 сервиса
                hallo2_error = hallo2_data.get("reason", "Unknown Hallo2 error")
                
                print(f"❌ Hallo2 failed: {hallo2_error}")
                ws_sender.send_sync({
                    "error": f"Hallo2 generation failed: {hallo2_error}",
                    "step": "hallo2_error",
                    "hallo2_response": hallo2_data
                })
                
                return {
                    "status": "partial",
                    "session_id": session_id,
                    "generated_audio": {
                        "url": tts_result['url'],
                        "filename": tts_result['filename']
                    },
                    "generated_video": {
                        "status": "failed",
                        "reason": hallo2_error
                    }
                }, 200
                
        except requests.exceptions.Timeout:
            error_msg = "Hallo2 service request timed out (10 minutes)"
            print(f"❌ {error_msg}")
            ws_sender.send_sync({"error": error_msg})
            
            return {
                "status": "partial",
                "session_id": session_id,
                "generated_audio": {
                    "url": tts_result['url']
                },
                "generated_video": {
                    "status": "failed",
                    "reason": error_msg
                }
            }, 200
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to Hallo2 service at {HALLO2_SERVICE_URL}"
            print(f"❌ {error_msg}")
            ws_sender.send_sync({"error": error_msg})
            
            return {
                "status": "partial",
                "session_id": session_id,
                "generated_audio": {
                    "url": tts_result['url']
                },
                "generated_video": {
                    "status": "failed",
                    "reason": error_msg
                }
            }, 200
            
        except Exception as e:
            error_msg = f"Hallo2 service error: {str(e)}"
            print(f"❌ {error_msg}")
            ws_sender.send_sync({"error": error_msg})
            
            return {
                "status": "partial",
                "session_id": session_id,
                "generated_audio": {
                    "url": tts_result['url']
                },
                "generated_video": {
                    "status": "failed",
                    "reason": error_msg
                }
            }, 200
        
    except Exception as e:
        error_msg = f"Generation failed: {str(e)}"
        print(f"❌ {error_msg}")
        ws_sender.send_sync({
            "error": error_msg,
            "step": "error",
            "traceback": str(e)
        })
        
        # В случае ошибки тоже освобождаем ресурсы
        try:
            release_tts_processor()
        except:
            pass
        
        return {"status": "error", "message": error_msg}, 500
    
    finally:
        # Финальная очистка памяти в любом случае
        free_gpu_memory()

@app.route('/generate_local', methods=['POST'])
def generate_local():
    """Debug endpoint для сохранения локально (без S3 и Hallo2)"""
    data = request.get_json()
    
    ws_sender.send_sync({"info": "Local generation request accepted!"})
    
    if not data.get("pdf_filename") or not data.get("wav_filename"):
        error_msg = "Missing pdf_filename or wav_filename"
        return {"status": "error", "message": error_msg}, 400
    
    pdf_filename = data.get("pdf_filename")
    wav_filename = data.get("wav_filename")
    session_id = data.get("session_id", "unknown")
    
    try:
        # Извлекаем текст из PDF
        pdf_url = make_s3_url(pdf_filename)
        text = extract_text_from_pdf_url(pdf_url)
        ws_sender.send_sync({"info": f"Text length: {len(text)}"})
        
        # Получаем URL WAV файла
        wav_url = make_s3_url(wav_filename)
        
        # Инициализируем TTS
        processor = get_tts_processor()
        
        # Сохраняем локально для отладки
        output_filename = f"tts_output/{session_id}_{pdf_filename.replace('.pdf', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        
        ws_sender.send_sync({"info": "Starting TTS generation (local save for debug)..."})
        
        saved_path = processor.generate_and_save_local(
            ref_audio_url=wav_url,
            text_to_synthesize=text,
            output_path=output_filename
        )
        
        ws_sender.send_sync({
            "info": "TTS generation completed!",
            "output_file": saved_path
        })
        
        print(f"✅ TTS completed, file saved to: {saved_path}")
        
        # Освобождаем ресурсы
        release_tts_processor()
        
        return {
            "status": "ok", 
            "local_file": saved_path,
            "session_id": session_id
        }, 200
        
    except Exception as e:
        error_msg = f"TTS generation failed: {str(e)}"
        print(f"❌ {error_msg}")
        ws_sender.send_sync({"error": error_msg})
        
        # Освобождаем ресурсы при ошибке
        release_tts_processor()
        
        return {"status": "error", "message": error_msg}, 500

@app.route('/release_resources', methods=['POST'])
def release_resources():
    """Endpoint для принудительного освобождения ресурсов"""
    print("🔄 Manual resource release requested")
    ws_sender.send_sync({"info": "🔄 Manual resource release requested"})
    
    release_tts_processor()
    free_gpu_memory()
    
    memory_status = {}
    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        allocated = torch.cuda.memory_allocated(0) / 1024**3
        free = total - allocated
        memory_status = {
            "total_gb": total,
            "free_gb": free,
            "allocated_gb": allocated
        }
    
    return {
        "status": "ok",
        "message": "Resources released",
        "gpu_memory": memory_status
    }, 200

@app.route('/status', methods=['GET'])
def status():
    """Получение текущего статуса сервиса"""
    memory_status = {}
    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        allocated = torch.cuda.memory_allocated(0) / 1024**3
        free = total - allocated
        memory_status = {
            "total_gb": round(total, 2),
            "free_gb": round(free, 2),
            "allocated_gb": round(allocated, 2),
            "gpu_name": torch.cuda.get_device_name(0)
        }
    
    return {
        "status": "running",
        "tts_loaded": tts_processor is not None,
        "websocket_available": ws_sender.ws_available,
        "gpu_memory": memory_status if memory_status else "CUDA not available",
        "hallo2_service": HALLO2_SERVICE_URL,
        "s3_host": s3_host,
        "s3_bucket": s3_bucket
    }, 200

@app.route('/finish', methods=['POST'])
def finish():
    """Endpoint для завершения и отправки статуса"""
    data = request.get_json()
    ws_sender.send_sync(data)
    return data, 200

if __name__ == '__main__':
    os.makedirs("tts_output", exist_ok=True)
    
    print("=" * 60)
    print("🚀 Starting Unified Generation Service")
    print(f"📡 WebSocket: {WS_URI} (available: {ws_sender.ws_available})")
    print(f"🎬 Hallo2 Service: {HALLO2_SERVICE_URL}")
    print(f"☁️  S3: {s3_host}/{s3_bucket}")
    print(f"💡 TTS Models: Lazy loading (initialized on first use)")
    print(f"🧹 Resource cleanup: Automatic after TTS phase")
    
    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"🎮 GPU: {torch.cuda.get_device_name(0)}")
        print(f"💾 GPU Memory: {total:.2f} GB total")
    
    print("=" * 60)
    
    # Начальная очистка памяти
    free_gpu_memory()
    
    app.run(host='0.0.0.0', port=5000, debug=True)