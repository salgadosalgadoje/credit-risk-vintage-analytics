"""Load the LendingClub extract into the PostgreSQL dimensional warehouse."""

from __future__ import annotations

import io
import time
from datetime import date
from math import floor
from typing import Any, Literal, TypeVar

import polars as pl
import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config import settings

RAW_COLUMNS = [
    "id",
    "issue_d",
    "last_pymnt_d",
    "loan_status",
    "grade",
    "sub_grade",
    "term",
    "emp_length",
    "home_ownership",
    "verification_status",
    "addr_state",
    "annual_inc",
    "purpose",
    "application_type",
    "dti",
    "fico_range_low",
    "fico_range_high",
    "loan_amnt",
    "funded_amnt",
    "int_rate",
    "installment",
    "total_pymnt",
    "total_rec_prncp",
    "total_rec_int",
    "recoveries",
    "collection_recovery_fee",
]

DEFAULT_STATUSES = {
    "Charged Off",
    "Default",
    "Does not meet the credit policy. Status:Charged Off",
}
COPY_COLUMNS = [
    "loan_id",
    "issue_date_key",
    "last_pymnt_date_key",
    "risk_grade_key",
    "borrower_key",
    "purpose_key",
    "loan_status",
    "is_default",
    "months_on_book",
    "annual_inc",
    "dti",
    "fico_score",
    "loan_amnt",
    "funded_amnt",
    "int_rate",
    "installment",
    "total_pymnt",
    "total_rec_prncp",
    "total_rec_int",
    "recoveries",
    "collection_recovery_fee",
    "principal_loss",
    "net_profit",
]
FACT_NUMERIC_COLUMNS = [
    "loan_id",
    "issue_date_key",
    "last_pymnt_date_key",
    "risk_grade_key",
    "borrower_key",
    "purpose_key",
    "months_on_book",
    "annual_inc",
    "dti",
    "fico_score",
    "loan_amnt",
    "funded_amnt",
    "int_rate",
    "installment",
    "total_pymnt",
    "total_rec_prncp",
    "total_rec_int",
    "recoveries",
    "collection_recovery_fee",
    "principal_loss",
    "net_profit",
]
COPY_SQL = (
    f"COPY dw.fact_loans ({', '.join(COPY_COLUMNS)}) "
    "FROM STDIN WITH (FORMAT CSV, NULL '')"
)


class DimensionDateContract(BaseModel):
    model_config = ConfigDict(strict=True)

    date_key: int
    full_date: date
    year: int = Field(ge=1900, le=9999)
    quarter: int = Field(ge=1, le=4)
    year_quarter: str = Field(min_length=1, max_length=7)
    month: int = Field(ge=1, le=12)
    month_name: str = Field(min_length=1, max_length=15)
    year_month: str = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def date_attributes_match(self) -> DimensionDateContract:
        if self.date_key != -1 and self.date_key != int(self.full_date.strftime("%Y%m%d")):
            raise ValueError("date_key must match full_date in YYYYMMDD format")
        if (
            self.year != self.full_date.year
            or self.month != self.full_date.month
            or self.quarter != (self.full_date.month - 1) // 3 + 1
        ):
            raise ValueError("calendar attributes must match full_date")
        return self


class RiskGradeContract(BaseModel):
    model_config = ConfigDict(strict=True)

    grade: Literal["A", "B", "C", "D", "E", "F", "G"]
    sub_grade: str = Field(pattern=r"^[A-G][1-5]$", max_length=3)
    term_months: Literal[36, 60]
    risk_segment: Literal["Prime", "Near-Prime", "Subprime"]

    @model_validator(mode="after")
    def grade_attributes_match(self) -> RiskGradeContract:
        if self.sub_grade[0] != self.grade:
            raise ValueError("sub_grade must begin with grade")
        expected = "Prime" if self.grade in {"A", "B"} else (
            "Near-Prime" if self.grade in {"C", "D"} else "Subprime"
        )
        if self.risk_segment != expected:
            raise ValueError("risk_segment does not match grade")
        return self


class BorrowerContract(BaseModel):
    model_config = ConfigDict(strict=True)

    emp_length: str = Field(min_length=1, max_length=25)
    home_ownership: str = Field(min_length=1, max_length=20)
    verification_status: str = Field(min_length=1, max_length=30)
    addr_state: str = Field(min_length=1, max_length=10)
    income_tier: Literal["<40K", "40K-75K", "75K-120K", "120K+", "Unknown"]


