# Beneish M-Score Screener

A single-file Python tool that computes the **Beneish M-Score** for any US public company, pulling financial data live from the SEC EDGAR XBRL API. It shows every component so you can see *why* a company flags — not just whether it does.

Scores above **−1.78** indicate a likely earnings manipulator.

---

## What it does

The Beneish M-Score (Beneish 1999) is an 8-factor accounting model trained to detect earnings manipulation. It compares two consecutive years of financial data and looks for the kinds of distortions that typically accompany fraud: receivables growing faster than revenue, margins deteriorating, asset quality deteriorating, and earnings far above operating cash flow.

This tool:
- Resolves any US ticker to its SEC CIK via the EDGAR company index
- Downloads the company's full XBRL fact set from `data.sec.gov`
- Extracts the 12 required line items for year *t* and *t-1*, with multi-tag fallback lists to handle the inconsistent naming companies use in practice
- Computes all 8 M-Score components and the composite score
- Runs a validation suite against known fraud cases and clean controls

---

## How it works — SEC EDGAR XBRL

The SEC has required machine-readable XBRL tagging of financial statements since 2009 (large accelerated filers first). Every 10-K filed since then has its numerical data available at:

```
https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json
```

This JSON contains every tagged value a company has ever filed, keyed by XBRL concept name (e.g. `AccountsReceivableNetCurrent`, `Revenues`). The script filters for:
- `fp == "FY"` — full-year figures only
- `form` starts with `"10-K"` — annual report filings only
- For flow items (revenue, expenses): period length of 330–400 days to exclude quarters

Because companies use different XBRL tag names for the same concept, each input has an ordered fallback list. The first tag that resolves a value wins.

**Structural limitations:** Companies with unclassified balance sheets (financial holding companies like GE Capital-era GE) or non-standard income statements (car rental companies that report no COGS) will not produce a score.

---

## Setup

```bash
pip3 install requests
```

Then open `mscore.py` and set `EDGAR_UA` to your name and email — the SEC requires a real contact header on all API requests:

```python
EDGAR_UA = "Your Name youremail@example.com"
```

No API key required. The SEC EDGAR API is free and public.

---

## Usage

**Run the validation suite (13 cases):**
```bash
python3 mscore.py --validate
```

**Score a single company:**
```bash
python3 mscore.py UAA 2016
```
Output:
```
  DSRI     1.264
  GMI      1.041
  AQI      0.997
  SGI      1.282
  DEPI     1.0
  SGAI     1.0
  LVGI     1.15
  TATA     -0.037
  M_SCORE  -2.374
  FLAG     False
```

**Debug — list available revenue/debt/income XBRL tags for a company:**
```bash
python3 mscore.py --tags MDXG
```
Use this to diagnose missing-field errors by finding what tags a company actually files.

---

## Validation results

All 13 cases pull live from EDGAR. Results as of June 2025:

| Company | FY | M-Score | Flagged | Known |
|---|---|---|---|---|
| Valeant / BHC (Philidor channel) | 2014 | −2.511 | no | fraud |
| Valeant / BHC (Philidor channel) | 2015 | −2.486 | no | fraud |
| Under Armour | 2016 | −2.374 | no | fraud |
| Under Armour | 2017 | −2.955 | no | fraud |
| MiMedx (channel stuffing) | 2015 | −1.235 | **YES** | fraud |
| MiMedx (channel stuffing) | 2016 | −2.639 | no | fraud |
| Herbalife (MLM misclassification) | 2015 | −3.205 | no | fraud |
| Herbalife (MLM misclassification) | 2016 | −2.553 | no | fraud |
| Kraft Heinz (procurement fraud) | 2017 | −2.453 | no | fraud |
| Perrigo (segment misclassification) | 2016 | −4.209 | no | fraud |
| Mattel (revenue recognition) | 2017 | −2.878 | no | fraud |
| Microsoft (control) | 2019 | −2.506 | no | clean |
| P&G (control) | 2019 | −2.909 | no | clean |

