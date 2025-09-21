# streamlit_app.py
import streamlit as st
import pandas as pd
import json
from google.oauth2.service_account import Credentials
import gspread
import plotly.express as px

st.set_page_config(page_title="Live Expense Tracker", layout="wide")
st.title("💸 Live Expense Tracker (Google Sheets → Streamlit)")

# Sidebar inputs
st.sidebar.header("Google Sheet connection")
SHEET_ID = st.sidebar.text_input("Google Sheet ID (between /d/ and /edit)", "")
sheet_name_override = st.sidebar.text_input("Worksheet name (optional)", "")
refresh = st.sidebar.button("Refresh now")

st.sidebar.markdown("---")
st.sidebar.markdown(
    "Instructions: Put your Google service account JSON in Streamlit Secrets under the key `gcp_service_account`.\n"
    "Share the sheet with the service account email (client_email) as Viewer."
)

def load_service_account_from_secrets():
    if "gcp_service_account" not in st.secrets:
        raise KeyError("gcp_service_account not found in Streamlit secrets.")
    raw = st.secrets["gcp_service_account"]
    if isinstance(raw, dict):
        return raw
    s = str(raw).strip()
    if s.startswith('"""') and s.endswith('"""'):
        s = s[3:-3].strip()
    if s.startswith("'''") and s.endswith("'''"):
        s = s[3:-3].strip()
    try:
        return json.loads(s)
    except Exception:
        return json.loads(s.replace('\\n','\n'))

@st.cache_data(ttl=300)
def get_gspread_client():
    creds_info = load_service_account_from_secrets()
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(credentials)
    return gc

@st.cache_data(ttl=60)
def get_sheet_titles(sheet_id: str):
    if not sheet_id:
        return []
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    return [ws.title for ws in sh.worksheets()]

@st.cache_data(ttl=60)
def load_sheet_as_df(sheet_id: str, worksheet_name: str | None):
    if not sheet_id:
        return pd.DataFrame()
    gc = get_gspread_client()
    try:
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        st.error(f"Unable to open sheet: {e}")
        return pd.DataFrame()
    try:
        ws = sh.worksheet(worksheet_name) if worksheet_name else sh.get_worksheet(0)
    except Exception:
        ws = sh.get_worksheet(0)
    records = ws.get_all_records(empty2zero=False, head=1)
    df = pd.DataFrame.from_records(records)
    return df

if refresh:
    st.experimental_rerun()

worksheet_titles = []
if SHEET_ID:
    try:
        worksheet_titles = get_sheet_titles(SHEET_ID)
    except Exception:
        worksheet_titles = []

selected_sheet = None
if worksheet_titles:
    selected_sheet = st.sidebar.selectbox("Choose worksheet", options=worksheet_titles, index=0)
if sheet_name_override.strip():
    selected_sheet = sheet_name_override.strip()

df = load_sheet_as_df(SHEET_ID, selected_sheet)
if df.empty:
    st.info("No data loaded yet. Provide Sheet ID and ensure worksheet has rows and headers.")
    st.stop()

st.subheader("Raw data preview")
st.dataframe(df.head(10), use_container_width=True)

col_map = {c.lower(): c for c in df.columns}
date_col = col_map.get("datetime") or col_map.get("date")
amount_col = col_map.get("amount") or col_map.get("amt")
type_col = col_map.get("type")
message_col = col_map.get("message") or col_map.get("msg")

work = df.copy()
if amount_col in work.columns:
    work["Amount"] = pd.to_numeric(work[amount_col], errors="coerce")
else:
    numeric_cols = work.select_dtypes(include="number").columns
    work["Amount"] = pd.to_numeric(work[numeric_cols[0]], errors="coerce") if len(numeric_cols)>0 else pd.NA
if date_col in work.columns:
    work["DateTime"] = pd.to_datetime(work[date_col], errors="coerce")
else:
    work["DateTime"] = pd.NaT

work["Date"] = pd.to_datetime(work["DateTime"]).dt.date
work["Month"] = pd.to_datetime(work["DateTime"]).dt.to_period("M").astype(str)
work["Weekday"] = pd.to_datetime(work["DateTime"]).dt.day_name()

if type_col in work.columns:
    work["Type"] = work[type_col].astype(str).str.lower().str.strip()
else:
    work["Type"] = "unknown"
    work.loc[work["Amount"] < 0, "Type"] = "debit"
    work.loc[work["Amount"] > 0, "Type"] = "credit"

st.markdown("---")
st.header("Summary metrics")
col1, col2, col3, col4 = st.columns(4)
total_debit = work.loc[work["Type"] == "debit", "Amount"].sum()
total_credit = work.loc[work["Type"] == "credit", "Amount"].sum()
txn_count = len(work)
last_update = work["DateTime"].max()
col1.metric("Total Spent (Debit)", f"{total_debit:,.2f}")
col2.metric("Total Credit", f"{total_credit:,.2f}")
col3.metric("Transactions", txn_count)
col4.metric("Latest txn", str(last_update) if pd.notna(last_update) else "N/A")

st.markdown("---")
st.subheader("Interactive charts")
daily = work[work["Type"] == "debit"].groupby("Date")["Amount"].sum().reset_index()
if not daily.empty:
    fig1 = px.line(daily, x="Date", y="Amount", title="Daily Spending (debits)", markers=True)
    st.plotly_chart(fig1, use_container_width=True)

monthly = work.groupby(["Month", "Type"])["Amount"].sum().reset_index()
if not monthly.empty:
    monthly_pivot = monthly.pivot(index="Month", columns="Type", values="Amount").fillna(0).reset_index()
    types = [c for c in monthly_pivot.columns if c != "Month"]
    fig2 = px.bar(monthly_pivot, x="Month", y=types, title="Monthly Debit vs Credit (stacked)", barmode="stack")
    st.plotly_chart(fig2, use_container_width=True)

st.markdown("---")
st.subheader("Cleaned transactions (preview)")
st.dataframe(work.head(200), use_container_width=True)
csv = work.to_csv(index=False).encode("utf-8")
st.download_button("Download cleaned CSV", data=csv, file_name="transactions_cleaned.csv", mime="text/csv")
