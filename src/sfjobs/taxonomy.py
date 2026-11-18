"""Classify postings into Salesforce clouds and roles using config/taxonomy.yaml."""
from __future__ import annotations

import os
import re
from functools import lru_cache

import yaml

from .common import CONFIG_DIR


class Taxonomy:
    def __init__(self, path: str | None = None):
        path = path or os.path.join(CONFIG_DIR, "taxonomy.yaml")
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.relevance = [re.compile(p, re.I | re.S) for p in cfg["relevance"]]
        self.groups = {k: v["label"] for k, v in cfg["groups"].items()}
        self.clouds = {}
        for key, c in cfg["clouds"].items():
            self.clouds[key] = {
                "label": c["label"],
                "group": c["group"],
                "needs_sf": c.get("needs_salesforce", False),
                "res": [re.compile(p, re.I) for p in c["patterns"]],
            }
        self.roles = {k: [re.compile(p, re.I) for p in v] for k, v in cfg["roles"].items()}
        self.title_signals = [re.compile(p, re.I) for p in cfg["title_signals"]]
        self.tech_role = re.compile(cfg["technical_roles"], re.I)
        self.exclude_titles = re.compile(cfg["exclude_titles"], re.I)

    def is_relevant(self, text: str) -> bool:
        return any(r.search(text) for r in self.relevance)

    def classify(self, title: str, text: str) -> dict:
        full = f"{title}\n{text}"
        relevant = self.is_relevant(full)
        clouds = []
        for key, c in self.clouds.items():
            if c["needs_sf"] and not relevant:
                continue
            if any(r.search(full) for r in c["res"]):
                clouds.append(key)
        title_hit = any(r.search(title or "") for r in self.title_signals)
        if self.exclude_titles.search(title or "") and not re.search(r"salesforce|sfdc", title or "", re.I):
            return {"relevant": False, "clouds": [], "roles": []}
        specific = bool(clouds)
        ecosystem = title_hit or (
            (relevant or specific) and specific and bool(self.tech_role.search(title or ""))
        )
        roles = [k for k, rs in self.roles.items() if any(r.search(title or "") for r in rs)]
        if ecosystem and not clouds:
            clouds = ["salesforce_general"]
        return {"relevant": ecosystem, "clouds": clouds if ecosystem else [], "roles": roles}

    def label(self, key: str) -> str:
        if key == "salesforce_general":
            return "Salesforce (no specific cloud)"
        return self.clouds.get(key, {}).get("label", key)

    def group_of(self, key: str) -> str:
        if key == "salesforce_general":
            return "general"
        return self.clouds.get(key, {}).get("group", "other")

    def industries_keys(self) -> list[str]:
        return [k for k, c in self.clouds.items() if c["group"] == "industries"]


@lru_cache(maxsize=1)
def get_taxonomy() -> Taxonomy:
    return Taxonomy()
