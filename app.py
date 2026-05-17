from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import date, datetime, timedelta
from models import db, Bill, Occurrence, PaycheckBalance, ExtraExpense
from calendar import monthrange
import calendar
import os
import json
from types import SimpleNamespace
from recurrence import calculate_daily_forecast, get_paycheck_window

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ============================================================
#  DATABASE CONFIG
# ============================================================
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bills.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# ============================================================
# PAYDAY SETTINGS
# ============================================================
# FIRST paycheck the user received.
PAYDAY_START = date(2025, 12, 5)      # <-- You may update this if needed
PAYDAY_INTERVAL = 14                 # bi-weekly pay cycle

def generate_paydays(start, count=200):
    """Generate a long list of future bi-weekly paydays."""
    return [start + timedelta(days=PAYDAY_INTERVAL * i) for i in range(count)]

PAYDAYS = generate_paydays(PAYDAY_START)

def get_previous_and_next_paydays(today):
    """Return most recent and upcoming payday."""
    past = [p for p in PAYDAYS if p <= today]
    future = [p for p in PAYDAYS if p > today]
    return (past[-1] if past else None), (future[0] if future else None)

def get_next_payday():
    today = date.today()
    for p in PAYDAYS:
        if p >= today:
            return p
    return PAYDAYS[-1]


# ============================================================
#  CATEGORY ICONS
# ============================================================
CATEGORY_ICONS = {
    "Utilities": "💡",
    "Rent/Mortgage": "🏡",
    "Home Expenses": "🏘️",
    "Credit Cards": "💳",
    "Subscriptions": "📺",
    "Insurance": "🛡️",
    "Installment Loans": "💳",
    "Loans": "💰",
    "Auto Services": "🚗",
    "OnStar": "🚘",
    "Sirius XM": "📻",
    "Misc": "🧾",
    "Indiana Taxes": "🏛️",
}

# ============================================================
#  RECURRENCE ENGINE
# ============================================================
def add_months(original_date, months):
    year = original_date.year + (original_date.month - 1 + months) // 12
    month = (original_date.month - 1 + months) % 12 + 1
    last_day = monthrange(year, month)[1]
    day = min(original_date.day, last_day)
    return date(year, month, day)

def generate_occurrences(bill):
    """Regenerate future occurrences but preserve paid ones."""
    
    # Get all old occurrences
    old = Occurrence.query.filter_by(bill_id=bill.id).all()

    # Preserve paid occurrences
    preserved_paid = [o for o in old if o.status == "Paid"]

    # Delete unpaid occurrences
    for o in old:
        if o.status != "Paid":
            db.session.delete(o)

    db.session.commit()

    # Start recurrence generation from bill.first_due
    current = bill.first_due

    occurrences_created = 0
    natural_end = bill.end_date or date.today().replace(year=date.today().year + 5)

    while True:
        if current > natural_end:
            break
        if bill.end_after_occurrences and occurrences_created >= bill.end_after_occurrences:
            break

        # Only create occurrences that don't already exist
        exists = any(o.due_date == current for o in preserved_paid)
        if not exists:
            occ = Occurrence(
                bill_id=bill.id,
                due_date=current,
                status="Unpaid",
                is_overdue=False
            )
            db.session.add(occ)

        occurrences_created += 1

        rec = bill.recurrence.lower()
        if rec == "one-time":
            break
        elif rec == "weekly":
            current += timedelta(weeks=1)
        elif rec == "biweekly":
            current += timedelta(weeks=2)
        elif rec == "monthly":
            current = add_months(current, 1)
        elif rec == "quarterly":
            current = add_months(current, 3)
        elif rec == "yearly":
            current = add_months(current, 12)
        elif rec == "custom":
            if bill.custom_unit == "days":
                current += timedelta(days=bill.custom_interval)
            elif bill.custom_unit == "weeks":
                current += timedelta(weeks=bill.custom_interval)
            elif bill.custom_unit == "months":
                current = add_months(current, bill.custom_interval)
            else:
                raise Exception("Invalid custom recurrence unit")
        else:
            raise Exception(f"Unknown recurrence type: {bill.recurrence}")

    db.session.commit()

def cleanup_old_occurrences(months=6):
    cutoff = date.today() - timedelta(days=months * 30)
    Occurrence.query.filter(Occurrence.due_date < cutoff).delete()
    db.session.commit()


# ============================================================
# HELPER – Rolls forward paycheck windows until we find the window that contains 'today'.
#    Ensures dashboard always reflects the correct current period.
# ============================================================

def get_active_pay_window(today, pay_frequency_days=14):
    """
    Returns the *current* active pay window based on today's date.
    If today's date is past the previous period, keep rolling forward.
    """

    pay_start = PAYDAY_START  # whatever your first paycheck date is
    pay_end = pay_start + timedelta(days=pay_frequency_days)

    # Move forward until we reach the correct active window
    while today >= pay_end:
        pay_start = pay_end
        pay_end = pay_start + timedelta(days=pay_frequency_days)

    return pay_start, pay_end

