#!/bin/bash

# Video Cut Flask Server Launcher
# This script automatically uses Python 3.12 for proper YouTube video extraction

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check Python 3.12 is available
if ! command -v python3.12 &> /dev/null; then
    echo "ERROR: Python 3.12 not found!"
    echo "Please install Python 3.12 first."
    exit 1
fi

# ========== AUTOMATIC TIMEOUT CALCULATION ==========
# Server now automatically detects video duration and sets appropriate timeout
# Formula: (duration_in_seconds * 1.5) + 120 seconds buffer
# No manual configuration needed - all video lengths are supported!

# Show info
echo "=========================================="
echo "🎬 Video Cut Flask Server"
echo "=========================================="
echo "Python Version: $(python3.12 --version)"
echo "Server Directory: $SCRIPT_DIR"
echo "URL: http://127.0.0.1:5000"
echo "Download support: Automatic for all video lengths"
echo "=========================================="
echo ""
echo "✅ Automatic timeout enabled"
echo "   Videos of ANY length will work without manual setup!"
echo ""
echo "Press CTRL+C to stop the server"
echo ""

# Start server with Python 3.12
cd "$SCRIPT_DIR"
exec python3.12 app.py
