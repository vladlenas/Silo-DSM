# Silo from the TorrServer "silo" branch, pinned to one commit. Update all
# three together: SILO_VERSION is the upstream tag (or a name for the commit),
# SILO_COMMIT the full commit id, and the last number of PKG_VERSION is the
# Silo build (Silo.1 -> x.y.1).
SILO_VERSION := Silo.1-332fee3
SILO_COMMIT := 332fee39477ee40d486c8c4b2d7a41b2a2c8537b
PKG_VERSION := 0.13.1

# Only amd64 is released for now (build-package.sh also knows arm64 and arm7).
ARCHES := amd64

.PHONY: all binaries plugins clean

# Optional Silo plugins, released as zips next to the package (Silo → admin
# panel → Plugins → upload). None at the moment: the plugins in plugins/ are
# built into Silo (overlay/apply.py, BUILTIN_PLUGINS).
PLUGIN_ZIPS :=

all: $(addprefix silo-,$(ARCHES)) plugins

silo-%:
	@./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$*" "$(PKG_VERSION)"

# Only compile the binaries (dest_bin/<commit>-<overlay>/silo-linux-<arch>).
binaries:
	@for arch in $(ARCHES); do \
		BUILD_ONLY=1 ./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$$arch" "$(PKG_VERSION)" || exit 1; \
	done

plugins:
	@mkdir -p spk
	@for p in $(PLUGIN_ZIPS); do \
		v=$$(sed -n 's/^version: *"\(.*\)"/\1/p' plugins/$$p/info.yaml); \
		python3 -c 'import sys, os, zipfile; src, out = sys.argv[1], sys.argv[2]; z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED); [z.write(os.path.join(r, f), os.path.relpath(os.path.join(r, f), src)) for r, _, fs in sorted(os.walk(src)) for f in sorted(fs)]; z.close()' plugins/$$p spk/$$p-$$v.zip || exit 1; \
		echo ">>> spk/$$p-$$v.zip"; \
	done

clean:
	rm -rf spk dest_bin dest_src build