# ============================================================
# GET MOST RECENT PAYDAY
# ============================================================
def get_most_recent_payday(today):
    """Return the most recent payday (including today if it's payday)."""
    next_pd = get_next_payday()
    prev_pd = next_pd - timedelta(days=14)
    if today < next_pd:
        return prev_pd
    return next_pd


# ============================================================
# CALCULATE DAILY FORECAST
# ============================================================
def calculate_daily_forecast(starting_balance, last_payday, start_from_date=None):
    """
    Returns a list of daily balance projections for the paycheck window.

    If start_from_date is provided, the forecast starts on that date (within the window),
    and starting_balance is treated as the balance AT THAT DATE (not payday).
    """

    window_start = last_payday
    window_end = last_payday + timedelta(days=13)  # 14-day window

    # Decide where to start the forecast
    current_date = start_from_date or window_start
    if current_date < window_start:
        current_date = window_start
    if current_date > window_end:
        return []  # nothing to forecast

    balance = starting_balance
    forecast = []

    # Only pull bills/extras from the forecast start date forward
    occurrences = (
        Occurrence.query
        .filter(Occurrence.due_date >= current_date)
        .filter(Occurrence.due_date <= window_end)
        .all()
    )

    bills_by_date = {}
    for o in occurrences:
        bills_by_date.setdefault(o.due_date, 0)
        bills_by_date[o.due_date] += o.bill.amount

    extras = (
        ExtraExpense.query
        .filter(ExtraExpense.date >= current_date)
        .filter(ExtraExpense.date <= window_end)
        .all()
    )

    extras_by_date = {}
    for e in extras:
        extras_by_date.setdefault(e.date, 0)
        extras_by_date[e.date] += e.amount  # negative=expense, positive=deposit

    while current_date <= window_end:
        day_bills = bills_by_date.get(current_date, 0)
        balance_after_bills = balance - day_bills

        day_extras = extras_by_date.get(current_date, 0)
        balance_after_extras = balance_after_bills + day_extras

        safe_to_spend = balance_after_extras - balance

        forecast.append({
            "date": current_date,
            "balance_start": balance,
            "bills": day_bills,
            "extras": day_extras,
            "safe_to_spend": safe_to_spend,
            "balance_end": balance_after_extras
        })

        balance = balance_after_extras
        current_date += timedelta(days=1)

    return forecast


def get_hidden_dashboard_occurrence_ids():
    """
    Returns a set of occurrence IDs that the user chose to hide
    only on the dashboard Upcoming/Overdue cards.
    """
    hidden_ids = session.get("hidden_dashboard_occurrence_ids", [])
    return set(hidden_ids)



# ============================================================
# ROUTES
# ============================================================
# ============================================================
# HOME REDIRECT
# ============================================================
@app.route("/")
def home():
    return redirect(url_for("dashboard"))

