# model/Dockerfile
FROM ollama/ollama:latest

# Установка NVIDIA Container Toolkit зависимостей
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tini \
        curl \
        nvidia-container-toolkit; \
    rm -rf /var/lib/apt/lists/*

EXPOSE 11434/tcp

# Добавляем поддержку GPU
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

ENTRYPOINT ["tini","--","ollama"]
CMD ["serve"]