package handlers

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"path/filepath"
	"time"

	"github.com/gofiber/fiber/v2"
	"github.com/minio/minio-go"
)

type Cloud struct {
	Client *minio.Client
	Bucket string
}

func (cl *Cloud) Upload(c *fiber.Ctx) error {
	fileType := c.Params("type")

	// Валидация типа файла
	if fileType != "jpg" && fileType != "wav" && fileType != "pdf" {
		return c.Status(400).JSON(fiber.Map{
			"error": "Неподдерживаемый тип файла",
		})
	}

	// Получаем файл из формы
	file, err := c.FormFile("file")
	if err != nil {
		return c.Status(400).JSON(fiber.Map{
			"error": "Не удалось получить файл",
		})
	}

	// Проверяем расширение файла
	ext := filepath.Ext(file.Filename)
	validExt := false
	switch fileType {
	case "jpg":
		validExt = ext == ".jpg" || ext == ".jpeg"
	case "wav":
		validExt = ext == ".wav"
	case "pdf":
		validExt = ext == ".pdf"
	}

	if !validExt {
		return c.Status(400).JSON(fiber.Map{
			"error": "Неверное расширение файла",
		})
	}

	// Открываем файл из формы
	src, err := file.Open()
	if err != nil {
		return c.Status(500).JSON(fiber.Map{
			"error": "Не удалось открыть файл",
		})
	}
	defer src.Close()

	// Читаем содержимое файла в память
	fileContent, err := io.ReadAll(src)
	if err != nil {
		return c.Status(500).JSON(fiber.Map{
			"error": "Не удалось прочитать файл",
		})
	}

	// Создаем объект для загрузки в S3
	reader := bytes.NewReader(fileContent)
	
	// Генерируем уникальное имя для файла (опционально)
	objectName := file.Filename
	// objectName := fmt.Sprintf("%d_%s", time.Now().Unix(), file.Filename) // с префиксом времени
	
	// Загружаем файл в S3
	_, err = cl.Client.PutObject(
		cl.Bucket,
		objectName,
		reader,
		int64(len(fileContent)),
		minio.PutObjectOptions{
			ContentType: file.Header.Get("Content-Type"),
		},
	)
	if err != nil {
		return c.Status(500).JSON(fiber.Map{
			"error": "Не удалось загрузить файл в хранилище",
		})
	}

	log.Printf("Файл %s успешно загружен в S3 bucket %s", file.Filename, cl.Bucket)

	// Возвращаем URL для доступа к файлу (опционально)
	// Получаем presigned URL для доступа к файлу
	reqParams := make(url.Values)
	presignedURL, err := cl.Client.PresignedGetObject(cl.Bucket, objectName, time.Hour*24, reqParams)
	var fileURL string
	if err == nil {
		fileURL = presignedURL.String()
	} else {
		fileURL = ""
	}

	return c.JSON(fiber.Map{
		"message":  "Файл успешно загружен",
		"filename": file.Filename,
		"bucket":   cl.Bucket,
		"url":      fileURL,
	})
}

// Generate проксирует запрос на внешний сервис генерации видео
func Generate(c *fiber.Ctx) error {
	// Получаем данные от клиента
	var requestData map[string]interface{}
	if err := c.BodyParser(&requestData); err != nil {
		log.Printf("Ошибка парсинга JSON: %v", err)
		return c.Status(400).JSON(fiber.Map{
			"error": "Неверный формат запроса",
		})
	}

	log.Printf("Получен запрос на генерацию с данными: %v", requestData)

	// Сериализуем данные в JSON
	jsonData, err := json.Marshal(requestData)
	if err != nil {
		log.Printf("Ошибка маршалинга JSON: %v", err)
		return c.Status(500).JSON(fiber.Map{
			"error": "Ошибка подготовки запроса",
		})
	}

	// Создаем HTTP клиент с таймаутом
	client := &http.Client{
		Timeout: 0 * time.Second,
	}

	// Создаем запрос к внешнему сервису
	externalURL := "http://localhost:5000/generate"
	req, err := http.NewRequest("POST", externalURL, bytes.NewBuffer(jsonData))
	if err != nil {
		log.Printf("Ошибка создания запроса: %v", err)
		return c.Status(500).JSON(fiber.Map{
			"error": "Ошибка создания запроса к сервису генерации",
		})
	}

	// Устанавливаем заголовки
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	// Отправляем запрос
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("Ошибка отправки запроса к %s: %v", externalURL, err)
		return c.Status(502).JSON(fiber.Map{
			"error": "Сервис генерации видео недоступен",
		})
	}
	defer resp.Body.Close()

	// Читаем ответ от внешнего сервиса
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Ошибка чтения ответа: %v", err)
		return c.Status(500).JSON(fiber.Map{
			"error": "Ошибка чтения ответа от сервиса генерации",
		})
	}

	log.Printf("Ответ от сервиса генерации (статус %d): %s", resp.StatusCode, string(body))

	// Проверяем статус ответа
	if resp.StatusCode != http.StatusOK {
		return c.Status(resp.StatusCode).JSON(fiber.Map{
			"error":   "Ошибка в сервисе генерации",
			"details": string(body),
		})
	}

	// Парсим ответ от внешнего сервиса
	var responseData map[string]interface{}
	if err := json.Unmarshal(body, &responseData); err != nil {
		log.Printf("Ошибка парсинга ответа JSON: %v", err)
		// Если не JSON, возвращаем как есть
		return c.Status(resp.StatusCode).Send(body)
	}

	// Возвращаем ответ клиенту
	return c.JSON(responseData)
}