# ------------------------------------------------------------
# DASHBOARD (FINAL REGENERATED VERSION)
# ------------------------------------------------------------
@app.route("/dashboard")
def dashboard():

    cleanup_old_occurrences()

    today = date.today()

    # Find the most recent payday INCLUDING today
    last_or_current_payday = max([p for p in PAYDAYS if p <= today])

    # The next payday AFTER today
    next_payday = next(p for p in PAYDAYS if p > today)
    prev_payday, upcoming_payday = get_previous_and_next_paydays(today)

       # All occurrences
    all_occ = Occurrence.query.order_by(Occurrence.due_date).all()

    # Dashboard-only hidden occurrence IDs
    hidden_dashboard_ids = get_hidden_dashboard_occurrence_ids()

    # ------------------------------------------------------------
    # CURRENT PAYCHECK WINDOW
    # ------------------------------------------------------------
    current_start = prev_payday
    current_end = upcoming_payday

    current_window_bills = [
        o for o in all_occ
        if current_start <= o.due_date < current_end
    ]

    current_window = SimpleNamespace(
        start=current_start,
        end=current_end,
        bills=current_window_bills,
        total_amount=sum(o.bill.amount for o in current_window_bills),
        total_count=len(current_window_bills),
        paid_count=sum(1 for o in current_window_bills if o.status == "Paid"),
        unpaid_count=sum(1 for o in current_window_bills if o.status != "Paid"),
    )
    
    # ------------------------------------------------------------
    # DAILY TOTALS FOR BAR CHART
    # ------------------------------------------------------------
    # Build a dict {date: amount}
    daily_dict = {}

    for occ in current_window_bills:
        day = occ.due_date
        if day not in daily_dict:
            daily_dict[day] = 0
        daily_dict[day] += occ.bill.amount

    # Convert dict → sorted list of SimpleNamespace objects
    daily_totals = [
        SimpleNamespace(date=d, amount=amt)
        for d, amt in sorted(daily_dict.items())
    ]

    current_window.daily_totals = daily_totals

    # ------------------------------------------------------------
    # NEXT TWO PAYCHECK WINDOWS
    # ------------------------------------------------------------
    next1_start = current_end
    next1_end = next1_start + timedelta(days=14)

    next2_start = next1_end
    next2_end = next2_start + timedelta(days=14)

    pay1_bills = [o for o in all_occ if next1_start <= o.due_date < next1_end]
    pay2_bills = [o for o in all_occ if next2_start <= o.due_date < next2_end]

    two_paychecks = SimpleNamespace(
        pay1_date=next1_start,
        pay1_bills=pay1_bills,
        pay1_total=sum(o.bill.amount for o in pay1_bills),
        pay1_window_start=next1_start,
        pay1_window_end=next1_end,

        pay2_date=next2_start,
        pay2_bills=pay2_bills,
        pay2_total=sum(o.bill.amount for o in pay2_bills),
        pay2_window_start=next2_start,
        pay2_window_end=next2_end,
    )

    # ------------------------------------------------------------
    # MONTHLY CALCULATIONS
    # ------------------------------------------------------------
    this_month = [
        o for o in all_occ
        if o.due_date.month == today.month and o.due_date.year == today.year
    ]

    total_due_month = sum(o.bill.amount for o in this_month)
    total_paid = sum(o.bill.amount for o in this_month if o.status == "Paid")

    # ------------------------------------------------------------
    # TRUE OVERDUE (all overdue bills prior to today)
    overdue_all = [
        o for o in all_occ
        if o.status != "Paid"
        and o.due_date < today
        and o.id not in hidden_dashboard_ids
    ]
    
    overdue = overdue_all  # full overdue list (used in tab)

    # Metric card should match overdue tab: total of ALL overdue bills
    total_overdue = sum(o.bill.amount for o in overdue_all)

    # ------------------------------------------------------------
    # UPCOMING BILLS UNTIL NEXT PAYDAY (for metric card)
    # ------------------------------------------------------------
    upcoming_until_payday = [
        o for o in all_occ
        if o.status != "Paid"
        and today <= o.due_date < current_end
        and o.id not in hidden_dashboard_ids
    ]
    
    upcoming_until_payday_total = sum(o.bill.amount for o in upcoming_until_payday)

    # ------------------------------------------------------------
    # PAYCHECK BALANCE BOX
    # ------------------------------------------------------------
    pb = PaycheckBalance.query.first()
    paycheck_amount = pb.amount if pb else 0.0
    remaining_balance = paycheck_amount - upcoming_until_payday_total

    # ------------------------------------------------------------
    # UPCOMING TAB LIST (unpaid bills UNTIL END OF CURRENT PAYCHECK WINDOW)
    # ------------------------------------------------------------
    upcoming = [
        o for o in all_occ
        if o.status != "Paid"
        and today <= o.due_date < current_end
        and o.id not in hidden_dashboard_ids   # dashboard-only hidden cards
    ]

    # ------------------------------------------------------------
    # RENDER TEMPLATE
    # ------------------------------------------------------------
    return render_template(
        "dashboard.html",
        today=today,
        total_due_month=total_due_month,
        total_paid=total_paid,
        total_overdue=total_overdue,
        last_or_current_payday=last_or_current_payday,
        overdue=overdue,              # Full overdue list (template filters it)
        next_payday=next_payday,

        total_upcoming_until_next_payday=upcoming_until_payday_total,
        paycheck_amount=paycheck_amount,
        remaining_balance=remaining_balance,

        two_paychecks=two_paychecks,
        upcoming=upcoming,
        current_window=current_window,

        category_icons=CATEGORY_ICONS,
        timedelta=timedelta
    )
    
    
# =====================================================================
# hide a dashboard card
# =====================================================================
@app.route("/dashboard/hide-occurrence/<int:occ_id>", methods=["POST"])
def hide_dashboard_occurrence(occ_id):
    """
    Hides an occurrence only from the dashboard Upcoming/Overdue cards
    and the related metric cards. It does NOT delete the occurrence
    from the database or remove it from other pages.
    """
    hidden_ids = session.get("hidden_dashboard_occurrence_ids", [])

    if occ_id not in hidden_ids:
        hidden_ids.append(occ_id)

    session["hidden_dashboard_occurrence_ids"] = hidden_ids
    return redirect(url_for("dashboard"))

# ============================================================
# UPDATE PAYCHECK AMOUNT
# ============================================================
@app.route("/update-paycheck", methods=["POST"])
def update_paycheck():
    amount_str = request.form.get("paycheck_amount", "0").strip()

    try:
        amount = float(amount_str)
    except ValueError:
        amount = 0.0

    pb = PaycheckBalance.query.first()
    if not pb:
        pb = PaycheckBalance(amount=amount)
        db.session.add(pb)
    else:
        pb.amount = amount

    db.session.commit()

    # NEW: allow redirect back to forecast OR dashboard
    redirect_target = request.form.get("redirect") or "dashboard"
    return redirect(url_for(redirect_target))


