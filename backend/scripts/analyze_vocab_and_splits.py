"""
Analyze vocabulary and OOV rates on full HDFS splits.
"""

import csv
import random
from collections import Counter

all_normal = []
all_anomaly = []

with open("data/benchmarks/hdfs_full/raw/Event_traces.csv", "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        b_id = row["BlockId"]
        lbl = 1 if row["Label"].strip().lower() == "fail" else 0
        raw_feat = row["Features"].strip("[]").replace("'", "").replace('"', "").split(",")
        events = [e.strip() for e in raw_feat if e.strip()]
        if lbl == 0:
            all_normal.append((b_id, events))
        else:
            all_anomaly.append((b_id, events))

print(f"Total Normal: {len(all_normal):,}, Total Anomaly: {len(all_anomaly):,}")

# Shuffle with seed 42
rng = random.Random(42)
rng.shuffle(all_normal)
rng.shuffle(all_anomaly)

# 80/10/10 split
n_train = int(len(all_normal) * 0.8)
n_val = int(len(all_normal) * 0.1)
train_norm = all_normal[:n_train]
val_norm = all_normal[n_train:n_train + n_val]
test_norm = all_normal[n_train + n_val:]

n_anom_val = int(len(all_anomaly) * 0.5)
val_anom = all_anomaly[:n_anom_val]
test_anom = all_anomaly[n_anom_val:]

# Collect templates
train_events = Counter()
for _, evs in train_norm:
    for e in evs:
        train_events[e] += 1

val_events = Counter()
for _, evs in (val_norm + val_anom):
    for e in evs:
        val_events[e] += 1

test_events = Counter()
for _, evs in (test_norm + test_anom):
    for e in evs:
        test_events[e] += 1

print(f"Unique templates in Train (Normal): {len(train_events)}")
print("Train template counts (sorted):")
for k, v in train_events.most_common():
    print(f"  {k}: {v:,}")

val_oov = set(val_events.keys()) - set(train_events.keys())
test_oov = set(test_events.keys()) - set(train_events.keys())
print(f"OOV templates in Validation: {val_oov}")
print(f"OOV templates in Test: {test_oov}")

total_val_tokens = sum(val_events.values())
total_val_oov_tokens = sum(val_events[oov] for oov in val_oov)
val_oov_rate = (total_val_oov_tokens / total_val_tokens * 100) if total_val_tokens else 0.0

total_test_tokens = sum(test_events.values())
total_test_oov_tokens = sum(test_events[oov] for oov in test_oov)
test_oov_rate = (total_test_oov_tokens / total_test_tokens * 100) if total_test_tokens else 0.0

print(f"Validation total tokens: {total_val_tokens:,}, OOV tokens: {total_val_oov_tokens:,} ({val_oov_rate:.4f}%)")
print(f"Test total tokens: {total_test_tokens:,}, OOV tokens: {total_test_oov_tokens:,} ({test_oov_rate:.4f}%)")
