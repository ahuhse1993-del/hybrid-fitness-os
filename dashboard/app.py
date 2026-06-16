import streamlit as st
import pandas as pd
import sys
from datetime import date
sys.path.append('.')
from database.connection import get_connection

st.set_page_config(
    page_title="Hybrid Fitness OS",
    page_icon="💪",
    layout="wide"
)

st.title("Hybrid Fitness OS 💪")
st.subheader("Willkommen zurück, Alexander")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Trainings diese Woche", "4")
with col2:
    st.metric("Kilometer diese Woche", "37.2 km")
with col3:
    st.metric("Aktuelle Form", "Gut 🟢")

st.divider()
st.subheader("Wie fühlst du dich heute?")

col1, col2, col3 = st.columns(3)
with col1:
    energy = st.slider("Energie", 1, 10, 7)
with col2:
    sleep = st.slider("Schlafqualität", 1, 10, 7)
with col3:
    stress = st.slider("Stress", 1, 10, 3)

notes = st.text_input("Notiz (optional)")

if st.button("Befinden speichern"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO feelings (date, energy_level, sleep_quality, stress_level, notes) VALUES (%s, %s, %s, %s, %s)",
        (date.today(), energy, sleep, stress, notes)
    )
    conn.commit()
    conn.close()
    st.success("✅ Befinden gespeichert!")

st.divider()
st.subheader("Letzte Trainings")

conn = get_connection()
df = pd.read_sql("SELECT date, type, duration_minutes, distance_km, rating FROM trainings ORDER BY date DESC", conn)
conn.close()

st.dataframe(df, use_container_width=True)