# ============================================================
# NEXT TWO PAYCHECKS
# ============================================================
def get_next_two_paychecks(today, occurrences):
    """Return the next two paycheck windows with bills inside each 14-day window."""

    next1 = get_next_payday()             # e.g., Dec 5
    next2 = next1 + timedelta(days=14)    # e.g., Dec 19

    # Your defined windows
    window1_start = next1
    window1_end   = next1 + timedelta(days=13)   # Dec 5 → Dec 18

    window2_start = next2
    window2_end   = next2 + timedelta(days=13)   # Dec 19 → Jan 1

    # Bills in Paycheck #1 window
    bills_1 = [
        o for o in occurrences
        if window1_start <= o.due_date <= window1_end
    ]

    # Bills in Paycheck #2 window
    bills_2 = [
        o for o in occurrences
        if window2_start <= o.due_date <= window2_end
    ]

    return {
        "pay1_date": next1,
        "pay1_bills": bills_1,
        "pay2_date": next2,
        "pay2_bills": bills_2,
        "pay1_window_start": window1_start,
        "pay1_window_end": window1_end,
        "pay2_window_start": window2_start,
        "pay2_window_end": window2_end,
    }



# ============================================================
# CURRENT PAYCHECK WINDOW  (FIXED)
# ============================================================
def get_current_paycheck_window(today, occurrences):
    next1 = get_next_payday()
    last = next1 - timedelta(days=14)  # previous paycheck

    # bills strictly BEFORE the next paycheck
    bills = [
        o for o in occurrences
        if last <= o.due_date < next1
    ]

    total_amount = sum(o.bill.amount for o in bills)
    total_count = len(bills)
    paid_count = len([o for o in bills if o.status == "Paid"])
    unpaid_count = len([o for o in bills if o.status != "Paid"])

    return {
        "start": last,
        "end": next1,
        "bills": bills,
        "total_amount": total_amount,
        "total_count": total_count,
        "paid_count": paid_count,
        "unpaid_count": unpaid_count
    }

# ============================================================
# GET BILLS DUE TODAY NOTIFICATION
# ============================================================
from datetime import date

def get_bills_due_today():
    today = date.today()
    bills = Bill.query.filter_by(due_date=today).all()
    return bills


# ============================================================
# ADD BILL
# ============================================================
@app.route("/add-bill", methods=["GET", "POST"])
def add_bill():
    if request.method == "POST":

        name = request.form["name"]
        amount = float(request.form["amount"])
        category = request.form["category"]

        recurrence = request.form["recurrence"]
        custom_interval = request.form.get("custom_interval") or None
        custom_unit = request.form.get("custom_unit") or None
        end_after_occurrences = request.form.get("end_after_occurrences") or None

        if custom_interval:
            custom_interval = int(custom_interval)
        if end_after_occurrences:
            end_after_occurrences = int(end_after_occurrences)

        first_due = datetime.strptime(request.form["first_due"], "%Y-%m-%d").date()
        end_date_raw = request.form.get("end_date")
        end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else None

        auto_pay = request.form["auto_pay"] == "True"
        notes = request.form.get("notes")

        bill = Bill(
            name=name,
            amount=amount,
            category=category,
            recurrence=recurrence,
            custom_interval=custom_interval,
            custom_unit=custom_unit,
            end_after_occurrences=end_after_occurrences,
            first_due=first_due,
            end_date=end_date,
            auto_pay=auto_pay,
            notes=notes
        )

        db.session.add(bill)
        db.session.commit()

        generate_occurrences(bill)
        return redirect(url_for("dashboard"))

    return render_template("edit_bill.html", bill=None)

# ============================================================
# EDIT BILL
# ============================================================
@app.route("/edit-bill/<int:bill_id>", methods=["GET", "POST"])
def edit_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)

    if request.method == "POST":

        bill.name = request.form["name"]
        bill.amount = float(request.form["amount"])
        bill.category = request.form["category"]

        bill.recurrence = request.form["recurrence"]
        custom_interval = request.form.get("custom_interval") or None
        custom_unit = request.form.get("custom_unit") or None
        end_after_occurrences = request.form.get("end_after_occurrences") or None

        bill.custom_interval = int(custom_interval) if custom_interval else None
        bill.custom_unit = custom_unit if custom_unit else None
        bill.end_after_occurrences = int(end_after_occurrences) if end_after_occurrences else None

        bill.first_due = datetime.strptime(request.form["first_due"], "%Y-%m-%d").date()
        end_date_raw = request.form.get("end_date")
        bill.end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date() if end_date_raw else None

        bill.auto_pay = request.form["auto_pay"] == "True"
        bill.notes = request.form.get("notes")

        db.session.commit()
        generate_occurrences(bill)
        return redirect(url_for("bills"))

    return render_template("edit_bill.html", bill=bill)

