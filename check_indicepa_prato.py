#!/usr/bin/env python3
"""Check IndicePA data for Prato."""
import pandas as pd
from pathlib import Path

cache_dir = Path.home() / '.cache' / 'polizia_locale'
csv_path = cache_dir / 'indicepa_uo.csv'

print('Loading IndicePA UO CSV...')
df = pd.read_csv(csv_path, dtype=str, low_memory=False)

# Prato ISTAT code
target_istat = '047015'

prato_rows = df[df['Codice_comune_ISTAT'].astype(str).str.zfill(6) == target_istat]
print(f'Found {len(prato_rows)} records for Prato')

if len(prato_rows) > 0:
    for idx, row in prato_rows.iterrows():
        print(f"\n=== Record {idx+1} ===")
        print(f"Descrizione UO: {row.get('Descrizione_uo', 'N/A')}")
        print(f"Mail1: {row.get('Mail1', '')} (Tipo: {row.get('Tipo_Mail1', '')})")
        print(f"Mail2: {row.get('Mail2', '')} (Tipo: {row.get('Tipo_Mail2', '')})")
        print(f"Mail3: {row.get('Mail3', '')} (Tipo: {row.get('Tipo_Mail3', '')})")
        print(f"Mail4: {row.get('Mail4', '')} (Tipo: {row.get('Tipo_Mail4', '')})")
        print(f"Mail5: {row.get('Mail5', '')} (Tipo: {row.get('Tipo_Mail5', '')})")
else:
    print("No records found for Prato in IndicePA UO!")
