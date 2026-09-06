import time
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener")

# 1. Load Sector, Company, and Symbol from master_stock_list.csv
@st.cache_data(ttl=86400)
def load_stock_master():
    try:
        df = pd.read_csv("master_stock_list.csv")
        
        # Standardize column mappings
        column_mapping = {
            "Symbol": "Symbol", "symbol": "Symbol", "SYMBOL": "Symbol",
            "Company Name": "Company", "Company": "Company", "company": "Company",
            "Sector": "Sector", "sector": "Sector", "Industry": "Sector"
        }
        df = df.rename(columns=column_mapping)
        df = df.dropna(subset=["Symbol", "Sector"])
        
        # Strip string whitespace
        df["Symbol"] = df["Symbol"].astype(str).str.strip()
        df["Company"] = df["Company"].astype(str).str.strip()
        df["Sector"] = df["Sector"].astype(str).str.strip()
        
        # Filter out invalid internal database IDs (SCRIP-XXXXXX)
        valid_df = df[~df["Symbol"].str.startswith("SCRIP-", na=False)].copy()
        return valid_df[["Symbol", "Company", "Sector"]]
    except FileNotFoundError:
        st.error("`master_stock_list.csv` not found. Please upload the file to your app directory.")
        return pd.DataFrame(columns=["Symbol", "Company", "Sector"])

df_master = load_stock_master()

if not df_master.empty:
    # 2. Sector Selector UI
    sectors = sorted(df_master['Sector'].unique().tolist())
    selected_sector = st.selectbox("Select Target Sector", sectors)
    
    # Filter stocks belonging to selected sector
    sector_stocks = df_master[df_master['Sector'] == selected_sector]

    st.sidebar.header("Filter Criteria")
    min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)
    max_scan_limit = st.sidebar.number_input("Max Stocks to Scan (Prevents API Limits)", min_value=5, max_value=200, value=50)

    # 3. Live Scan Logic
    @st.cache_data(ttl=3600)
    def fetch_sector_live_data(sector_name, scan_limit):
        sector_df = df_master[df_master['Sector'] == sector_name].head(scan_limit)
        results = []
        financial_histories = {}
        
        progress_bar = st.progress(0, text=f"Scanning {len(sector_df)} stocks in {sector_name}...")
        
        for i, (_, row) in enumerate(sector_df.iterrows()):
            symbol = str(row['Symbol']).strip()
            company_name = str(row['Company']).strip()
            
            # Primary lookup on NSE (.NS), fallback to BSE (.BO)
            tickers_to_try = [f"{symbol}.NS", f"{symbol}.BO"]
            info = {}
            fin = pd.DataFrame()
            hist_max = pd.DataFrame()
            active_ticker = None

            for ticker_symbol in tickers_to_try:
                try:
                    t = yf.Ticker(ticker_symbol)
                    temp_info = t.info
                    # Check if ticker returned valid price or name data
                    if temp_info and ("currentPrice" in temp_info or "previousClose" in temp_info):
                        info = temp_info
                        fin = t.financials
                        hist_max = t.history(period="max")
                        active_ticker = ticker_symbol
                        break
                except Exception:
                    continue

            if not info or not active_ticker:
                progress_bar.progress((i + 1) / len(sector_df), text=f"Skipped {symbol} (No Live Data)")
                continue

            try:
                is_ath_sales = False
                is_ath_profit = False
                hist_df = pd.DataFrame()
                
                # Check 4-year Annual Financials
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
                    hist_df = hist_df / 10**7  # Convert to ₹ Crores

                current_price = info.get("currentPrice") or info.get("previousClose", 0)
                profit_growth = round((info.get("earningsQuarterlyGrowth") or 0) * 100, 2)
                
                # Live Market Cap (Converted to ₹ Crores)
                raw_mcap = info.get("marketCap", 0)
                market_cap_cr = round(raw_mcap / 10**7, 2) if raw_mcap else 0
                
                # Percent Down from Lifetime High
                ath_price = hist_max["High"].max() if not hist_max.empty else current_price
                percent_down_ath = 0
                if ath_price and ath_price > 0 and current_price:
                    percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2))
                    
                peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
                peg_ratio = round(peg_ratio, 2) if peg_ratio else None
                
                promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
                fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

                financial_histories[symbol] = hist_df
                
                # Profit Breakout YoY (%) placed directly next to Price (₹)
                results.append({
                    "Ticker": symbol,
                    "Company": company_name,
                    "Sector": sector_name,
                    "Price (₹)": current_price,
                    "Profit Breakout YoY (%)": profit_growth,
                    "Market Cap (₹ Cr)": market_cap_cr,
                    "% Down from ATH": percent_down_ath,
                    "PEG Ratio": peg_ratio,
                    "Promoter (%)": promoter_holding,
                    "FII (%)": fii_holding,
                    "ATH Sales": is_ath_sales,
                    "ATH Profit": is_ath_profit
                })
            except Exception:
                continue

            # Small sleep delay to prevent Yahoo Rate Limits
            time.sleep(0.1)
            progress_bar.progress((i + 1) / len(sector_df), text=f"Analyzing {symbol}...")
            
        progress_bar.empty()
        return pd.DataFrame(results), financial_histories

    st.write(f"Found **{len(sector_stocks)}** valid exchange-listed companies in **{selected_sector}**.")

    if st.button("Run Live Sector Scan"):
        df, financial_histories = fetch_sector_live_data(selected_sector, max_scan_limit)
        
        if not df.empty:
            filtered_df = df[
                (df["ATH Sales"] == True) & 
                (df["ATH Profit"] == True) & 
                (df["Profit Breakout YoY (%)"] >= min_breakout)
            ].sort_values(by="Profit Breakout YoY (%)", ascending=False)
            
            st.subheader(f"✅ Matching Assets in {selected_sector} ({len(filtered_df)} found)")
            
            st.dataframe(
                filtered_df, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "% Down from ATH": st.column_config.ProgressColumn(
                        "% Down from ATH",
                        format="%f %%",
                        min_value=0,
                        max_value=100
                    ),
                    "Market Cap (₹ Cr)": st.column_config.NumberColumn("Market Cap (₹ Cr)", format="₹ %'d Cr"),
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
                            go.Bar(
                                x=hist.index, y=hist["Revenue"], name="Revenue", 
                                marker_color="#1f77b4", text=hist["Revenue"].round(1), textposition="auto"
                            ), row=1, col=1
                        )
                        fig.add_trace(
                            go.Bar(
                                x=hist.index, y=hist["Net Income"], name="Net Income", 
                                marker_color="#2ca02c", text=hist["Net Income"].round(1), textposition="auto"
                            ), row=1, col=2
                        )
                        fig.update_layout(height=300, showlegend=False, margin=dict(l=20, r=20, t=40, b=20))
                        fig.update_xaxes(type='category')
                        st.plotly_chart(fig, use_container_width=True)
            
            st.markdown("---")
            st.subheader("❌ Did Not Meet Criteria")
            failed_df = df[~df["Ticker"].isin(filtered_df["Ticker"])]
            st.dataframe(failed_df, use_container_width=True, hide_index=True)
        else:
            st.error("No valid market data returned. Please lower the scan limit or try again in a few minutes.")
