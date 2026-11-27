# Fixed, liquid US large-cap universe -- see playbook.md. Deliberately not
# "the whole market": a fixed list keeps nightly data volume and backtest
# time bounded, and names move on/off only as a deliberate playbook decision,
# never as a reaction to one bad week for one name.
#
# ~150 large, liquid names spread across sectors, hand-picked rather than
# fetched from a third party at runtime -- one less moving part, and this
# isn't the kind of list that goes stale fast.
UNIVERSE: list[str] = sorted(
    {
        # Mega-cap tech / communication services
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL", "CRM",
        "ADBE", "CSCO", "AMD", "QCOM", "TXN", "INTC", "IBM", "NFLX", "DIS",
        "CMCSA", "TMUS", "VZ", "T", "UBER", "ABNB", "SHOP", "NOW", "INTU",
        "AMAT", "MU", "LRCX", "KLAC", "PANW", "SNPS", "CDNS", "ANET", "PYPL",
        # Financials
        "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "SPGI",
        "V", "MA", "USB", "PNC", "TFC", "COF", "ICE", "CME", "MMC", "PGR",
        "TRV", "ALL", "AIG", "MET", "AFL",
        # Healthcare
        "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
        "AMGN", "GILD", "VRTX", "ISRG", "CVS", "CI", "ELV", "HUM", "REGN",
        "MDT", "SYK", "BSX", "ZTS",
        # Consumer discretionary / staples
        "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG", "CMG",
        "TGT", "WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "CL", "EL",
        "MDLZ", "KHC", "GIS", "STZ", "YUM",
        # Industrials
        "GE", "RTX", "HON", "UNP", "UPS", "CAT", "DE", "BA", "LMT", "NOC",
        "GD", "MMM", "ETN", "EMR", "ITW", "CSX", "NSC", "FDX", "WM",
        # Energy
        "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "OXY", "WMB",
        # Materials / real estate
        "LIN", "APD", "ECL", "NEM", "FCX", "PLD", "AMT", "EQIX", "SPG",
        "O", "PSA",
        # Utilities
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE",
    }
)
