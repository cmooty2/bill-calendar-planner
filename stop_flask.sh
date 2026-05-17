#!/bin/zsh

echo "🛑 Stopping Bills Dashboard Flask App..."

# Kill any running flask dev server
pkill -f "flask --app app run" 2>/dev/null || true

# Optional: if you want to be extra sure, also kill plain 'flask'
pkill -f "flask" 2>/dev/null || true

# (Optional) If you ONLY ever run this app's python, you could also:
# pkill -f "python" 2>/dev/null || true

echo "✅ Flask dev server stopped."