class LoanPurposeContract(BaseModel):
    model_config = ConfigDict(strict=True)

    purpose: str = Field(min_length=1, max_length=50)
    application_type: str = Field(min_length=1, max_length=25)


class FactLoanContract(BaseModel):
    model_config = ConfigDict(strict=True)

    loan_id: int = Field(gt=0)
    issue_date_key: int = Field(gt=0)
    last_pymnt_date_key: int
    risk_grade_key: int = Field(gt=0)
    borrower_key: int = Field(gt=0)
    purpose_key: int = Field(gt=0)
    loan_status: str = Field(min_length=1, max_length=60)
    is_default: bool
    months_on_book: int = Field(ge=0)
    annual_inc: float | None = Field(default=None, ge=0)
    dti: float | None = None
    fico_score: int | None = Field(default=None, ge=300, le=850)
    loan_amnt: float = Field(gt=0)
    funded_amnt: float = Field(gt=0)
    int_rate: float = Field(ge=0, le=1)
    installment: float = Field(ge=0)
    total_pymnt: float = Field(ge=0)
    total_rec_prncp: float = Field(ge=0)
    total_rec_int: float = Field(ge=0)
    recoveries: float = Field(ge=0)
    collection_recovery_fee: float = Field(ge=0)
    principal_loss: float = Field(ge=0)
    net_profit: float


ContractT = TypeVar("ContractT", bound=BaseModel)


def _numeric_expr(column: str) -> pl.Expr:
    return (
        pl.col(column)
        .str.strip_chars()
        .str.replace_all(",", "")
        .str.replace_all("%", "")
        .cast(pl.Float64, strict=False)
        .alias(column)
    )


def _normalized_text(column: str, fallback: str) -> pl.Expr:
    value = pl.col(column).str.strip_chars()
    return (
        pl.when(value.is_null() | (value == ""))
        .then(pl.lit(fallback))
        .otherwise(value)
        .alias(column)
    )


