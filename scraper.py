import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from twilio_request import send_email_notification  # Import the email sending function


def scrape_visir_properties():
    # 1. Define the target URL and base URL
    base_url = "https://fasteignir.visir.is"

    # This is the exact URL you provided in your second message
    start_url = "https://fasteignir.visir.is/search/results/?stype=sale#/?zip=104,105&price=70000000,85000000&bedroom=2,10&category=2,1,4,7,17&stype=sale"

    # Load ignored address substrings from config.json
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    skip_address_substrings = config.get("ignored_strings", [])

    # --- Selenium Setup ---
    print("Setting up Chrome driver...")
    service = Service(ChromeDriverManager().install())

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(service=service, options=options)

    print(f"Opening browser and navigating to {start_url}...")
    driver.get(start_url)

    print("Waiting for initial page load...")
    time.sleep(5)

    all_properties = []

    # --- Pagination Loop ---
    while True:
        # --- BeautifulSoup Parsing ---
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # --- Data Extraction ---
        property_cards = soup.find_all(
            "div", class_=lambda c: c and "estate__item" in c
        )

        print(f"Found {len(property_cards)} properties on the current page.")

        for card in property_cards:
            link_tag = card.find("a", class_="js-property-link", href=True)
            address_tag = card.find("div", class_="estate__item-title")
            price_tag = card.find("div", class_="estate__price")
            size_tag = card.find("div", class_="estate__parameters--1")
            rooms_tag = card.find("div", class_="estate__parameters--2")
            bedrooms_tag = card.find("div", class_="estate__parameters--4")

            link = urljoin(base_url, link_tag["href"]) if link_tag else "N/A"
            address = (
                address_tag.get_text(strip=True, separator=" ")
                if address_tag
                else "N/A"
            )

            if any(
                substring.lower() in address.lower()
                for substring in skip_address_substrings
            ):
                continue

            price_str = price_tag.get_text(strip=True) if price_tag else "N/A"

            if price_str == "Tilboð":
                continue

            try:
                price_num = int(price_str.replace(".", "").replace(" kr", ""))
                if not 70000000 <= price_num <= 85000000:
                    continue
            except (ValueError, TypeError):
                # Could not convert to number, skip it
                continue

            size = size_tag.get_text(strip=True) if size_tag else "N/A"
            total_rooms = rooms_tag.get_text(strip=True) if rooms_tag else "N/A"
            bedrooms = bedrooms_tag.get_text(strip=True) if bedrooms_tag else "N/A"

            if link != "N/A" and address != "N/A":
                all_properties.append(
                    {
                        "address": address,
                        "price": price_str,
                        "size_m2": size,
                        "total_rooms": total_rooms,
                        "bedrooms": bedrooms,
                        "link": link,
                    }
                )

        # --- Find and click the 'next' button ---
        try:
            # Find the 'next' button. It's an 'a' tag with class 'b-navigation-direction-next'
            # that does NOT have a 'disabled' class.
            next_button = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "a.b-navigation-direction-next:not(.disabled)")
                )
            )
            print("Found 'next' page button. Clicking it...")
            driver.execute_script("arguments[0].click();", next_button)
            # Wait for the page to reload with new properties
            time.sleep(5)
        except Exception:
            print(
                "No more 'next' page button found, or it is disabled. Ending pagination."
            )
            break  # Exit the loop if no 'next' button is found

    # --- Cleanup ---
    driver.quit()

    # Remove duplicates that might be loaded across pages
    unique_properties = [dict(t) for t in {tuple(d.items()) for d in all_properties}]

    return unique_properties


# --- Run the scraper ---
properties_list = scrape_visir_properties()

# Print the results
print(f"\n--- Total unique properties found: {len(properties_list)} ---")
for i, prop in enumerate(properties_list):
    print(f"\nProperty #{i+1}")
    print(f"  Address: {prop['address']}")
    print(f"  Price: {prop['price']}")
    print(f"  Size: {prop['size_m2']}")
    print(f"  Bedrooms: {prop['bedrooms']}")
    print(f"  Link: {prop['link']}")

# --- Send email notification if properties are found ---
if properties_list:
    subject = f"New Properties Found: {len(properties_list)} listings"
    body_lines = ["New properties matching your criteria have been found:"]
    for prop in properties_list:
        body_lines.append(f"\nAddress: {prop['address']}")
        body_lines.append(f"Price: {prop['price']}")
        body_lines.append(f"Size: {prop['size_m2']}")
        body_lines.append(f"Bedrooms: {prop['bedrooms']}")
        body_lines.append(f"Link: {prop['link']}")
    body = "\n".join(body_lines)

    print("\nAttempting to send email notification...")
    send_email_notification(subject, body)
else:
    print("\nNo new properties found. No email notification sent.")
