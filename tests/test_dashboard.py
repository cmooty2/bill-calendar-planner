# ============================================================
#  TEST DASHBOARD PAGE LOADS
# ============================================================

from playwright.sync_api import Page, expect
from config import BASE_URL
import re

def test_dashboard_loads(page: Page):

    page.goto(f"{BASE_URL}/dashboard")

    expect(
        page.get_by_role("heading", name="Dashboard Overview")
    ).to_be_visible()

    expect(
        page.get_by_role("link", name="Calendar")
    ).to_be_visible()

    expect(
        page.get_by_role("link", name="Bills")
    ).to_be_visible()