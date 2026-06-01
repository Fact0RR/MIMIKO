let ws;
let sessionId = null;
let uploadedFiles = {
    jpg: false,
    wav: false,
    pdf: false
};
let uploadedFileNames = {
    jpg: null,
    wav: null,
    pdf: null
};
let videoUrl = null;
let isGenerating = false;

// Генерация уникального ID
function getSessionId() {
    let id = localStorage.getItem('sessionId');
    if (!id) {
        id = 'user_' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('sessionId', id);
    }
    return id;
}

function generateFileName(originalName, extension) {
    const timestamp = Date.now();
    const formattedTime = new Date(timestamp).toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const id = getSessionId();
    return `${id}_${formattedTime}.${extension}`;
}

function updateGenerateButton() {
    const generateBtn = document.getElementById('generateBtn');
    const allUploaded = uploadedFiles.jpg && uploadedFiles.wav && uploadedFiles.pdf;
    
    if (allUploaded && !isGenerating) {
        generateBtn.disabled = false;
        addLog('✅ Все файлы загружены! Кнопка генерации разблокирована', 'success');
        document.getElementById('generationStatus').innerHTML = '<span class="status-ready">✓ Все файлы готовы к генерации</span>';
    } else if (isGenerating) {
        generateBtn.disabled = true;
    } else {
        generateBtn.disabled = true;
        const missing = [];
        if (!uploadedFiles.jpg) missing.push('JPG');
        if (!uploadedFiles.wav) missing.push('WAV');
        if (!uploadedFiles.pdf) missing.push('PDF');
        document.getElementById('generationStatus').innerHTML = `<span class="status-waiting">⏳ Ожидаются файлы: ${missing.join(', ')}</span>`;
    }
}

function updateFileStatus(type, success) {
    const statusDiv = document.getElementById(`${type}Status`);
    if (success) {
        statusDiv.innerHTML = '<span class="status-success">✓ Загружено</span>';
        uploadedFiles[type] = true;
    } else {
        statusDiv.innerHTML = '<span class="status-error">✗ Ошибка загрузки</span>';
        uploadedFiles[type] = false;
    }
    updateGenerateButton();
}

function addLog(message, type = 'info') {
    const logsDiv = document.getElementById('logs');
    const logEntry = document.createElement('div');
    logEntry.className = `log-entry log-${type}`;
    logEntry.textContent = `${new Date().toLocaleTimeString()} - ${message}`;
    
    // Стили для разных типов сообщений
    if (type === 'error') {
        logEntry.style.color = '#ff6b6b';
        logEntry.style.fontWeight = 'bold';
    } else if (type === 'success') {
        logEntry.style.color = '#51cf66';
    } else if (type === 'warning') {
        logEntry.style.color = '#ffd43b';
    } else if (type === 'info') {
        logEntry.style.color = '#74c0fc';
    }
    
    logsDiv.appendChild(logEntry);
    logsDiv.scrollTop = logsDiv.scrollHeight;
}

