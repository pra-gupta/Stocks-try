import streamlit as st
import pandas as pd
import yfinance as yf

# 1. Load the comprehensive stock list efficiently
@st.cache_data(ttl=86400) # Cache for 24 hours to speed up app reloads
def load_stock_master():
    """
    Loads a master list of all NSE/BSE companies.
    For production, download the official EQUITY_L.csv from NSE and place it in your app directory.
    Here, we fallback to a publicly available Nifty 500 CSV if the local file isn't found.
    """
    try:
        # Ideally, you keep a 'master_stock_list.csv' in your project repo
        # Required columns: 'Symbol', 'Company Name', 'Sector', 'Exchange'
        df = pd.read_csv("master_stock_list.csv")
    except FileNotFoundError:
        st.warning("Local master CSV not found. Falling back to public Nifty 500 list [1].")
        # Fallback URL for NIFTY 500 which includes the 'Industry' mapping
        url = "https://raw.githubusercontent.com/kprohith/nse-stock-analysis/master/ind_nifty500list.csv"
        df = pd.read_csv(url)
        # Rename columns to standardize our app logic
        df = df.rename(columns={"Symbol": "Symbol", "Industry": "Sector", "Company Name": "Company Name"})
        df['Exchange'] = 'NSE'
        
    return df

st.title("Indian Equities Sector Screener")

# 2. Initialize Data
df_stocks = load_stock_master()

# 3. Create UI for Sector and Exchange Selection
# Drop missing values and get unique sectors
sectors = sorted(df_stocks['Sector'].dropna().unique().tolist())
selected_sector = st.selectbox("Select a Sector", sectors)

exchanges = sorted(df_stocks['Exchange'].dropna().unique().tolist())
selected_exchange = st.radio("Select Exchange", exchanges, horizontal=True)

# 4. Filter the Data based on user selection
filtered_df = df_stocks[
    (df_stocks['Sector'] == selected_sector) & 
    (df_stocks['Exchange'] == selected_exchange)
]

st.write(f"Found **{len(filtered_df)}** companies in the **{selected_sector}** sector on **{selected_exchange}**.")
st.dataframe(filtered_df[['Symbol', 'Company Name', 'Sector']])

# 5. Format symbols for Yahoo Finance
if st.button("Fetch Market Data"):
    with st.spinner("Fetching data from Yahoo Finance..."):
        # Yahoo finance requires .NS suffix for NSE and .BO suffix for BSE
        suffix = ".NS" if selected_exchange == "NSE" else ".BO"
        
        # Create a list of formatted tickers (e.g., "TCS.NS", "RELIANCE.NS")
        yf_tickers = [str(sym) + suffix for sym in filtered_df['Symbol'].tolist()]
        
        if yf_tickers:
            # yf.download fetches all tickers in parallel automatically
            # We fetch just the last closing price to keep the app fast
            data = yf.download(yf_tickers, period="1d", group_by="ticker")
            
            # Parse the downloaded data into a clean dataframe
            results = []
            for ticker in yf_tickers:
                try:
                    # Handle single vs multiple ticker download structures
                    if len(yf_tickers) == 1:
                        last_close = data['Close'].iloc[-1]
                    else:
                        last_close = data[ticker]['Close'].iloc[-1]
                        
                    results.append({"Yahoo Ticker": ticker, "Last Close (₹)": round(last_close, 2)})
                except Exception as e:
                    results.append({"Yahoo Ticker": ticker, "Last Close (₹)": "Data Not Found"})
                    
            results_df = pd.DataFrame(results)
            st.success("Data fetched successfully!")
            st.dataframe(results_df)
        else:
            st.error("No valid tickers found to fetch.")
