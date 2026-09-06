import time
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener")

# 1. Load Sector, Company, Symbol, and Market Cap from master_stock_list.csv
@st.cache_data(ttl=86400)
def load_stock_master():
    try:
        df = pd.read_csv("master_stock_list.csv")
        column_mapping = {
            "Symbol": "Symbol", "symbol": "Symbol", "SYMBOL": "Symbol",
            "Company Name": "Company", "Company": "Company", "company": "Company",
            "Sector": "Sector", "sector": "Sector", "Industry": "Sector",
            "Market Cap": "MarketCapCSV", "Market Cap (Cr)": "MarketCapCSV"
        }
        df = df.rename(columns=column_mapping)
        df = df.dropna(subset=["Symbol", "Sector"])
        df["Symbol"] = df["Symbol"].astype(str).str.strip()
        df["Company"] = df["Company"].astype(str).str.strip()
        df["Sector"] = df["Sector"].astype(str).str.strip()
        if "MarketCapCSV" in df.columns:
            df["MarketCapCSV"] = pd.to_numeric(df["MarketCapCSV"], errors="coerce").fillna(0)
        else:
            df["MarketCapCSV"] = 0.0
        return df[["Symbol", "Company", "Sector", "MarketCapCSV"]]
    except FileNotFoundError:
        st.error("`master_stock_list.csv` not found in current directory.")
        return pd.DataFrame(columns=["Symbol", "Company", "Sector", "MarketCapCSV"])

df_master = load_stock_master()

def generate_ticker_candidates(raw_symbol, company_name=""):
    """
    Generates ordered ticker candidates:
    - Numeric Tickers:
        1. Base + .BO (e.g., 500325.BO)
        2. First name of Company in UPPERCASE + .BO (e.g., RELIANCE.BO)
    - Non-Numeric Tickers:
        1. Standard .NS
        2. NSE SME -SM.NS
    """
    clean = str(raw_symbol).strip()
    if clean.endswith(".0"):
        clean = clean[:-2]
        
    company_clean = str(company_name).strip()
    base = clean.replace("SCRIP-", "")
    
    # Extract first word of company name in uppercase
    company_first_name = ""
    if company_clean:
        words = [w for w in company_clean.split() if w.strip()]
        if words:
            company_first_name = "".join(ch.upper() for ch in words[0])
            
    if base.isdigit():
        candidates = [f"{base}.BO"]
        if company_first_name:
            candidates.append(f"{company_first_name}.BO")
            candidates.append(f"{company_first_name}.NS")
            candidates.append(f"{company_first_name}-SM.NS")
    else:
        candidates = [f"{base}.NS", f"{base}-SM.NS"]
        if company_first_name:
            candidates.append(f"{company_first_name}.NS")
            candidates.append(f"{company_first_name}-SM.NS")
        
    # Preserve order while removing duplicates
    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return base, ordered

def get_financial_row(df_fin, candidates):
    """Flexible lookup for Yahoo Finance financial row names."""
    if df_fin is None or df_fin.empty:
        return None
    for idx in df_fin.index:
        idx_str = str(idx).strip().lower()
        for cand in candidates:
            if cand.lower() == idx_str:
                res = df_fin.loc[idx]
                if isinstance(res, pd.DataFrame):
                    return res.iloc[0]
                return res
    return None

