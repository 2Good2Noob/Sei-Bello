import requests
import json
import os

def fetch_booksy_prices():
    # URL do API Booksy (używamy publicznego endpointu na podstawie Twojego ID salonu)
    # Na podstawie Twojego repozytorium ID to prawdopodobnie część Twojego profilu
    API_URL = "https://booksy.com/api/pl/v2/customer/businesses/223403/services" # Upewnij się, że ID 223403 jest poprawne dla Twojego salonu
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()
        data = response.json()

        structured_data = {"categories": []}

        # Iteracja po grupach usług (kategoriach) z Booksy
        for group in data.get('data', []):
            category = {
                "name": group.get('name'),
                "items": []
            }

            for service in group.get('services', []):
                service_entry = {
                    "name": service.get('name'),
                    "description": service.get('description', ''),
                    "variants": []
                }

                # Sprawdzenie czy usługa ma warianty
                variants = service.get('variants', [])
                if variants:
                    for v in variants:
                        variant_data = {
                            "name": v.get('name') if v.get('name') else "Cena od",
                            "price": f"{v.get('price')},00 zł" if v.get('price') else "Cena zmienna",
                            "duration": f"{v.get('duration') // 60}min" if v.get('duration') else ""
                        }
                        # Formatowanie czasu jeśli powyżej 60 min
                        if v.get('duration') and v.get('duration') >= 60:
                            h = v.get('duration') // 60
                            m = v.get('duration') % 60
                            variant_data["duration"] = f"{h}g {m}min" if m > 0 else f"{h}g"
                            
                        service_entry["variants"].append(variant_data)
                
                category["items"].append(service_entry)
            
            if category["items"]:
                structured_data["categories"].append(category)

        # Zapis do pliku
        os.makedirs('data', exist_ok=True)
        with open('data/booksy-prices.json', 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
        
        print("Ceny zostały pomyślnie zaktualizowane.")

    except Exception as e:
        print(f"Błąd podczas pobierania danych: {e}")

if __name__ == "__main__":
    fetch_booksy_prices()
