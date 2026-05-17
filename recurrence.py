from datetime import timedelta, date
from models import db, Bill, Occurrence, ExtraExpense
from flask import current_app

def generate_occurrences(bill_id):
    bill = Bill.query.get(bill_id)
    if not bill:
        return

    start = bill.first_due
    occurrences = []

    for i in range(36):
        if bill.recurrence == "Monthly":
            next_date = start.replace(month=((start.month - 1 + i) % 12) + 1)
        elif bill.recurrence == "Weekly":
            next_date = start + timedelta(days=7 * i)
        else:
            next_date = start

        occurrences.append(Occurrence(
            bill_id=bill.id,
            due_date=min(next_date, next_date.replace(day=28))
        ))

    db.session.add_all(occurrences)
    db.session.commit()


def get_paycheck_window(payday: date):
    """Return paycheck start/end dates (14-day window)."""
    start = payday
    end = payday + timedelta(days=13)
    return start, end


def get_daily_bills(start, end):
    """Return dict: { date: [bill_amounts...] }"""
    daily = {}

    occurrences = Occurrence.query.filter(
        Occurrence.due_date >= start,
        Occurrence.due_date <= end
    ).all()

    for occ in occurrences:
        d = occ.due_date
        if d not in daily:
            daily[d] = []
        daily[d].append(occ.bill.amount)

    return daily


def get_daily_extra_expenses(start, end):
    """Return dict: { date: [extra_expense_amounts...] }"""
    daily = {}

    extras = ExtraExpense.query.filter(
        ExtraExpense.date >= start,
        ExtraExpense.date <= end
    ).all()

    for ex in extras:
        d = ex.date
        if d not in daily:
            daily[d] = []
        daily[d].append(ex.amount)

    return daily


def calculate_daily_forecast(paycheck_amount, payday):
    """Main safe-to-spend forecast engine."""
    
    start, end = get_paycheck_window(payday)

    daily_bills = get_daily_bills(start, end)
    daily_extras = get_daily_extra_expenses(start, end)

    # STARTING BALANCE = paycheck
    balance = paycheck_amount

    forecast = []

    current = start
    while current <= end:
        bills_today = sum(daily_bills.get(current, []))
        extras_today = sum(daily_extras.get(current, []))

        total_out = bills_today + extras_today
        safe_to_spend = max(balance - total_out, 0)

        # Record the day's forecast
        forecast.append({
            "date": current,
            "balance_start": balance,
            "bills": bills_today,
            "extras": extras_today,
            "safe_to_spend": safe_to_spend,
            "balance_end": balance - total_out
        })

        balance -= total_out
        current += timedelta(days=1)

    return forecast


