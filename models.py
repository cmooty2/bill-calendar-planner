from flask_sqlalchemy import SQLAlchemy
from datetime import date

db = SQLAlchemy()

class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50))

    # Recurrence type now supports: "One-Time", "Weekly",
    # "Biweekly", "Monthly", "Quarterly", "Yearly", "Custom"
    recurrence = db.Column(db.String(20), default="Monthly")

    # NEW: advanced recurrence options
    custom_interval = db.Column(db.Integer, nullable=True)   # e.g. 3
    custom_unit = db.Column(db.String(10), nullable=True)    # "days", "weeks", "months"
    end_after_occurrences = db.Column(db.Integer, nullable=True)  # e.g. stop after 10 payments

    first_due = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    auto_pay = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text, nullable=True)

    occurrences = db.relationship(
        "Occurrence",
        backref="bill",
        cascade="all, delete-orphan"
    )

class Occurrence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=False)

    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="Unpaid")

    is_overdue = db.Column(db.Boolean, default=False)
    
class PaycheckBalance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, default=0.0)

class ExtraExpense(db.Model):
    __tablename__ = "extra_expense"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255))
    
    # NEW FIELD → "expense" or "deposit"
    type = db.Column(db.String(20), default="expense")  

    def __repr__(self):
        return f"<ExtraExpense {self.date} ${self.amount}>"