if not df_master.empty:
    sectors = sorted(df_master['Sector'].unique().tolist())
    selected_sector = st.selectbox("Select Target Sector", sectors)
    
    sector_stocks = df_master[df_master['Sector'] == selected_sector]

    st.sidebar.header("Filter Criteria")
    min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)
    max_scan_limit = st.sidebar.number_input("Max Stocks to Scan", min_value=5, max_value=250, value=100)

    @st.cache_data(ttl=3600)
    def fetch_sector_live_data(sector_name, scan_limit):
        sector_df = df_master[df_master['Sector'] == sector_name].head(scan_limit)
        results = []
        financial_histories = {}
        
        progress_bar = st.progress(0, text=f"Scanning {len(sector_df)} stocks in {sector_name}...")
        
        for i, (_, row) in enumerate(sector_df.iterrows()):
            raw_symbol = str(row['Symbol']).strip()
            company_name = str(row['Company']).strip()
            csv_mcap = float(row.get('MarketCapCSV', 0))
            
            base_symbol, candidate_tickers = generate_ticker_candidates(raw_symbol, company_name)
            progress_bar.progress(i+2,text=f"abc is {str(candidate_tickers)}")
            t = None
            hist_recent = pd.DataFrame()
            resolved_ticker = None
            
            # Fallback Resolution Sequence
            for cand in candidate_tickers:
                try:
                    temp_t = yf.Ticker(cand)
                    h = temp_t.history(period="5d")
                    if h.empty:
                        h = temp_t.history(period="1mo")
                    if not h.empty:
                        hist_recent = h
                        t = temp_t
                        resolved_ticker = cand
                        break
                except Exception:
                    continue
            
            if t is None or hist_recent.empty:
                progress_bar.progress((i + 1) / len(sector_df), text=f"Skipped {base_symbol} (No Market Data)")
                continue

            current_price = float(hist_recent['Close'].iloc[-1])
            hist_max = t.history(period="max")
            
            try:
                info = t.info or {}
            except Exception:
                info = {}
            
            try:
                fin = t.financials
            except Exception:
                fin = pd.DataFrame()

            is_ath_sales = False
            is_ath_profit = False
            is_sme_or_new = False
            hist_df = pd.DataFrame()
            
            revenue = get_financial_row(fin, ["Total Revenue", "Operating Revenue", "Revenue"])
            net_income = get_financial_row(fin, ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation"])
            
            # SME / Newly Listed Equity Handling
            if fin.empty or fin.shape[1] < 2 or resolved_ticker.endswith("-SM.NS"):
                is_sme_or_new = True
                is_ath_sales = True
                is_ath_profit = True
            else:
                if revenue is not None and not revenue.dropna().empty:
                    rev_clean = revenue.dropna()
                    is_ath_sales = bool(rev_clean.iloc[0] >= (rev_clean.max() * 0.98))
                
                if net_income is not None and not net_income.dropna().empty:
                    net_clean = net_income.dropna()
                    is_ath_profit = bool(net_clean.iloc[0] >= (net_clean.max() * 0.98))

            if not fin.empty:
                rev_series = revenue.dropna() if revenue is not None else pd.Series()
                net_series = net_income.dropna() if net_income is not None else pd.Series()
                if not rev_series.empty or not net_series.empty:
                    hist_df = pd.DataFrame({
                        "Revenue": rev_series,
                        "Net Income": net_series
                    }).fillna(0)
                    hist_df.index = pd.to_datetime(hist_df.index).year.astype(str)
                    hist_df = hist_df.groupby(hist_df.index).first()
                    hist_df = hist_df.sort_index().tail(4) / 10**7 # Convert to ₹ Cr

            # Profit Growth Calculation
            profit_growth = info.get("earningsQuarterlyGrowth")
            if profit_growth is not None and not pd.isna(profit_growth):
                profit_growth = round(float(profit_growth) * 100, 2)
            else:
                profit_growth = max(min_breakout, 20.0) if is_sme_or_new else 0.0

            # Market Cap Calculation with CSV Fallback
            raw_mcap = info.get("marketCap", 0)
            if raw_mcap and raw_mcap > 0:
                market_cap_cr = round(raw_mcap / 10**7, 2)
            elif csv_mcap > 0:
                market_cap_cr = csv_mcap
            else:
                market_cap_cr = 0.0
            
            ath_price = float(hist_max["High"].max()) if not hist_max.empty else current_price
            percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2)) if ath_price and current_price else 0.0
                
            peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
            peg_ratio = round(float(peg_ratio), 2) if peg_ratio else None
            
            promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
            fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

            financial_histories[base_symbol] = hist_df
            
            results.append({
                "Ticker": base_symbol,
                "Resolved Symbol": resolved_ticker,
                "Company": company_name,
                "Sector": sector_name,
                "Price (₹)": round(current_price, 2),
                "Profit Breakout YoY (%)": profit_growth,
                "Market Cap (₹ Cr)": market_cap_cr,
                "% Down from ATH": percent_down_ath,
                "PEG Ratio": peg_ratio,
                "Promoter (%)": promoter_holding,
                "FII (%)": fii_holding,
                "ATH Sales": is_ath_sales,
                "ATH Profit": is_ath_profit,
                "Type": "NSE SME" if resolved_ticker.endswith("-SM.NS") else ("BSE" if resolved_ticker.endswith(".BO") else "NSE Mainboard")
            })

            time.sleep(0.05)
            progress_bar.progress((i + 1) / len(sector_df), text=f"Analyzed {resolved_ticker}...")
            
        progress_bar.empty()
        return pd.DataFrame(results), financial_histories

    st.write(f"Found **{len(sector_stocks)}** target companies in **{selected_sector}**.")

    if st.button("Run Live Sector Scan"):
        df, financial_histories = fetch_sector_live_data(selected_sector, max_scan_limit)
        
        if not df.empty:
            filtered_df = df[
                (df["ATH Sales"] == True) & 
                (df["ATH Profit"] == True) & 
                (df["Profit Breakout YoY (%)"] >= min_breakout)
            ].sort_values(by="Profit Breakout YoY (%)", ascending=False)
            
            st.subheader(f"✅ Matching Assets in {selected_sector} ({len(filtered_df)} found)")
            st.dataframe(filtered_df, use_container_width=True, hide_index=True)
            
            if not filtered_df.empty:
                st.markdown("---")
                st.header("📊 Financial Trajectory (₹ Crores)")
                for _, row in filtered_df.iterrows():
                    ticker = row["Ticker"]
                    company = row["Company"]
                    hist = financial_histories.get(ticker)
                    
                    st.subheader(f"{company} ({ticker}) [{row['Type']}]")
                    if hist is not None and not hist.empty:
                        fig = make_subplots(rows=1, cols=2, subplot_titles=("Total Revenue (₹ Cr)", "Net Income (₹ Cr)"))
                        fig.add_trace(go.Bar(x=hist.index, y=hist["Revenue"], name="Revenue", marker_color="#1f77b4"), row=1, col=1)
                        fig.add_trace(go.Bar(x=hist.index, y=hist["Net Income"], name="Net Income", marker_color="#2ca02c"), row=1, col=2)
                        fig.update_layout(height=300, showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
            
            st.markdown("---")
            st.subheader("❌ Did Not Meet Criteria")
            failed_df = df[~df["Ticker"].isin(filtered_df["Ticker"])]
            st.dataframe(failed_df, use_container_width=True, hide_index=True)
