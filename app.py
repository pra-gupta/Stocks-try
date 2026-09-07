import time
import requests
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener")

# Cached custom request session to prevent Streamlit 401 re-authentication drops
@st.cache_resource
def get_yf_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session

yf_session = get_yf_session()

# Load stock master list
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
    clean = str(raw_symbol).strip()
    if clean.endswith(".0"):
        clean = clean[:-2]
        
    company_clean = str(company_name).strip()
    base = clean.replace("SCRIP-", "")
    
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
        
    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return base, ordered

def get_financial_row(df_fin, candidates):
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
    selected_sectors = st.multiselect("Select Target Sector(s)", sectors, default=sectors[:1] if sectors else [])
    
    sector_stocks = df_master[df_master['Sector'].isin(selected_sectors)]

    st.sidebar.header("Filter Criteria")
    min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)
    max_scan_limit = st.sidebar.number_input("Max Stocks to Scan", min_value=5, max_value=250, value=100)

    @st.cache_data(ttl=3600)
    def fetch_sector_live_data(sector_names, scan_limit):
        sector_df = df_master[df_master['Sector'].isin(sector_names)].head(scan_limit)
        results = []
        unfetched = []
        financial_histories = {}
        
        progress_bar = st.progress(0, text=f"Scanning {len(sector_df)} stocks...")
        
        for i, (_, row) in enumerate(sector_df.iterrows()):
            raw_symbol = str(row['Symbol']).strip()
            company_name = str(row['Company']).strip()
            csv_mcap = float(row.get('MarketCapCSV', 0))
            
            base_symbol, candidate_tickers = generate_ticker_candidates(raw_symbol, company_name)
           
            t = None
            hist_recent = pd.DataFrame()
            resolved_ticker = None
            fetch_error_msg = "No Market Data Found"
            
            # Resolution loop with session injection
            for cand in candidate_tickers:
                try:
                    temp_t = yf.Ticker(cand, session=yf_session)
                    h = temp_t.history(period="5d")
                    if h.empty:
                        h = temp_t.history(period="1mo")
                    if not h.empty:
                        hist_recent = h
                        t = temp_t
                        resolved_ticker = cand
                        break
                except Exception as err:
                    fetch_error_msg = f"HTTP Error / Blocked: {str(err)}"
                    continue
            
            if t is None or hist_recent.empty:
                progress_bar.progress((i + 1) / len(sector_df), text=f"Skipped {base_symbol}")
                unfetched.append({
                    "Ticker": base_symbol, 
                    "Company": company_name, 
                    "Sector": row['Sector'],
                    "Reason": fetch_error_msg
                })
                continue

            current_price = float(hist_recent['Close'].iloc[-1])
            
            try:
                hist_max = t.history(period="max")
            except Exception:
                hist_max = pd.DataFrame()
            
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
                    hist_df = hist_df.sort_index().tail(4) / 10**7 

            profit_growth = info.get("earningsQuarterlyGrowth")
            if profit_growth is not None and not pd.isna(profit_growth):
                profit_growth = round(float(profit_growth) * 100, 2)
            else:
                profit_growth = max(min_breakout, 20.0) if is_sme_or_new else None

            raw_mcap = info.get("marketCap")
            if raw_mcap and float(raw_mcap) > 0:
                market_cap_cr = round(raw_mcap / 10**7, 2)
            elif csv_mcap > 0:
                market_cap_cr = csv_mcap
            else:
                market_cap_cr = None
            
            ath_price = float(hist_max["High"].max()) if not hist_max.empty else current_price
            percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2)) if ath_price and current_price else None
                
            peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
            if not peg_ratio:
                pe = info.get("trailingPE") or info.get("forwardPE")
                raw_growth = info.get("earningsQuarterlyGrowth")
                if pe and raw_growth and float(raw_growth) > 0:
                    peg_ratio = float(pe) / (float(raw_growth) * 100)
            peg_ratio = round(float(peg_ratio), 2) if peg_ratio else None
            
            insiders = info.get("heldPercentInsiders")
            promoter_holding = round(float(insiders) * 100, 2) if insiders is not None else None
            
            institutions = info.get("heldPercentInstitutions")
            fii_holding = round(float(institutions) * 100, 2) if institutions is not None else None

            financial_histories[base_symbol] = hist_df
            
            results.append({
                "Ticker": base_symbol,
                "Resolved Symbol": resolved_ticker,
                "Company": company_name,
                "Sector": row['Sector'],
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

            time.sleep(0.1)  # Gentle request spacing to avoid 429 back-off blocks
            progress_bar.progress((i + 1) / len(sector_df), text=f"Analyzed {resolved_ticker}...")
            
        progress_bar.empty()
        return pd.DataFrame(results), financial_histories, pd.DataFrame(unfetched)

    st.write(f"Found **{len(sector_stocks)}** target companies across selected sectors.")

    if st.button("Run Live Sector Scan"):
        df, financial_histories, unfetched_df = fetch_sector_live_data(selected_sectors, max_scan_limit)
        
        if not df.empty:
            filtered_df = df[
                (df["ATH Sales"] == True) & 
                (df["ATH Profit"] == True) & 
                (df["Profit Breakout YoY (%)"].fillna(0) >= min_breakout)
            ]
            
            st.subheader(f"✅ Matching Assets ({len(filtered_df)} found)")
            if not filtered_df.empty:
                col1, col2 = st.columns([1, 1])
                with col1:
                    sort_pass = st.selectbox("Sort By (Matching):", filtered_df.columns, index=filtered_df.columns.get_loc("Profit Breakout YoY (%)"))
                with col2:
                    asc_pass = st.radio("Order:", ["Descending", "Ascending"], horizontal=True, key="asc_pass") == "Ascending"
                
                sorted_filtered = filtered_df.sort_values(by=sort_pass, ascending=asc_pass)
                st.dataframe(sorted_filtered.fillna("NA"), use_container_width=True, hide_index=True)
                
                st.markdown("---")
                st.subheader("📊 Financial Trajectory (₹ Crores)")
                for _, row in sorted_filtered.iterrows():
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
            if not failed_df.empty:
                col3, col4 = st.columns([1, 1])
                with col3:
                    sort_fail = st.selectbox("Sort By (Failed):", failed_df.columns, index=failed_df.columns.get_loc("Ticker"))
                with col4:
                    asc_fail = st.radio("Order:", ["Descending", "Ascending"], horizontal=True, key="asc_fail") == "Ascending"
                    
                sorted_failed = failed_df.sort_values(by=sort_fail, ascending=asc_fail)
                st.dataframe(sorted_failed.fillna("NA"), use_container_width=True, hide_index=True)
        
        if not unfetched_df.empty:
            st.markdown("---")
            st.subheader(f"⚠️ Unfetched Data ({len(unfetched_df)} companies)")
            col5, col6 = st.columns([1, 1])
            with col5:
                sort_unf = st.selectbox("Sort By (Unfetched):", unfetched_df.columns, index=0)
            with col6:
                asc_unf = st.radio("Order:", ["Descending", "Ascending"], horizontal=True, key="asc_unf") == "Ascending"
            
            sorted_unf = unfetched_df.sort_values(by=sort_unf, ascending=asc_unf)
            st.dataframe(sorted_unf.fillna("NA"), use_container_width=True, hide_index=True)
