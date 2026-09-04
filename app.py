import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Live Sector Breakout Screener", layout="wide")
st.title("📈 Live Sector Breakout Screener with Advanced Metrics")

# Target sectors and their representative NSE tickers
SECTOR_TICKERS = {
    "Aerospace & Defence": ["HAL.NS", "BEL.NS", "DATAPATTNS.NS", "BDL.NS"],
    "Precision Engineering": ["AZAD.NS", "MTARTECH.NS", "CENTUM.NS"],
    "Manufacturing": ["SIEMENS.NS", "DIXON.NS", "BOSCHLTD.NS"],
    "Technology": ["TCS.NS", "INFY.NS", "KPITTECH.NS", "NETWEB.NS"],
    "Green Energy": ["SUZLON.NS", "PREMIERENE.NS", "TATAPOWER.NS", "IREDA.NS"]
}

st.sidebar.header("Filter Parameters")
selected_sectors = st.sidebar.multiselect(
    "Active Sectors", 
    list(SECTOR_TICKERS.keys()), 
    default=list(SECTOR_TICKERS.keys())
)
min_breakout = st.sidebar.slider("Minimum Profit Breakout YoY (%)", 0, 100, 15)

@st.cache_data(ttl=3600)
def fetch_live_market_data(sectors):
    results = []
    financial_histories = {}
    
    tickers_to_fetch = []
    for sector in sectors:
        for ticker in SECTOR_TICKERS[sector]:
            tickers_to_fetch.append((ticker, sector))
            
    progress_bar = st.progress(0, text="Fetching live Yahoo Finance data...")
    
    for i, (ticker, sector) in enumerate(tickers_to_fetch):
        try:
            t = yf.Ticker(ticker)
            info = t.info
            fin = t.financials
            # Fetch max history to calculate true All-Time High price
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

                # Extract last 4 years of financials
                hist_df = pd.DataFrame({
                    "Revenue": revenue,
                    "Net Income": net_income
                }).dropna()
                
                hist_df.index = pd.to_datetime(hist_df.index).year.astype(str)
                hist_df = hist_df.sort_index().tail(4)
                
                # Convert values to ₹ Crores
                hist_df = hist_df / 10**7

            current_price = info.get("currentPrice") or info.get("previousClose", 0)
            profit_growth = round((info.get("earningsQuarterlyGrowth") or 0) * 100, 2)
            
            # 1. Percent down from All-Time High (ATH)
            ath_price = hist_max["High"].max() if not hist_max.empty else current_price
            percent_down_ath = 0
            if ath_price and ath_price > 0 and current_price:
                percent_down_ath = max(0, round(((ath_price - current_price) / ath_price) * 100, 2))
                
            # 2. PEG Ratio
            peg_ratio = info.get("pegRatio") or info.get("trailingPegRatio")
            peg_ratio = round(peg_ratio, 2) if peg_ratio else None
            
            # 3. Shareholding Patterns (Convert from decimals to %)
            # In yfinance, Insiders = Promoters, Institutions = FII/DII combined
            promoter_holding = round((info.get("heldPercentInsiders") or 0) * 100, 2)
            fii_holding = round((info.get("heldPercentInstitutions") or 0) * 100, 2)

            clean_ticker = ticker.replace(".NS", "")
            financial_histories[clean_ticker] = hist_df
            
            results.append({
                "Ticker": clean_ticker,
                "Company": info.get("shortName", ticker),
                "Sector": sector,
                "Price (₹)": current_price,
                "% Down from ATH": percent_down_ath,
                "PEG Ratio": peg_ratio,
                "Promoter (%)": promoter_holding,
                "FII (%)": fii_holding,
                "ATH Sales": is_ath_sales,
                "ATH Profit": is_ath_profit,
                "Profit Breakout YoY (%)": profit_growth
            })
        except Exception:
            continue
            
        progress_bar.progress((i + 1) / len(tickers_to_fetch), text=f"Analyzing {ticker}...")
        
    progress_bar.empty()
    return pd.DataFrame(results), financial_histories

if st.button("Run Live Scan"):
    df, financial_histories = fetch_live_market_data(selected_sectors)
    
    if not df.empty:
        # Sort and filter based on user inputs
        filtered_df = df[
            (df["ATH Sales"] == True) & 
            (df["ATH Profit"] == True) & 
            (df["Profit Breakout YoY (%)"] >= min_breakout)
        ].sort_values(by="Profit Breakout YoY (%)", ascending=False)
        
        st.subheader(f"✅ Matching Assets ({len(filtered_df)} found)")
        
        # Display DataFrame with new columns
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
        
        # Plotly Charts Section for Passing Assets
        if not filtered_df.empty:
            st.markdown("---")
            st.header("📊 4-Year Financial Trajectory (₹ Crores)")
            
            for _, row in filtered_df.iterrows():
                ticker = row["Ticker"]
                company = row["Company"]
                hist = financial_histories.get(ticker)
                
                st.subheader(f"{company} ({ticker}) — {row['Sector']}")
                
                if hist is not None and not hist.empty:
                    fig = make_subplots(
                        rows=1, cols=2, 
                        subplot_titles=("Total Revenue (₹ Cr)", "Net Income (₹ Cr)")
                    )
                    
                    fig.add_trace(
                        go.Bar(
                            x=hist.index, 
                            y=hist["Revenue"], 
                            name="Revenue", 
                            marker_color="#1f77b4",
                            text=hist["Revenue"].round(1),
                            textposition="auto"
                        ),
                        row=1, col=1
                    )
                    
                    fig.add_trace(
                        go.Bar(
                            x=hist.index, 
                            y=hist["Net Income"], 
                            name="Net Income", 
                            marker_color="#2ca02c",
                            text=hist["Net Income"].round(1),
                            textposition="auto"
                        ),
                        row=1, col=2
                    )
                    
                    fig.update_layout(
                        height=320, 
                        showlegend=False, 
                        margin=dict(l=20, r=20, t=40, b=20)
                    )
                    fig.update_xaxes(type='category')
                    
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Historical annual financials unavailable.")
        
        st.markdown("---")
        st.subheader("❌ Did Not Meet Criteria")
        failed_df = df[~df["Ticker"].isin(filtered_df["Ticker"])]
        st.dataframe(failed_df, use_container_width=True, hide_index=True)
    else:
        st.error("Error fetching data from Yahoo Finance. Try again in a few minutes.")
