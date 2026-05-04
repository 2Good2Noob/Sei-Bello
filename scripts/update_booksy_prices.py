import json
import re
import os
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BOOKSY_URL = "https://booksy.com/pl-pl/214823_sei-bello-studio-pielegnacji-wlosow_fryzjer_10172_zator?do=invite&utm_medium=profile_share_from_profile"
OUT_PATH = "data/booksy-prices.json"

PRICE_RE = re.compile(r"\b\d{1,4},\d{2}\s*zł\+?\b", re.IGNORECASE)
DUR_RE = re.compile(r"\b(\d+\s*g(?:\s*\d+\s*min)?|\d+\s*min)\b", re.IGNORECASE)
COUNT_RE = re.compile(r"^\d+\s+usług[iy]?$", re.IGNORECASE)

KNOWN_CATEGORIES = {
    "Popularne usługi",
    "Usługi męskie",
    "Usługi damskie",
    "Koloryzacja",
    "Zabiegi regenerujące włosy",
    "Prostowanie",
    "Fryzura okolicznościowa",
    "Fryzury okolicznościowe",
    "Trwała ondulacja",
}

# usługi, po których na Booksy lecą warianty (włosy/variant/itp.)
VARIANT_SERVICES = {
    "Strzyżenie damskie + mycie + stylizacja",
    "Koloryzacja włosów",
    "Farbowanie-odrost",
    "Metamorfoza koloru",
    "Przyciemnianie koloru blond+strzyżenie",
    "Tonowanie włosów + strzyżenie damskie",
    "Rozjaśnianie - odrost",
    "Refleksy/sombre",
    "Dekoloryzacja włosów",
    "Baleyage",
    "Regeneracja włosów-botox",
    "Silna regeneracja włosów-PRO REPAIR COMBO+SPA",
    "Nanoplastia",
    "Trwała ondulacja",
}

SKIP_EXACT = {
    "Umów",
    "Zarezerwuj",
    "Zapisz termin",
    "Pokaż wszystkie zdjęcia",
    "* * *",
    "Udogodnienia",
    "Parking",
    "Internet (Wi-Fi)",
    "Przyjazne dla dzieci",
}

