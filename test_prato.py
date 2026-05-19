#!/usr/bin/env python3
"""Test script to check Prato data in IndicePA."""
import pandas as pd
from pathlib import Path

cache_dir = Path.home() / '.cache' / 'polizia_locale'
uo_file = cache_dir / 'indicepa_uo.xlsx'

print(f'Cache file exists: {uo_file.exists()}')
print(f'File size: {uo_file.stat().st_size / 1024 / 1024:.1f} MB\n')

# Load the Excel file
print('Loading Excel file with openpyxl...')
try:
    df = pd.read_excel(uo_file, dtype=str, engine='openpyxl')
except Exception as e:
    print(f'Error with openpyxl: {e}')
    print('Trying with default engine...')
    df = pd.read_excel(uo_file, dtype=str)
print(f'Loaded {len(df)} rows')
print(f'Columns: {list(df.columns[:10])}...\n')

# Search for Prato
target_istat = '047015'  # Prato ISTAT code
if 'Codice_comune_ISTAT' in df.columns:
    prato_rows = df[df['Codice_comune_ISTAT'].astype(str).str.zfill(6) == target_istat]
    print(f'Rows for Prato (ISTAT {target_istat}): {len(prato_rows)}')
    if len(prato_rows) > 0:
        for idx, row in prato_rows.iterrows():
            desc = row.get('Descrizione_uo', 'N/A')
            mail1 = row.get('Mail1', '')
            tipo1 = row.get('Tipo_Mail1', '')
            print(f'  Description: {desc}')
            print(f'  Mail1: {mail1} (Tipo: {tipo1})')
            print()
else:
    print('Column Codice_comune_ISTAT not found!')
    print(f'Available columns: {list(df.columns)}')
