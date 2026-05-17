#!/bin/zsh

echo "🛑 Stopping running Flask/Python processes..."
pkill -f flask 2>/dev/null
pkill -f python 2>/dev/null

echo "🚀 Starting Bills Dashboard Flask App..."

# Activate venv manually because VS Code tasks do NOT inherit the terminal session
source "$(dirname "$0")/venv/bin/activate"

# Run flask from inside the venv
flask --app app run --debug


# cd to project folder:  /Users/cherriemooty/bill-calendar/restart.sh
# $ pkill -f flask
# $ pkill -f python
# $ source venv/bin/activate
# $ flask --app app run --debug