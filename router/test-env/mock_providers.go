package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"time"
)

type MockConfig struct {
	Behavior           string            `json:"behavior"`            // "success", "503", "429", "timeout", "recovery", "403", "404", "hardfail_recovery", "401", "stream_failure", "stream_commit_then_drop"
	FailCount          int               `json:"fail_count"`
	FailAfter          int               `json:"fail_after"`          // succeed first N calls, then fail
	Delay              int               `json:"delay_ms"`
	Port               int               `json:"port"`
	ModelBehaviors     map[string]string `json:"model_behaviors"`     // per-model behavior override: model -> behavior
	ModelCallCounts    map[string]*int32 `json:"-"`                   // per-model call tracking
	ModelFailCounts    map[string]*int32 `json:"-"`                   // per-model failure tracking for recovery
	ResetModelCounts   []string          `json:"reset_model_counts"`  // models to reset call counts for
}

var (
	configs   = make(map[int]*MockConfig)
	callCount = make(map[int]*int32)
)

func main() {
	// Default configs for test ports
	configs[19990] = &MockConfig{Behavior: "success", Port: 19990}
	configs[19991] = &MockConfig{Behavior: "success", Port: 19991}
	configs[19992] = &MockConfig{Behavior: "success", Port: 19992}
	configs[19993] = &MockConfig{Behavior: "recovery", FailCount: 1, Port: 19993} // fail once, then succeed
	configs[19994] = &MockConfig{Behavior: "timeout", Delay: 180000, Port: 19994}
	configs[19995] = &MockConfig{Behavior: "503", Port: 19995}
	configs[19996] = &MockConfig{Behavior: "429", Port: 19996}

	for port := range configs {
		callCount[port] = new(int32)
	}

	// Control endpoint to change behavior at runtime
		http.HandleFunc("/control", func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				http.Error(w, "POST only", http.StatusMethodNotAllowed)
				return
			}
			var cfg MockConfig
			if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}

			// Handle reset_model_counts (sent as array of model names)
			if len(cfg.ResetModelCounts) > 0 {
				if existingCfg := configs[cfg.Port]; existingCfg != nil {
					if existingCfg.ModelCallCounts == nil {
						existingCfg.ModelCallCounts = make(map[string]*int32)
					}
					if existingCfg.ModelFailCounts == nil {
						existingCfg.ModelFailCounts = make(map[string]*int32)
					}
					for _, model := range cfg.ResetModelCounts {
						existingCfg.ModelCallCounts[model] = new(int32)
						existingCfg.ModelFailCounts[model] = new(int32)
					}
					fmt.Printf("Mock %d: reset call counts for models: %v\n", cfg.Port, cfg.ResetModelCounts)
					w.WriteHeader(http.StatusOK)
					json.NewEncoder(w).Encode(map[string]string{"status": "model call counts reset"})
					return
				}
			}

			configs[cfg.Port] = &cfg

			// Reset global call count
			callCount[cfg.Port] = new(int32)

			// Initialize per-model call counts if model_behaviors provided
			if cfg.ModelBehaviors != nil {
				if cfg.ModelCallCounts == nil {
					cfg.ModelCallCounts = make(map[string]*int32)
				}
				if cfg.ModelFailCounts == nil {
					cfg.ModelFailCounts = make(map[string]*int32)
				}
				for model := range cfg.ModelBehaviors {
					cfg.ModelCallCounts[model] = new(int32)
					cfg.ModelFailCounts[model] = new(int32)
				}
			}

			fmt.Printf("Mock %d: behavior=%s\n", cfg.Port, cfg.Behavior)
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
		})

	// Health endpoint
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	// Models endpoint for catalog
	http.HandleFunc("/models", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": []map[string]string{
				{"id": "moonshotai/kimi-k3"},
				{"id": "z-ai/glm-5.3"},
				{"id": "nvidia/nemotron-3-ultra-550b-a55b"},
				{"id": "deepseek-ai/deepseek-v4.1-flash"},
			},
		})
	})

	// Chat completions endpoint
	http.HandleFunc("/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		port := getPort(r)
		cfg := configs[port]
		if cfg == nil {
			cfg = &MockConfig{Behavior: "success"}
		}

		n := atomic.AddInt32(callCount[port], 1)

		var reqBody map[string]interface{}
		json.NewDecoder(r.Body).Decode(&reqBody)
		model := reqBody["model"]
		modelStr := ""
		if model != nil {
			modelStr = model.(string)
		}
		
		var modelCallNum int32 = 1
		if modelStr != "" && cfg.ModelCallCounts != nil {
			if cfg.ModelCallCounts[modelStr] == nil {
				cfg.ModelCallCounts[modelStr] = new(int32)
			}
			modelCallNum = atomic.AddInt32(cfg.ModelCallCounts[modelStr], 1)
		}
		
		fmt.Printf("Mock %d: requested model=%v\n", port, model)

		// Check for per-model behavior override
		behavior := cfg.Behavior
		if cfg.ModelBehaviors != nil {
			if mb, ok := cfg.ModelBehaviors[modelStr]; ok {
				behavior = mb
			}
		}
		fmt.Printf("Mock %d: call #%d (model=%s #%d), behavior=%s (resolved=%s)\n", port, n, modelStr, modelCallNum, cfg.Behavior, behavior)

			// Non-streaming requests get a JSON completion response for the
			// behaviors that exercise the router's non-stream path.
			streamRequested := false
			if s, sok := reqBody["stream"].(bool); sok {
				streamRequested = s
			}
			if !streamRequested {
				switch behavior {
				case "success":
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprint(w, `{"id":"chatcmpl-mock","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"Hello world"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":20,"completion_tokens_details":{"reasoning_tokens":5}}}`)
					return
				case "length_truncated":
					w.Header().Set("Content-Type", "application/json")
					hasAssistant := false
					if msgs, mok := reqBody["messages"].([]interface{}); mok {
						for _, m := range msgs {
							if mm, mmok := m.(map[string]interface{}); mmok && mm["role"] == "assistant" {
								hasAssistant = true
							}
						}
					}
					if hasAssistant {
						fmt.Fprint(w, `{"id":"chatcmpl-mock","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":" part two"},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":9,"completion_tokens_details":{"reasoning_tokens":3}}}`)
					} else {
						fmt.Fprint(w, `{"id":"chatcmpl-mock","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"part one"},"finish_reason":"length"}],"usage":{"prompt_tokens":10,"completion_tokens":8,"completion_tokens_details":{"reasoning_tokens":2}}}`)
					}
					return
				case "length_mid_toolcall":
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprint(w, `{"id":"chatcmpl-mock","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"","tool_calls":[{"id":"call_1","type":"function","function":{"name":"get_weather","arguments":"{\"city\":"}}]},"finish_reason":"length"}],"usage":{"prompt_tokens":10,"completion_tokens":15,"completion_tokens_details":{"reasoning_tokens":0}}}`)
					return
				case "length_cap_hit":
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprint(w, `{"id":"chatcmpl-mock","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"chunk"},"finish_reason":"length"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"completion_tokens_details":{"reasoning_tokens":1}}}`)
					return
				}
			}

			switch behavior {
		case "timeout":
			fmt.Printf("Mock %d: sleeping %d ms\n", port, cfg.Delay)
			time.Sleep(time.Duration(cfg.Delay) * time.Millisecond)
			// Should never reach here - client will timeout
			http.Error(w, "timeout", http.StatusGatewayTimeout)

		case "503":
			http.Error(w, "service unavailable", http.StatusServiceUnavailable)

		case "429":
			w.Header().Set("Retry-After", "60")
			http.Error(w, "rate limited", http.StatusTooManyRequests)

		case "403":
			http.Error(w, "forbidden", http.StatusForbidden)

		case "404":
				http.Error(w, "not found", http.StatusNotFound)

			case "401":
						http.Error(w, "unauthorized", http.StatusUnauthorized)

					case "sse_503":
				w.Header().Set("Content-Type", "text/event-stream")
				w.Header().Set("Cache-Control", "no-cache")
				w.WriteHeader(http.StatusOK)
				fmt.Fprint(w, "data: {\"error\":{\"message\":\"Service temporarily overloaded\",\"type\":\"service_unavailable\",\"code\":503}}\n\ndata: [DONE]\n\n")
				return

			case "stream_failure":
						// Send headers successfully, then fail mid-stream with actual error
						w.Header().Set("Content-Type", "text/event-stream")
						w.Header().Set("Cache-Control", "no-cache")
						w.Header().Set("Connection", "keep-alive")
						flusher, ok := w.(http.Flusher)
						if !ok {
							http.Error(w, "streaming unsupported", http.StatusInternalServerError)
							return
						}

						// Send first chunk successfully
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n")
						flusher.Flush()
						time.Sleep(10 * time.Millisecond)

						// Then fail - cause actual read error by closing hijacked connection
						hj, ok := w.(http.Hijacker)
						if ok {
							conn, _, err := hj.Hijack()
							if err == nil {
								conn.Close()
							}
						}
						return

				case "stream_commit_then_drop":
						// CRITICAL: HE-04 test behavior
						// Sends HTTP 200 + valid SSE chunk (commits downstream response)
						// Then goes silent (simulates provider stall after commit)
						// Router should hit 60s idle timeout and terminate stream
						// Client receives truncated 200 SSE - this is the interop test
						w.Header().Set("Content-Type", "text/event-stream")
						w.Header().Set("Cache-Control", "no-cache")
						w.Header().Set("Connection", "keep-alive")
						flusher, ok := w.(http.Flusher)
						if !ok {
							http.Error(w, "streaming unsupported", http.StatusInternalServerError)
							return
						}

						// Send first chunk - this commits the downstream response
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Committed chunk\"}}]}\n\n")
						flusher.Flush()
						fmt.Printf("Mock %d: stream_commit_then_drop - sent committed chunk, now going silent\\n", port)

						// Go silent - don't send [DONE], don't close, just hang
						// This simulates provider that commits then stops sending
						// Router's 60s idle timeout should trigger
						select {} // block forever

				case "headers_then_stall":
					// 200 headers then COMPLETE silence (no data bytes).
					// Router should abort pre-commit via first-event timeout and fail over.
					w.Header().Set("Content-Type", "text/event-stream")
					w.Header().Set("Cache-Control", "no-cache")
					w.Header().Set("Connection", "keep-alive")
					w.WriteHeader(http.StatusOK)
					if f, ok := w.(http.Flusher); ok {
						f.Flush()
					}
					fmt.Printf("Mock %d: headers_then_stall - sent headers, now silent (no data)\n", port)
					select {} // block forever, never send data

				case "pre_header_timeout":
						// Timeout before sending headers (simulates upstream timeout)
						fmt.Printf("Mock %d: sleeping %d ms for pre_header_timeout\n", port, cfg.Delay)
						time.Sleep(time.Duration(cfg.Delay) * time.Millisecond)
						http.Error(w, "pre-header timeout", http.StatusGatewayTimeout)

					case "stream_30s_gap":
						// Continuous stream with 30s gaps between chunks (should succeed with 60s idle timeout)
						w.Header().Set("Content-Type", "text/event-stream")
						w.Header().Set("Cache-Control", "no-cache")
						w.Header().Set("Connection", "keep-alive")
						flusher, ok := w.(http.Flusher)
						if !ok {
							http.Error(w, "streaming unsupported", http.StatusInternalServerError)
							return
						}

						// Send first chunk
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Chunk 1\"}}]}\n\n")
						flusher.Flush()

						// Wait 30 seconds
						time.Sleep(30 * time.Second)

						// Send second chunk
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Chunk 2\"}}]}\n\n")
						flusher.Flush()

						// Wait 30 seconds
						time.Sleep(30 * time.Second)

						// Send third chunk
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Chunk 3\"}}]}\n\n")
						flusher.Flush()

						time.Sleep(10 * time.Millisecond)
						fmt.Fprintf(w, "data: [DONE]\n\n")
						flusher.Flush()
						return

					case "stream_90s_gap":
						// Stream with 90s idle gap (should timeout with 60s idle timeout)
						w.Header().Set("Content-Type", "text/event-stream")
						w.Header().Set("Cache-Control", "no-cache")
						w.Header().Set("Connection", "keep-alive")
						flusher, ok := w.(http.Flusher)
						if !ok {
							http.Error(w, "streaming unsupported", http.StatusInternalServerError)
							return
						}

						// Send first chunk
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Chunk 1\"}}]}\n\n")
						flusher.Flush()

						// Wait 90 seconds - should trigger idle timeout
						time.Sleep(90 * time.Second)

						// This should never be reached due to timeout
						fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Chunk 2\"}}]}\n\n")
						flusher.Flush()
						return

					case "recovery":
							// Use per-model failure count for deterministic per-model recovery
							var modelFailNum int32 = 0
							if modelStr != "" && cfg.ModelFailCounts != nil {
								if cfg.ModelFailCounts[modelStr] == nil {
									cfg.ModelFailCounts[modelStr] = new(int32)
								}
								modelFailNum = atomic.AddInt32(cfg.ModelFailCounts[modelStr], 1)
							}
		
							if modelFailNum <= int32(cfg.FailCount) {
								http.Error(w, "temporary failure", http.StatusServiceUnavailable)
								return
							}
							fallthrough

		case "hardfail_recovery":
			if n <= int32(cfg.FailCount) {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
			fallthrough

		case "length_truncated":
			// First call: content delta then finish_reason=length.
			// Continuation call (assistant message present in messages): finish with stop.
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			flusher, ok := w.(http.Flusher)
			if !ok {
				http.Error(w, "streaming unsupported", http.StatusInternalServerError)
				return
			}
			hasAssistant := false
			if msgs, mok := reqBody["messages"].([]interface{}); mok {
				for _, m := range msgs {
					if mm, mmok := m.(map[string]interface{}); mmok && mm["role"] == "assistant" {
						hasAssistant = true
					}
				}
			}
			fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"part one\"}}]}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			if hasAssistant {
				fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\" part two\"}},{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":12,\"completion_tokens\":9,\"completion_tokens_details\":{\"reasoning_tokens\":3}}}\n\n")
			} else {
				fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"length\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":8,\"completion_tokens_details\":{\"reasoning_tokens\":2}}}\n\n")
			}
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: [DONE]\n\n")
			flusher.Flush()

		case "length_mid_toolcall":
			// Truncation mid-tool-call: router must NEVER stitch this — passthrough.
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			flusher, ok := w.(http.Flusher)
			if !ok {
				http.Error(w, "streaming unsupported", http.StatusInternalServerError)
				return
			}
			fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"arguments\":\"{\\\"city\\\":\"}}]}}]}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"length\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":15,\"completion_tokens_details\":{\"reasoning_tokens\":0}}}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: [DONE]\n\n")
			flusher.Flush()

		case "length_cap_hit":
			// Always finish_reason=length, even on continuations: router must hit its cap.
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			flusher, ok := w.(http.Flusher)
			if !ok {
				http.Error(w, "streaming unsupported", http.StatusInternalServerError)
				return
			}
			fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"chunk\"}}]}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"length\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"completion_tokens_details\":{\"reasoning_tokens\":1}}}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: [DONE]\n\n")
			flusher.Flush()

		case "success":
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			w.Header().Set("Connection", "keep-alive")
			flusher, ok := w.(http.Flusher)
			if !ok {
				http.Error(w, "streaming unsupported", http.StatusInternalServerError)
				return
			}

			// Send a proper SSE response
			fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			// Terminal chunk with finish_reason + usage (exercises router truncation telemetry)
			fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":20,\"completion_tokens_details\":{\"reasoning_tokens\":5}}}\n\n")
			flusher.Flush()
			time.Sleep(10 * time.Millisecond)
			fmt.Fprintf(w, "data: [DONE]\n\n")
			flusher.Flush()
		}
	})

	// Start all mock servers
	for port := range configs {
		p := port
		go func() {
			addr := fmt.Sprintf("127.0.0.1:%d", p)
			fmt.Printf("Mock provider starting on %s\n", addr)
			if err := http.ListenAndServe(addr, nil); err != nil {
				fmt.Printf("Mock %d error: %v\n", p, err)
			}
		}()
	}

	// Control server on 19999
	fmt.Println("Control server on 127.0.0.1:19999")
	http.ListenAndServe("127.0.0.1:19999", nil)
}

func getPort(r *http.Request) int {
	// Extract port from Host header
	host := r.Host
	for port := range configs {
		if host == fmt.Sprintf("127.0.0.1:%d", port) {
			return port
		}
	}
	return 19990
}
