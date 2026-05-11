#!/usr/bin/env bash
# =============================================================================
# setup_cycronet.sh — Install cyCronet (Chrome 144 TLS / libcronet 144.0.7506.0)
#
# Installs the pre-built binary distribution of cyCronet into Python
# site-packages. No Rust toolchain required.
#
# Supported: Linux x86_64, Python 3.8 – 3.12
# Tested on: Ubuntu 20.04/22.04, Debian 11/12
#
# Usage:
#   bash setup_cycronet.sh                 # auto-detect python3
#   bash setup_cycronet.sh /usr/bin/python3.11
#   CYCRONET_REPO=/path/to/local/clone bash setup_cycronet.sh
#
# Source repo: https://github.com/2833844911/cyCronet
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
PYTHON="${1:-python3}"
REPO_URL="https://github.com/2833844911/cyCronet"
CLONE_DIR="${CYCRONET_REPO:-/tmp/cyCronet}"
BUILD_DIR="$CLONE_DIR/cycronet-build"

# ── Helpers ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
step() { echo -e "\n${YELLOW}══ $* ${NC}"; }

# ── Step 1: Detect Python ─────────────────────────────────────────────────────
step "Detecting Python environment"
PYTHON_BIN=$(command -v "$PYTHON" 2>/dev/null) || die "Python not found: $PYTHON"
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
PY_VER_DOT=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ARCH=$("$PYTHON_BIN" -c "import platform; print(platform.machine())")
ABI_SUFFIX=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
SITE_PACKAGES=$("$PYTHON_BIN" -c "import site; print(site.getsitepackages()[0])")

ok "Python $PY_VER_DOT at $PYTHON_BIN"
ok "site-packages: $SITE_PACKAGES"
ok "ABI suffix: $ABI_SUFFIX"
ok "Architecture: $ARCH"

[[ "$ARCH" == "x86_64" ]] || die "Only x86_64 is supported (got $ARCH)"
[[ "$PY_VER" -ge 38 && "$PY_VER" -le 312 ]] || die "Python 3.8–3.12 required (got $PY_VER_DOT)"

# ── Step 2: Clone / update repo ───────────────────────────────────────────────
step "Fetching cyCronet repository"
if [[ -d "$CLONE_DIR/.git" ]]; then
    warn "Repo already exists at $CLONE_DIR — pulling latest"
    git -C "$CLONE_DIR" pull --ff-only 2>/dev/null || warn "Pull failed, using existing checkout"
elif [[ -d "$CLONE_DIR" && -d "$BUILD_DIR" ]]; then
    ok "Using existing build dir at $BUILD_DIR (set CYCRONET_REPO to override)"
else
    echo "Cloning $REPO_URL → $CLONE_DIR"
    git clone --depth=1 "$REPO_URL" "$CLONE_DIR"
fi

[[ -d "$BUILD_DIR" ]] || die "Build dir not found: $BUILD_DIR"
ok "Build dir: $BUILD_DIR"

# ── Step 3: Verify required files ─────────────────────────────────────────────
step "Verifying build artifacts"

# Rust extension .so
CLOAK_SO="$BUILD_DIR/target/maturin/libcronet_cloak.so"
[[ -f "$CLOAK_SO" ]] || die "Rust extension not found: $CLOAK_SO"
ok "Rust extension: $(basename "$CLOAK_SO") ($(du -sh "$CLOAK_SO" | cut -f1))"

# libcronet
LIBCRONET=$(ls "$BUILD_DIR"/cronet-libs/linux/libcronet.*.so 2>/dev/null | head -1)
[[ -n "$LIBCRONET" && -f "$LIBCRONET" ]] || die "libcronet not found in $BUILD_DIR/cronet-libs/linux/"
ok "libcronet: $(basename "$LIBCRONET") ($(du -sh "$LIBCRONET" | cut -f1))"
LIBCRONET_NAME=$(basename "$LIBCRONET")

