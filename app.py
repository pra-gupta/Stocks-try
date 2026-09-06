import time
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener")

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
        df["Company"] = df["Company Name"] if "Company Name" in df.columns else df["Company"].astype(str).str.strip()
        df["Sector"] = df["Sector"].astype(str).str.strip()
        return df[["Symbol", "Company", "Sector"]]
    except FileNotFoundError:
        st.error("`master_stock_list.csv` not found in directory.")
        return pd.DataFrame(columns=["Symbol", "Company", "Sector"])

df_master = load_stock_master()

def get_financial_row(df_fin, candidates):
    """Flexible lookup for Yahoo Finance row names."""
    if df_fin.empty:
        return None
    for idx in df_fin.index:
        idx_str = str(idx).strip().lower()
        for cand in candidates:
            if cand.lower() == idx_str:
                return df_fin.loc[idx]
    return None

def compute_fallback_quarterly_growth(t):
    """Calculates YoY quarterly net income growth directly from quarterly financials if info is missing."""
    try:
        q_fin = t.quarterly_financials
        if q_fin.empty:
            return 0.0
        net_inc_row = get_financial_row(q_fin, ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation"])
        if net_inc_row is not None and len(net_inc_row.dropna()) >= 4:
            s = net_inc_row.dropna()
            latest_q = s.iloc[0]
            yoy_q = s.iloc[3] # 4 quarters ago
            if yoy_q and yoy_q > 0:
                return round(((latest_q - yoy_q) / yoy_q) * 100, 2)
    except Exception:
        pass
    return 0.0

if not df_master.empty:
    sectors = sorted(df_master['Sector'].unique().tolist())
    selected_sector = st.selectbox("Select Target Sector", sectors)
    
    sector_stocks = df_master[df_master['Sector'] == selected_sector]

    st.sidebar.header("Filter Criteria")
    min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)
    max_scan_limit = st.sidebar.number_input("Max Stocks to Scan", min_value=5, max_value=250, value=50)

    @st.cache_data(ttl=3600)
    def fetch_sector_live_data(sector_name, scan_limit):
        sector_df = df_master[df_master['Sector'] == sector_name].head(scan_limit)
        results = []
        financial_histories = {}
        
        progress_bar = st.progress(0, text=f"Scanning {len(sector_df)} stocks in {sector_name}...")
        
        for i, (_, row) in enumerate(sector_df.iterrows()):
            ticker_symbol = str(row['Symbol']).strip()
            clean_symbol = ticker_symbol.replace(".NS", "").replace(".BO", "")
            company_name = str(row['Company']).strip()
            
            try:
                t = yf.Ticker(ticker_symbol)
                
                # Fetch price history with fallback window expansion
                hist_recent = t.history(period="5d")
                if hist_recent.empty:
                    hist_recent = t.history(period="1mo")
                    
                if hist_recent.empty:
                    progress_bar.progress((i + 1) / len(sector_df), text=f"Skipped {clean_symbol} (No Price)")
                    continue
                
                current_price = float(hist_recent['Close'].iloc[-1])
                hist_max = t.history(period="max")
                
                try:
                    info = t.info
                except Exception:
                    info = {}
                
                try:
                    fin = t.financials
                except Exception:
                    fin = pd.DataFrame()

                is_ath_sales = False
                is_ath_profit = False
                hist_df = pd.DataFrame()
                
                # Search across multiple key variations for revenue & net income
                revenue = get_financial_row(fin, ["Total Revenue", "Operating Revenue", "Revenue"])
                net_income = get_financial_row(fin, ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation"])
                
                if revenue is not None and not revenue.dropna().empty:
                    rev_clean = revenue.dropna()
                    is_ath_sales = rev_clean.iloc[0] >= (rev_clean.max() * 0.99)
                else:
                    rev_clean = pd.Series()

                if net_income is not None and not net_income.dropna().empty:
                    net_clean = net_income.dropna()
                    is_ath_profit = net_clean.iloc[0] >= (net_clean.max() * 0.99)
                else:
                    net_clean = pd.Series()

                if not rev_clean.empty or not net_clean.empty:
                    hist_df = pd.DataFrame({
                        "Revenue": rev_clean,
                        "Net Income": net_clean
                    }).dropna(how="all").fillna(0)
                    
                    hist_df.index = pd.to_datetime(hist_df.index).year.astype(str)
                    hist_df = hist_df.sort_index().tail(4) / 10**7 # Convert to ₹ Cr

                # Compute Profit Breakout YoY % with fallback for SME / Small-cap missing info
                profit_growth = info.get("earningsQuarterlyGrowth")
                if profit_growth is not None:
                    profit_growth = round(profit_growth * 100, 2)
                else:
                    profit_growth = compute_fallback_quarterly_growth(t)

                raw_mcap = info.get("marketCap", 0)
                if not raw_mcap and "sharesOutstanding" in info:
                    raw_mcap = info.get("sharesOutstanding", 0) * current_price
                market_cap_cr = round(raw_mcap / 10**7, 2) if raw_mcap else 0
                
                ath_price = hist_max["High"].max() if not hist_max.empty else current_price
                percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2)) if ath_price else 0
                    
                peg_ratio = round(info.get("pegRatio") or info.get("trailingPegRatio") or 0, 2)
                promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
                fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

                financial_histories[clean_symbol] = hist_df
                
                results.append({
                    "Ticker": clean_symbol,
                    "Company": company_name,
                    "Sector": sector_name,
                    "Price (₹)": round(current_price, 2),
                    "Profit Breakout YoY (%)": profit_growth,
                    "Market Cap (₹ Cr)": market_cap_cr,
                    "% Down from ATH": percent_down_ath,
                    "PEG Ratio": peg_ratio if peg_ratio else None,
                    "Promoter (%)": promoter_holding,
                    "FII (%)": fii_holding,
                    "ATH Sales": is_ath_sales,
                    "ATH Profit": is_ath_profit
                })
            except Exception:
                continue

            time.sleep(0.05)
            progress_bar.progress((i + 1) / len(sector_df), text=f"Analyzing {clean_symbol}...")
            
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
                    
                    st.subheader(f"{company} ({ticker})")
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