# ============================================================
# MANAGE BILLS PAGE
# ============================================================
@app.route("/bills")
def bills():
    bills = Bill.query.order_by(Bill.name).all()
    today = date.today() 
    
    # If bills.html uses category dropdown, also pass categories:
    categories = CATEGORY_ICONS.keys()
    
    return render_template(
        "bills.html",
        bills=bills,
        today=today,              # <-- Now available in template
        categories=categories
    )

# ============================================================
# ALL OCCURRENCES PAGE
# ============================================================
@app.route("/occurrences")
@app.route("/all_occurrences")
def occurrences():
    items = Occurrence.query.order_by(Occurrence.due_date).all()
    return render_template("occurrences.html", occurrences=items)

# ============================================================
# MARK UNPAID
# ============================================================
@app.route("/mark-unpaid/<int:occ_id>")
def mark_unpaid(occ_id):
    occ = Occurrence.query.get_or_404(occ_id)

    occ.status = "Unpaid"
    occ.paid_date = None

    db.session.commit()
    flash(f"{occ.bill.name} was marked as UNPAID.", "warning")

    return redirect(url_for("occurrences"))

# ============================================================
# CALENDAR PAGE
# ============================================================
@app.route("/calendar/<int:year>/<int:month>")
def calendar_view(year, month):
    today = date.today()

    # ---------------------------------------------------------------
    # PAYCHECK WINDOW CALCULATION
    # ---------------------------------------------------------------
    prev_payday, next_payday = get_previous_and_next_paydays(today)

    if prev_payday is None:
        prev_payday = today
    if next_payday is None:
        next_payday = today + timedelta(days=14)

    paycheck_start = prev_payday
    paycheck_end = next_payday

    # ---------------------------------------------------------------
    # MONTH MATRIX FOR CALENDAR GRID
    # ---------------------------------------------------------------
    cal = calendar.Calendar(firstweekday=6)  # Sunday start
    month_matrix = cal.monthdayscalendar(year, month)

    # ---------------------------------------------------------------
    # BILL OCCURRENCES FOR THIS MONTH
    # ---------------------------------------------------------------
    month_start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = date(year, month, last_day)

    bill_occurrences = (
        db.session.query(Occurrence)
        .join(Bill, Occurrence.bill_id == Bill.id)
        .filter(Occurrence.due_date >= month_start)
        .filter(Occurrence.due_date <= month_end)
        .order_by(Occurrence.due_date)
        .all()
    )

    # ---------------------------------------------------------------
    # GROUP OCCURRENCES BY DAY
    # ---------------------------------------------------------------
    calendar_days = {}

    for occ in bill_occurrences:
        day_num = occ.due_date.day
        if day_num not in calendar_days:
            calendar_days[day_num] = []
        calendar_days[day_num].append(occ)


    # ---------------------------------------------------------------
    # PAYDAYS IN CURRENT MONTH
    # ---------------------------------------------------------------
    paydays_in_month = [
        p for p in PAYDAYS if p.year == year and p.month == month
    ]

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        today=today,
        month_matrix=month_matrix,
        calendar_days=calendar_days,
        paycheck_start=paycheck_start,
        paycheck_end=paycheck_end,
        category_icons=CATEGORY_ICONS,
        paydays_in_month=paydays_in_month,
        next_payday=next_payday,
        date=date,
    )


# ============================================================
# DELETE BILL
# ============================================================
@app.route("/delete-bill/<int:bill_id>", methods=["POST"])
def delete_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)

    Occurrence.query.filter_by(bill_id=bill_id).delete()

    db.session.delete(bill)
    db.session.commit()

    return redirect("/bills")

# ============================================================
# MARK PAID FROM CALENDAR
# ============================================================
@app.route("/mark-paid/<int:occ_id>")
def mark_paid(occ_id):
    occ = Occurrence.query.get_or_404(occ_id)

    occ.status = "Paid"
    occ.paid_date = datetime.utcnow()

    db.session.commit()
    flash(
        f"{occ.bill.name} marked as PAID. "
        f"<a href='/undo-paid/{occ.id}' style='color:white;text-decoration:underline;'>Undo</a>",
        "success")
    return redirect(f"/calendar/{occ.due_date.year}/{occ.due_date.month}")

# ============================================================
# MARK AS UNPAID FROM CALENDAR
# ============================================================
@app.route("/undo-paid/<int:occ_id>")
def undo_paid(occ_id):
    occ = Occurrence.query.get_or_404(occ_id)
    occ.status = "Unpaid"
    occ.paid_date = None
    db.session.commit()
    flash(f"Undo: {occ.bill.name} restored to UNPAID.", "warning")
    return redirect(f"/calendar/{occ.due_date.year}/{occ.due_date.month}")