# NSS deps
NSS_DIR="$BUILD_DIR/linux_deps"
[[ -d "$NSS_DIR" ]] || die "NSS deps dir not found: $NSS_DIR"
NSS_COUNT=$(ls "$NSS_DIR"/*.so 2>/dev/null | wc -l)
[[ "$NSS_COUNT" -ge 8 ]] || die "Expected ≥8 NSS .so files in $NSS_DIR, found $NSS_COUNT"
ok "NSS deps: $NSS_COUNT files in $NSS_DIR"

# Python package source
PKG_SRC="$BUILD_DIR/python/cycronet"
[[ -d "$PKG_SRC" ]] || die "Python package source not found: $PKG_SRC"
ok "Python package source: $PKG_SRC"

# ── Step 4: Install ───────────────────────────────────────────────────────────
step "Installing cycronet to $SITE_PACKAGES/cycronet/"
PKG_DST="$SITE_PACKAGES/cycronet"
mkdir -p "$PKG_DST"

# 4a. Python package files (.py + .pyi + .json)
echo "  Copying Python package files..."
for f in "$PKG_SRC"/*.py "$PKG_SRC"/*.pyi "$PKG_SRC"/*.json; do
    [[ -f "$f" ]] && cp "$f" "$PKG_DST/"
done
ok "Python package files installed"

# 4b. Rename Rust .so to correct ABI-tagged name
CLOAK_DST="$PKG_DST/cronet_cloak${ABI_SUFFIX}"
cp "$CLOAK_SO" "$CLOAK_DST"
ok "Rust extension → $(basename "$CLOAK_DST")"

# 4c. libcronet
cp "$LIBCRONET" "$PKG_DST/$LIBCRONET_NAME"
ok "libcronet → $LIBCRONET_NAME"

# 4c-2. Create hashed symlink — the Rust extension's DT_NEEDED references a
#        content-hashed name (e.g. libcronet-5aa54642.144.0.7506.0.so).
#        Extract that name from the extension's DT_NEEDED and create a symlink.
HASHED_NAME=$(objdump -p "$CLOAK_SO" 2>/dev/null \
    | grep NEEDED \
    | grep -oP 'libcronet-[0-9a-f]+\.\S+\.so' \
    | head -1)
if [[ -n "$HASHED_NAME" ]]; then
    ln -sf "$PKG_DST/$LIBCRONET_NAME" "$PKG_DST/$HASHED_NAME"
    ok "Hashed symlink → $HASHED_NAME → $LIBCRONET_NAME"
else
    warn "Could not determine hashed libcronet name from DT_NEEDED — skipping symlink"
    warn "If import fails, run: ln -sf $PKG_DST/$LIBCRONET_NAME $PKG_DST/libcronet-HASH.so"
fi

# 4d. NSS deps
echo "  Copying NSS dependency libraries..."
for f in "$NSS_DIR"/*.so; do
    cp "$f" "$PKG_DST/"
done
ok "NSS deps copied ($(ls "$NSS_DIR"/*.so | wc -l) files)"

# 4e. Patch __init__.py — remove PyCronetWebSocket (not exported by sync-only build)
INIT_FILE="$PKG_DST/__init__.py"
if grep -q "PyCronetWebSocket" "$INIT_FILE" 2>/dev/null; then
    echo "  Patching __init__.py (removing PyCronetWebSocket)..."
    python3 - "$INIT_FILE" << 'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    src = f.read()
# Remove PyCronetWebSocket from import line
src = re.sub(r',\s*PyCronetWebSocket', '', src)
src = re.sub(r'PyCronetWebSocket,\s*', '', src)
# Remove from __all__
src = re.sub(r',\s*"PyCronetWebSocket"', '', src)
src = re.sub(r'"PyCronetWebSocket",\s*', '', src)
with open(path, 'w') as f:
    f.write(src)
print("    patched OK")
PYEOF
    ok "__init__.py patched (PyCronetWebSocket removed)"
else
    ok "__init__.py already clean (no PyCronetWebSocket)"
fi

# ── Step 5: Verify RPATH on .so ───────────────────────────────────────────────
step "Verifying RPATH / library resolution"
if command -v patchelf &>/dev/null; then
    RPATH=$(patchelf --print-rpath "$CLOAK_DST" 2>/dev/null || echo "")
    if [[ "$RPATH" != *"\$ORIGIN"* ]]; then
        warn "RPATH='$RPATH' — patching to \$ORIGIN"
        patchelf --set-rpath '$ORIGIN' "$CLOAK_DST" "$PKG_DST/$LIBCRONET_NAME" 2>/dev/null || \
            warn "patchelf failed (non-fatal, native loader handles it)"
    else
        ok "RPATH=\$ORIGIN already set"
    fi
else
    warn "patchelf not installed — skipping RPATH check (usually fine)"
fi

# ── Step 6: Smoke test ────────────────────────────────────────────────────────
step "Running smoke test"
SMOKE_RESULT=$("$PYTHON_BIN" - << 'PYEOF' 2>&1
import sys
sys.path.insert(0, ".")
try:
    import cycronet
    from cycronet import CronetClient, Cookie, CookieJar, Response
    # Test CronetClient (high-level session API)
    client = CronetClient(verify=False)
    print("CronetClient OK")
    # Test CookieJar
    jar = CookieJar()
    jar.set("test", "val", domain="example.com", path="/")
    cookies = list(jar.iter_cookies())
    assert len(cookies) == 1 and cookies[0].name == "test", f"CookieJar FAIL: {cookies}"
    print("CookieJar OK")
    # Test PyCronetClient is importable (underlying Rust binding)
    from cycronet import PyCronetClient
    print("PyCronetClient OK")
    print("PASS")
except Exception as e:
    print(f"FAIL: {e}")
    import traceback; traceback.print_exc()
PYEOF
)

if echo "$SMOKE_RESULT" | grep -q "^PASS$"; then
    ok "Smoke test passed"
    echo "$SMOKE_RESULT" | grep -v "^PASS$" | sed 's/^/    /'
else
    die "Smoke test FAILED:\n$SMOKE_RESULT"
fi

# ── Step 7: Live HTTP test (optional) ─────────────────────────────────────────
step "Live HTTP/TLS test (httpbin.org)"
HTTP_RESULT=$("$PYTHON_BIN" - << 'PYEOF' 2>&1
import cycronet, json
try:
    r = cycronet.get("https://httpbin.org/get", verify=False, timeout=10)
    data = r.json()
    proto = "HTTP/2" if r._response.http_version == 2 else "HTTP/1.1"
    print(f"Status: {r.status_code}")
    print(f"Protocol: {proto}")
    print("PASS")
except Exception as e:
    print(f"SKIP ({e})")
PYEOF
)
if echo "$HTTP_RESULT" | grep -qE "^PASS|^SKIP"; then
    ok "HTTP test: $(echo "$HTTP_RESULT" | head -3 | tr '\n' '  ')"
else
    warn "HTTP test inconclusive (no network?): $HTTP_RESULT"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  cyCronet installed successfully!                  ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Install path : $PKG_DST"
echo "  libcronet    : $LIBCRONET_NAME"
echo "  Extension    : cronet_cloak${ABI_SUFFIX}"
echo "  NSS deps     : $NSS_COUNT files"
echo ""
echo "  Verify with: $PYTHON_BIN -c \"import cycronet; print('OK')\""
echo ""