**Threshold:** M-Score > −1.78 → likely manipulator. Fraud cases span four types: channel stuffing (BHC, MiMedx), revenue pull-forward (Under Armour), MLM revenue misclassification (Herbalife), procurement cost inflation (Kraft Heinz), and segment reporting manipulation (Perrigo, Mattel).

---

## Where the model works — and where it misses

### The one true positive: MiMedx 2015

MiMedx (tissue graft manufacturer, SEC settled 2019) is the only case that crosses the −1.78 threshold. Its DSRI — the ratio of days-sales-in-receivables — spiked hard in 2015 as MiMedx stuffed product into its distribution channel before recognising revenue. That's exactly the pattern Beneish's DSRI was designed to catch: receivables growing much faster than sales signals that some of those "sales" haven't actually been collected and may never be.

### Why the model misses most modern frauds

**1. Valeant / BHC (channel stuffing via Philidor)**
The manipulation ran through Philidor Rx Services, a captive specialty pharmacy that was *off-balance-sheet*. Valeant recognised revenue when it shipped to Philidor, but Philidor's receivables and inventory never appeared on Valeant's consolidated balance sheet. The M-Score's DSRI and TATA components, which look for receivables inflation and accrual gaps, have nothing to latch onto because the channel was hidden. The model needs the fraud to show up in the financial statements to detect it.

**2. Under Armour (revenue pull-forward)**
Under Armour's SEC settlement (2021, $9M) described pulling future-quarter orders into the current quarter to hit Wall Street targets. The XBRL data in EDGAR reflects the *as-filed* numbers, which already embed the pulled-forward revenue — there's no restatement visible in the time series. Without a clean prior-year comparison, DSRI and SGI look normal. The fraud was about misleading analysts on guidance calls, not a balance-sheet-level distortion.

**3. Kraft Heinz (procurement cost manipulation)**
KHC's fraud ($62M SEC settlement, 2021) involved inflating projected "cost savings" from supplier negotiations and booking those savings as current-period income reductions. This is a cost-accounting manipulation — it reduces COGS and inflates gross margin. The GMI component (gross margin index) should flag this, but the magnitudes were not large enough relative to total revenue to move the composite above −1.78. Procurement fraud tends to be incremental and spread across many line items.

**4. Herbalife (MLM revenue misclassification)**
Herbalife's $20M SEC settlement (2020) wasn't about falsifying GAAP numbers — it was about misleading investors regarding *how much* of their product was sold to end consumers versus consumed by distributors themselves. The financial statements are technically correct under the company's accounting policies; the deception was in the narrative and supplemental metrics, not the income statement. No XBRL ratio can detect that.

**5. Perrigo (segment misclassification)**
Perrigo's fraud ($8M SEC settlement, 2021) involved misallocating costs between business segments and misstating forward-looking financial guidance. At the consolidated level — which is what the M-Score uses — the total revenue, COGS, and assets were accurate. The manipulation lived inside the segment footnotes, invisible to any ratio based on consolidated statements.

**6. Mattel (revenue recognition timing)**
Mattel's $3.5M SEC settlement (2019) covered a single mis-recorded customer return in Q3 2017 that was later corrected. As a one-quarter timing error, it barely moves full-year ratios. The EDGAR data also reflects the restated 10-K, so the fraudulent period is partly normalised in the archive.

### What this means in practice

The M-Score is most effective when fraud is **balance-sheet-visible and sustained** — when management inflates receivables, deflates depreciation, or builds up large accrual gaps over multiple years. It was calibrated on 1990s fraud cases (Enron-era), where the manipulation was often blunter.

Modern enforcement cases increasingly involve:
- Off-balance-sheet structures that hide the distortion
- Narrative and guidance fraud with accurate GAAP statements
- Segment-level manipulation invisible at the consolidated level
- One-time adjustments too small to move annual ratios

Use the M-Score as a **screening signal**, not a verdict. A high score warrants investigation; a low score does not clear a company.
