"""Best-effort mapping of free-text job locations to a country name."""
from __future__ import annotations

import re

COUNTRIES = {
    "United States": ["united states", "usa", "u.s.", "us", "america"],
    "Canada": ["canada", "ca"],
    "United Kingdom": ["united kingdom", "uk", "england", "scotland", "wales", "great britain", "gb"],
    "Ireland": ["ireland", "ie"],
    "India": ["india", "in"],
    "Australia": ["australia", "au"],
    "New Zealand": ["new zealand", "nz"],
    "Germany": ["germany", "deutschland", "de"],
    "France": ["france", "fr"],
    "Netherlands": ["netherlands", "nl", "holland"],
    "Belgium": ["belgium", "be"],
    "Spain": ["spain", "españa", "es"],
    "Portugal": ["portugal", "pt"],
    "Italy": ["italy", "italia", "it"],
    "Switzerland": ["switzerland", "ch"],
    "Austria": ["austria", "at"],
    "Sweden": ["sweden", "se"],
    "Denmark": ["denmark", "dk"],
    "Norway": ["norway", "no"],
    "Finland": ["finland", "fi"],
    "Poland": ["poland", "pl"],
    "Czech Republic": ["czech republic", "czechia", "cz"],
    "Romania": ["romania", "ro"],
    "Hungary": ["hungary", "hu"],
    "Bulgaria": ["bulgaria", "bg"],
    "Greece": ["greece", "gr"],
    "Ukraine": ["ukraine", "ua"],
    "Israel": ["israel", "il"],
    "Turkey": ["turkey", "türkiye", "tr"],
    "United Arab Emirates": ["united arab emirates", "uae", "ae"],
    "Saudi Arabia": ["saudi arabia", "ksa", "sa"],
    "Qatar": ["qatar", "qa"],
    "Egypt": ["egypt", "eg"],
    "South Africa": ["south africa", "za"],
    "Nigeria": ["nigeria", "ng"],
    "Kenya": ["kenya", "ke"],
    "Singapore": ["singapore", "sg"],
    "Malaysia": ["malaysia", "my"],
    "Philippines": ["philippines", "ph"],
    "Indonesia": ["indonesia", "id"],
    "Thailand": ["thailand", "th"],
    "Vietnam": ["vietnam", "viet nam", "vn"],
    "Japan": ["japan", "jp"],
    "South Korea": ["south korea", "korea", "kr"],
    "China": ["china", "cn"],
    "Hong Kong": ["hong kong", "hk"],
    "Taiwan": ["taiwan", "tw"],
    "Brazil": ["brazil", "brasil", "br"],
    "Mexico": ["mexico", "méxico", "mx"],
    "Argentina": ["argentina", "ar"],
    "Chile": ["chile", "cl"],
    "Colombia": ["colombia", "co"],
    "Costa Rica": ["costa rica", "cr"],
    "Sri Lanka": ["sri lanka", "lk"],
    "Pakistan": ["pakistan", "pk"],
}

