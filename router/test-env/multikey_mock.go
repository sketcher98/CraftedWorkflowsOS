package main

import (
	"fmt"
	"net/http"
	"strings"
)

func main() {
	http.HandleFunc("/models", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"data":[{"id":"moonshotai/kimi-k3"}]}`)
	})

	http.HandleFunc("/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		fmt.Printf("request Authorization=%q\n", auth)

		if strings.TrimSpace(auth) == "Bearer fake-key-1" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}

		if strings.TrimSpace(auth) == "Bearer fake-key-2" {
			w.Header().Set("Content-Type", "text/event-stream")
			w.Header().Set("Cache-Control", "no-cache")
			fmt.Fprint(w, "data: {\"id\":\"multikey-test\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"KEY2_OK\"},\"finish_reason\":null}]}\n\n")
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}

		http.Error(w, "unauthorized", http.StatusUnauthorized)
	})

	fmt.Println("multikey mock: 19998")
	if err := http.ListenAndServe("127.0.0.1:19998", nil); err != nil {
		panic(err)
	}
}