function showVideo(videoUrl) {
    // Останавливаем анимацию ожидания
    isGenerating = false;
    
    // Обновляем статус генерации
    const generationStatus = document.getElementById('generationStatus');
    generationStatus.innerHTML = '<span class="status-complete">✨ Видео успешно сгенерировано!</span>';
    
    // Показываем видео плеер
    const videoSection = document.getElementById('videoSection');
    const videoPlayer = document.getElementById('videoPlayer');
    
    videoSection.style.display = 'block';
    videoPlayer.src = videoUrl;
    videoPlayer.load();
    
    // Обновляем кнопку генерации
    const generateBtn = document.getElementById('generateBtn');
    generateBtn.disabled = true;
    generateBtn.textContent = '✓ Видео готово';
    
    // Добавляем кнопку скачивания
    addDownloadButton(videoUrl);
    
    // Прокручиваем к видео
    videoSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function showError(reason) {
    // Останавливаем анимацию ожидания
    isGenerating = false;
    
    // Декодируем Unicode строку (например: \u0430\u0432\u0442\u043e\u0440)
    let decodedReason = reason;
    try {
        decodedReason = decodeURIComponent(reason);
        // Если не помогло, пробуем JSON.parse
        if (decodedReason.includes('\\u')) {
            decodedReason = JSON.parse(`"${reason}"`);
        }
    } catch (e) {
        decodedReason = reason;
    }
    
    // Обновляем статус генерации с ошибкой
    const generationStatus = document.getElementById('generationStatus');
    generationStatus.innerHTML = `<span class="status-error">❌ Ошибка генерации: ${decodedReason}</span>`;
    
    // Обновляем кнопку генерации для повторной попытки
    const generateBtn = document.getElementById('generateBtn');
    generateBtn.disabled = false;
    generateBtn.textContent = '🎬 Повторить генерацию';
    
    // Добавляем сообщение об ошибке в логи
    addLog(`❌ Генерация видео не удалась!`, 'error');
    addLog(`📛 Причина: ${decodedReason}`, 'error');
    
    // Создаем выделенное сообщение об ошибке в логах
    const logsDiv = document.getElementById('logs');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'log-entry log-error';
    errorDiv.style.backgroundColor = '#2d1f1f';
    errorDiv.style.borderLeft = '4px solid #ff6b6b';
    errorDiv.style.padding = '8px';
    errorDiv.style.margin = '5px 0';
    errorDiv.style.borderRadius = '5px';
    errorDiv.innerHTML = `
        <strong>❌ ОШИБКА ГЕНЕРАЦИИ</strong><br>
        Причина: ${decodedReason}
    `;
    logsDiv.appendChild(errorDiv);
    logsDiv.scrollTop = logsDiv.scrollHeight;
}

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        addLog('✅ Соединение с WebSocket установлено', 'success');
    };
    
    ws.onmessage = (event) => {
        //addLog(`📨 Получено: ${event.data}`, 'info');
        
        try {
            const data = JSON.parse(event.data);
            
            // Проверяем статус finish (успешная генерация)
            if (data.status === 'finish' && data.url) {
                addLog(`🎉 Видео успешно сгенерировано!`, 'success');
                addLog(`🔗 Ссылка на видео: ${data.url}`, 'success');
                showVideo(data.url);
            } 
            // Проверяем статус failed (ошибка генерации)
            else if (data.status === 'failed') {
                const reason = data.reason || 'Неизвестная ошибка';
                showError(reason);
            }
            // Старый формат для обратной совместимости
            else if (data.sender === 'finisher' && data.status) {
                // Если status содержит URL (не начинается с http, то это может быть URL)
                if (data.status.startsWith('http')) {
                    addLog(`🎉 Видео готово (старый формат)!`, 'success');
                    showVideo(data.status);
                } else {
                    showError(data.status);
                }
            }
            // Обновление прогресса
            else if (data.progress) {
                addLog(`📊 Прогресс генерации: ${data.progress}%`, 'info');
                if (isGenerating) {
                    document.getElementById('generationStatus').innerHTML = `<span class="status-processing">🔄 Генерация видео: ${data.progress}%</span>`;
                }
            }
            // Логирование других сообщений
            else {
                addLog(`📝 Сообщение: ${JSON.stringify(data)}`, 'info');
            }
            
        } catch (e) {
            // Если не JSON, выводим как обычное сообщение
            addLog(`📝 Текст: ${event.data}`, 'info');
        }
    };
    
    ws.onerror = (error) => {
        addLog(`❌ Ошибка WebSocket: ${error}`, 'error');
        if (isGenerating) {
            showError('Ошибка WebSocket соединения');
        }
    };
    
    ws.onclose = () => {
        addLog('🔌 WebSocket соединение закрыто', 'warning');
        if (isGenerating) {
            showError('WebSocket соединение было закрыто');
        }
        setTimeout(connectWebSocket, 3000);
    };
}

function addDownloadButton(videoUrl) {
    // Удаляем старую кнопку если есть
    const existingBtn = document.getElementById('downloadVideoBtn');
    if (existingBtn) existingBtn.remove();
    
    // Создаем кнопку скачивания
    const videoSection = document.getElementById('videoSection');
    const downloadBtn = document.createElement('button');
    downloadBtn.id = 'downloadVideoBtn';
    downloadBtn.textContent = '📥 Скачать видео';
    downloadBtn.className = 'download-btn';
    downloadBtn.onclick = () => {
        const a = document.createElement('a');
        a.href = videoUrl;
        a.download = videoUrl.split('/').pop();
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        addLog('📥 Загрузка видео началась', 'success');
    };
    
    videoSection.appendChild(downloadBtn);
}