CITIES = {
    "United States": ["new york", "san francisco", "chicago", "atlanta", "dallas", "austin", "seattle",
                      "boston", "denver", "los angeles", "indianapolis", "houston", "phoenix", "charlotte",
                      "miami", "washington", "philadelphia", "minneapolis", "nashville", "raleigh",
                      "san jose", "san diego", "portland", "detroit", "columbus", "salt lake city",
                      "pittsburgh", "st. louis", "kansas city", "tampa", "orlando", "jersey city",
                      "bellevue", "palo alto", "mountain view", "sunnyvale", "irvine", "plano",
                      ", ny", ", ca", ", tx", ", il", ", ga", ", wa", ", ma", ", co", ", nc", ", fl",
                      ", va", ", nj", ", pa", ", oh", ", mn", ", az", ", tn", ", or", ", mi", ", ut",
                      ", md", ", mo", ", wi", ", dc"],
    "Canada": ["toronto", "vancouver", "montreal", "calgary", "ottawa", "mississauga", ", on", ", bc", ", qc", ", ab"],
    "United Kingdom": ["london", "manchester", "edinburgh", "glasgow", "birmingham", "leeds", "bristol",
                       "reading", "belfast", "cardiff", "milton keynes", "newcastle"],
    "Ireland": ["dublin", "cork", "galway"],
    "India": ["bengaluru", "bangalore", "hyderabad", "pune", "chennai", "mumbai", "gurugram", "gurgaon",
              "noida", "delhi", "kolkata", "ahmedabad", "jaipur", "kochi", "coimbatore", "indore",
              "chandigarh", "trivandrum", "thiruvananthapuram", "mohali", "nagpur", "bhubaneswar", "vadodara",
              "mysore", "mysuru", "visakhapatnam"],
    "Australia": ["sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra"],
    "New Zealand": ["auckland", "wellington", "christchurch"],
    "Germany": ["berlin", "munich", "münchen", "frankfurt", "hamburg", "düsseldorf", "dusseldorf",
                "cologne", "köln", "stuttgart"],
    "France": ["paris", "lyon", "toulouse", "lille", "nantes"],
    "Netherlands": ["amsterdam", "rotterdam", "utrecht", "the hague", "eindhoven"],
    "Spain": ["madrid", "barcelona", "valencia", "malaga", "málaga"],
    "Portugal": ["lisbon", "lisboa", "porto"],
    "Italy": ["milan", "milano", "rome", "roma", "turin"],
    "Switzerland": ["zurich", "zürich", "geneva", "basel"],
    "Sweden": ["stockholm", "gothenburg"],
    "Denmark": ["copenhagen"],
    "Poland": ["warsaw", "krakow", "kraków", "wroclaw", "wrocław", "gdansk"],
    "Czech Republic": ["prague", "brno"],
    "Romania": ["bucharest", "cluj"],
    "Israel": ["tel aviv"],
    "United Arab Emirates": ["dubai", "abu dhabi"],
    "Saudi Arabia": ["riyadh", "jeddah"],
    "Singapore": [],
    "Malaysia": ["kuala lumpur"],
    "Philippines": ["manila", "makati", "taguig", "cebu"],
    "Japan": ["tokyo", "osaka"],
    "South Korea": ["seoul"],
    "China": ["shanghai", "beijing", "shenzhen", "dalian"],
    "Brazil": ["são paulo", "sao paulo", "rio de janeiro"],
    "Mexico": ["mexico city", "guadalajara", "monterrey"],
    "Argentina": ["buenos aires"],
    "Colombia": ["bogota", "bogotá", "medellin", "medellín"],
}

_CODE_TO_COUNTRY = {}
_NAME_TO_COUNTRY = {}
for country, aliases in COUNTRIES.items():
    for a in aliases:
        if len(a) == 2:
            _CODE_TO_COUNTRY[a] = country
        else:
            _NAME_TO_COUNTRY[a] = country

_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _NAME_TO_COUNTRY), key=len, reverse=True)) + r")\b"
)


def country_of(location: str | None, hint: str | None = None) -> str:
    """Return a normalized country name or 'Unknown'/'Remote'."""
    if hint:
        h = hint.strip().lower()
        if h in _CODE_TO_COUNTRY:
            return _CODE_TO_COUNTRY[h]
        if h in _NAME_TO_COUNTRY:
            return _NAME_TO_COUNTRY[h]
        for c in COUNTRIES:
            if c.lower() == h:
                return c
    loc = (location or "").lower()
    if not loc:
        return "Unknown"
    m = _NAME_RE.search(loc)
    if m:
        return _NAME_TO_COUNTRY[m.group(1)]
    for country, cities in sorted(CITIES.items(), key=lambda kv: kv[0] == "United States"):
        for c in cities:
            if c in loc:
                return country
    if "singapore" in loc:
        return "Singapore"
    # trailing 2-letter code e.g. "Pune, IN"
    m = re.search(r",\s*([a-z]{2})\s*$", loc)
    if m and m.group(1) in _CODE_TO_COUNTRY:
        return _CODE_TO_COUNTRY[m.group(1)]
    if "remote" in loc or "anywhere" in loc:
        return "Remote"
    return "Unknown"
