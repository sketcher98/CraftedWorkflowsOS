# Router release builder.
# Usage:
#   make bundle            # build all bundles under dist/
#   make bundle-linux-amd64
#   make bundle-windows-amd64
#   make bundle-darwin-arm64
#
# Each bundle contains: the router binary, routeops/ (TUI), setup/ (wizard
# + installers), and a README stub.

GO ?= go
CGO_ENABLED ?= 0
DIST := dist
COMMIT := $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo dev)

define bundle_template
bundle-$(1)-$(2): | $(DIST)
	@echo "== building $(1)/$(2) (commit $(COMMIT)) =="
	GOOS=$(1) GOARCH=$(2) CGO_ENABLED=$(CGO_ENABLED) $(GO) build -trimpath \
		-ldflags "-s -w -X main.buildID=$(COMMIT)" \
		-o $(DIST)/build/$(1)-$(2)/router$(3) .
	mkdir -p $(DIST)/build/$(1)-$(2)/routeops \
		$(DIST)/build/$(1)-$(2)/setup
	cp -r routeops $(DIST)/build/$(1)-$(2)/ 2>/dev/null || true
	cp -r setup $(DIST)/build/$(1)-$(2)/ 2>/dev/null || true
	printf 'Router bundle for %s/%s (commit %s)\nSee https://github.com/sketcher98/CraftedWorkflowsOS\n' \
		$(1) $(2) $(COMMIT) > $(DIST)/build/$(1)-$(2)/README.txt
	@cd $(DIST)/build/$(1)-$(2) && tar -czf ../../router-$(1)-$(2).tar.gz . && cd - >/dev/null
	@echo "ok: $(DIST)/router-$(1)-$(2).tar.gz"
endef

.PHONY: bundle-all clean
bundle-all: bundle-linux-amd64 bundle-linux-arm64 bundle-linux-armv7 \
	bundle-darwin-amd64 bundle-darwin-arm64 bundle-windows-amd64

$(eval $(call bundle_template,linux,amd64,))
$(eval $(call bundle_template,linux,arm64,))
$(eval $(call bundle_template,darwin,amd64,))
$(eval $(call bundle_template,darwin,arm64,))
$(eval $(call bundle_template,windows,amd64,.exe))

# armv7 (Termux/Android 32-bit) — GOARCH=arm + GOARM=7, cgo off.
bundle-linux-armv7: | $(DIST)
	@echo "== building linux/armv7 (commit $(COMMIT)) =="
	GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=$(CGO_ENABLED) $(GO) build -trimpath \
		-ldflags "-s -w -X main.buildID=$(COMMIT)" \
		-o $(DIST)/build/linux-armv7/router .
	mkdir -p $(DIST)/build/linux-armv7/routeops $(DIST)/build/linux-armv7/setup
	cp -r routeops $(DIST)/build/linux-armv7/ 2>/dev/null || true
	cp -r setup $(DIST)/build/linux-armv7/ 2>/dev/null || true
	printf 'Router bundle for linux/armv7 (commit %s)\nSee https://github.com/sketcher98/CraftedWorkflowsOS\n' \
		$(COMMIT) > $(DIST)/build/linux-armv7/README.txt
	@cd $(DIST)/build/linux-armv7 && tar -czf ../../router-linux-armv7.tar.gz . && cd - >/dev/null
	@echo "ok: $(DIST)/router-linux-armv7.tar.gz"

$(DIST):
	mkdir -p $(DIST)/build
clean:
	rm -rf $(DIST)
