# ============================================================
#  TEST ADD BILL PAGE LOADS
# ============================================================

from playwright.sync_api import Page, expect
from config import BASE_URL
import re

def test_add_bills_loads(page: Page):

    page.goto(f"{BASE_URL}/add-bill")
    
    expect(
        page.get_by_role("heading", name="Add Bill")
    ).to_be_visible()