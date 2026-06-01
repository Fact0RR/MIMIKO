package main

import (
	"diplom/handlers"
	"diplom/storage"
	"log"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/cors"
	"github.com/gofiber/websocket/v2"
)

func main() {
	endpoint := "localhost:9000"
	bucketName := "files"
	s3Client, err := storage.InitMinioClient(endpoint, bucketName)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("S3 подключен, хост:%s, bucket:%s", endpoint, bucketName)

	app := fiber.New(fiber.Config{
		BodyLimit: 200 * 1024 * 1024,
		// Увеличиваем конвейер для больших файлов
		StreamRequestBody: true, // Включаем стриминг для больших файлов
	})

	// CORS middleware
	app.Use(cors.New(cors.Config{
		AllowOrigins: "*",
		AllowMethods: "GET,POST,PUT,DELETE",
		AllowHeaders: "Content-Type",
	}))

	// Статические файлы из папки front
	app.Static("/static", "./front")

	// Главная страница
	app.Get("/", func(c *fiber.Ctx) error {
		return c.SendFile("./front/index.html")
	})

	// WebSocket обработчик с поддержкой ID
	app.Get("/ws/:id?", websocket.New(func(c *websocket.Conn) {
		handlers.WebSocketHandler(c)
	}))

	cloud := handlers.Cloud{
		Client: s3Client,
		Bucket: bucketName,
	}
	// Загрузка файлов
	app.Post("/upload/:type", cloud.Upload)

	app.Post("/generate", handlers.Generate)

	// Запуск сервера
	log.Println("Сервер запущен на http://localhost:8000")
	if err := app.Listen(":8000"); err != nil {
		log.Fatal("Ошибка при запуске сервера:", err)
	}
}
