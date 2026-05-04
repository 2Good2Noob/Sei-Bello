import json, re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)

SKIP = {
    "Umów", "Zarezerwuj", "Zapisz termin", "Pokaż wszystkie zdjęcia",
}

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))

def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))

def find_section(lines: list[str]) -> list[str]:
    # znajdź “Usługi” takie, po którym w okolicy jest “Popularne usługi”
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi" and any("Popularne usługi" in x for x in lines[i:i+120]):
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for j in range(start+1, len(lines)):
        if lines[j] == "Opinie":
            end = j
            break
    return lines[start:end or len(lines)]

def parse():
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x and x not in SKIP]

    sec = find_section(lines)

    categories = []
    current = {"name": "Cennik", "items": []}
    categories.append(current)

    def start_category(name: str):
        nonlocal current
        current = {"name": name, "items": []}
        categories.append(current)

    i = 0
    while i < len(sec):
        line = sec[i]

        # kategoria: np. “Usługi męskie” + następna linia “4 usługi”
        if i + 1 < len(sec) and re.fullmatch(r"\d+\s+usług[iy]?", sec[i+1]):
            start_category(line)
            i += 2
            continue

        # kategoria bez licznika (często “Popularne usługi”)
        if line in {"Popularne usługi", "Koloryzacja", "Prostowanie", "Fryzury okolicznościowe", "Trwała ondulacja", "Zabiegi regenerujące włosy"}:
            start_category(line)
            i += 1
            continue

        # wpis usługi: linia -> cena -> czas (czas czasem jest dalej)
        if (i + 1 < len(sec)) and is_price(sec[i+1]) and not is_price(line) and not is_duration(line):
            name = line
            price_line = sec[i+1]
            # znajdź pierwszy czas po cenie
            dur = ""
            j = i + 2
            while j < min(i + 10, len(sec)):
                if is_duration(sec[j]):
                    dur = clean(sec[j])
                    break
                j += 1

            # promki: jeśli w jednej “cenie” są 2 ceny, weź ostatnią jako aktualną
            prices = PRICE_RE.findall(price_line)
            price = prices[-1] if prices else clean(price_line)

            current["items"].append({
                "name": name,
                "price": clean(price),
                "duration": dur,
            })
            i += 2
            continue

        i += 1

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c["items"]],
    }

    return payload

if __name__ == "__main__":
    data = parse()
    import os
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"OK -> {OUT_PATH} ({sum(len(c['items']) for c in data['categories'])} pozycji)")