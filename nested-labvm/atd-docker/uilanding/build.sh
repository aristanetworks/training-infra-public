#!/bin/bash
# Build script for UILanding frontend minification
# This script minifies custom JS and CSS files during Docker image build
#
# IMPORTANT: This script modifies ES module files in-place!
# Only run this during Docker build, not on development source files.
#
# Strategy:
# - Regular scripts (loaded via <script src>): create .min.js versions
# - ES modules (loaded via import): minify in-place to preserve import paths
# - CSS files: create .min.css versions

set -e

# Safety check - prevent running on development source files
if [ -z "${DOCKER_BUILD:-}" ] && [ -z "${FORCE_BUILD:-}" ]; then
    echo ""
    echo "WARNING: This script modifies ES module files in-place!"
    echo "It is intended to run only during Docker build."
    echo ""
    echo "If you really want to run locally, set FORCE_BUILD=1"
    echo "Example: FORCE_BUILD=1 ./build.sh"
    echo ""
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTML_DIR="${SCRIPT_DIR}/src/html"

echo "=== UILanding Frontend Build ==="
echo "Working directory: ${HTML_DIR}"

# Install dependencies if node_modules doesn't exist
if [ ! -d "${SCRIPT_DIR}/node_modules" ]; then
    echo "Installing npm dependencies..."
    cd "${SCRIPT_DIR}"
    npm install --production=false
fi

# Function to minify a regular JS file (creates .min.js version)
minify_js_to_min() {
    local src="$1"
    local dest="${src%.js}.min.js"

    # Skip if already a .min.js file
    if [[ "$src" == *.min.js ]]; then
        return 0
    fi

    # Skip vendor files that already have minified versions
    local basename=$(basename "$src")
    case "$basename" in
        jquery.js|bootstrap.js|popper.js|foundation.js)
            echo "  Skipping vendor file: $basename (already has .min.js)"
            return 0
            ;;
    esac

    echo "  Minifying: $(basename "$src") -> $(basename "$dest")"
    npx terser "$src" \
        --compress \
        --mangle \
        --output "$dest" \
        --source-map "url='$(basename "$dest").map'" \
        2>/dev/null || {
            echo "    Warning: Failed to minify $src, copying as-is"
            cp "$src" "$dest"
        }
}

# Function to minify an ES module in-place (preserves import paths)
minify_js_inplace() {
    local src="$1"

    # Skip if already a .min.js file
    if [[ "$src" == *.min.js ]]; then
        return 0
    fi

    local basename=$(basename "$src")
    echo "  Minifying in-place: $basename"

    # Create temp file for output
    local tmpfile="${src}.tmp"

    npx terser "$src" \
        --compress \
        --mangle \
        --output "$tmpfile" \
        --source-map "url='$(basename "$src").map'" \
        2>/dev/null && mv "$tmpfile" "$src" || {
            echo "    Warning: Failed to minify $src, keeping original"
            rm -f "$tmpfile"
        }
}

# Function to minify a single CSS file (creates .min.css version)
minify_css() {
    local src="$1"
    local dest="${src%.css}.min.css"

    # Skip if already a .min.css file
    if [[ "$src" == *.min.css ]]; then
        return 0
    fi

    # Skip vendor files that already have minified versions
    local basename=$(basename "$src")
    case "$basename" in
        bootstrap.css|bootstrap-grid.css|bootstrap-reboot.css|foundation.css)
            echo "  Skipping vendor file: $basename (already has .min.css)"
            return 0
            ;;
    esac

    echo "  Minifying: $(basename "$src") -> $(basename "$dest")"
    npx csso "$src" --output "$dest" 2>/dev/null || {
        echo "    Warning: Failed to minify $src, copying as-is"
        cp "$src" "$dest"
    }
}

# Capture original sizes for comparison
ORIG_JS_SIZE=$(find "${HTML_DIR}/js" -name "*.js" ! -name "*.min.js" -exec cat {} + 2>/dev/null | wc -c)
ORIG_CSS_SIZE=$(find "${HTML_DIR}/css" -name "*.css" ! -name "*.min.css" -exec cat {} + 2>/dev/null | wc -c)

# Minify JavaScript files
echo ""
echo "=== Minifying JavaScript (ES Modules - In-Place) ==="

