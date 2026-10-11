# Silo from the TorrServer "silo" branch, pinned to one commit. Update all
# three together: SILO_VERSION is the upstream tag (or a name for the commit),
# SILO_COMMIT the full commit id, and the last number of PKG_VERSION is the
# Silo build (Silo.1 -> x.y.1).
SILO_VERSION := Silo.1
SILO_COMMIT := fc5795e10234d18dda509a781472a82e402322bd
PKG_VERSION := 0.3.1

# Only amd64 is released for now (build-package.sh also knows arm64 and arm7).
ARCHES := amd64

.PHONY: all binaries clean

all: $(addprefix silo-,$(ARCHES))

silo-%:
	@./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$*" "$(PKG_VERSION)"

# Only compile the binaries (dest_bin/<commit>/silo-linux-<arch>).
binaries:
	@for arch in $(ARCHES); do \
		BUILD_ONLY=1 ./build-package.sh "$(SILO_VERSION)" "$(SILO_COMMIT)" "$$arch" "$(PKG_VERSION)" || exit 1; \
	done

clean:
	rm -rf spk dest_bin dest_src build
