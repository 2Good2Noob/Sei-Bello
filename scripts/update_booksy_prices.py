import json
import os
from datetime import datetime, timezone
# Zamiast zwykłego requests, używamy curl_cffi, które świetnie omija Cloudflare
from curl_cffi import requests

def fetch_booksy_data():
    BUSINESS_ID = "214823"
    API_URL = f"https://booksy.com/api/pl/v2/customer/businesses/{BUSINESS_ID}/services"
    OUT_PATH = "data/booksy-prices.json"
    
    headers = {
        'Accept': 'application/json',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': 'https://booksy.com',
        'Referer': f'https://booksy.com/pl-pl/{BUSINESS_ID}',
    }

    try:
        print(f"Pobieranie danych dla salonu ID: {BUSINESS_ID} z użyciem curl_cffi...")
        
        # Kluczowa zmiana: impersonate="chrome110" udaje prawdziwą przeglądarkę na poziomie protokołu TLS
        response = requests.get(
            API_URL, 
            headers=headers, 
            impersonate="chrome110", 
            timeout=30
        )
        
        response.raise_for_status()
        data = response.json()
        
        if not data.get('data'):
            raise ValueError("Otrzymano pustą listę usług z API. Możliwa blokada.")

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

        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
        
        print(f"Sukces! Plik zapisany w {OUT_PATH}")

    except Exception as e:
        print(f"BŁĄD KRYTYCZNY: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Kod błędu: {e.response.status_code}")
            print(f"Treść odpowiedzi: {e.response.text[:200]}")
        # Przerywamy skrypt z błędem, żeby GitHub Actions zaświeciło się na czerwono
        exit(1)

if __name__ == "__main__":
    fetch_booksy_data()