# ============================================================
#   GET PREVIOUS BALANCE
# ============================================================
def get_previous_balance(last_payday):
    """
    Calculate the true ending balance before this paycheck window.
    Only subtract bills that were actually PAID.
    """

    paycheck_row = PaycheckBalance.query.first()
    paycheck_amount = paycheck_row.amount if paycheck_row else 0.0

    # Look back exactly 14 days before last payday
    prev_window_start = last_payday - timedelta(days=14)

    # 👍 Only include PAID occurrences in previous window
    paid_occurrences = Occurrence.query.filter(
        Occurrence.due_date >= prev_window_start,
        Occurrence.due_date < last_payday,
        Occurrence.status == "Paid"   # <<<<<< FIXED
    ).all()

    total_prev_bills = sum(o.bill.amount for o in paid_occurrences)

    # Extras inside previous window (expenses negative, deposits positive)
    extras = ExtraExpense.query.filter(
        ExtraExpense.date >= prev_window_start,
        ExtraExpense.date < last_payday
    ).all()

    total_prev_extras = sum(e.amount for e in extras)

    # REAL previous balance
    previous_balance = paycheck_amount - total_prev_bills + total_prev_extras

    return previous_balance



# ===================================================
# FORECAST
# ===================================================
@app.route("/forecast")
def forecast_page():
    # 1. Determine today and last payday
    today = date.today()
    last_payday = get_most_recent_payday(today)

    # 2. Get paycheck amount from DB
    paycheck_row = PaycheckBalance.query.first()
    paycheck_amount = paycheck_row.amount if paycheck_row else 0.0

    # 3. Overdue bills total (ALL unpaid bills before today)
    overdue_occ = Occurrence.query.filter(
        Occurrence.status != "Paid",
        Occurrence.due_date < today
    ).all()
    overdue_total = sum(o.bill.amount for o in overdue_occ)

    # 4. Auto starting balance = Paycheck - Overdue bills
    computed_starting_balance = paycheck_amount - overdue_total

    # 5. Optional override starting balance (Option C-Plus)
    override_param = (request.args.get("override_start_balance") or "").strip()
    override_start_balance = None
    if override_param:
        try:
            override_start_balance = float(override_param)
        except ValueError:
            override_start_balance = None  # ignore bad input

    # 6. Window dates
    window_start, window_end = get_paycheck_window(last_payday)

    # ------------------------------------------------------------
    # NEW: Decide what date the forecast should start from
    # ------------------------------------------------------------
    if override_start_balance is not None:
        forecast_start_date = max(today, window_start)
        starting_balance = override_start_balance
    else:
        forecast_start_date = window_start
        starting_balance = computed_starting_balance

    # Defaults (so template never breaks)
    forecast = []
    total_bills = 0.0
    total_extras = 0.0
    ending_balance = starting_balance
    negative_balance_warnings = []
    deposit_recommendation = None
    current_window = None

    # NEW: “Insights” for professional summary cards
    min_balance = None
    min_balance_date = None
    safe_avg = 0.0

    # If today is beyond the paycheck window end, nothing to forecast
    if forecast_start_date <= window_end:
        # ------------------------------------------------------------
        # 7. Build CURRENT PAYCHECK WINDOW object (for charts tab)
        # ------------------------------------------------------------
        all_occ = Occurrence.query.order_by(Occurrence.due_date).all()

        current_window_bills = [
            o for o in all_occ
            if window_start <= o.due_date < window_end
        ]

        current_window = SimpleNamespace(
            start=window_start,
            end=window_end,
            bills=current_window_bills,
            total_amount=sum(o.bill.amount for o in current_window_bills),
            total_count=len(current_window_bills),
            paid_count=sum(1 for o in current_window_bills if o.status == "Paid"),
            unpaid_count=sum(1 for o in current_window_bills if o.status != "Paid"),
        )

        # DAILY TOTALS FOR BAR CHART (same structure as Dashboard)
        daily_dict = {}
        for occ in current_window_bills:
            day = occ.due_date
            daily_dict[day] = daily_dict.get(day, 0) + occ.bill.amount

        current_window.daily_totals = [
            SimpleNamespace(date=d, amount=amt)
            for d, amt in sorted(daily_dict.items())
        ]

        # ------------------------------------------------------------
        # 8. Run forecast calculation
        # IMPORTANT: calculate_daily_forecast(starting_balance, last_payday, start_from_date=None)
        # ------------------------------------------------------------
        raw_forecast = calculate_daily_forecast(
            starting_balance,
            last_payday,
            start_from_date=forecast_start_date
        )
        forecast = [SimpleNamespace(**day) for day in raw_forecast]

        # 9. Summary totals
        total_bills = sum(d.bills for d in forecast)
        total_extras = sum(d.extras for d in forecast)
        ending_balance = forecast[-1].balance_end if forecast else starting_balance

        # ------------------------------------------------------------
        # NEW: Insights for Summary Cards
        # ------------------------------------------------------------
        if forecast:
            min_day = min(forecast, key=lambda d: d.balance_end)
            min_balance = float(min_day.balance_end)
            min_balance_date = min_day.date.strftime("%b %d")

            safe_vals = [d.safe_to_spend for d in forecast if (d.safe_to_spend or 0) > 0]
            safe_avg = (sum(safe_vals) / len(safe_vals)) if safe_vals else 0.0

        # ------------------------------------------------------------
        # 10. NEGATIVE BALANCE WARNINGS + deposit suggestion
        # (UPDATED: include date_raw for clickable jump links)
        # ------------------------------------------------------------
        first_negative_day = None

        for day in forecast:
            if day.balance_end < 0:
                negative_balance_warnings.append({
                    "date": day.date.strftime("%b %d"),
                    "date_raw": day.date.strftime("%Y-%m-%d"),  # ✅ NEW for anchor links
                    "amount": f"${day.balance_end:,.2f}"
                })
                if first_negative_day is None:
                    first_negative_day = day

        deposit_recommendation = None
        if first_negative_day is not None:
            needed = -first_negative_day.balance_end  # amount to bring to zero
            deposit_recommendation = SimpleNamespace(
                date=first_negative_day.date.strftime("%b %d"),
                date_raw=first_negative_day.date.strftime("%Y-%m-%d"),  # ✅ NEW
                amount=needed
            )

    # ------------------------------------------------------------
    # Render template
    # ------------------------------------------------------------
    return render_template(
        "forecast.html",
        today=today,
        last_payday=last_payday,
        paycheck_amount=paycheck_amount,
        overdue_total=overdue_total,
        computed_starting_balance=computed_starting_balance,
        override_start_balance=override_start_balance,
        window_start=window_start,
        window_end=window_end,
        forecast_start_date=forecast_start_date,
        starting_balance=starting_balance,
        forecast=forecast,
        total_bills=total_bills,
        total_extras=total_extras,
        ending_balance=ending_balance,
        negative_balance_warnings=negative_balance_warnings,
        deposit_recommendation=deposit_recommendation,
        current_window=current_window,

        # ✅ NEW summary-card variables
        min_balance=min_balance,
        min_balance_date=min_balance_date,
        safe_avg=safe_avg,

        # keep if you’re using active_tab in forecast.html tabs
        active_tab="forecast",
    )


