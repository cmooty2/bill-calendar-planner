from app import app, db
from models import ExtraExpense

# You MUST open an app context for db.create_all()
with app.app_context():
    db.create_all()
    print("ExtraExpense table created (or already exists).")
