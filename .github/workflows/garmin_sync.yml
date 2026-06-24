name: Garmin Sync

on:
  schedule:
    - cron: '0 */2 * * *'
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install garminconnect psycopg2-binary python-dotenv

      - name: Run Garmin sync
        env:
          GARMIN_EMAIL: ${{ secrets.GARMIN_EMAIL }}
          GARMIN_PASSWORD: ${{ secrets.GARMIN_PASSWORD }}
          RAILWAY_DATABASE_URL: ${{ secrets.RAILWAY_DATABASE_URL }}
        run: python data/garmin_sync_all.py