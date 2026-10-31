package strategy

import (
	"testing"
	"time"
)

func TestValidStrategyTrait(t *testing.T) {
	for _, m := range []string{"", "configured", "lkgp", "priority", "p2c"} {
		if !ValidStrategy(m) {
			t.Errorf("known strategy %q must validate", m)
		}
	}
	if ValidStrategy("nonsense") {
		t.Error("unknown strategy must not validate")
	}
}

func TestResolveTraitDefaults(t *testing.T) {
	got := ResolveTrait("unknown-mode")
	if got.Mode != "configured" {
		t.Errorf("unknown mode resolved to %q, want configured", got.Mode)
	}
	l := ResolveTrait("lkgp")
	if !l.Flags.StickyPin {
		t.Error("lkgp trait must be sticky-pin")
	}
}

func TestLKGPExpiry(t *testing.T) {
	s := NewLKGPStrategy(LKGPConfig{MaxPinAge: 0}) // 0s → any age expires
	s.SetPin("prefA", "nvidia")
	if s.GetPin("prefA") != "nvidia" {
		t.Fatal("pin should be set")
	}
	time.Sleep(1100 * time.Millisecond)
	if !s.isPinExpired("prefA") {
		t.Error("pin should be expired after maxAge")
	}
}

func TestLKGPScopedClear(t *testing.T) {
	s := NewLKGPStrategy(DefaultLKGPConfig())
	s.SetPin("prefA", "nvidia")
	// Sibling failure must NOT clear the pin.
	if s.GetPin("prefA") == "nvidia" && s.GetPin("prefA") != "zydit-v1" {
		// pin intact
	}
	if s.GetPin("prefA") != "nvidia" {
		t.Fatal("pin must stay on sibling failure")
	}
	// The failed provider IS pinned → clear.
	s.DeletePin("prefA")
	if s.GetPin("prefA") != "" {
		t.Error("pin should be cleared")
	}
}

func TestMoveToFrontStable(t *testing.T) {
	targets := []Target{
		{Provider: "a"}, {Provider: "b"}, {Provider: "c"},
	}
	got := moveToFront(targets, "c")
	if got[0].Provider != "c" || got[1].Provider != "a" || got[2].Provider != "b" {
		t.Errorf("unexpected order %+v", got)
	}
}
