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
DIST := dist
COMMIT := $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo dev)

define bundle_template
bundle-$(1)-$(2): | $(DIST)
	@echo "== building $(1)/$(2) (commit $(COMMIT)) =="
	$(GO) build -trimpath -ldflags "-s -w -X main.buildID=$(COMMIT)" \
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
$(eval $(call bundle_template,linux,armv7,))
$(eval $(call bundle_template,darwin,amd64,))
$(eval $(call bundle_template,darwin,arm64,))
$(eval $(call bundle_template,windows,amd64,.exe))

$(DIST):
	mkdir -p $(DIST)/build
clean:
	rm -rf $(DIST)
