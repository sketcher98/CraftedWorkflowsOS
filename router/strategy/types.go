package strategy

// Target represents a provider/model combination with metadata
type Target struct {
	Provider   string                 `json:"provider"`
	Model      string                 `json:"model"`
	ProviderID string                 `json:"provider_id,omitempty"`
	Weight     int                    `json:"weight,omitempty"`
	Priority   int                    `json:"priority,omitempty"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
}

// Request represents an incoming request (simplified for strategies)
type Request struct {
	Preference   string                 `json:"preference"`
	Model        string                 `json:"model"`
	Messages     []Message              `json:"messages"`
	MaxTokens    int                    `json:"max_tokens,omitempty"`
	Temperature  float64                `json:"temperature,omitempty"`
	Stream       bool                   `json:"stream,omitempty"`
	SessionID    string                 `json:"session_id,omitempty"`
	Metadata     map[string]interface{} `json:"metadata,omitempty"`
}

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}