# ES module files in main js/ directory - minify in-place to preserve import paths
MAIN_ES_MODULES=(
    "exam-session-guard.js"
    "honorlock-common.js"
    "honorlock-backend-v1.js"
    "honorlock-examsubmit-v1.js"
)

for basename in "${MAIN_ES_MODULES[@]}"; do
    jsfile="${HTML_DIR}/js/${basename}"
    [ -f "$jsfile" ] && minify_js_inplace "$jsfile"
done

# ES module files in topology/ directory - minify in-place to preserve import paths
TOPOLOGY_ES_MODULES=(
    "topology-manager.js"
    "cytoscape-styles.js"
    "layout-config.js"
    "event-handlers.js"
    "filter-manager.js"
    "status-updater.js"
    "capture-panel.js"
    "orphaned-slots-monitor.js"
)

for basename in "${TOPOLOGY_ES_MODULES[@]}"; do
    jsfile="${HTML_DIR}/js/topology/${basename}"
    [ -f "$jsfile" ] && minify_js_inplace "$jsfile"
done

echo ""
echo "=== Minifying JavaScript (Regular Scripts) ==="

# Main JS files - create .min.js versions (skip ES modules)
for jsfile in "${HTML_DIR}/js"/*.js; do
    [ -f "$jsfile" ] || continue
    basename=$(basename "$jsfile")

    # Skip ES module files (already processed in-place)
    skip=false
    for esfile in "${MAIN_ES_MODULES[@]}"; do
        if [[ "$basename" == "$esfile" ]]; then
            skip=true
            break
        fi
    done

    if [[ "$skip" == false ]]; then
        minify_js_to_min "$jsfile"
    fi
done

echo ""
echo "=== Minifying JavaScript (Topology Regular Scripts) ==="

# Topology files that are NOT ES modules - create .min.js versions
for jsfile in "${HTML_DIR}/js/topology"/*.js; do
    [ -f "$jsfile" ] || continue
    basename=$(basename "$jsfile")

    # Skip ES module files (already processed in-place)
    skip=false
    for esfile in "${TOPOLOGY_ES_MODULES[@]}"; do
        if [[ "$basename" == "$esfile" ]]; then
            skip=true
            break
        fi
    done

    if [[ "$skip" == false ]]; then
        minify_js_to_min "$jsfile"
    fi
done

# Minify CSS files
echo ""
echo "=== Minifying CSS ==="

for cssfile in "${HTML_DIR}/css"/*.css; do
    [ -f "$cssfile" ] && minify_css "$cssfile"
done

# Show summary
echo ""
echo "=== Build Complete ==="
echo ""
echo "Minified files created:"
MIN_JS_COUNT=$(find "${HTML_DIR}" -name "*.min.js" 2>/dev/null | wc -l | tr -d ' ')
MIN_CSS_COUNT=$(find "${HTML_DIR}" -name "*.min.css" 2>/dev/null | wc -l | tr -d ' ')
echo "  JavaScript (.min.js): ${MIN_JS_COUNT}"
echo "  CSS (.min.css): ${MIN_CSS_COUNT}"
echo ""

# Calculate sizes
NEW_JS_SIZE=$(find "${HTML_DIR}/js" -name "*.min.js" -exec cat {} + 2>/dev/null | wc -c)
NEW_CSS_SIZE=$(find "${HTML_DIR}/css" -name "*.min.css" -exec cat {} + 2>/dev/null | wc -c)

echo "Size comparison (regular scripts only):"
printf "  Original JS:  %.1fKB\n" $(echo "scale=1; ${ORIG_JS_SIZE}/1024" | bc)
printf "  Minified JS:  %.1fKB\n" $(echo "scale=1; ${NEW_JS_SIZE}/1024" | bc)
printf "  Original CSS: %.1fKB\n" $(echo "scale=1; ${ORIG_CSS_SIZE}/1024" | bc)
printf "  Minified CSS: %.1fKB\n" $(echo "scale=1; ${NEW_CSS_SIZE}/1024" | bc)

if [ "$ORIG_JS_SIZE" -gt 0 ]; then
    JS_SAVINGS=$(echo "scale=0; (1 - ${NEW_JS_SIZE}/${ORIG_JS_SIZE}) * 100" | bc)
    echo ""
    echo "  JS size reduction: ~${JS_SAVINGS}%"
fi
