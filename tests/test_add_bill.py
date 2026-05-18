# ============================================================
#  TEST ADD BILL PAGE LOADS
# ============================================================

from playwright.sync_api import Page, expect
from config import BASE_URL
import re
from datetime import datetime


def test_add_new_bill(page: Page):
    # Use a unique bill name so each test run does not conflict with old data
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    bill_name = f"Water Playwright Test {timestamp}"

    # Go to Add Bill page
    page.goto(f"{BASE_URL}/add-bill")

    # Verify Add Bill page loaded
    expect(
        page.get_by_role("heading", name=re.compile("Add Bill"))
    ).to_be_visible()

    # Enter bill details
    page.get_by_role("textbox", name=re.compile("Bill Name")).fill(bill_name)
    page.locator('input[name="amount"]').fill("100.99")

    # Select category
    page.get_by_role("button", name=re.compile("Choose Category")).click()
    page.get_by_text("Utilities").click()

    # Select recurrence
    page.locator('select[name="recurrence"]').select_option("One-Time")

    # Enter first due date
    page.locator('input[name="first_due"]').fill("2026-06-01")

    # Click Save
    page.get_by_role("button", name=re.compile("Save")).click()

    # Verify app stays on Add Bill page
    expect(page).to_have_url(re.compile("/add-bill"))

    # Verify success message
    success_message = page.get_by_text("Bill Added Successfully")
    expect(success_message).to_be_visible()

    # Screenshot success message
    success_message.screenshot(
        path="screenshots/bill_added_successfully.png"
    )
    
    # Screenshot full Add Bill page
    page.screenshot(
        path="screenshots/add_bill_success_page.png",
        full_page=True
    )

    # Go to Manage Bills page
    page.goto(f"{BASE_URL}/bills")

    # Search for the bill just added
    search_box = page.locator("#searchInput")
    search_box.click()
    search_box.fill(bill_name)
    search_box.press("Enter")

    # Verify the new bill is visible
    matching_row = page.locator("table tbody tr").filter(has_text=bill_name)
    expect(matching_row).to_have_count(1)
    #expect(matching_row.first()).to_be_visible()

    # Wait until non-matching rows are hidden by the app's filter
    expect(
        page.locator("table tbody tr:visible")
    ).to_have_count(1)

    # Screenshot only the visible filtered table area
    page.locator(".page-container").screenshot(
        path="screenshots/manage_bills_water_search.png"
    )