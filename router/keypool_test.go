package main

import (
	"os"
	"testing"
)

// TestRoundRobinKeyDistribution proves the round_robin key strategy yields an
// exact even long-run distribution across healthy keys.
func TestRoundRobinKeyDistribution(t *testing.T) {
	envs := []string{"TEST_KEY_A", "TEST_KEY_B", "TEST_KEY_C"}
	os.Setenv("TEST_KEY_A", "ka")
	os.Setenv("TEST_KEY_B", "kb")
	os.Setenv("TEST_KEY_C", "kc")
	defer func() {
		for _, e := range envs {
			os.Unsetenv(e)
		}
	}()

	p := &Provider{
		KeyEnvs:     envs,
		keyStrategy: "round_robin",
		keys: map[string]*KeyHealth{
			"TEST_KEY_A": {},
			"TEST_KEY_B": {},
			"TEST_KEY_C": {},
		},
	}

	counts := map[string]int{}
	for i := 0; i < 90; i++ {
		// per-request used set (mirrors production call shape)
		env, _, ok := selectProviderKey(p, map[string]bool{})
		if !ok {
			t.Fatal("expected a healthy key")
		}
		counts[env]++
	}

	for _, e := range envs {
		if counts[e] != 30 {
			t.Fatalf("round-robin not even: %v (want 30 each)", counts)
		}
	}
}
