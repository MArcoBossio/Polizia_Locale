#!/usr/bin/env python3
"""Check Prato in output JSON."""
import json

with open('output/polizia_locale_toscana.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print("Searching for Prato...\n")
found = False
for item in data:
    if 'Prato' in item.get('comune', ''):
        found = True
        print(f"Comune: {item['comune']}")
        print(f"Email: {item.get('mail', '')}")
        print(f"PEC: {item.get('pec', '')}")
        print(f"Fonte: {item.get('fonte', '')}")
        print()

if not found:
    print("Prato not found in output!")