def clean(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def should_skip(line: str) -> bool:
    if not line:
        return True
    if line in SKIP_EXACT:
        return True
    low = line.lower()
    # te linie wchodzą z altów obrazków i rozwalają kolejność name->price->time
    if "portfolio usługi" in low:
        return True
    if low.startswith("image:"):
        return True
    if low.startswith("booksy logo"):
        return True
    return False

def is_price(line: str) -> bool:
    return bool(PRICE_RE.search(line))

def is_duration(line: str) -> bool:
    return bool(DUR_RE.search(line))

def is_count(line: str) -> bool:
    return bool(COUNT_RE.match(line))

def normalize_name(s: str) -> str:
    s = s.lower()
    s = s.replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def find_section(lines: list[str]) -> list[str]:
    start = None
    for i, l in enumerate(lines):
        if l == "Usługi":
            start = i
            break
    if start is None:
        raise RuntimeError("Nie znaleziono sekcji 'Usługi' na stronie Booksy.")

    end = None
    for j in range(start + 1, len(lines)):
        # ważniejsze: stop na opiniach
        if lines[j] == "Opinie":
            end = j
            break

    sec = lines[start:end or len(lines)]

    # jeśli trafią się śmieci po drodze – utnij wcześnie
    if "Udogodnienia" in sec:
        sec = sec[:sec.index("Udogodnienia")]

    return sec

def extract_price(line: str) -> str:
    prices = PRICE_RE.findall(line)
    # jeśli w tekście będą 2 ceny (promka), weź ostatnią
    return clean(prices[-1]) if prices else clean(line)

def extract_duration(lines: list[str], start_idx: int, lookahead: int = 6) -> tuple[str, int]:
    """
    Szuka czasu w kolejnych liniach od start_idx (włącznie),
    zwraca (duration, idx_po_zużyciu).
    """
    j = start_idx
    while j < min(start_idx + lookahead, len(lines)):
        if is_duration(lines[j]):
            return clean(lines[j]), j + 1
        # przerwij jeśli trafisz na nową kategorię / nową usługę z ceną
        j += 1
    return "", start_idx

def is_category_header(sec: list[str], i: int) -> bool:
    if sec[i] in KNOWN_CATEGORIES:
        return True
    if i + 1 < len(sec) and is_count(sec[i + 1]):
        return True
    return False

def is_variant_of(parent: str, cand: str) -> bool:
    p = normalize_name(parent)
    c = normalize_name(cand)

    # uniwersalne warianty
    if c.startswith("włosy") or c.startswith("wlosy") or c.startswith("wariant"):
        return True

    # specjalne przypadki
    if p == normalize_name("Strzyżenie damskie + mycie + stylizacja"):
        return c.startswith("strzyżenie damskie -") or c.startswith("strzyzenie damskie -") or c.startswith("metamorfoza fryzury")

    if p == normalize_name("Farbowanie-odrost"):
        return c.startswith("farbowanie-odrost")

    if p == normalize_name("Rozjaśnianie - odrost"):
        return c.startswith("rozjaśnianie odrost") or c.startswith("rozjasnianie odrost")

    if "tonowanie" in p:
        return c.startswith("wariant") or c.startswith("tonowanie")

    if "baleyage" in p or "balayage" in p:
        return c.startswith("baleyage") or c.startswith("balayage") or c.startswith("beleyage")

    if "trwała ondulacja" in p or "trwala ondulacja" in p:
        return c.startswith("trwała") or c.startswith("trwala") or c.startswith("włosy") or c.startswith("wlosy")

    # domyślnie: warianty często są "Włosy ..." – już złapane wyżej
    return False

def parse():
    r = requests.get(
        BOOKSY_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SeiBelloPriceBot/1.0)"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    raw_lines = [clean(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in raw_lines if not should_skip(x)]

    sec = find_section(lines)

    categories: list[dict] = []
    current_cat: dict | None = None

    i = 0
    while i < len(sec):
        line = sec[i]

        # kategoria z licznikiem: "Usługi męskie" + "4 usługi"
        if (i + 1 < len(sec)) and is_count(sec[i + 1]):
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += 2
            continue

        # kategoria bez licznika: "Popularne usługi" itp.
        if line in KNOWN_CATEGORIES:
            current_cat = {"name": line, "items": []}
            categories.append(current_cat)
            i += 1
            continue

        # jeśli jeszcze nie ma kategorii, omiń
        if current_cat is None:
            i += 1
            continue

        # pomiń śmieci
        if is_price(line) or is_duration(line) or is_count(line):
            i += 1
            continue

        # --- parsuj usługę ---
        service_name = line
        base_price = ""
        base_dur = ""
        j = i + 1

        # baza: nazwa -> cena -> czas
        if j < len(sec) and is_price(sec[j]):
            base_price = extract_price(sec[j])
            j += 1
            base_dur, j2 = extract_duration(sec, j)
            # jeśli znaleziono czas, przesuń indeks
            j = j2

        # warianty (tylko dla znanych usług wariantowych)
        if service_name in VARIANT_SERVICES:
            variants = []

            # jeśli jest baza z ceną – wrzuć jako "Cena od"
            if base_price or base_dur:
                variants.append({
                    "name": "Cena od",
                    "price": base_price,
                    "duration": base_dur,
                })

            # zbieraj warianty: nazwa -> (opcjonalnie cena + czas)
            while j < len(sec):
                # stop na nowej kategorii
                if is_category_header(sec, j):
                    break

                cand = sec[j]
                # jeśli to nie wariant tej usługi, kończymy warianty
                if not is_variant_of(service_name, cand):
                    break

                v_name = cand
                v_price = ""
                v_dur = ""
                j += 1

                if j < len(sec) and is_price(sec[j]):
                    v_price = extract_price(sec[j])
                    j += 1
                    v_dur, j2 = extract_duration(sec, j)
                    j = j2

                variants.append({
                    "name": v_name,
                    "price": v_price,
                    "duration": v_dur,
                })

            # jeżeli faktycznie zebraliśmy >1 wariant (albo 1 + Cena od), zapisujemy jako warianty
            if len(variants) >= 2:
                current_cat["items"].append({
                    "name": service_name,
                    "variants": variants,
                })
            else:
                # brak realnych wariantów → zwykły wpis
                current_cat["items"].append({
                    "name": service_name,
                    "price": base_price,
                    "duration": base_dur,
                })

            i = j
            continue

        # zwykły wpis
        current_cat["items"].append({
            "name": service_name,
            "price": base_price,
            "duration": base_dur,
        })
        i = j
        continue

    payload = {
        "source": BOOKSY_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [c for c in categories if c.get("items")],
    }
    return payload

if __name__ == "__main__":
    data = parse()
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = 0
    for c in data["categories"]:
        total += len(c.get("items", []))
    print(f"OK -> {OUT_PATH} ({total} grup/usług)")
