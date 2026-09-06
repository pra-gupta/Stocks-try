import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener")

# 1. Load the Master CSV
@st.cache_data(ttl=86400) # Cache for 24 hours to prevent constant disk reads
def load_stock_master():
    try:
        df = pd.read_csv("master_stock_list.csv")
        # Strip whitespace from column names just in case
        df.columns = df.columns.str.strip()
        
        required_cols = ['Symbol', 'Company Name', 'Sector', 'Market Cap']
        for col in required_cols:
            if col not in df.columns:
                st.error(f"Missing required column in CSV: '{col}'. Found: {list(df.columns)}")
                st.stop()
        return df
    except FileNotFoundError:
        st.error("❌ 'master_stock_list.csv' not found in the directory. Please create it to continue.")
        st.stop()

df_master = load_stock_master()

# 2. Dynamic Sidebar UI
st.sidebar.header("Filter Parameters")

# Get unique sectors from the CSV, drop empty ones, and sort alphabetically
available_sectors = sorted(df_master['Sector'].dropna().unique().tolist())
selected_sector = st.sidebar.selectbox("Select Sector to Scan", available_sectors)

min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)

# Filter the master dataframe for the selected sector
sector_stocks_df = df_master[df_master['Sector'] == selected_sector]

st.markdown(f"**Target Sector:** {selected_sector} | **Stocks in Queue:** {len(sector_stocks_df)}")

# 3. Fetch live data function
@st.cache_data(ttl=3600)
def fetch_live_market_data(stocks_list):
    results = []
    financial_histories = {}
    
    progress_bar = st.progress(0, text="Initializing connection to Yahoo Finance...")
    total_stocks = len(stocks_list)
    
    for i, row in enumerate(stocks_list):
        # We assume NSE stocks, so we append .NS. Adjust if using BSE (.BO).
        ticker = f"{row['Symbol']}.NS"
        company_name = row['Company Name']
        mcap = row['Market Cap']
        
        progress_bar.progress((i + 1) / total_stocks, text=f"Analyzing {ticker} ({i+1}/{total_stocks})...")
        
        try:
            t = yf.Ticker(ticker)
            info = t.info
            fin = t.financials
            hist_max = t.history(period="max")
            
            is_ath_sales = False
            is_ath_profit = False
            hist_df = pd.DataFrame()
            
            if not fin.empty and "Total Revenue" in fin.index and "Net Income" in fin.index:
                revenue = fin.loc["Total Revenue"].dropna()
                net_income = fin.loc["Net Income"].dropna()
                
                if not revenue.empty:
                    is_ath_sales = revenue.iloc[0] >= (revenue.max() * 0.99)
                if not net_income.empty:
                    is_ath_profit = net_income.iloc[0] >= (net_income.max() * 0.99)

                hist_df = pd.DataFrame({
                    "Revenue": revenue,
                    "Net Income": net_income
                }).dropna()
                
                hist_df.index = pd.to_datetime(hist_df.index).year.astype(str)
                hist_df = hist_df.sort_index().tail(4)
                hist_df = hist_df / 10**7 # Convert to ₹ Crores

            current_price = info.get("currentPrice") or info.get("previousClose", 0)
            profit_growth = round((info.get("earningsQuarterlyGrowth") or 0) * 100, 2)
            
            # ATH Price Calculation
            ath_price = hist_max["High"].max() if not hist_max.empty else current_price
            percent_down_ath = 0
            if ath_price and ath_price > 0 and current_price:
                percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2))
                
            peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
            
            promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
            fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

            financial_histories[row['Symbol']] = hist_df
            
            results.append({
                "Symbol": row['Symbol'],
                "Company": company_name,
                "Market Cap": mcap,
                "Price (₹)": current_price,
                "% Down from ATH": percent_down_ath,
                "PEG Ratio": round(peg_ratio, 2) if peg_ratio else None,
                "Promoter (%)": promoter_holding,
                "FII (%)": fii_holding,
                "ATH Sales": is_ath_sales,
                "ATH Profit": is_ath_profit,
                "Profit Breakout YoY (%)": profit_growth
            })
        except Exception:
            continue
            
    progress_bar.empty()
    return pd.DataFrame(results), financial_histories

# 4. Execution Block
if st.button("Run Live Scan for Selected Sector"):
    if sector_stocks_df.empty:
        st.warning("No stocks found for this sector in the CSV.")
    else:
        # Convert DataFrame to a list of dictionaries to pass into the cached function
        stocks_to_scan = sector_stocks_df.to_dict('records')
        
        df, financial_histories = fetch_live_market_data(stocks_to_scan)
        
        if not df.empty:
            filtered_df = df[
                (df["ATH Sales"] == True) & 
                (df["ATH Profit"] == True) & 
                (df["Profit Breakout YoY (%)"] >= min_breakout)
            ].sort_values(by="Profit Breakout YoY (%)", ascending=False)
            
            st.subheader(f"✅ Matching Assets ({len(filtered_df)} found)")
            
            st.dataframe(
                filtered_df, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "Market Cap": st.column_config.NumberColumn("M.Cap", format="%d"),
                    "% Down from ATH": st.column_config.ProgressColumn(
                        "% Down from ATH",
                        format="%f %%", min_value=0, max_value=100,
                    ),
                    "Promoter (%)": st.column_config.NumberColumn("Promoter (%)", format="%f %%"),
                    "FII (%)": st.column_config.NumberColumn("FII (%)", format="%f %%")
                }
            )
            
            if not filtered_df.empty:
                st.markdown("---")
                st.header("📊 4-Year Financial Trajectory (₹ Crores)")
                
                for _, row in filtered_df.iterrows():
                    symbol = row["Symbol"]
                    hist = financial_histories.get(symbol)
                    
                    st.subheader(f"{row['Company']} ({symbol})")
                    
                    if hist is not None and not hist.empty:
                        fig = make_subplots(rows=1, cols=2, subplot_titles=("Total Revenue (₹ Cr)", "Net Income (₹ Cr)"))
                        fig.add_trace(go.Bar(x=hist.index, y=hist["Revenue"], name="Revenue", marker_color="#1f77b4", text=hist["Revenue"].round(1), textposition="auto"), row=1, col=1)
                        fig.add_trace(go.Bar(x=hist.index, y=hist["Net Income"], name="Net Income", marker_color="#2ca02c", text=hist["Net Income"].round(1), textposition="auto"), row=1, col=2)
                        
                        fig.update_layout(height=320, showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
                        fig.update_xaxes(type='category')
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("Historical annual financials unavailable.")
            
            st.markdown("---")
            st.subheader("❌ Did Not Meet Criteria")
            failed_df = df[~df["Symbol"].isin(filtered_df["Symbol"])]
            st.dataframe(failed_df, use_container_width=True, hide_index=True)
        else:
            st.error("Error fetching data from Yahoo Finance. API rate limit may have been reached.")
