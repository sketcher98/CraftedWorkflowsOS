package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/http/httptest"
	"sync"
	"time"
)

type MockProvider struct {
	mu           sync.Mutex
	behavior     string
	callCount    int
	chunks       []string
	chunkDelay   time.Duration
	hangAfter    int
	server       *httptest.Server
	url          string
}

func NewMockProvider() *MockProvider {
	mp := &MockProvider{}
	mp.server = httptest.NewServer(http.HandlerFunc(mp.handleRequest))
	mp.url = mp.server.URL
	return mp
}

func (m *MockProvider) URL() string {
	return m.url
}

func (m *MockProvider) SetBehavior(behavior string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.behavior = behavior
	m.callCount = 0
}

func (m *MockProvider) SetStreamingChunks(chunks []string, chunkDelay time.Duration, hangAfter int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.behavior = "stream"
	m.chunks = chunks
	m.chunkDelay = chunkDelay
	m.hangAfter = hangAfter
	m.callCount = 0
}

func (m *MockProvider) Close() {
	m.server.Close()
}

func (m *MockProvider) handleRequest(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	m.callCount++
	behavior := m.behavior
	chunks := m.chunks
	chunkDelay := m.chunkDelay
	hangAfter := m.hangAfter
	callCount := m.callCount
	m.mu.Unlock()

	log.Printf("MockProvider: call=%d behavior=%s", callCount, behavior)

	w.Header().Set("Content-Type", "application/json")

	switch behavior {
	case "success":
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"id":      "chatcmpl-mock",
			"object":  "chat.completion",
			"model":   "mock-model",
			"choices": []map[string]interface{}{{"index": 0, "message": map[string]string{"role": "assistant", "content": "Hello from mock!"}, "finish_reason": "stop"}},
			"usage":   map[string]int{"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
		})

	case "stream":
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
			return
		}

		for i, chunk := range chunks {
			if i >= hangAfter && hangAfter > 0 {
				log.Printf("MockProvider: hanging after %d chunks", i)
				select {} // Hang forever - KEY for SPEC #42
			}

			data := fmt.Sprintf("data: %s\n\n", chunk)
			w.Write([]byte(data))
			if flusher != nil {
				flusher.Flush()
			}
			if chunkDelay > 0 {
				time.Sleep(chunkDelay)
			}
		}
		w.Write([]byte("data: [DONE]\n\n"))
		if flusher != nil {
			flusher.Flush()
		}

	case "timeout":
		time.Sleep(30 * time.Second)
		w.WriteHeader(http.StatusRequestTimeout)

	case "error503":
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "service unavailable"})

	case "error429":
		w.Header().Set("Retry-After", "1")
		w.WriteHeader(http.StatusTooManyRequests)
		json.NewEncoder(w).Encode(map[string]string{"error": "rate limited"})

	case "error401":
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized"})

	case "error400":
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "bad request"})

	default:
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"message": "OK"})
	}
}

func main() {
	log.Println("=== SPEC #42 E2E Test: Post-Commit Stream Failure ===")

	mockProvider := NewMockProvider()
	defer mockProvider.Close()

	mockProvider.SetStreamingChunks(
		[]string{
			`{"id":"chatcmpl-test","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}`,
		},
		0,  // no delay
		1,  // HANG AFTER 1 CHUNK
	)
	defer mockProvider.Close()

	_ = "http://127.0.0.1:8643"

	log.Println("=== SPEC #42 E2E Test: Post-Commit Stream Failure ===")
	log.Println("Mock: 1 chunk then HANG FOREVER")
	log.Println("Router should: try GLM -> fail -> fallback to nemotron -> hit 60s idle timeout -> terminate stream")
	log.Println("Watch logs for: 'preference fallback glm-5.3 -> nemotron' then 60s idle timeout")

	bodyBytes, _ := json.Marshal(map[string]interface{}{
		"model":       "glm-5.3",
		"messages":    []map[string]string{{"role": "user", "content": "Say hello"}},
		"max_tokens":  50,
		"stream":      true,
	})

	req, err := http.NewRequest("POST", "http://127.0.0.1:8643/v1/chat/completions", bytes.NewReader(bodyBytes))
	if err != nil {
		log.Printf("Failed to create request: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 5 * time.Minute}
	log.Println("Starting request...")

	resp, err := client.Do(req)
	if err != nil {
		log.Printf("Request error: %v", err)
		return
	}
	defer resp.Body.Close()

	log.Printf("Response status: %d", resp.StatusCode)
	body := make([]byte, 4096)
	n, _ := resp.Body.Read(body)
	log.Printf("Response (%d bytes): %s", n, string(body[:n]))
}

func init() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
}
