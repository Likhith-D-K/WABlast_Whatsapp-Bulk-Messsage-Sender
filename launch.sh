#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# launch.sh — Run WhatsApp Bulk Messenger
# ──────────────────────────────────────────────────────────────────────────────
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv"

# Create venv if missing
if [ ! -d "$VENV" ]; then
    echo "📦 Creating virtual environment…"
    python3 -m venv "$VENV"
fi

# Install / upgrade deps
echo "📦 Installing dependencies…"
"$VENV/bin/pip" install -q -r "$DIR/requirements.txt"

# Launch app
echo "🚀 Starting app…"
"$VENV/bin/python" "$DIR/app.py"
