"""Synthetic warehouse foundation with explicit metric semantics and real SQL execution."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

metadata = MetaData()
products = Table(
    "products",
    metadata,
    Column("product_id", String, primary_key=True),
    Column("family", String, nullable=False),
)
equipment = Table(
    "equipment",
    metadata,
    Column("equipment_id", String, primary_key=True),
    Column("chamber_id", String, nullable=False),
)
programs = Table(
    "test_programs",
    metadata,
    Column("program_version", String, primary_key=True),
    Column("stage", String, nullable=False),
)
lots = Table(
    "lots",
    metadata,
    Column("lot_id", String, primary_key=True),
    Column("product_id", ForeignKey("products.product_id"), nullable=False),
    Column("family_id", String, nullable=False),
)
test_results = Table(
    "test_results",
    metadata,
    Column("test_result_id", String, primary_key=True),
    Column("source_system", String, nullable=False),
    Column("source_record_id", String, nullable=False),
    Column("lot_id", ForeignKey("lots.lot_id"), nullable=False),
    Column("unit_id", String, nullable=False),
    Column("wafer_id", String),
    Column("stage", String, nullable=False),
    Column("program_version", ForeignKey("test_programs.program_version"), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("event_time", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("pass_flag", Boolean, nullable=False),
    Column("is_valid", Boolean, nullable=False),
    Column("equipment_id", ForeignKey("equipment.equipment_id"), nullable=False),
    CheckConstraint("attempt_no > 0"),
    CheckConstraint("stage IN ('CP','FT')"),
    UniqueConstraint("source_system", "source_record_id"),
)
bin_results = Table(
    "bin_results",
    metadata,
    Column("bin_result_id", String, primary_key=True),
    Column("test_result_id", ForeignKey("test_results.test_result_id"), nullable=False),
    Column("bin_type", String, nullable=False),
    Column("bin_code", String, nullable=False),
    UniqueConstraint("test_result_id", "bin_type", "bin_code"),
)
defect_records = Table(
    "defect_records",
    metadata,
    Column("defect_id", String, primary_key=True),
    Column("lot_id", ForeignKey("lots.lot_id"), nullable=False),
    Column("wafer_id", String, nullable=False),
    Column("x_um", Float, nullable=False),
    Column("y_um", Float, nullable=False),
    Column("category", String, nullable=False),
    Column("image_asset_id", String),
    Column("event_time", DateTime(timezone=True), nullable=False),
)
process_events = Table(
    "process_events",
    metadata,
    Column("process_event_id", String, primary_key=True),
    Column("lot_id", ForeignKey("lots.lot_id"), nullable=False),
    Column("wafer_id", String, nullable=False),
    Column("step", String, nullable=False),
    Column("equipment_id", ForeignKey("equipment.equipment_id"), nullable=False),
    Column("chamber_id", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
    Column("is_rework", Boolean, nullable=False),
    CheckConstraint("ended_at >= started_at"),
)
fdc_events = Table(
    "fdc_events",
    metadata,
    Column("source_event_id", String, primary_key=True),
    Column("process_event_id", ForeignKey("process_events.process_event_id"), nullable=False),
    Column("equipment_id", ForeignKey("equipment.equipment_id"), nullable=False),
    Column("chamber_id", String, nullable=False),
    Column("parameter", String, nullable=False),
    Column("sample_time", DateTime(timezone=True), nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String, nullable=False),
    Column("quality", String, nullable=False),
    Column("is_alert", Boolean, nullable=False),
)
maintenance_events = Table(
    "maintenance_events",
    metadata,
    Column("maintenance_id", String, primary_key=True),
    Column("equipment_id", ForeignKey("equipment.equipment_id"), nullable=False),
    Column("chamber_id", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("ended_at >= started_at"),
)
unit_traceability = Table(
    "unit_traceability",
    metadata,
    Column("mapping_id", String, primary_key=True),
    Column("lot_id", ForeignKey("lots.lot_id"), nullable=False),
    Column("cp_unit_id", String, nullable=False),
    Column("ft_unit_id", String, nullable=False),
    Column("source", String, nullable=False),
    UniqueConstraint("lot_id", "cp_unit_id", "ft_unit_id"),
)
dataset_registry = Table(
    "dataset_registry",
    metadata,
    Column("dataset_id", String, primary_key=True),
    Column("content_hash", String, nullable=False),
    Column("origin", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

METRIC_VERSION = "yield-cohort-v1"


class YieldQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lot_ids: list[str] = Field(min_length=1, max_length=100)
    stage: str = Field(pattern="^(CP|FT)$")
    program_version: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime
    as_of: AwareDatetime
    metric: str = Field(default="final", pattern="^(first|final)$")

    @model_validator(mode="after")
    def valid_window(self):
        if self.start >= self.end or self.as_of < self.end:
            raise ValueError("Require start < end <= as_of")
        return self


def generate(
    seed: int,
    *,
    lot_count: int = 12,
    units_per_lot: int = 24,
    start: datetime | None = None,
    prefix: str = "DEV",
) -> dict:
    if lot_count < 2 or units_per_lot < 4:
        raise ValueError("At least two lots and four units are required")
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    if start.tzinfo is None:
        raise ValueError("Start requires timezone")
    rng = random.Random(seed)

    def uid(value):
        return str(uuid5(NAMESPACE_URL, f"semibrain/{prefix}/{seed}/{value}"))

    tables = {table.name: [] for table in metadata.sorted_tables if table is not dataset_registry}
    tables["products"] = [
        {"product_id": prefix + "-P-A", "family": "logic"},
        {"product_id": prefix + "-P-B", "family": "mixed-signal"},
    ]
    tables["equipment"] = [
        {"equipment_id": prefix + "-EQ-A", "chamber_id": "C1"},
        {"equipment_id": prefix + "-EQ-B", "chamber_id": "C2"},
    ]
    tables["test_programs"] = [
        {"program_version": s + "-v" + str(v), "stage": s} for s in ["CP", "FT"] for v in [1, 2]
    ]
    for index in range(lot_count):
        # Scenario is a generation parameter, never a label in the online tables.
        scenario = index % 6
        lot = f"{prefix}-Lot-{index:04d}"
        eq = prefix + ("-EQ-A" if index % 2 == 0 else "-EQ-B")
        chamber = "C1" if index % 2 == 0 else "C2"
        event = start + timedelta(hours=index * 4)
        product = prefix + ("-P-B" if scenario == 1 else "-P-A")
        tables["lots"].append(
            {"lot_id": lot, "product_id": product, "family_id": f"{prefix}-family-{index // 3}"}
        )
        process = uid(f"process/{index}")
        tables["process_events"].append(
            dict(
                process_event_id=process,
                lot_id=lot,
                wafer_id=lot + "-W01",
                step="ETCH",
                equipment_id=eq,
                chamber_id=chamber,
                started_at=event - timedelta(hours=2),
                ended_at=event - timedelta(hours=1),
                is_rework=False,
            )
        )
        for n in range(8):
            tables["fdc_events"].append(
                dict(
                    source_event_id=uid(f"fdc/{index}/{n}"),
                    process_event_id=process,
                    equipment_id=eq,
                    chamber_id=chamber,
                    parameter="pressure",
                    sample_time=event - timedelta(minutes=115 - n * 7),
                    value=round(rng.gauss(12 if scenario == 1 else 10, 0.5), 3),
                    unit="mTorr",
                    quality="valid",
                    is_alert=scenario == 1 and n > 5,
                )
            )
        if index % 3 == 0:
            tables["maintenance_events"].append(
                dict(
                    maintenance_id=uid(f"maint/{index}"),
                    equipment_id=eq,
                    chamber_id=chamber,
                    event_type="scheduled_clean",
                    started_at=event - timedelta(hours=4),
                    ended_at=event - timedelta(hours=3),
                    ingested_at=event + timedelta(hours=2),
                )
            )
        for n in range(rng.randint(1, 5)):
            tables["defect_records"].append(
                dict(
                    defect_id=uid(f"defect/{index}/{n}"),
                    lot_id=lot,
                    wafer_id=lot + "-W01",
                    x_um=rng.uniform(-5000, 5000),
                    y_um=rng.uniform(-5000, 5000),
                    category="unclassified",
                    image_asset_id=None,
                    event_time=event,
                )
            )
        for unit in range(units_per_lot):
            for stage in ["CP", "FT"]:
                unit_id = f"{lot}-W01-D{unit:04d}" if stage == "CP" else f"{lot}-PKG-{unit:04d}"
                stage_time = event + timedelta(minutes=unit, hours=0 if stage == "CP" else 1)
                version = stage + "-v" + ("2" if scenario == 1 else "1")
                passed = rng.random() < (0.7 if scenario == 1 else 0.92)
                if scenario == 3 and unit == 0:
                    passed = False
                test = dict(
                    test_result_id=uid(f"test/{index}/{stage}/{unit}/1"),
                    source_system="YMS",
                    source_record_id=f"{lot}/{stage}/{unit}/1",
                    lot_id=lot,
                    unit_id=unit_id,
                    wafer_id=lot + "-W01" if stage == "CP" else None,
                    stage=stage,
                    program_version=version,
                    attempt_no=1,
                    event_time=stage_time,
                    ingested_at=stage_time
                    + timedelta(hours=36 if scenario == 2 and unit < 3 else 0, minutes=2),
                    pass_flag=passed,
                    is_valid=True,
                    equipment_id=eq,
                )
                tables["test_results"].append(test)
                if scenario == 3 and not passed:
                    retry = test | dict(
                        test_result_id=uid(f"test/{index}/{stage}/{unit}/2"),
                        source_record_id=f"{lot}/{stage}/{unit}/2",
                        attempt_no=2,
                        event_time=stage_time + timedelta(hours=2),
                        ingested_at=stage_time + timedelta(hours=2, minutes=2),
                        pass_flag=True,
                    )
                    tables["test_results"].append(retry)
                if scenario == 4 and unit == 0:
                    # Repeated test, invalid final attempt must not overwrite valid result.
                    tables["test_results"].append(
                        test
                        | dict(
                            test_result_id=uid(f"invalid/{index}/{stage}"),
                            source_record_id=f"{lot}/{stage}/{unit}/invalid",
                            attempt_no=9,
                            is_valid=False,
                            pass_flag=False,
                        )
                    )
                for bin_type, code in [
                    ("hard", "1" if passed else "9"),
                    ("soft", "PASS" if passed else "FAIL"),
                ]:
                    tables["bin_results"].append(
                        dict(
                            bin_result_id=uid(f"bin/{test['test_result_id']}/{bin_type}"),
                            test_result_id=test["test_result_id"],
                            bin_type=bin_type,
                            bin_code=code,
                        )
                    )
            if scenario != 4 and unit % 5 != 0:
                tables["unit_traceability"].append(
                    dict(
                        mapping_id=uid(f"map/{index}/{unit}"),
                        lot_id=lot,
                        cp_unit_id=f"{lot}-W01-D{unit:04d}",
                        ft_unit_id=f"{lot}-PKG-{unit:04d}",
                        source="synthetic-packaging-map",
                    )
                )
    canonical = json.dumps(
        tables, default=lambda d: d.isoformat(), sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "dataset_id": uid("dataset"),
        "content_hash": digest,
        "origin": "synthetic",
        "tables": tables,
    }


def save_dataset(dataset: dict, path: Path):
    path.write_text(
        json.dumps(dataset, default=lambda d: d.isoformat(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_dataset(engine, dataset: dict):
    if dataset.get("origin") != "synthetic":
        raise ValueError("This loader accepts synthetic data only")
    actual_hash = hashlib.sha256(
        json.dumps(
            dataset["tables"],
            default=lambda d: d.isoformat(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if actual_hash != dataset["content_hash"]:
        raise ValueError("DATASET_HASH_MISMATCH")
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(
            select(dataset_registry.c.content_hash).where(
                dataset_registry.c.dataset_id == dataset["dataset_id"]
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing != dataset["content_hash"]:
                raise ValueError("DATASET_HASH_CONFLICT")
            return False
        for table in metadata.sorted_tables:
            if table is dataset_registry:
                continue
            records = dataset["tables"][table.name]
            normalized = []
            for row in records:
                normalized.append(
                    {
                        k: datetime.fromisoformat(v)
                        if isinstance(table.c[k].type, DateTime) and isinstance(v, str)
                        else v
                        for k, v in row.items()
                    }
                )
            if normalized:
                if table is programs:
                    conn.execute(pg_insert(table).on_conflict_do_nothing(), normalized)
                    for record in normalized:
                        actual = conn.execute(
                            select(table.c.stage).where(
                                table.c.program_version == record["program_version"]
                            )
                        ).scalar_one()
                        if actual != record["stage"]:
                            raise ValueError("PROGRAM_VERSION_CONFLICT")
                else:
                    conn.execute(insert(table), normalized)
        conn.execute(
            insert(dataset_registry),
            dict(
                dataset_id=dataset["dataset_id"],
                content_hash=dataset["content_hash"],
                origin="synthetic",
                created_at=datetime.now(timezone.utc),
            ),
        )
    return True


YIELD_SQL = """
WITH eligible AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY source_system, lot_id, unit_id, stage, program_version
    ORDER BY attempt_no ASC, event_time ASC, test_result_id ASC) AS first_rank,
    ROW_NUMBER() OVER (
    PARTITION BY source_system, lot_id, unit_id, stage, program_version
    ORDER BY attempt_no DESC, event_time DESC, test_result_id DESC) AS final_rank
  FROM test_results
  WHERE lot_id = ANY(:lot_ids) AND stage=:stage AND program_version=:program_version
    AND is_valid AND ingested_at <= :as_of AND event_time <= :as_of
), cohort AS (
  SELECT source_system, lot_id, unit_id, stage, program_version
  FROM eligible WHERE first_rank=1 AND event_time >= :start AND event_time < :end
), chosen AS (
  SELECT e.* FROM eligible e JOIN cohort c USING(source_system,lot_id,unit_id,stage,program_version)
  WHERE (:metric='first' AND e.first_rank=1) OR (:metric='final' AND e.final_rank=1)
)
SELECT COUNT(*) AS denominator,
       COUNT(*) FILTER (WHERE pass_flag) AS numerator,
       MAX(ingested_at) AS watermark,
       ARRAY(SELECT DISTINCT l.product_id FROM chosen c JOIN lots l USING(lot_id)
             ORDER BY l.product_id) AS product_ids
