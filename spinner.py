import os
import re
import time
import traceback

import ddddocr
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

PHONE_NUMBERS = ["03347417368"]
URL = "https://my.ptcl.net.pk/SpinTheWheel/Default.aspx"
MAX_CAPTCHA_RETRIES = 3


def find_browser_binary():
    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def make_driver():
    options = Options()
    browser_binary = find_browser_binary()
    if browser_binary:
        options.binary_location = browser_binary

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-infobars")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-features=PrivacySandboxSettings4")

    print("Downloading/Verifying ChromeDriver (auto-detect)...")
    service = Service(ChromeDriverManager().install())
    print("Launching Chrome (headless)...")
    return webdriver.Chrome(service=service, options=options)


def wait_for_image_load(driver, element, timeout=10):
    driver.execute_script(
        "arguments[0].complete || arguments[0].addEventListener('load', function() {}, false);",
        element,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.execute_script("return arguments[0].naturalWidth;", element) > 0:
            return True
        time.sleep(0.3)
    return False


def read_captcha(driver, wait, ocr):
    captcha_img = wait.until(EC.presence_of_element_located((By.ID, "imgCaptcha")))
    if not wait_for_image_load(driver, captcha_img):
        print("  ⚠️  CAPTCHA image did not fully load.")
        return None
    captcha_img.screenshot("captcha.png")
    with open("captcha.png", "rb") as f:
        img_bytes = f.read()
    raw = ocr.classification(img_bytes).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{3,8}", raw):
        print(f"  ⚠️  OCR result looks invalid: '{raw}' — will retry.")
        return None
    return raw


def refresh_captcha(driver):
    try:
        captcha_img = driver.find_element(By.ID, "imgCaptcha")
        driver.execute_script("arguments[0].click();", captcha_img)
        time.sleep(1.5)
    except Exception:
        pass


def get_page_error(driver):
    selectors = [
        (By.CSS_SELECTOR, "span[style*='color:Red']"),
        (By.CSS_SELECTOR, "span[style*='color: red']"),
        (By.CSS_SELECTOR, "[id*='Error']"),
        (By.CSS_SELECTOR, "[id*='error']"),
        (By.CLASS_NAME, "validation-summary-errors"),
        (By.CLASS_NAME, "alert-danger"),
        (By.CLASS_NAME, "error"),
    ]
    messages = []
    for sel in selectors:
        try:
            for el in driver.find_elements(*sel):
                txt = el.text.strip()
                if txt:
                    messages.append(txt)
        except Exception:
            pass
    return messages


def safe_click(driver, element):
    """Try standard click first, fall back to JS click if intercepted."""
    try:
        element.click()
    except ElementClickInterceptedException:
        print("  ⚠️  Click intercepted — using JS click fallback.")
        driver.execute_script("arguments[0].click();", element)


def process_number(driver, wait, ocr, number):
    print(f"\n{'─' * 50}")
    print(f"Processing: {number}")
    print(f"{'─' * 50}")
    driver.get(URL)

    try:
        dismiss = driver.find_element(By.XPATH, "//button[contains(text(),'Got it')]")
        dismiss.click()
        time.sleep(0.5)
    except Exception:
        pass

    phone_input = wait.until(EC.presence_of_element_located((By.ID, "txtMobile")))
    phone_input.clear()
    phone_input.send_keys(number)

    captcha_text = None
    for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
        print(f"  Reading CAPTCHA (attempt {attempt}/{MAX_CAPTCHA_RETRIES})...")
        if attempt > 1:
            refresh_captcha(driver)
        captcha_text = read_captcha(driver, wait, ocr)
        if captcha_text:
            print(f"  ✅ CAPTCHA detected: '{captcha_text}'")
            break
        print("  Retrying CAPTCHA...")

    if not captcha_text:
        print(
            f"  ❌ Could not read CAPTCHA after {MAX_CAPTCHA_RETRIES} attempts. Skipping {number}."
        )
        return

    captcha_input = wait.until(EC.presence_of_element_located((By.ID, "txtCaptcha")))
    captcha_input.clear()
    captcha_input.send_keys(captcha_text)

    print("  Accepting Terms & Conditions...")
    terms_checkbox = wait.until(EC.presence_of_element_located((By.ID, "chkTerms")))
    if not terms_checkbox.is_selected():
        driver.execute_script("arguments[0].click();", terms_checkbox)
    time.sleep(0.5)
    if not terms_checkbox.is_selected():
        driver.execute_script("arguments[0].checked = true;", terms_checkbox)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change'));", terms_checkbox
        )
    print(f"  Checkbox checked: {terms_checkbox.is_selected()}")

    time.sleep(0.8)
    print("  Clicking Start Game...")
    start_button = wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", start_button
    )
    time.sleep(0.3)
    driver.execute_script("arguments[0].click();", start_button)

    print("  Waiting for page response...")
    deadline = time.time() + 15
    outcome = None
    while time.time() < deadline and outcome is None:
        src = driver.page_source.lower()
        if any(
            phrase in src
            for phrase in [
                "already played",
                "already spun",
                "come back tomorrow",
                "played today",
            ]
        ):
            outcome = "already_played"
        elif driver.find_elements(
            By.XPATH,
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'spin')]",
        ):
            outcome = "wheel_ready"
        else:
            errors = get_page_error(driver)
            if errors:
                outcome = f"error: {' | '.join(errors)}"
        if outcome is None:
            time.sleep(0.5)

    driver.save_screenshot(f"after_submit_{number}.png")

    if outcome is None:
        print("  ❌ Timed out. Screenshot saved.")
        return
    if outcome == "already_played":
        print(f"  ℹ️  Already played today for {number}. Try again tomorrow.")
        return
    if outcome.startswith("error:"):
        print(f"  ❌ Server rejected the form: {outcome}")
        return

    print("  Wheel loaded! Spinning...")
    spin_button = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'spin')]",
            )
        )
    )
    driver.execute_script("arguments[0].click();", spin_button)
    print(f"  🎉 Successfully spun the wheel for {number}!")
    time.sleep(10)


def main():
    ocr = ddddocr.DdddOcr(show_ad=False)
    driver = make_driver()
    wait = WebDriverWait(driver, 15)
    try:
        for number in PHONE_NUMBERS:
            try:
                process_number(driver, wait, ocr, number)
            except Exception:
                print(f"  ❌ Unexpected error for {number}:")
                traceback.print_exc()
                try:
                    driver.save_screenshot(f"error_{number}.png")
                except Exception:
                    pass
    finally:
        print("\nCleaning up — closing browser.")
        driver.quit()
    print("\nFinished processing all numbers.")


if __name__ == "__main__":
    main()
