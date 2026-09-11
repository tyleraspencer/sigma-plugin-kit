#!/usr/bin/env python3
"""Generate the seat map rows the Movie Theater Seating plugin binds to.

One row per seat. Header names must match the plugin's column bindings in
src/App.jsx exactly -- build-plugin-workbook.py binds by matching the editor
panel entry name against the raw data header, and a mismatch is silently
absent from the plugin's config.

Deterministic (fixed seed) so a re-run publishes the same house.
"""
import csv, random

ROWS = [
    # (row letter, seats in the row, section, price)
    ("A", 12, "Recliner",  24.50),
    ("B", 12, "Recliner",  24.50),
    ("C", 14, "Premium",   19.50),
    ("D", 14, "Premium",   19.50),
    ("E", 14, "Premium",   19.50),
    ("F", 16, "Standard",  15.00),
    ("G", 16, "Standard",  15.00),
    ("H", 16, "Standard",  15.00),
    ("J", 14, "Balcony",   12.00),
    ("K", 14, "Balcony",   12.00),
]

rnd = random.Random(20260911)
out = []
for letter, count, section, price in ROWS:
    for n in range(1, count + 1):
        # Middle seats sell first, back rows sell last -- so the map looks
        # like a real house rather than uniform noise.
        centrality = 1 - abs(n - (count + 1) / 2) / ((count + 1) / 2)
        p_sold = 0.10 + 0.45 * centrality - 0.03 * ROWS.index((letter, count, section, price))
        out.append({
            "seat": "%s%d" % (letter, n),
            "seatRow": letter,
            "seatNumber": n,
            "section": section,
            "status": "sold" if rnd.random() < p_sold else "available",
            "price": price,
        })

with open("seats.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["seat", "seatRow", "seatNumber",
                                       "section", "status", "price"])
    w.writeheader()
    w.writerows(out)

sold = sum(1 for r in out if r["status"] == "sold")
print("seats.csv: %d seats, %d sold, %d available" % (len(out), sold, len(out) - sold))