# ------------------------------------------------------------
#   PAYCHECK PLANNER
# ------------------------------------------------------------
@app.route("/paycheck-planner")
def paycheck_planner_page():
    today = date.today()

    # Get paycheck amount from DB (you already do this in forecast/dashboard)
    pb = PaycheckBalance.query.first()
    paycheck_amount = pb.amount if pb else 0.0

    # Build next 6 paycheck dates based on your PAYDAY_START + PAYDAY_INTERVAL
    # (You already use PAYDAY_START/PAYDAY_INTERVAL patterns elsewhere.)
    def get_next_payday_from(today):
        # roll forward from PAYDAY_START until >= today
        d = PAYDAY_START
        while d < today:
            d += timedelta(days=PAYDAY_INTERVAL)
        return d

    first = get_next_payday_from(today)
    paycheck_dates = [first + timedelta(days=PAYDAY_INTERVAL * i) for i in range(6)]

    # Bills to plan: upcoming unpaid occurrences for the next ~60 days
    # Bills to plan: include a small overdue lookback + upcoming horizon
    # This helps catch bills due yesterday (ex: Mortgage on the 1st when today is the 2nd)
    lookback_days = 7
    horizon_days = 60

    start_date = today - timedelta(days=lookback_days)
    horizon_end = today + timedelta(days=horizon_days)

    occ = (
        Occurrence.query
        .filter(Occurrence.due_date >= start_date)
        .filter(Occurrence.due_date <= horizon_end)
        .order_by(Occurrence.due_date.asc())
        .all()
    )


    bills = []
    for o in occ:
        bills.append({
            "id": o.id,
            "name": o.bill.name,
            "due_date": o.due_date.strftime("%Y-%m-%d"),
            "due_label": o.due_date.strftime("%b %d"),
            "category": getattr(o.bill, "category", "") or "",
            "amount": float(o.bill.amount or 0.0),
            "status": o.status,
            "auto_pay": int(getattr(o.bill, "auto_pay", 0) or 0),
            "autopay": "🔁" if int(getattr(o.bill, "auto_pay", 0) or 0) == 1 else "❌"
        })

    # --- Affirm payoff tracker (wired to DB) ---
    # We treat each "Affirm - ..." Bill as a loan, and compute remaining from unpaid occurrences.
    # Remaining = unpaid_occurrence_count * monthly_payment
    # Months left = unpaid_occurrence_count

    affirm_loans = []

    # Pull Affirm & Klarna bills from DB (optionally also require category == "Installment Loans")
    affirm_bills = (
        Bill.query
        .filter(Bill.name.like("Affirm -%"))
        .all()
    )

        # --- Affirm / Klarna payoff tracker (wired to DB) ---
    # We treat each "Affirm - ..." or "Klarna ..." Bill as a loan.
    # Remaining = unpaid_occurrence_count * monthly_payment
    # Months left = unpaid_occurrence_count
    # Progress % = paid_occurrence_count / total_occurrence_count

    payoff_loans = []

    tracker_bills = (
        Bill.query
        .filter(
            (Bill.name.like("Affirm -%")) |
            (Bill.name.like("Klarna%"))
        )
        .all()
    )

    for b in tracker_bills:
        monthly = float(b.amount or 0.0)

        total_count = (
            Occurrence.query
            .filter(Occurrence.bill_id == b.id)
            .count()
        )

        paid_count = (
            Occurrence.query
            .filter(Occurrence.bill_id == b.id)
            .filter(Occurrence.status == "Paid")
            .count()
        )

        unpaid_count = max(total_count - paid_count, 0)
        remaining = monthly * unpaid_count
        pct_paid = (paid_count / total_count * 100.0) if total_count > 0 else 0.0

        payoff_loans.append({
            "name": b.name,
            "monthly": monthly,
            "remaining": remaining,
            "months_left_est": unpaid_count,
            "total_count": total_count,
            "paid_count": paid_count,
            "pct_paid": pct_paid
        })

    # Sort by remaining balance (least → most)
    payoff_loans.sort(key=lambda x: float(x.get("remaining", 0.0)))

    # Add a simple “months left” estimate for display
    for loan in payoff_loans:
        m = loan["monthly"] if loan["monthly"] else 0.0
        loan["months_left_est"] = int((loan["remaining"] / m) + 0.999) if m > 0 else 0

    return render_template(
        "paycheck_planner.html",
        paycheck_amount=paycheck_amount,
        paycheck_dates=paycheck_dates,
        bills=bills,
        #affirm_loans=affirm_loans,
        payoff_loans=payoff_loans,
        today=today,
        # pass JSON for drag/drop UI
        bills_json=json.dumps(bills),
        active_tab="forecast",
        paycheck_dates_json=json.dumps([d.strftime("%Y-%m-%d") for d in paycheck_dates]),
    )