def _source_frame() -> pl.LazyFrame:
    if not settings.raw_data_path.is_file():
        raise FileNotFoundError(f"Raw dataset not found: {settings.raw_data_path}")

    raw = (
        pl.scan_csv(
            settings.raw_data_path,
            infer_schema=False,
            missing_columns="raise",
            extra_columns="ignore",
        )
        .select(RAW_COLUMNS)
        .with_columns(
            pl.col("id")
            .str.strip_chars()
            .cast(pl.Int64, strict=False)
            .alias("loan_id"),
            pl.col("issue_d")
            .str.strip_chars()
            .str.strptime(pl.Date, format="%b-%Y", strict=False)
            .alias("issue_date"),
            pl.col("last_pymnt_d")
            .str.strip_chars()
            .str.strptime(pl.Date, format="%b-%Y", strict=False)
            .alias("last_pymnt_date"),
        )
        .filter(pl.col("loan_id").is_not_null() & pl.col("issue_date").is_not_null())
        .with_columns(
            _numeric_expr("annual_inc"),
            _numeric_expr("dti"),
            _numeric_expr("fico_range_low"),
            _numeric_expr("fico_range_high"),
            _numeric_expr("loan_amnt"),
            _numeric_expr("funded_amnt"),
            _numeric_expr("int_rate"),
            _numeric_expr("installment"),
            _numeric_expr("total_pymnt"),
            _numeric_expr("total_rec_prncp"),
            _numeric_expr("total_rec_int"),
            _numeric_expr("recoveries"),
            _numeric_expr("collection_recovery_fee"),
            _normalized_text("loan_status", "Unknown"),
            _normalized_text("grade", "Unknown"),
            _normalized_text("sub_grade", "Unknown"),
            _normalized_text("emp_length", "Unknown"),
            _normalized_text("home_ownership", "OTHER"),
            _normalized_text("verification_status", "Not Verified"),
            _normalized_text("addr_state", "UNK"),
            _normalized_text("purpose", "Unknown"),
            _normalized_text("application_type", "Unknown"),
            pl.col("term")
            .str.extract(r"(\d+)", 1)
            .cast(pl.Int16, strict=False)
            .alias("term_months"),
        )
        .with_columns(
            pl.col("grade").str.to_uppercase().alias("grade"),
            pl.col("sub_grade").str.to_uppercase().alias("sub_grade"),
        )
        .with_columns(
            pl.when(pl.col("grade").is_in(["A", "B"]))
            .then(pl.lit("Prime"))
            .when(pl.col("grade").is_in(["C", "D"]))
            .then(pl.lit("Near-Prime"))
            .otherwise(pl.lit("Subprime"))
            .alias("risk_segment"),
            pl.when(pl.col("annual_inc").is_null())
            .then(pl.lit("Unknown"))
            .when(pl.col("annual_inc") < 40000)
            .then(pl.lit("<40K"))
            .when(pl.col("annual_inc") < 75000)
            .then(pl.lit("40K-75K"))
            .when(pl.col("annual_inc") < 120000)
            .then(pl.lit("75K-120K"))
            .otherwise(pl.lit("120K+"))
            .alias("income_tier"),
            (
                (pl.col("last_pymnt_date").dt.year() - pl.col("issue_date").dt.year())
                * 12
                + pl.col("last_pymnt_date").dt.month()
                - pl.col("issue_date").dt.month()
            )
            .fill_null(0)
            .clip(lower_bound=0)
            .cast(pl.Int32)
            .alias("months_on_book"),
        )
        .with_columns(
            pl.col("loan_status")
            .is_in(sorted(DEFAULT_STATUSES))
            .alias("is_default")
        )
        .with_columns(
            ((pl.col("fico_range_low") + pl.col("fico_range_high")) / 2)
            .cast(pl.Int64, strict=False)
            .alias("fico_score"),
            pl.when(pl.col("int_rate") > 1)
            .then(pl.col("int_rate") / 100)
            .otherwise(pl.col("int_rate"))
            .alias("int_rate"),
            pl.col("total_pymnt").fill_null(0.0),
            pl.col("total_rec_prncp").fill_null(0.0),
            pl.col("total_rec_int").fill_null(0.0),
            pl.col("recoveries").fill_null(0.0),
            pl.col("collection_recovery_fee").fill_null(0.0),
        )
        .with_columns(
            pl.col("issue_date")
            .dt.strftime("%Y%m%d")
            .cast(pl.Int32)
            .alias("issue_date_key"),
            pl.when(pl.col("is_default"))
            .then(
                pl.max_horizontal(
                    pl.lit(0.0),
                    pl.col("funded_amnt")
                    - pl.col("total_rec_prncp")
                    - pl.col("recoveries"),
                )
            )
            .otherwise(0.0)
            .alias("principal_loss"),
            (
                pl.col("total_pymnt")
                - pl.col("funded_amnt")
                - pl.col("collection_recovery_fee")
            ).alias("net_profit"),
        )
        .select(
            "loan_id",
            "issue_date",
            "last_pymnt_date",
            "loan_status",
            "grade",
            "sub_grade",
            "term_months",
            "risk_segment",
            "emp_length",
            "home_ownership",
            "verification_status",
            "addr_state",
            "income_tier",
            "purpose",
            "application_type",
            "annual_inc",
            "dti",
            "fico_score",
            "loan_amnt",
            "funded_amnt",
            "int_rate",
            "installment",
            "total_pymnt",
            "total_rec_prncp",
            "total_rec_int",
            "recoveries",
            "collection_recovery_fee",
            "months_on_book",
            "is_default",
            "principal_loss",
            "net_profit",
        )
    )
    return raw


def _dimension_date_rows(frame: pl.DataFrame) -> list[dict[str, Any]]:
    dates = (
        pl.concat(
            [
                frame.select(pl.col("issue_date").alias("full_date")),
                frame.select(pl.col("last_pymnt_date").alias("full_date")),
            ]
        )
        .drop_nulls()
        .unique()
        .get_column("full_date")
        .to_list()
    )
    rows = [
        {
            "date_key": -1,
            "full_date": date(1900, 1, 1),
            "year": 1900,
            "quarter": 1,
            "year_quarter": "Unknown",
            "month": 1,
            "month_name": "Unknown",
            "year_month": "Unknown",
        }
    ]
    for full_date in sorted(dates):
        quarter = (full_date.month - 1) // 3 + 1
        rows.append(
            {
                "date_key": int(full_date.strftime("%Y%m%d")),
                "full_date": full_date,
                "year": full_date.year,
                "quarter": quarter,
                "year_quarter": f"{full_date.year}Q{quarter}",
                "month": full_date.month,
                "month_name": full_date.strftime("%B"),
                "year_month": full_date.strftime("%Y-%m"),
            }
        )
    return rows


