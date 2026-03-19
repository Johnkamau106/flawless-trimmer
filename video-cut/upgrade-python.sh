#!/bin/bash

# Setup Python 3.12 environment for video-cut project
# This fixes YouTube signature extraction failures with Python 3.8

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "📦 Setting up Python 3.12 environment in: $PROJECT_DIR"

# Create venv with Python 3.12
echo "🐍 Creating Python 3.12 virtual environment..."
python3.12 -m venv "$PROJECT_DIR/venv_py312"

# Activate and upgrade pip
echo "📥 Installing pip packages..."
"$PROJECT_DIR/venv_py312/bin/pip" install --upgrade pip setuptools wheel

# Install requirements
echo "📦 Installing project dependencies..."
cd "$PROJECT_DIR/server"
"$PROJECT_DIR/venv_py312/bin/pip" install -r requirements.txt

# Test import
echo "✅ Testing imports..."
"$PROJECT_DIR/venv_py312/bin/python" -c "import yt_dlp, flask; print('✓ All packages working!')"

echo ""
echo "========================================="
echo "✅ Setup complete!"
echo "========================================="
echo ""
echo "To run the Flask app with Python 3.12, use:"
echo ""
echo "  cd $PROJECT_DIR"
echo "  ./venv_py312/bin/python server/app.py"
echo ""
echo "Or activate the venv and run normally:"
echo ""
echo "  source ./venv_py312/bin/activate"
echo "  python server/app.py"
echo ""