async function uploadFile(type) {
    let inputId, fileType, extension;
    switch(type) {
        case 'jpg':
            inputId = 'jpgInput';
            fileType = 'jpg';
            extension = 'jpg';
            break;
        case 'wav':
            inputId = 'wavInput';
            fileType = 'wav';
            extension = 'wav';
            break;
        case 'pdf':
            inputId = 'pdfInput';
            fileType = 'pdf';
            extension = 'pdf';
            break;
    }
    
    const input = document.getElementById(inputId);
    const file = input.files[0];
    
    if (!file) {
        addLog(`⚠️ Выберите ${fileType.toUpperCase()} файл для загрузки`, 'warning');
        return;
    }
    
    // Генерируем новое имя файла
    const newFileName = generateFileName(file.name, extension);
    
    // Сохраняем сгенерированное имя файла
    uploadedFileNames[type] = newFileName;
    
    // Создаем новый File объект с новым именем
    const renamedFile = new File([file], newFileName, { type: file.type });
    
    const formData = new FormData();
    formData.append('file', renamedFile);
    
    addLog(`📤 Загрузка ${renamedFile.name}...`, 'info');
    updateFileStatus(type, false);
    
    try {
        const response = await fetch(`/upload/${fileType}`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (response.ok) {
            addLog(`✅ Файл ${renamedFile.name} успешно загружен`, 'success');
            updateFileStatus(type, true);
        } else {
            addLog(`❌ Ошибка: ${result.error}`, 'error');
            updateFileStatus(type, false);
            uploadedFileNames[type] = null;
        }
    } catch (error) {
        addLog(`❌ Ошибка при загрузке: ${error.message}`, 'error');
        updateFileStatus(type, false);
        uploadedFileNames[type] = null;
    }
}

async function generateVideo() {
    if (isGenerating) {
        addLog('⚠️ Генерация уже выполняется, подождите...', 'warning');
        return;
    }
    
    const generateBtn = document.getElementById('generateBtn');
    isGenerating = true;
    generateBtn.disabled = true;
    generateBtn.textContent = '⏳ Генерация видео...';
    
    // Показываем анимацию ожидания
    const generationStatus = document.getElementById('generationStatus');
    generationStatus.innerHTML = '<span class="status-processing">🔄 Генерация видео, пожалуйста, подождите...</span>';
    
    addLog('🚀 Запрос на генерацию видео отправлен', 'info');
    
    // Проверяем, что все файлы загружены
    if (!uploadedFileNames.jpg || !uploadedFileNames.wav || !uploadedFileNames.pdf) {
        addLog('❌ Не все файлы успешно загружены', 'error');
        generateBtn.disabled = false;
        generateBtn.textContent = '🎬 Сгенерировать видео';
        generationStatus.innerHTML = '<span class="status-error">❌ Не все файлы загружены</span>';
        isGenerating = false;
        return;
    }
    
    // Скрываем предыдущее видео если было
    const videoSection = document.getElementById('videoSection');
    videoSection.style.display = 'none';
    const videoPlayer = document.getElementById('videoPlayer');
    videoPlayer.src = '';
    
    // Формируем данные для отправки
    const requestData = {
        jpg_filename: uploadedFileNames.jpg,
        wav_filename: uploadedFileNames.wav,
        pdf_filename: uploadedFileNames.pdf,
        session_id: getSessionId()
    };
    
    addLog(`📦 Отправка данных на сервер: ${JSON.stringify(requestData, null, 2)}`, 'info');
    
    try {
        const response = await fetch('/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            addLog(`✅ Запрос на генерацию принят: ${result.message || 'Успешно'}`, 'success');
            addLog('⏳ Ожидайте результат через WebSocket...', 'info');
        } else {
            addLog(`❌ Ошибка при генерации: ${result.error || 'Неизвестная ошибка'}`, 'error');
            generateBtn.disabled = false;
            generateBtn.textContent = '🎬 Сгенерировать видео';
            generationStatus.innerHTML = '<span class="status-error">❌ Ошибка генерации</span>';
            isGenerating = false;
        }
    } catch (error) {
        addLog(`❌ Ошибка при запросе генерации: ${error.message}`, 'error');
        generateBtn.disabled = false;
        generateBtn.textContent = '🎬 Сгенерировать видео';
        generationStatus.innerHTML = '<span class="status-error">❌ Ошибка соединения</span>';
        isGenerating = false;
    }
}

// Функция для очистки логов
function clearLogs() {
    const logsDiv = document.getElementById('logs');
    logsDiv.innerHTML = '';
    addLog('🧹 Логи очищены', 'info');
}

// Добавляем кнопку очистки логов
function addClearLogsButton() {
    const logsSection = document.querySelector('.logs-section');
    const clearBtn = document.createElement('button');
    clearBtn.textContent = '🗑️ Очистить логи';
    clearBtn.className = 'clear-logs-btn';
    clearBtn.onclick = clearLogs;
    clearBtn.style.marginTop = '10px';
    clearBtn.style.padding = '8px 15px';
    clearBtn.style.backgroundColor = '#6c757d';
    clearBtn.style.color = 'white';
    clearBtn.style.border = 'none';
    clearBtn.style.borderRadius = '5px';
    clearBtn.style.cursor = 'pointer';
    logsSection.appendChild(clearBtn);
}

// Инициализация
connectWebSocket();
setTimeout(addClearLogsButton, 100);