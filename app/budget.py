"""Total points budget: accumulates across launches; reserve first, record only on success, refund on failure; refuse when the cap is exceeded.

Ledger: runs/budget_ledger.json (not version-controlled). Prices follow the official feature-cost (checked 2026-10-15).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "runs" / "budget_ledger.json"
PRICES = {"makeup-vto": 1, "face-attr-analysis(1-5)": 10, "skin-tone-analysis": 20, "skin-analysis-hd(1-4)": 12,
          "mu-transfer": 2, "image-to-image": 1, "obj-replace": 1,   # makeup transfer V1.0, image-to-image V2.0 (feature-cost, checked 2026-10-16)
          "hair-color": 1, "beard-style": 2}   # official spec page, 2026-10-16: hair color Full / Ombre 1 point each, beard style 2 points


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, cap: int, path: Path = LEDGER):
        self.cap = cap
        self.path = path
        self.lock = threading.Lock()
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.spent = int(data.get("spent", 0))
        self.events = data.get("events", [])
        self.reserved = 0

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"cap_last_used": self.cap, "spent": self.spent,
                                   "events": self.events[-500:]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)   # atomic: a crash mid-write never leaves a half-written ledger

    def remaining(self) -> int:
        return max(0, self.cap - self.spent - self.reserved)

    def reserve(self, feature: str) -> int:
        cost = PRICES[feature]
        with self.lock:
            if self.spent + self.reserved + cost > self.cap:
                raise BudgetExceeded(f"預算上限 {self.cap} 點，已用 {self.spent} 點，這次需要 {cost} 點")
            self.reserved += cost
        return cost

    def settle(self, feature: str, cost: int, success: bool, note: str = ""):
        with self.lock:
            self.reserved -= cost
            if success:
                self.spent += cost
            self.events.append({"at": dt.datetime.now().isoformat(timespec="seconds"), "feature": feature,
                                "cost": cost if success else 0, "success": success, "note": note})
            try:
                self._save()
            except OSError as exc:   # the in-memory count stays right even if the disk write fails
                print(f"ERROR budget ledger not saved: {exc}", file=sys.stderr)

    def status(self) -> dict:
        return {"cap": self.cap, "spent": self.spent, "remaining": self.remaining(), "prices": PRICES}
