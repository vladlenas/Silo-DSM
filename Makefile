# Silo from the TorrServer "silo" branch, pinned to one commit. Update all
# three together: SILO_VERSION is the upstream tag (or a name for the commit),
# SILO_COMMIT the full commit id, and the last number of PKG_VERSION is the
# Silo build (Silo.1 -> x.y.1).
SILO_VERSION := Silo.1
SILO_COMMIT := fc5795e10234d18dda509a781472a82e402322bd
PKG_VERSION := 0.2.1

# Only amd64 is released for now (build-package.sh also knows arm64 and arm7).
ARCHES := amd64

# WebDAV plugin for Silo (installed in Silo's admin panel, not part of the spk).
WEBDAV_VERSION := $(shell sed -n 's/^version: *"\(.*\)"/\1/p' plugins/silo-webdav/info.yaml)

.PHONY: all binaries plugin clean

all: $(addprefix silo-,$(ARCHES)) plugin

silo-%:
	@./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$*" "$(PKG_VERSION)"

# Only compile the binaries (dest_bin/<commit>/silo-linux-<arch>).
binaries:
	@for arch in $(ARCHES); do \
		BUILD_ONLY=1 ./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$$arch" "$(PKG_VERSION)" || exit 1; \
	done

# spk/silo-webdav-<version>.zip, to upload in Silo: admin panel, Plugins.
plugin:
	@mkdir -p spk
	@python3 -c 'import sys, zipfile, os; src, out = sys.argv[1], sys.argv[2]; z = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED); [z.write(os.path.join(src, f), f) for f in sorted(os.listdir(src))]; z.close()' plugins/silo-webdav spk/silo-webdav-$(WEBDAV_VERSION).zip
	@echo ">>> spk/silo-webdav-$(WEBDAV_VERSION).zip"

clean:
	rm -rf spk dest_bin dest_src build
