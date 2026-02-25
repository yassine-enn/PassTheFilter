import asyncio
import json
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def scrape_indeed_details(keyword, location, nb_posts=5):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) # Important pour voir le captcha
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await stealth_async(page) # Cache le fait qu'on est un robot

        search_query = keyword.replace(" ", "+")
        url = f"https://fr.indeed.com/jobs?q={search_query}&l={location}"
        
        print(f"Navigation vers : {url}")
        await page.goto(url)

        # PAUSE : Si tu vois le captcha de ton image, valide-le maintenant !
        print("Vérification en cours... Si un CAPTCHA apparaît, cochez-le dans la fenêtre.")
        
        # On attend que la liste des jobs soit VRAIMENT là avant de continuer
        try:
            await page.wait_for_selector('.job_seen_beacon', timeout=30000) 
        except:
            print("Le CAPTCHA n'a pas été passé à temps.")
            await browser.close()
            return

        job_data = []
        cards = page.locator('.job_seen_beacon')
        count = await cards.count()

        for i in range(min(count, nb_posts)):
            try:
                current_card = cards.nth(i)
                
                # Scroll jusqu'à l'élément pour faire "humain"
                await current_card.scroll_into_view_if_needed()
                await asyncio.sleep(1)
                
                title = await current_card.locator('h2.jobTitle').inner_text()
                print(f"Extraction de : {title}")

                await current_card.click()
                
                # On attend que la description apparaisse (le sélecteur peut varier)
                # On essaie d'être plus souple sur le sélecteur
                await page.wait_for_selector('#jobDescriptionText', timeout=10000)
                
                description = await page.locator('#jobDescriptionText').inner_text()
                
                job_data.append({
                    "title": title,
                    "description": description
                })
                
                await asyncio.sleep(3) # Pause entre deux clics

            except Exception as e:
                print(f"Erreur sur l'offre {i} : {e}")
                # En cas d'erreur, on tente de fermer d'éventuelles popups
                await page.keyboard.press("Escape")

        await browser.close()
        return job_data

if __name__ == "__main__":
    results = asyncio.run(scrape_indeed_details("Data Analyst", "Paris", 3))
    with open('offres_indeed.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    print(f"Terminé avec {len(results)} offres.")