import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener (Master List Sync)")

# 1. Load the comprehensive stock list dynamically
@st.cache_data(ttl=86400)
def load_stock_master():
    try:
        df = pd.read_csv("master_stock_list.csv")
    except FileNotFoundError:
        st.sidebar.warning("master_stock_list.csv not found. Falling back to public Nifty 500.")
        url = "https://raw.githubusercontent.com/kprohith/nse-stock-analysis/master/ind_nifty500list.csv"
        df = pd.read_csv(url)
        df = df.rename(columns={"Symbol": "Symbol", "Industry": "Sector", "Company Name": "Company Name"})
        df['Exchange'] = 'NSE'
    return df

df_master = load_stock_master()

# 2. Sidebar Filters & Dynamic Sector Selection
st.sidebar.header("Filter Parameters")
sectors = sorted(df_master['Sector'].dropna().unique().tolist())
selected_sector = st.sidebar.selectbox("Select Target Sector", sectors)
min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)

@st.cache_data(ttl=3600)
def fetch_live_market_data(ticker_data):
    results = []
    financial_histories = {}
    
    progress_bar = st.progress(0, text=f"Fetching live data for {len(ticker_data)} companies...")
    
    for i, row in enumerate(ticker_data):
        symbol = row["Symbol"]
        company = row["Company Name"]
        exchange = row.get("Exchange", "NSE")
        sector = row["Sector"]
        
        # Auto-append correct Yahoo Finance suffix
        yf_ticker = f"{symbol}.NS" if exchange == "NSE" else f"{symbol}.BO"
        
        try:
            t = yf.Ticker(yf_ticker)
            info = t.info
            fin = t.financials
            hist_max = t.history(period="max")
            
            is_ath_sales = False
            is_ath_profit = False
            hist_df = pd.DataFrame()
            
            # Fundamental Analysis for ATH metrics
            if not fin.empty and "Total Revenue" in fin.index and "Net Income" in fin.index:
                revenue = fin.loc["Total Revenue"].dropna()
                net_income = fin.loc["Net Income"].dropna()
                
                if not revenue.empty:
                    is_ath_sales = revenue.iloc[0] >= (revenue.max() * 0.99)
                if not net_income.empty:
                    is_ath_profit = net_income.iloc[0] >= (net_income.max() * 0.99)

                hist_df = pd.DataFrame({"Revenue": revenue, "Net Income": net_income}).dropna()
                hist_df.index = pd.to_datetime(hist_df.index).year.astype(str)
                hist_df = hist_df.sort_index().tail(4)
                hist_df = hist_df / 10**7

            # Price & Breakout Metrics
            current_price = info.get("currentPrice") or info.get("previousClose", 0)
            profit_growth = round((info.get("earningsQuarterlyGrowth") or 0) * 100, 2)
            
            # Technical & Shareholding Metrics
            ath_price = hist_max["High"].max() if not hist_max.empty else current_price
            percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2)) if ath_price and current_price else 0
                
            peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
            peg_ratio = round(peg_ratio, 2) if peg_ratio else None
            
            promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
            fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

            financial_histories[symbol] = hist_df
            
            # Append in exact column order requested
            results.append({
                "Ticker": symbol,
                "Company": company,
                "Sector": sector,
                "Price (₹)": current_price,
                "Profit Breakout YoY (%)": profit_growth,
                "% Down from ATH": percent_down_ath,
                "PEG Ratio": peg_ratio,
                "Promoter (%)": promoter_holding,
                "FII (%)": fii_holding,
                "ATH Sales": is_ath_sales,
                "ATH Profit": is_ath_profit
            })
        except Exception:
            continue
            
        progress_bar.progress((i + 1) / len(ticker_data), text=f"Analyzing {symbol}...")
        
    progress_bar.empty()
    return pd.DataFrame(results), financial_histories

# 3. Main Execution Block
if st.button(f"Run Live Scan on {selected_sector}"):
    # Filter down to the selected sector only
    sector_df = df_master[df_master['Sector'] == selected_sector]
    ticker_data = sector_df.to_dict('records')
    
    if not ticker_data:
        st.error("No companies found for this sector in the CSV.")
    else:
        df, financial_histories = fetch_live_market_data(ticker_data)
        
        if not df.empty:
            # Apply Breakout and ATH rules
            filtered_df = df[
                (df["ATH Sales"] == True) & 
                (df["ATH Profit"] == True) & 
                (df["Profit Breakout YoY (%)"] >= min_breakout)
            ].sort_values(by="Profit Breakout YoY (%)", ascending=False)
            
            st.subheader(f"✅ Matching Assets ({len(filtered_df)} found in {selected_sector})")
            
            # Render DataFrame with progress bars and formatting
            st.dataframe(
                filtered_df, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "% Down from ATH": st.column_config.ProgressColumn(
                        "% Down from ATH",
                        help="How far the stock is from its lifetime highest price",
                        format="%f %%",
                        min_value=0,
                        max_value=100,
                    ),
                    "Promoter (%)": st.column_config.NumberColumn("Promoter (%)", format="%f %%"),
                    "FII (%)": st.column_config.NumberColumn("FII (%)", format="%f %%")
                }
            )
            
            # Plotly Charts Section
            if not filtered_df.empty:
                st.markdown("---")
                st.header("📊 4-Year Financial Trajectory (₹ Crores)")
                
                for _, row in filtered_df.iterrows():
                    ticker = row["Ticker"]
                    company = row["Company"]
                    hist = financial_histories.get(ticker)
                    
                    st.subheader(f"{company} ({ticker})")
                    
                    if hist is not None and not hist.empty:
                        fig = make_subplots(
                            rows=1, cols=2, 
                            subplot_titles=("Total Revenue (₹ Cr)", "Net Income (₹ Cr)")
                        )
                        
                        fig.add_trace(
                            go.Bar(x=hist.index, y=hist["Revenue"], name="Revenue", marker_color="#1f77b4", text=hist["Revenue"].round(1), textposition="auto"),
                            row=1, col=1
                        )
                        fig.add_trace(
                            go.Bar(x=hist.index, y=hist["Net Income"], name="Net Income", marker_color="#2ca02c", text=hist["Net Income"].round(1), textposition="auto"),
                            row=1, col=2
                        )
                        fig.update_layout(height=320, showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
                        fig.update_xaxes(type='category')
                        
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("Historical annual financials unavailable.")
            
            st.markdown("---")
            st.subheader(f"❌ Did Not Meet Criteria ({len(df) - len(filtered_df)} assets)")
            failed_df = df[~df["Ticker"].isin(filtered_df["Ticker"])]
            st.dataframe(failed_df, use_container_width=True, hide_index=True)
        else:
            st.error("Error fetching data from Yahoo Finance. Try again.")
