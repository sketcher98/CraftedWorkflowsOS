package main

import (
	"sync"
	"time"
)

// WindowCounter is a 2-bucket sliding-window counter (OmniRoute
// sqliteQuotaStore.ts:56-125): effective = prev × (1 − elapsed/window) + curr.
// No timers, no cron — exact enough for quota/budget windows.
type WindowCounter struct {
	mu       sync.Mutex
	windowMs int64
	bucketA  int64
	bucketB  int64
	lastMs   int64
}

func NewWindowCounter(window time.Duration) *WindowCounter {
	return &WindowCounter{
		windowMs: window.Milliseconds(),
		lastMs:   time.Now().UnixMilli(),
	}
}

func (w *WindowCounter) advance(nowMs int64) {
	elapsed := nowMs - w.lastMs
	if elapsed <= 0 {
		return
	}
	if elapsed >= w.windowMs {
		w.bucketA = 0
		w.bucketB = 0
	} else {
		frac := float64(w.windowMs-elapsed) / float64(w.windowMs)
		w.bucketA = int64(float64(w.bucketA) * frac)
		// bucketB becomes bucketA in the next window
		w.bucketB = int64(float64(w.bucketB) * frac)
	}
	w.lastMs = nowMs
}

// Add increments the counter for the current window.
func (w *WindowCounter) Add(n int64) {
	w.mu.Lock()
	defer w.mu.Unlock()
	now := time.Now().UnixMilli()
	w.advance(now)
	w.bucketA += n
}

// Value returns the current effective count.
func (w *WindowCounter) Value() int64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.advance(time.Now().UnixMilli())
	return w.bucketA + w.bucketB
}

// Reserve would atomically ADD to the counter and return the new effective
// total, so admission can compare against a limit and refund on reject.
func (w *WindowCounter) Reserve(n int64) int64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	now := time.Now().UnixMilli()
	w.advance(now)
	w.bucketA += n
	return w.bucketA + w.bucketB
}

// Refund subtracts a reservation that was rejected/failed.
func (w *WindowCounter) Refund(n int64) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.advance(time.Now().UnixMilli())
	if w.bucketA >= n {
		w.bucketA -= n
	} else {
		w.bucketA = 0
	}
}

// budgetRegistry tracks per-key-per-provider windows.
type budgetRegistry struct {
	mu      sync.Mutex
	windows map[string]*WindowCounter
}

var budgetReg = &budgetRegistry{windows: make(map[string]*WindowCounter)}

func (b *budgetRegistry) windowFor(provider, key string, window time.Duration) *WindowCounter {
	b.mu.Lock()
	defer b.mu.Unlock()
	id := provider + "|" + key
	if w, ok := b.windows[id]; ok {
		return w
	}
	w := NewWindowCounter(window)
	b.windows[id] = w
	return w
}