def _validated_records(
    contract: type[ContractT], rows: list[dict[str, Any]], dimension: str
) -> list[dict[str, Any]]:
    validated = [contract.model_validate(row).model_dump() for row in rows]
    print(f"Contrato {dimension}: {len(validated):,}/{len(rows):,} combinaciones válidas (100%).")
    return validated


def _batched_executemany(
    cursor: psycopg.Cursor[Any],
    statement: str,
    rows: list[tuple[Any, ...]],
    batch_size: int = 10000,
) -> None:
    for offset in range(0, len(rows), batch_size):
        cursor.executemany(statement, rows[offset : offset + batch_size])


def _load_dimensions(
    connection: psycopg.Connection[Any], frame: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, int]]:
    date_rows = _validated_records(
        DimensionDateContract, _dimension_date_rows(frame), "dim_date"
    )
    risk_rows = _validated_records(
        RiskGradeContract,
        frame.select("grade", "sub_grade", "term_months", "risk_segment")
        .unique(maintain_order=True)
        .to_dicts(),
        "dim_risk_grade",
    )
    borrower_rows = _validated_records(
        BorrowerContract,
        frame.select(
            "emp_length",
            "home_ownership",
            "verification_status",
            "addr_state",
            "income_tier",
        )
        .unique(maintain_order=True)
        .to_dicts(),
        "dim_borrower",
    )
    purpose_rows = _validated_records(
        LoanPurposeContract,
        frame.select("purpose", "application_type")
        .unique(maintain_order=True)
        .to_dicts(),
        "dim_loan_purpose",
    )

    with connection.transaction():
        with connection.cursor() as cursor:
            _batched_executemany(
                cursor,
                """
                INSERT INTO dw.dim_date
                    (date_key, full_date, year, quarter, year_quarter, month, month_name, year_month)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date_key) DO NOTHING
                """,
                [
                    (
                        row["date_key"],
                        row["full_date"],
                        row["year"],
                        row["quarter"],
                        row["year_quarter"],
                        row["month"],
                        row["month_name"],
                        row["year_month"],
                    )
                    for row in date_rows
                ],
            )
            _batched_executemany(
                cursor,
                """
                INSERT INTO dw.dim_risk_grade (grade, sub_grade, term_months, risk_segment)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sub_grade, term_months) DO NOTHING
                """,
                [
                    (
                        row["grade"],
                        row["sub_grade"],
                        row["term_months"],
                        row["risk_segment"],
                    )
                    for row in risk_rows
                ],
            )
            _batched_executemany(
                cursor,
                """
                INSERT INTO dw.dim_borrower
                    (emp_length, home_ownership, verification_status, addr_state, income_tier)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT
                    (emp_length, home_ownership, verification_status, addr_state, income_tier)
                DO NOTHING
                """,
                [
                    (
                        row["emp_length"],
                        row["home_ownership"],
                        row["verification_status"],
                        row["addr_state"],
                        row["income_tier"],
                    )
                    for row in borrower_rows
                ],
            )
            _batched_executemany(
                cursor,
                """
                INSERT INTO dw.dim_loan_purpose (purpose, application_type)
                VALUES (%s, %s)
                ON CONFLICT (purpose, application_type) DO NOTHING
                """,
                [
                    (row["purpose"], row["application_type"])
                    for row in purpose_rows
                ],
            )

            cursor.execute(
                "SELECT date_key, full_date FROM dw.dim_date"
            )
            date_lookup = pl.DataFrame(
                cursor.fetchall(),
                schema={"date_key": pl.Int32, "full_date": pl.Date},
                orient="row",
            )
            cursor.execute(
                """
                SELECT risk_grade_key, grade, sub_grade, term_months, risk_segment
                FROM dw.dim_risk_grade
                """
            )
            risk_lookup = pl.DataFrame(
                cursor.fetchall(),
                schema={
                    "risk_grade_key": pl.Int32,
                    "grade": pl.String,
                    "sub_grade": pl.String,
                    "term_months": pl.Int16,
                    "risk_segment": pl.String,
                },
                orient="row",
            )
            cursor.execute(
                """
                SELECT borrower_key, emp_length, home_ownership, verification_status, addr_state, income_tier
                FROM dw.dim_borrower
                """
            )
            borrower_lookup = pl.DataFrame(
                cursor.fetchall(),
                schema={
                    "borrower_key": pl.Int32,
                    "emp_length": pl.String,
                    "home_ownership": pl.String,
                    "verification_status": pl.String,
                    "addr_state": pl.String,
                    "income_tier": pl.String,
                },
                orient="row",
            )
            cursor.execute(
                """
                SELECT purpose_key, purpose, application_type
                FROM dw.dim_loan_purpose
                """
            )
            purpose_lookup = pl.DataFrame(
                cursor.fetchall(),
                schema={
                    "purpose_key": pl.Int32,
                    "purpose": pl.String,
                    "application_type": pl.String,
                },
                orient="row",
            )

    dimension_counts = {
        "dim_date": date_lookup.height,
        "dim_risk_grade": risk_lookup.height,
        "dim_borrower": borrower_lookup.height,
        "dim_loan_purpose": purpose_lookup.height,
    }
    return date_lookup, risk_lookup, borrower_lookup, purpose_lookup, dimension_counts