# ============================================================
#   EXTRAS
# ============================================================
@app.route("/extras")
def list_extras():
    extras = (
        ExtraExpense.query
        .order_by(
            ExtraExpense.date.desc(),   # newest date first
            ExtraExpense.id.desc()      # newest entry within that date first
        )
        .all()
    )
    return render_template("extras_list.html", extras=extras)


# ============================================================
#   ADD EXTRA (Expense or Deposit)
# ============================================================
@app.route("/add-extra", methods=["GET", "POST"])
def add_extra():
    if request.method == "POST":
        # Get form fields
        date_str = request.form["date"]
        amount = float(request.form["amount"])
        note = request.form.get("note", "")
        extra_type = request.form.get("type", "expense")  # NEW FIELD

        # Convert date
        date_value = datetime.strptime(date_str, "%Y-%m-%d")

        # Apply sign logic:
        # Expense → store negative (-10)
        # Deposit → store positive (+50)
        if extra_type == "expense":
            amount = -abs(amount)
        else:
            amount = abs(amount)

        # Create DB entry
        new_extra = ExtraExpense(
            date=date_value,
            amount=amount,
            note=note,
            type=extra_type
        )

        db.session.add(new_extra)
        db.session.commit()

        flash("Extra saved!", "success")
        return redirect(url_for("forecast_page"))

    # GET request → show the form
    return render_template("add_extra.html")


# ============================================================
#   DELETE
# ============================================================
@app.route("/extras/delete/<int:id>")
def delete_extra(id):
    extra = ExtraExpense.query.get_or_404(id)
    db.session.delete(extra)
    db.session.commit()
    flash("Extra expense deleted.", "success")
    return redirect(url_for("list_extras"))

# ============================================================
#   EDIT
# ============================================================
# ============================================================
#   EDIT EXTRA (Expense or Deposit)
# ============================================================
# ============================================================
#   EDIT EXTRA (Expense or Deposit)
# ============================================================
@app.route("/extras/edit/<int:id>", methods=["GET", "POST"])
def edit_extra(id):
    extra = ExtraExpense.query.get_or_404(id)

    if request.method == "POST":
        # Get incoming values
        date_str = request.form["date"]
        amount = float(request.form["amount"])
        note = request.form.get("note", "")
        extra_type = request.form.get("type", "expense")

        # Convert date
        extra.date = datetime.strptime(date_str, "%Y-%m-%d")

        # Apply positive/negative logic
        if extra_type == "expense":
            extra.amount = -abs(amount)  # always store negative
        else:
            extra.amount = abs(amount)   # always positive

        # Update type + note
        extra.type = extra_type
        extra.note = note

        db.session.commit()
        flash("Extra updated successfully!", "success")
        return redirect(url_for("list_extras"))

    return render_template("edit_extra.html", extra=extra)




# ============================================================
# APP START
# ============================================================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
