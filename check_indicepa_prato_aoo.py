#!/usr/bin/env python3
"""Check IndicePA AOO data for Prato."""
import pandas as pd
from pathlib import Path

cache_dir = Path.home() / '.cache' / 'polizia_locale'
csv_path = cache_dir / 'indicepa_aoo.csv'

print('Loading IndicePA AOO CSV...')
if not csv_path.exists():
    print("AOO CSV doesn't exist yet, converting from Excel...")
    import sys
    sys.exit(1)

df = pd.read_csv(csv_path, dtype=str, low_memory=False)

# Prato ISTAT code
target_istat = '047015'

prato_rows = df[df['Codice_comune_ISTAT'].astype(str).str.zfill(6) == target_istat]
print(f'Found {len(prato_rows)} records for Prato in AOO')

if len(prato_rows) > 0:
    for idx, row in prato_rows.iterrows():
        print(f"\n=== Record {idx+1} ===")
        print(f"Descrizione AOO: {row.get('Descrizione_aoo', 'N/A')}")
        print(f"Mail1: {row.get('Mail1', '')} (Tipo: {row.get('Tipo_Mail1', '')})")
        print(f"Mail2: {row.get('Mail2', '')} (Tipo: {row.get('Tipo_Mail2', '')})")
else:
    print("No records found for Prato in IndicePA AOO!")
