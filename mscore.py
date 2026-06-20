"""
Beneish M-Score screener — pulls live from SEC EDGAR XBRL.

Computes the 8-factor Beneish M-Score for any US public company and shows
every component so you can see WHY it flags. Scores above -1.78 indicate a
likely earnings manipulator.

SETUP (one time):
    pip3 install requests
    Set EDGAR_UA below to your name + email (SEC requires a contact header).

USE:
    python3 mscore.py --validate        # run the fraud + control test set
    python3 mscore.py UAA 2016          # one company-year, full 8-component breakdown
    python3 mscore.py --tags MDXG       # debug: list available revenue/debt/income tags
"""
import sys
from datetime import date
import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
EDGAR_UA = "Tirth Patel tirthpatel286@gmail.com"   # <-- SEC requires real contact info

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL   = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
HEADERS     = {"User-Agent": EDGAR_UA}
THRESHOLD   = -1.78        # M > -1.78  =>  likely manipulator

# Each input -> (ordered fallback XBRL tags, period kind). First tag that resolves wins.
CONCEPTS = {
    "sales":           (["RevenueFromContractWithCustomerExcludingAssessedTax",
                         "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                         "RevenueFromContractWithCustomerIncludingAssessedTax"], "duration"),
    "cogs":            (["CostOfGoodsAndServicesSold", "CostOfRevenue",
                         "CostOfGoodsSold"], "duration"),
    "receivables":     (["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"], "instant"),
    "current_assets":  (["AssetsCurrent"], "instant"),
    "ppe_net":         (["PropertyPlantAndEquipmentNet"], "instant"),
    "total_assets":    (["Assets"], "instant"),
    "depreciation":    (["DepreciationDepletionAndAmortization",
                         "DepreciationAmortizationAndAccretionNet",
                         "DepreciationAndAmortization", "Depreciation"], "duration"),
    "sga":             (["SellingGeneralAndAdministrativeExpense",
                         "GeneralAndAdministrativeExpense"], "duration"),
    "current_liab":    (["LiabilitiesCurrent"], "instant"),
    "lt_debt":         (["LongTermDebtNoncurrent", "LongTermDebt"], "instant"),
    "income_cont_ops": (["IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
                         "IncomeLossFromContinuingOperations", "ProfitLoss",
                         "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"], "duration"),
    "cfo":             (["NetCashProvidedByUsedInOperatingActivities",
                         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], "duration"),
}

NEUTRALIZABLE = ("depreciation", "sga")   # if absent, force a neutral (=1) index
DEFAULT_ZERO  = ("lt_debt",)              # if absent, the company simply has none
REQUIRED      = ("sales", "cogs", "receivables", "current_assets", "ppe_net",
                 "total_assets", "current_liab", "income_cont_ops", "cfo")

# (ticker, fiscal year t, label, is_known_fraud)
# Valeant trades as BHC now, but its CIK holds the full Philidor-era history.
VALIDATION = [
    ("BHC",  2014, "Valeant (Philidor)",        True),
    ("BHC",  2015, "Valeant (Philidor)",        True),
    ("UAA",  2016, "Under Armour",              True),
    ("UAA",  2017, "Under Armour",              True),
    ("MDXG", 2015, "MiMedx (channel stuffing)", True),
    ("MDXG", 2016, "MiMedx (channel stuffing)", True),
    ("HLF",  2015, "Herbalife (MLM misclass.)",     True),
    ("HLF",  2016, "Herbalife (MLM misclass.)",     True),
    ("KHC",  2017, "Kraft Heinz (procurement)",     True),
    ("PRGO", 2016, "Perrigo (segment fraud)",       True),
    ("MAT",  2017, "Mattel (revenue recog.)",       True),
    ("MSFT", 2019, "Microsoft (control)",           False),
    ("PG",   2019, "P&G (control)",                False),
]

# ---------------------------------------------------------------------------
# SCORING ENGINE
# ---------------------------------------------------------------------------
def m_score(prior: dict, curr: dict) -> dict:
    """prior = year t-1 line items, curr = year t line items. Returns all 8 + composite."""
    dsri = (curr["receivables"] / curr["sales"]) / (prior["receivables"] / prior["sales"])

    gm_curr  = (curr["sales"]  - curr["cogs"])  / curr["sales"]
    gm_prior = (prior["sales"] - prior["cogs"]) / prior["sales"]
    gmi = gm_prior / gm_curr

    aq_curr  = 1 - (curr["current_assets"]  + curr["ppe_net"])  / curr["total_assets"]
    aq_prior = 1 - (prior["current_assets"] + prior["ppe_net"]) / prior["total_assets"]
    aqi = aq_curr / aq_prior

    sgi = curr["sales"] / prior["sales"]

    dep_rate_curr  = curr["depreciation"]  / (curr["depreciation"]  + curr["ppe_net"])
    dep_rate_prior = prior["depreciation"] / (prior["depreciation"] + prior["ppe_net"])
    depi = dep_rate_prior / dep_rate_curr

    sgai = (curr["sga"] / curr["sales"]) / (prior["sga"] / prior["sales"])

    lev_curr  = (curr["current_liab"]  + curr["lt_debt"])  / curr["total_assets"]
    lev_prior = (prior["current_liab"] + prior["lt_debt"]) / prior["total_assets"]
    lvgi = lev_curr / lev_prior

    # Earnings far above operating cash flow -> accrual manipulation. Biggest driver.
    tata = (curr["income_cont_ops"] - curr["cfo"]) / curr["total_assets"]

    m = (-4.84 + 0.920*dsri + 0.528*gmi + 0.404*aqi + 0.892*sgi
         + 0.115*depi - 0.172*sgai + 4.679*tata - 0.327*lvgi)

    return {"DSRI": round(dsri, 3), "GMI": round(gmi, 3), "AQI": round(aqi, 3),
            "SGI": round(sgi, 3), "DEPI": round(depi, 3), "SGAI": round(sgai, 3),
            "LVGI": round(lvgi, 3), "TATA": round(tata, 3),
            "M_SCORE": round(m, 3), "FLAG": m > THRESHOLD}

# ---------------------------------------------------------------------------
# EDGAR DATA LAYER
# ---------------------------------------------------------------------------
def ticker_to_cik(ticker: str) -> int:
    data = requests.get(TICKERS_URL, headers=HEADERS, timeout=30).json()
    t = ticker.upper()
    for row in data.values():
        if row["ticker"].upper() == t:
            return int(row["cik_str"])
    raise ValueError(f"ticker '{ticker}' not found in SEC index")

def load_company(ticker: str) -> dict:
    cik = ticker_to_cik(ticker)
    return requests.get(FACTS_URL.format(cik=cik), headers=HEADERS, timeout=30).json()

def _is_annual(start: str, end: str) -> bool:
    if not start or not end:
        return False
    y1, m1, d1 = map(int, start.split("-"))
    y2, m2, d2 = map(int, end.split("-"))
    return 330 <= (date(y2, m2, d2) - date(y1, m1, d1)).days <= 400

def _annual_value(facts: dict, tags, year: int, kind: str):
    """First as-filed 10-K value whose period END falls in `year`, else None."""
    usgaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        node = usgaap.get(tag)
        if not node:
            continue
        for entries in node.get("units", {}).values():
            for e in entries:
                if not str(e.get("end", "")).startswith(str(year)):
                    continue
                if e.get("fp") != "FY" or not str(e.get("form", "")).startswith("10-K"):
                    continue
                if kind == "duration" and not _is_annual(e.get("start", ""), e.get("end", "")):
                    continue
                return e["val"]
    return None

def build_inputs(facts: dict, year: int) -> dict:
    return {k: _annual_value(facts, tags, year, kind) for k, (tags, kind) in CONCEPTS.items()}

# ---------------------------------------------------------------------------
# RUNNER
# ---------------------------------------------------------------------------
def _reconcile(prior, curr):
    for k in NEUTRALIZABLE:
        if prior.get(k) is None or curr.get(k) is None:
            prior[k] = curr[k] = 1.0
    for k in DEFAULT_ZERO:
        if prior.get(k) is None: prior[k] = 0.0
        if curr.get(k) is None:  curr[k] = 0.0

def score_company(ticker, year, facts=None):
    facts = facts or load_company(ticker)
    prior = build_inputs(facts, year - 1)
    curr  = build_inputs(facts, year)
    _reconcile(prior, curr)
    missing = [k for k in REQUIRED if prior.get(k) is None or curr.get(k) is None]
    if missing:
        raise ValueError(f"missing required fields {missing} "
                         f"(try: python3 mscore.py --tags {ticker})")
    return m_score(prior, curr)

def validate():
    hdr = f"{'Company':<28}{'FY':<6}{'M-Score':<10}{'Flag':<7}{'Known'}"
    print(hdr); print("-" * len(hdr))
    cache = {}
    for ticker, year, label, fraud in VALIDATION:
        try:
            facts = cache.setdefault(ticker, load_company(ticker))
            r = score_company(ticker, year, facts)
            print(f"{label:<28}{year:<6}{r['M_SCORE']:<10}"
                  f"{('YES' if r['FLAG'] else 'no'):<7}{'fraud' if fraud else 'clean'}")
        except Exception as ex:
            print(f"{label:<28}{year:<6}ERROR: {ex}")

def list_tags(ticker):
    f = load_company(ticker)["facts"]["us-gaap"]
    rev = [k for k in f if "Revenue" in k or "Sales" in k]
    debt = [k for k in f if "Debt" in k or "Credit" in k]
    inc = [k for k in f if "Income" in k or "ProfitLoss" in k]
    print("REVENUE tags:", rev)
    print("DEBT tags:   ", debt)
    print("INCOME tags: ", inc)

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--validate":
        validate()
    elif len(sys.argv) == 3 and sys.argv[1] == "--tags":
        list_tags(sys.argv[2])
    elif len(sys.argv) == 3:
        r = score_company(sys.argv[1], int(sys.argv[2]))
        for k, v in r.items():
            print(f"  {k:<8} {v}")
    else:
        print(__doc__)
