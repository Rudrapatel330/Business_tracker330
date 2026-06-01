import asyncio
import logging
from scraper import scrape_google_maps

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def progress(info):
    print(f"  >> {info}")

results = asyncio.run(scrape_google_maps('restaurants', 'Vadodara', progress))

print(f"\n=== TOTAL RESULTS: {len(results)} ===")
for r in results[:5]:
    name = r.get("name", "?")
    print(f"  {name}")
