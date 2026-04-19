"""
Czech Real Estate Scanner - Data Models
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class Listing:
    source: str                          # sreality / bazos
    source_id: str
    title: str
    price_czk: int
    size_m2: Optional[float] = None
    disposition: Optional[str] = None
    disposition_code: Optional[int] = None
    locality: str = ""
    district: str = ""
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    construction_type: Optional[str] = None
    condition: Optional[str] = None
    ownership: Optional[str] = None
    energy_rating: Optional[str] = None
    floor: Optional[str] = None
    url: str = ""
    images: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    developer_project: Optional[str] = None   # matched from novostavby.com
    developer_name: Optional[str] = None       # developer company
    developer_url: Optional[str] = None        # novostavby.com project page
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Listing:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class EnrichedListing:
    listing: Listing
    price_per_m2: Optional[float] = None
    local_avg_price_per_m2: Optional[float] = None
    price_discount_pct: Optional[float] = None
    estimated_monthly_rent: Optional[float] = None
    rental_comp_count: int = 0
    gross_annual_yield_pct: Optional[float] = None
    population: Optional[int] = None
    population_trend_5y_pct: Optional[float] = None
    poi_score: Optional[float] = None
    poi_details: Optional[dict] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> EnrichedListing:
        listing_data = d.pop("listing", d)
        listing = Listing.from_dict(listing_data) if isinstance(listing_data, dict) else listing_data
        enrichment = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "listing"}
        return cls(listing=listing, **enrichment)


@dataclass
class ScoredListing:
    enriched: EnrichedListing
    composite_score: float = 0.0
    yield_score: float = 0.0
    discount_score: float = 0.0
    population_score: float = 0.0
    infrastructure_score: float = 0.0
    listing_quality_score: float = 0.0
    claude_summary: Optional[str] = None
    claude_verdict: Optional[str] = None  # BUY / WATCH / SKIP
    claude_risks: Optional[list[str]] = None
    claude_analysis: Optional[dict] = None  # Full Claude response

    @property
    def listing(self) -> Listing:
        return self.enriched.listing

    def to_dict(self) -> dict:
        return asdict(self)

    def short_label(self) -> str:
        l = self.listing
        return f"{l.disposition or '?'} | {l.locality} | {l.price_czk:,} CZK | {l.size_m2 or '?'} m2"
