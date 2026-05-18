# ============================================================
#  TEST CALENDAR PAGE LOADS
# ============================================================

from datetime import date
from playwright.sync_api import Page, expect
from config import BASE_URL
import re

def get_current_calendar_url():
    today = date.today()
    return f"{BASE_URL}/calendar/{today.year}/{today.month}"

def test_calendar_loads_current_month(page: Page):
    page.goto(get_current_calendar_url())

    expect(
        page.get_by_role("heading", name="Calendar")
    ).to_be_visible()