def _build_fact_frame(
    frame: pl.DataFrame,
    lookups: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> pl.DataFrame:
    date_lookup, risk_lookup, borrower_lookup, purpose_lookup = lookups
    fact = (
        frame.join(
            date_lookup.select(
                pl.col("full_date").alias("issue_date"),
                pl.col("date_key").alias("issue_date_key"),
            ),
            on="issue_date",
            how="left",
        )
        .join(
            date_lookup.select(
                pl.col("full_date").alias("last_pymnt_date"),
                pl.col("date_key").alias("last_pymnt_date_key"),
            ),
            on="last_pymnt_date",
            how="left",
        )
        .join(
            risk_lookup,
            on=["grade", "sub_grade", "term_months", "risk_segment"],
            how="left",
        )
        .join(
            borrower_lookup,
            on=[
                "emp_length",
                "home_ownership",
                "verification_status",
                "addr_state",
                "income_tier",
            ],
            how="left",
        )
        .join(purpose_lookup, on=["purpose", "application_type"], how="left")
        .with_columns(pl.col("last_pymnt_date_key").fill_null(-1))
    )
    missing_key_rows = fact.filter(
        pl.any_horizontal(
            [
                pl.col("issue_date_key").is_null(),
                pl.col("risk_grade_key").is_null(),
                pl.col("borrower_key").is_null(),
                pl.col("purpose_key").is_null(),
            ]
        )
    ).height
    if missing_key_rows:
        raise ValueError(f"{missing_key_rows:,} rows failed dimensional key resolution.")

    fact = fact.select(COPY_COLUMNS)
    if fact.get_column("loan_id").n_unique() != fact.height:
        raise ValueError("Duplicate loan_id values violate the fact table grain.")
    return fact


def _audit_fact_contracts(frame: pl.DataFrame, sample_size: int = 5000) -> int:
    target_size = min(sample_size, frame.height)
    strata = [
        (is_default, frame.filter(pl.col("is_default") == is_default))
        for is_default in (False, True)
    ]
    allocations = {
        is_default: min(
            group.height,
            floor(target_size * group.height / frame.height),
        )
        for is_default, group in strata
    }
    remaining = target_size - sum(allocations.values())
    while remaining:
        for is_default, group in sorted(
            strata, key=lambda item: item[1].height - allocations[item[0]], reverse=True
        ):
            if allocations[is_default] < group.height:
                allocations[is_default] += 1
                remaining -= 1
                if remaining == 0:
                    break

    sampled = [
        group.sample(n=allocations[is_default], seed=2026)
        for is_default, group in strata
        if allocations[is_default]
    ]
    random_sample = pl.concat(sampled) if sampled else frame.head(0)

    extreme_rows: list[pl.DataFrame] = []
    for column in FACT_NUMERIC_COLUMNS:
        minimum, maximum = frame.select(
            pl.col(column).min().alias("minimum"),
            pl.col(column).max().alias("maximum"),
        ).row(0)
        for value in {minimum, maximum}:
            if value is not None:
                extreme_rows.append(frame.filter(pl.col(column) == value).head(1))

    extremes = pl.concat(extreme_rows) if extreme_rows else frame.head(0)
    extremes_only = extremes.join(
        random_sample.select("loan_id"), on="loan_id", how="anti"
    )
    audit_frame = pl.concat([random_sample, extremes_only]).unique(subset=["loan_id"])
    for record in audit_frame.to_dicts():
        FactLoanContract.model_validate(record)

    print(
        "Auditoría fact_loans: "
        f"{random_sample.height:,} muestras estratificadas por default + "
        f"{extremes_only.height:,} extremos únicos adicionales; "
        f"{audit_frame.height:,} contratos válidos."
    )
    return audit_frame.height


def _copy_fact(connection: psycopg.Connection[Any], frame: pl.DataFrame) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE dw.fact_loans")
            with cursor.copy(COPY_SQL) as copy:
                for offset in range(0, frame.height, 100000):
                    buffer = io.BytesIO()
                    frame.slice(offset, 100000).write_csv(
                        buffer,
                        include_header=False,
                        null_value="",
                    )
                    copy.write(buffer.getvalue())


def _print_load_summary(
    connection: psycopg.Connection[Any],
    dimension_counts: dict[str, int],
    elapsed: float,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE is_default),
                   COALESCE(SUM(funded_amnt), 0)
            FROM dw.fact_loans
            """
        )
        fact_count, default_count, funded_total = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dw.fact_loans AS f
            LEFT JOIN dw.dim_date AS issue_date
                ON issue_date.date_key = f.issue_date_key
            LEFT JOIN dw.dim_date AS payment_date
                ON payment_date.date_key = f.last_pymnt_date_key
            LEFT JOIN dw.dim_risk_grade AS risk
                ON risk.risk_grade_key = f.risk_grade_key
            LEFT JOIN dw.dim_borrower AS borrower
                ON borrower.borrower_key = f.borrower_key
            LEFT JOIN dw.dim_loan_purpose AS purpose
                ON purpose.purpose_key = f.purpose_key
            WHERE issue_date.date_key IS NULL
               OR payment_date.date_key IS NULL
               OR risk.risk_grade_key IS NULL
               OR borrower.borrower_key IS NULL
               OR purpose.purpose_key IS NULL
            """
        )
        orphan_count = cursor.fetchone()[0]

    if orphan_count:
        raise RuntimeError(f"Referential integrity audit found {orphan_count:,} orphan fact rows.")
    table_counts = {**dimension_counts, "fact_loans": fact_count}
    if any(count == 0 for count in table_counts.values()):
        raise RuntimeError(f"One or more warehouse tables are empty: {table_counts}")

    default_rate = default_count / fact_count if fact_count else 0.0
    print("\nResumen ejecutivo")
    print(f"Tiempo total: {elapsed:,.1f} s")
    for table, count in table_counts.items():
        print(f"Filas {table}: {count:,}")
    print(f"Tasa global de default: {default_rate:.2%} ({default_count:,}/{fact_count:,})")
    print(f"Volumen total originado: ${funded_total:,.2f}")
    print(f"Filas huérfanas: {orphan_count:,}")


def run_etl() -> None:
    started_at = time.perf_counter()
    print(f"Leyendo columnas requeridas desde {settings.raw_data_path}...")
    raw_frame = _source_frame().collect()
    if raw_frame.is_empty():
        raise ValueError("No valid loan rows remain after filtering IDs and issue dates.")
    print(f"Préstamos válidos tras filtrar filas basura: {raw_frame.height:,}")

    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    ) as connection:
        print("Validando dimensiones e insertando combinaciones nuevas...")
        date_lookup, risk_lookup, borrower_lookup, purpose_lookup, dimension_counts = (
            _load_dimensions(connection, raw_frame)
        )
        fact_frame = _build_fact_frame(
            raw_frame,
            (date_lookup, risk_lookup, borrower_lookup, purpose_lookup),
        )
        _audit_fact_contracts(fact_frame)

        print(f"Cargando {fact_frame.height:,} filas con PostgreSQL COPY...")
        _copy_fact(connection, fact_frame)
        _print_load_summary(connection, dimension_counts, time.perf_counter() - started_at)


if __name__ == "__main__":
    run_etl()
