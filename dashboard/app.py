import streamlit as st

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
    st.metric("Trainings diese Woche", "3")

with col2:
    st.metric("Kilometer diese Woche", "24 km")

with col3:
    st.metric("Aktuelle Form", "Gut 🟢")

# Befinden
st.divider()
st.subheader("Wie fühlst du dich heute?")
befinden = st.slider("Befinden", 1, 10, 7)
st.write(f"Aktuelles Befinden: {befinden}/10")