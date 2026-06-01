package handlers

import (
	"log"
	"sync"

	"github.com/gofiber/websocket/v2"
)

// Client представляет подключенного клиента
type Client struct {
	Conn *websocket.Conn
	ID   string
}

// Hub управляет всеми WebSocket соединениями
type Hub struct {
	// Регистрация новых клиентов
	register chan *Client

	// Отмена регистрации клиентов
	unregister chan *Client

	// Все подключенные клиенты
	clients map[*Client]bool

	// Мьютекс для безопасного доступа к клиентам
	mu sync.RWMutex
}

// NewHub создает новый Hub
func NewHub() *Hub {
	return &Hub{
		register:   make(chan *Client),
		unregister: make(chan *Client),
		clients:    make(map[*Client]bool),
	}
}

// Run запускает основной цикл Hub
func (h *Hub) Run() {
	for {
		select {
		case client := <-h.register:
			h.mu.Lock()
			h.clients[client] = true
			h.mu.Unlock()
			//log.Printf("Клиент %s подключен. Всего клиентов: %d", client.ID, len(h.clients))

		case client := <-h.unregister:
			h.mu.Lock()
			if _, ok := h.clients[client]; ok {
				delete(h.clients, client)
				client.Conn.Close()
				//log.Printf("Клиент %s отключен. Всего клиентов: %d", client.ID, len(h.clients))
			}
			h.mu.Unlock()
		}
	}
}

// BroadcastToAllExcept отправляет сообщение всем клиентам, кроме указанного
func (h *Hub) BroadcastToAllExcept(sender *Client, messageType int, message []byte) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	sentCount := 0
	for client := range h.clients {
		// Пропускаем отправителя
		if client == sender {
			continue
		}

		// Отправляем сообщение
		if err := client.Conn.WriteMessage(messageType, message); err != nil {
			log.Printf("Ошибка отправки сообщения клиенту %s: %v", client.ID, err)
			// Отправляем клиента на удаление при ошибке
			go func(c *Client) {
				h.unregister <- c
			}(client)
		} else {
			sentCount++
		}
	}
	log.Printf("Сообщение отправлено %d клиентам (исключая отправителя %s)", sentCount, sender.ID)
}

// BroadcastToAll отправляет сообщение всем подключенным клиентам
func (h *Hub) BroadcastToAll(messageType int, message []byte) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	for client := range h.clients {
		if err := client.Conn.WriteMessage(messageType, message); err != nil {
			log.Printf("Ошибка отправки сообщения клиенту %s: %v", client.ID, err)
		}
	}
}

// GetClientsCount возвращает количество подключенных клиентов
func (h *Hub) GetClientsCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

// Глобальный экземпляр Hub
var hub = NewHub()

// init инициализирует Hub при запуске
func init() {
	go hub.Run()
}

// WebSocketHandler обрабатывает WebSocket соединения
func WebSocketHandler(c *websocket.Conn) {
	// Генерируем уникальный ID для клиента
	clientID := c.Params("id")
	if clientID == "" {
		// Если ID не передан, используем RemoteAddr
		clientID = c.RemoteAddr().String()
	}

	// Создаем нового клиента
	client := &Client{
		Conn: c,
		ID:   clientID,
	}

	// Регистрируем клиента в хабе
	hub.register <- client

	// Отправляем приветственное сообщение
	welcomeMsg := []byte("Добро пожаловать в WebSocket сервер! Ваш ID: " + clientID)
	if err := c.WriteMessage(websocket.TextMessage, welcomeMsg); err != nil {
		log.Println("Ошибка при отправке приветственного сообщения:", err)
		hub.unregister <- client
		return
	}

	// Закрываем соединение при выходе из функции
	defer func() {

		// Удаляем клиента из хаба
		hub.unregister <- client
		log.Printf("WebSocket соединение для клиента %s закрыто", clientID)
	}()

	// Бесконечный цикл для чтения сообщений
	for {
		// Читаем сообщение от клиента
		messageType, msg, err := c.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Printf("Ошибка чтения WebSocket сообщения от %s: %v", clientID, err)
			}
			break
		}

		// Логируем полученное сообщение
		log.Printf("Получено сообщение от %s: %s", clientID, msg)

		// Отправляем сообщение ВСЕМ КРОМЕ отправителя
		hub.BroadcastToAllExcept(client, messageType, msg)

		// Опционально: можно отправить подтверждение отправителю
		// confirmationMsg := []byte("Сообщение доставлено " + len(hub.clients) + " получателям")
		// if err := c.WriteMessage(websocket.TextMessage, confirmationMsg); err != nil {
		// 	log.Println("Ошибка при отправке подтверждения:", err)
		// }
	}
}

// BroadcastToAllClients рассылает сообщение всем подключенным клиентам
func BroadcastToAllClients(message string) {
	hub.BroadcastToAll(websocket.TextMessage, []byte(message))
	log.Printf("Broadcast сообщение: %s", message)
}

// BroadcastToAllClientsExcept рассылает сообщение всем клиентам, кроме указанного
func BroadcastToAllClientsExcept(senderID string, message string) {
	hub.mu.RLock()
	defer hub.mu.RUnlock()

	for client := range hub.clients {
		if client.ID == senderID {
			continue
		}
		if err := client.Conn.WriteMessage(websocket.TextMessage, []byte(message)); err != nil {
			log.Printf("Ошибка отправки broadcast клиенту %s: %v", client.ID, err)
		}
	}
}

// GetConnectedClients возвращает список ID всех подключенных клиентов
func GetConnectedClients() []string {
	hub.mu.RLock()
	defer hub.mu.RUnlock()

	clients := make([]string, 0, len(hub.clients))
	for client := range hub.clients {
		clients = append(clients, client.ID)
	}
	return clients
}
