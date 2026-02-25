from playwright.sync_api import sync_playwright
import hashlib
from datetime import datetime
import json


BASE_URL = "https://www.welcometothejungle.com"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    page.goto(
        "https://www.welcometothejungle.com/fr/jobs?query=data%20analyst",
        timeout=60000
    )

    # attendre que les cartes d'offres soient visibles
    page.wait_for_selector("a[href*='/jobs/']", timeout=60000)

    job_links = page.eval_on_selector_all(
        "a[href*='/jobs/']",
        "elements => elements.map(el => el.href)"
    )

    browser.close()

# nettoyage doublons
job_links = list(set(job_links))

print(f"{len(job_links)} offres trouvées")
job_links[:5]

def extract_job_details(page, url):
    page.goto(url, timeout=60000)
    page.wait_for_timeout(3000)

    body_text = page.evaluate("() => document.body.innerText")

    title = page.title()

    job_id = hashlib.md5(url.encode()).hexdigest()

    return {
        "id": job_id,
        "url": url,
        "title": title,
        "description_raw": body_text,
        "description_length": len(body_text)
    }

jobs_data = []

for job in jobs_data:
    print(job["title"])

output = {
    "source": "welcometothejungle",
    "query": "data analyst",
    "scraped_at": datetime.utcnow().isoformat(),
    "offers": []
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    for link in job_links:
        try:
            job = extract_job_details(page, link)
            output["offers"].append(job)
            print(f"OK : {job['title']}")
        except Exception as e:
            print(f"SKIP : {link}")

    browser.close()

with open("job_offers_wttj.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
