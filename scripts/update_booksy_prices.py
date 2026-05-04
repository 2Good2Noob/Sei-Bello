import requests
import json
import os
from datetime import datetime, timezone

def fetch_booksy_data():
    # To jest stałe ID Twojego salonu w systemie Booksy
    BUSINESS_ID = "214823"
    API_URL = f"https://booksy.com/api/pl/v2/customer/businesses/{BUSINESS_ID}/services"
    OUT_PATH = "data/booksy-prices.json"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9'
    }

    try:
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()
        data = response.json()

        structured_data = {
            "source": f"https://booksy.com/pl-pl/business/api/{BUSINESS_ID}",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "categories": []
        }

        # API Booksy zwraca dane w czystej strukturze grup i usług
        for group in data.get('data', []):
            category = {
                "name": group.get('name'),
                "items": []
            }

            for service in group.get('services', []):
                service_entry = {
                    "name": service.get('name'),
                    "variants": []
                }

                variants = service.get('variants', [])
                if variants:
                    for v in variants:
                        # Pobieranie czystej ceny i czasu z bazy danych Booksy
                        price_val = v.get('price')
                        duration_val = v.get('duration')
                        
                        variant_data = {
                            "name": v.get('name') if v.get('name') else service.get('name'),
                            "price": f"{int(price_val)},00 zł" if price_val else "Cena zmienna",
                            "duration": ""
                        }

                        # Precyzyjne formatowanie czasu (np. 90 min -> 1g 30min)
                        if duration_val:
                            h = duration_val // 3600
                            m = (duration_val % 3600) // 60
                            if h > 0:
                                variant_data["duration"] = f"{h}g" + (f" {m}min" if m > 0 else "")
                            else:
                                variant_data["duration"] = f"{m}min"
                            
                        service_entry["variants"].append(variant_data)
                
                # Jeśli usługa ma tylko jeden wariant, upraszczamy strukturę do nazwy i ceny
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

        # Zapis do pliku
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
        
        print(f"Sukces! Dane pobrane z API i zapisane w {OUT_PATH}")

    except Exception as e:
        print(f"Błąd krytyczny: {e}")

if __name__ == "__main__":
    fetch_booksy_data()
