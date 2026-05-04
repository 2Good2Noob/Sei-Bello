import json
import re
import os
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

def clean(s):
    if not s: return ""
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def parse():
    # Używamy ulepszonych nagłówków dla scrapingu
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    r = requests.get(BOOKSY_URL, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # Szukamy wszystkich kontenerów usług
    # Booksy grupuje usługi wewnątrz konkretnych elementów div
    service_items = soup.find_all('div', {'data-testid': 'service-item'})
    
    # Próbujemy znaleźć kategorie (nagłówki)
    categories_data = []
    
    # Pobieramy sekcje (np. Popularne usługi, Usługi męskie itp.)
    # Struktura Booksy zazwyczaj opiera się na listach pod nagłówkami h3/h2
    sections = soup.find_all(['h3', 'h2'])
    
    # Dla uproszczenia i maksymalnej precyzji, skupimy się na wyciąganiu 
    # danych bezpośrednio z obiektów usług, które zawierają nazwę, cenę i czas w jednym bloku.
    
    current_category = {"name": "Cennik usług", "items": []}
    
    # Mapa do grupowania wariantów
    groups = {}

    for item in service_items:
        # Wyciągamy dane z konkretnego bloku usługi
        name_elem = item.find(['h3', 'div'], {'class': lambda x: x and 'name' in x.lower()}) or item.find('div', style=lambda s: s and 'font-weight' in s)
        price_elem = item.find(string=re.compile(r'\d+,\d{2}\s*zł'))
        time_elem = item.find(string=re.compile(r'\b\d+\s*min\b|\b\d+\s*g\b'))

        if not name_elem: continue
        
        name = clean(name_elem.get_text())
        price = clean(price_elem) if price_elem else ""
        time = clean(time_elem) if time_elem else ""

        # Logika grupowania wariantów (np. Strzyżenie damskie -> Włosy krótkie)
        # Jeśli nazwa zawiera myślnik lub słowo kluczowe wariantu
        is_variant = False
        parent_name = ""

        if "Strzyżenie Damskie -" in name:
            parent_name = "Strzyżenie damskie + mycie + stylizacja"
            is_variant = True
        elif "Metamorfoza" in name:
            parent_name = "Strzyżenie damskie + mycie + stylizacja"
            is_variant = True
        elif "Włosy" in name and "Koloryzacja" not in name:
            # Zakładamy, że to wariant ostatniej głównej usługi typu Koloryzacja
            parent_name = "Koloryzacja włosów"
            is_variant = True

        if is_variant:
            if parent_name not in groups:
                groups[parent_name] = {"name": parent_name, "variants": []}
            groups[parent_name]["variants"].append({
                "name": name,
                "price": price,
                "duration": time
            })
        else:
            current_category["items"].append({
                "name": name,
                "price": price,
                "duration": time
            })

    # Scalanie grup wariantowych do głównej listy
    for group_name in groups:
        current_category["items"].append(groups[group_name])

    categories_data.append(current_category)

    return {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories_data
    }

if __name__ == "__main__":
    try:
        data = parse()
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Sukces! Dane zapisane w {OUT_PATH}")
    except Exception as e:
        print(f"BŁĄD: {e}")
        exit(1)
