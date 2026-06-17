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

# Daten aus Datenbank laden
conn = get_connection()
df = pd.read_sql("SELECT * FROM trainings ORDER BY date DESC", conn)
conn.close()

# Datum konvertieren
df['date'] = pd.to_datetime(df['date'])
today = pd.Timestamp.today()
week_start = today - pd.Timedelta(days=7)

# Metriken
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Trainings diese Woche", len(df[df['date'] >= week_start]))
with col2:
    st.metric("Kilometer diese Woche", f"{df[df['date'] >= week_start]['distance_km'].sum():.1f} km")
with col3:
    st.metric("Ø Herzfrequenz", f"{df['heart_rate_avg'].mean():.0f} bpm")

# Befinden Input
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

# Trainings
st.divider()
st.subheader("Letzte Aktivitäten")
st.dataframe(
    df[['date', 'type', 'duration_minutes', 'distance_km', 'heart_rate_avg', 'notes']].head(10),
    use_container_width=True
)