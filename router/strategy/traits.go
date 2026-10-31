package strategy

// TraitFlags describe what a strategy does — data, not string branches
// (OmniRoute strategyRegistry.ts:40-208 pattern).
type TraitFlags struct {
	StickyPin   bool // keeps a last-good provider pinned to the front
	QuotaGate   bool // consults quota/window state before ordering
	Weighted    bool // uses Target.Weight/score ordering
	Randomized  bool // samples/reshuffles for spread
	Explainable bool // emits per-step reasons (decision record)
}

// Trait is a frozen descriptor row for one strategy.
type Trait struct {
	Mode  string
	Flags TraitFlags
}

// Traits is the canonical registry of the trait rows. New strategies =
// new row, never a new if-chain.
var Traits = map[string]Trait{
	"configured": {Mode: "configured", Flags: TraitFlags{Explainable: true}},
	"priority":   {Mode: "priority", Flags: TraitFlags{Weighted: true, Explainable: true}},
	"p2c":       {Mode: "p2c", Flags: TraitFlags{Randomized: true, Explainable: true}},
	"lkgp":      {Mode: "lkgp", Flags: TraitFlags{StickyPin: true, Explainable: true}},
}

// ResolveTrait returns the trait row for a strategy mode; unknown modes
// resolve to "configured" (safe default, never a nil map read).
func ResolveTrait(mode string) Trait {
	if t, ok := Traits[mode]; ok {
		return t
	}
	return Traits["configured"]
}

// ValidStrategy reports whether a mode string is known.
func ValidStrategy(mode string) bool {
	if mode == "" {
		return true
	}
	_, ok := Traits[mode]
	return ok
}
