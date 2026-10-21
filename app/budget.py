"""點數總預算：跨次啟動累計，先預留、成功才記帳、失敗退回；超過上限就拒絕。

帳本：runs/budget_ledger.json（不進版控）。價目依官方 feature-cost（2026-10-15 實查）。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "runs" / "budget_ledger.json"
PRICES = {"makeup-vto": 1, "face-attr-analysis(1-5)": 10, "skin-tone-analysis": 20, "skin-analysis-hd(1-4)": 12,
          "mu-transfer": 2, "image-to-image": 1, "obj-replace": 1,   # 仿妝 V1.0、圖生圖 V2.0（feature-cost 2026-10-16 實查）
          "hair-color": 1, "beard-style": 2}   # 官方規格頁 2026-10-16：髮色 Full／Ombre 各 1 點、鬍子造型 2 點


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
        self.path.write_text(json.dumps({"cap_last_used": self.cap, "spent": self.spent,
                                         "events": self.events[-500:]}, ensure_ascii=False, indent=1),
                             encoding="utf-8")

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
            self._save()

    def status(self) -> dict:
        return {"cap": self.cap, "spent": self.spent, "remaining": self.remaining(), "prices": PRICES}
