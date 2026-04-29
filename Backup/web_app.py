import streamlit as st
import json
import time
import pandas as pd

st.set_page_config(page_title="PC Trading Dashboard", layout="wide")

st.title("📊 PC Trading Web Dashboard")

# 🟢 ฟังก์ชันดึงข้อมูลจากไฟล์
def load_data():
    try:
        with open("bot_status.json", "r") as f:
            return json.load(f)
    except:
        return None

data = load_data()

if data:
    # --- แถวบน: Status ทั่วไป ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Bot State", data["state"])
    col2.metric("Total Balance", f"${data['balance']:,.2f}")
    col3.metric("Risk Level", f"Level {data['risk_level']}/5")

    st.divider()

    # --- แถวกลาง: ข้อมูลรายคู่เงิน ---
    cols = st.columns(len(data["symbols"]))
    for i, (symbol, info) in enumerate(data["symbols"].items()):
        with cols[i]:
            st.subheader(f"🔷 {symbol}")
            st.write(f"**Price:** {info['price']:,.2f}")
            st.write(f"**RSI:** {info['rsi']:.2f}")
            st.info(f"Regime: {info['regime']}")

else:
    st.warning("⏳ กำลังรอข้อมูลจากบอทเทรด...")

# สั่งให้ Refresh หน้าเว็บเองอัตโนมัติทุก 10 วินาที
time.sleep(10)
st.rerun()