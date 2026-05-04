import requests
import json
import os
from datetime import datetime, timezone

def fetch_booksy_data():
    BUSINESS_ID = "214823"
    # Używamy endpointu mobilnego, który jest stabilniejszy
    API_URL = f"https://booksy.com/api/pl/v2/customer/businesses/{BUSINESS_ID}/services"
    OUT_PATH = "data/booksy-prices.json"
    
    # Rozszerzone nagłówki, aby oszukać zabezpieczenia Booksy
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': 'https://booksy.com',
        'Referer': f'https://booksy.com/pl-pl/{BUSINESS_ID}',
        'X-Requested-With': 'XMLHttpRequest'
    }

    try:
        print(f"Pobieranie danych dla salonu ID: {BUSINESS_ID}...")
        response = requests.get(API_URL, headers=headers, timeout=15)
        
        # Jeśli Booksy zwróci błąd (np. 403), to wyrzuci wyjątek i przerwie skrypt
        response.raise_for_status()
        
        data = response.json()
        
        # Prosta weryfikacja czy dane nie są puste
        if not data.get('data'):
            raise ValueError("Otrzymano pustą listę usług z API.")

        structured_data = {
            "source": f"https://booksy.com/api/pl/v2/customer/businesses/{BUSINESS_ID}/services",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "categories": []
        }

        for group in data.get('data', []):
            category = {"name": group.get('name'), "items": []}
            for service in group.get('services', []):
                service_entry = {"name": service.get('name'), "variants": []}
                variants = service.get('variants', [])
                if variants:
                    for v in variants:
                        price_val = v.get('price')
                        duration_val = v.get('duration')
                        variant_data = {
                            "name": v.get('name') if v.get('name') else service.get('name'),
                            "price": f"{int(price_val)},00 zł" if price_val else "Cena zmienna",
                            "duration": ""
                        }
                        if duration_val:
                            h = duration_val // 3600
                            m = (duration_val % 3600) // 60
                            if h > 0:
                                variant_data["duration"] = f"{h}g" + (f" {m}min" if m > 0 else "")
                            else:
                                variant_data["duration"] = f"{m}min"
                        service_entry["variants"].append(variant_data)
                
                if len(service_entry["variants"]) == 1:
                    category["items"].append({
                        "name": service_entry["name"],
                        "price": service_entry["variants"][0]["price"],
                        "duration": service_entry["variants"][0]["duration"]
                    })
                elif service_entry["variants"]:
                    category["items"].append(service_entry)
            
            if category["items"]:
                structured_data["categories"].append(category)

        # Gwarancja zapisu: tworzymy folder jeśli nie istnieje
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
        
        print(f"Sukces! Plik zapisany w {OUT_PATH}")

    except Exception as e:
        print(f"BLAD KRYTYCZNY: {e}")
        # Wymuszamy błąd procesu, aby GitHub Actions pokazało czerwony krzyżyk
        exit(1)

if __name__ == "__main__":
    fetch_booksy_data()
