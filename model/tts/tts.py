# tts/tts.py

import torch
import soundfile as sf
import whisper
from qwen_tts import Qwen3TTSModel
import os
import warnings
import tempfile
import requests
from io import BytesIO
import boto3
from botocore.client import Config

warnings.filterwarnings("ignore")

class TTSProcessor:
    def __init__(self, ws_sender=None, whisper_model_name="medium", 
                 s3_host=None, s3_bucket=None, s3_access_key=None, s3_secret_key=None):
        """
        Инициализация TTS процессора
        
        Args:
            ws_sender: объект для отправки сообщений в WebSocket
            whisper_model_name: название модели Whisper
            s3_host: хост S3 (например, http://localhost:9000)
            s3_bucket: имя бакета (например, "files")
            s3_access_key: Access Key для MinIO
            s3_secret_key: Secret Key для MinIO
        """
        self.ws_sender = ws_sender
        self.whisper_model_name = whisper_model_name
        self.whisper_model = None
        self.tts_model = None
        self.s3_host = s3_host
        self.s3_bucket = s3_bucket
        
        # Инициализация S3 клиента
        if s3_host and s3_bucket and s3_access_key and s3_secret_key:
            self.s3_client = boto3.client(
                's3',
                endpoint_url=s3_host,
                aws_access_key_id=s3_access_key,
                aws_secret_access_key=s3_secret_key,
                config=Config(signature_version='s3v4'),
                region_name='us-east-1'
            )
            self._log(f"🔗 S3 клиент инициализирован: {s3_host}/{s3_bucket}")
        else:
            self.s3_client = None
            self._log("⚠️ S3 не настроен, загрузка на S3 недоступна")
        
        # Логируем инициализацию
        self._log("🚀 Инициализация TTS процессора")
        if torch.cuda.is_available():
            self._log(f"🎮 GPU: {torch.cuda.get_device_name(0)}")
            self._log(f"📦 VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
        else:
            self._log("⚠️ CUDA не доступна, работаем на CPU")
    
    def _log(self, message, level="info"):
        """Внутренний метод для логирования через ws_sender"""
        print(message)
        if self.ws_sender:
            self.ws_sender.send_sync({
                "type": "tts_log",
                "level": level,
                "message": message
            })
    
    def _load_whisper(self):
        """Загрузка модели Whisper (ленивая загрузка)"""
        if self.whisper_model is None:
            self._log("📝 Загрузка модели Whisper...")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.whisper_model = whisper.load_model(self.whisper_model_name, device=device)
            self._log(f"✅ Модель Whisper загружена на {device}")
        return self.whisper_model
    
    def _load_tts_model(self):
        """Загрузка TTS модели (ленивая загрузка)"""
        if self.tts_model is None:
            self._log("🤖 Загрузка Qwen3-TTS модели...")
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            self.tts_model = Qwen3TTSModel.from_pretrained(
                "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                device_map=device,
                dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            )
            self._log("✅ Модель TTS загружена")
        return self.tts_model
    
    def _download_audio_from_url(self, audio_url):
        """Скачивание аудио файла по URL"""
        self._log(f"📥 Скачивание аудио с URL: {audio_url}")
        
        try:
            response = requests.get(audio_url, timeout=30)
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_file.write(response.content)
                tmp_path = tmp_file.name
            
            self._log(f"✅ Аудио скачано, размер: {len(response.content) / 1024:.1f} KB")
            return tmp_path
            
        except Exception as e:
            self._log(f"❌ Ошибка скачивания аудио: {e}", level="error")
            raise
    
    def generate_speech(self, ref_audio_url, text_to_synthesize):
        """Генерация речи с клонированием голоса"""
        temp_audio_path = None
        
        try:
            self._log("=" * 50)
            self._log("🎤 НАЧАЛО TTS ГЕНЕРАЦИИ")
            self._log(f"📝 Текст для синтеза (первые 150 символов): {text_to_synthesize[:150]}...")
            self._log(f"📏 Длина текста: {len(text_to_synthesize)} символов")
            
            # Шаг 1: Скачиваем аудио по URL
            self._log("📥 [1/4] Скачивание аудио образца...")
            temp_audio_path = self._download_audio_from_url(ref_audio_url)
            
            # Шаг 2: Распознаем текст из аудио через Whisper
            self._log("📝 [2/4] Распознавание речи из образца...")
            whisper_model = self._load_whisper()
            result = whisper_model.transcribe(temp_audio_path, language="ru")
            ref_text = result["text"].strip()
            self._log(f"✅ Распознанный текст: \"{ref_text[:150]}{'...' if len(ref_text) > 150 else ''}\"")
            
            # Шаг 3: Загружаем TTS модель
            self._log("🤖 [3/4] Загрузка TTS модели...")
            tts_model = self._load_tts_model()
            
            # Шаг 4: Генерируем речь
            self._log("🎤 [4/4] Синтез речи с клонированием голоса...")
            self._log("⏳ Это может занять некоторое время...")
            
            wavs, sample_rate = tts_model.generate_voice_clone(
                text=text_to_synthesize,
                language="Russian",
                ref_audio=temp_audio_path,
                ref_text=ref_text,
            )
            
            audio_data = wavs[0]
            duration = len(audio_data) / sample_rate
            
            self._log(f"✨ TTS генерация завершена!")
            self._log(f"   Длительность: {duration:.1f} сек")
            self._log(f"   Частота: {sample_rate} Гц")
            self._log(f"   Форма аудио: {audio_data.shape}")
            self._log("=" * 50)
            
            return audio_data, sample_rate
            
        except Exception as e:
            self._log(f"❌ Ошибка в TTS генерации: {e}", level="error")
            raise
            
        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                os.unlink(temp_audio_path)
                self._log("🗑️ Временный аудио файл удален")
    
    def generate_and_save_local(self, ref_audio_url, text_to_synthesize, output_path):
        """Генерация речи с сохранением в локальный файл для отладки"""
        self._log(f"💾 Будет сохранено локально: {output_path}")
        
        audio_data, sample_rate = self.generate_speech(ref_audio_url, text_to_synthesize)
        
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        sf.write(output_path, audio_data, sample_rate)
        
        self._log(f"✅ Аудио сохранено локально: {output_path}")
        self._log(f"   Размер файла: {os.path.getsize(output_path) / 1024:.1f} KB")
        
        return output_path
    
    def generate_and_upload_to_s3(self, ref_audio_url, text_to_synthesize, s3_filename):
        """
        Генерация речи и загрузка в S3
        
        Args:
            ref_audio_url: URL ссылка на .wav файл с образцом голоса
            text_to_synthesize: текст для синтеза речи
            s3_filename: имя файла для сохранения в S3
            
        Returns:
            dict: информация о загруженном файле (url, bucket, filename)
        """
        if not self.s3_client:
            raise Exception("S3 клиент не инициализирован. Проверьте настройки подключения к S3.")
        
        self._log(f"☁️ Будет загружено в S3: {self.s3_bucket}/{s3_filename}")
        
        # Генерируем речь во временный файл
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            temp_path = tmp_file.name
        
        try:
            # Сохраняем во временный файл
            audio_data, sample_rate = self.generate_speech(ref_audio_url, text_to_synthesize)
            sf.write(temp_path, audio_data, sample_rate)
            
            file_size = os.path.getsize(temp_path)
            self._log(f"📤 Загрузка в S3: {s3_filename} (размер: {file_size / 1024:.1f} KB)")
            
            # Загружаем в S3
            with open(temp_path, 'rb') as f:
                self.s3_client.upload_fileobj(
                    f,
                    self.s3_bucket,
                    s3_filename,
                    ExtraArgs={'ContentType': 'audio/wav'}
                )
            
            # Формируем URL
            file_url = f"{self.s3_host}/{self.s3_bucket}/{s3_filename}"
            
            self._log(f"✅ Файл успешно загружен в S3")
            self._log(f"   URL: {file_url}")
            self._log(f"   Bucket: {self.s3_bucket}")
            self._log(f"   Filename: {s3_filename}")
            
            return {
                "success": True,
                "url": file_url,
                "bucket": self.s3_bucket,
                "filename": s3_filename,
                "size_kb": file_size / 1024,
                "duration": len(audio_data) / sample_rate
            }
            
        except Exception as e:
            self._log(f"❌ Ошибка при загрузке в S3: {e}", level="error")
            raise
            
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                self._log("🗑️ Временный файл удален")


# Функция-обертка для простого использования
def generate_speech_from_url(ref_audio_url, text_to_synthesize, ws_sender=None):
    """Простая функция-обертка для генерации речи"""
    processor = TTSProcessor(ws_sender=ws_sender)
    return processor.generate_speech(ref_audio_url, text_to_synthesize)