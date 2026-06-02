.PHONY: init start stop restart

init:
	# Удаляем старые данные
	rm -rf model/leep_sync_photo/hallo2
	rm -rf model/leep_sync_photo/venv
	rm -rf model/venv_model
	
	# скачиваем репозиторий hallo2 и вставляем в проект
	git clone https://github.com/fudan-generative-vision/hallo2.git
	rm -rf hallo2/.git
	mkdir -p model/leep_sync_photo
	mv hallo2 model/leep_sync_photo/
	
	# Создаем виртуальное окружение в model/leep_sync_photo
	cd model/leep_sync_photo && \
	python3 -m venv venv && \
	. venv/bin/activate && \
	pip install --upgrade pip && \
	pip install -r requirements.txt && \
	pip install huggingface-hub
	
	# Создаем виртуальное окружение для model
	cd model && \
	python3 -m venv venv_model && \
	. venv_model/bin/activate && \
	pip install --upgrade pip && \
	pip install -r requirements.txt
	
	# Скачиваем предобученные модели Hallo2
	cd model/leep_sync_photo/hallo2 && \
	. ../venv/bin/activate && \
	huggingface-cli download fudan-generative-ai/hallo2 --local-dir pretrained_models

start:
	# Запускаем MinIO в Docker Compose
	docker-compose up -d minio
	
	# Ждем готовности MinIO
	sleep 5
	
	# Запускаем hallo.py в фоне
	cd model/leep_sync_photo && \
	. venv/bin/activate && \
	nohup python3 hallo.py &
	
	# Запускаем api.py в фоне
	cd model && \
	. venv_model/bin/activate && \
	nohup python3 api.py &
	
	# Запускаем Go сервис
	cd service && \
	go run main.py &
	
	echo "All services started"

stop:
	# Останавливаем все процессы
	pkill -f "hallo.py"
	pkill -f "api.py"
	pkill -f "go run main.go"
	
	# Останавливаем MinIO
	docker-compose down
	
	echo "All services stopped"

restart: stop start