FROM chosen
"""


def query_yield(engine, query: YieldQuery) -> dict:
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            row = conn.execute(text(YIELD_SQL), query.model_dump()).mappings().one()
    n, d = int(row["numerator"]), int(row["denominator"])
    return {
        "numerator": n,
        "denominator": d,
        "value": n / d if d else None,
        "unit": "fraction",
        "metric": query.metric,
        "metric_version": METRIC_VERSION,
        "stage": query.stage,
        "program_version": query.program_version,
        "product_ids": row["product_ids"],
        "cohort_start": query.start.isoformat(),
        "cohort_end": query.end.isoformat(),
        "as_of": query.as_of.isoformat(),
        "watermark": row["watermark"].isoformat() if row["watermark"] else None,
        "data_origin": "synthetic",
        "warnings": ["EMPTY_COHORT"] if not d else [],
    }


def compare_yields(target: dict, control: dict) -> dict:
    if len(target.get("product_ids", [])) != 1 or len(control.get("product_ids", [])) != 1:
        raise ValueError("UNMATCHED_COHORTS")
    for field in [
        "metric_version",
        "stage",
        "program_version",
        "metric",
        "as_of",
        "product_ids",
        "cohort_start",
        "cohort_end",
    ]:
        if target[field] != control[field]:
            raise ValueError("UNMATCHED_COHORTS")
    return {
        "difference_percentage_points": 100 * (target["value"] - control["value"])
        if target["value"] is not None and control["value"] is not None
        else None,
        "causal_conclusion": None,
    }


def engine_from_url(url: str):
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("An explicit PostgreSQL psycopg URL is required")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
