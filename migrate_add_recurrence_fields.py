from app import app, db
from sqlalchemy import text

# New recurrence fields to add
columns = [
    ("recurrence_type", "TEXT"),
    ("custom_interval", "INTEGER"),
    ("custom_unit", "TEXT"),
    ("end_after_occurrences", "INTEGER")
]

with app.app_context():      # <<< REQUIRED FIX
    engine = db.engine

    with engine.connect() as conn:
        # Read table info
        existing = conn.execute(text("PRAGMA table_info(bill);")).fetchall()
        existing_cols = {row[1] for row in existing}

        for col_name, col_type in columns:
            if col_name not in existing_cols:
                print(f"Adding column: {col_name}")
                conn.execute(text(f"ALTER TABLE bill ADD COLUMN {col_name} {col_type};"))
            else:
                print(f"Column already exists: {col_name}")

        print("\nMigration complete.")
