package storage

import (
	"fmt"

	"github.com/minio/minio-go"
)

// InitMinioClient инициализирует подключение к MinIO и проверяет наличие бакета voices.
func InitMinioClient(endpoint, bucketName string) (*minio.Client, error) {

	// Конфигурация подключения к MinIO
	accessKey := "minio-user"
	secretKey := "minio-password"
	useSSL := false

	// Создаем клиент
	client, err := minio.New(endpoint, accessKey, secretKey, useSSL)
	if err != nil {
		return nil, fmt.Errorf("не удалось создать MinIO клиент: %w", err)
	}

	exists, err := client.BucketExists(bucketName)
	if err != nil {
		return nil, fmt.Errorf("ошибка проверки бакета 'voices': %w", err)
	}

	if !exists {
		return nil, fmt.Errorf("бакет 'voices' не существует")
	}

	return client, nil
}
