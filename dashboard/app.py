import streamlit as st
import pandas as pd
import sys
sys.path.append('.')
from database.connection import get_connection

# Seitenkonfiguration
st.set_page_config(
    page_title="Hybrid Fitness OS",
    page_icon="💪",
    layout="wide"
)

# Header
st.title("Hybrid Fitness OS 💪")
st.subheader("Willkommen zurück, Alexander")

# Drei Spalten für Übersicht
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Trainings diese Woche", "1")
with col2:
    st.metric("Kilometer diese Woche", "8.5 km")
with col3:
    st.metric("Aktuelle Form", "Gut 🟢")

# Befinden
st.divider()
st.subheader("Wie fühlst du dich heute?")
befinden = st.slider("Befinden", 1, 10, 7)
st.write(f"Aktuelles Befinden: {befinden}/10")

# Trainings aus Datenbank
st.divider()
st.subheader("Letzte Trainings")

conn = get_connection()
df = pd.read_sql("SELECT date, type, duration_minutes, distance_km, rating FROM trainings ORDER BY date DESC", conn)
conn.close()

st.dataframe(df, use_container_width=True)