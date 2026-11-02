"""Typed access to config/*.yaml."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"

load_dotenv(REPO_ROOT / ".env")


class Sector(BaseModel):
    label: str
    keywords: list[str]
    title_terms: list[str] = []
    github_topics: list[str]
    arxiv_categories: list[str]
    arxiv_filtered: list[str] = []
    sbir_keywords: list[str]
    negative_keywords: list[str] = []


class Thesis(BaseModel):
    version: int
    sectors: dict[str, Sector]


def _load(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


@cache
def thesis() -> Thesis:
    return Thesis.model_validate(_load("thesis.yaml"))


@cache
def sources() -> dict[str, Any]:
    return _load("sources.yaml")


@cache
def weights() -> dict[str, Any]:
    return _load("weights.yaml")


@cache
def models() -> dict[str, Any]:
    return _load("models.yaml")
