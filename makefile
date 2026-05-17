.PHONY: restart run kill

# Kill all running Flask + Python processes
kill:
	pkill -f flask || true
	pkill -f python || true

# Start Flask app
run:
	flask --app app run

# Restart Flask server (kill + run)
restart: kill run
