"""Integration tests for the deployed PostgreSQL 16 credit risk warehouse."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.config import settings
from src.risk_stats import run_analysis


@pytest.fixture(scope="session")
def dw_engine() -> Iterator[Engine]:
    engine = create_engine(settings.sqlalchemy_database_uri)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def statistical_report(dw_engine: Engine) -> dict[str, Any]:
    return run_analysis(dw_engine)


def test_warehouse_has_expected_tables_and_views(dw_engine: Engine) -> None:
    with dw_engine.connect() as connection:
        objects = connection.execute(
            text(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'dw'
                """
            )
        ).all()

    tables = {
        name for name, kind in objects if kind == "BASE TABLE"
    }
    views = {name for name, kind in objects if kind == "VIEW"}
    assert tables == {
        "dim_date",
        "dim_risk_grade",
        "dim_borrower",
        "dim_loan_purpose",
        "fact_loans",
    }
    assert views == {
        "vw_portfolio_kpis_by_grade",
        "vw_origination_trends_monthly",
        "vw_borrower_risk_profile",
        "vw_vintage_cohort_matrix",
    }


def test_fact_loan_volume_keys_rates_and_amounts(dw_engine: Engine) -> None:
    with dw_engine.connect() as connection:
        count, invalid_foreign_keys, invalid_values = connection.execute(
            text(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE issue_date_key IS NULL
                           OR last_pymnt_date_key IS NULL
                           OR risk_grade_key IS NULL
                           OR borrower_key IS NULL
                           OR purpose_key IS NULL
                    ),
                    COUNT(*) FILTER (
                        WHERE int_rate < 0 OR int_rate > 1
                           OR loan_amnt <= 0 OR funded_amnt <= 0
                           OR installment < 0 OR total_pymnt < 0
                           OR total_rec_prncp < 0 OR total_rec_int < 0
                           OR recoveries < 0 OR collection_recovery_fee < 0
                           OR principal_loss < 0
                           OR (fico_score IS NOT NULL
                               AND (fico_score < 300 OR fico_score > 850))
                    )
                FROM dw.fact_loans
                """
            )
        ).one()

    assert count > 2_200_000
    assert invalid_foreign_keys == 0
    assert invalid_values == 0


def test_default_rate_and_lgd_increase_by_risk_segment(
    dw_engine: Engine,
) -> None:
    with dw_engine.connect() as connection:
        results = connection.execute(
            text(
                """
                SELECT
                    risk_segment,
                    SUM(default_count)::NUMERIC
                        / NULLIF(SUM(matured_loans), 0) AS matured_default_rate,
                    SUM(total_principal_loss)
                        / NULLIF(SUM(default_funded_amnt), 0) AS lgd_pct
                FROM dw.vw_portfolio_kpis_by_grade
                GROUP BY risk_segment
                """
            )
        ).mappings().all()

    metrics = {row["risk_segment"]: row for row in results}
    assert set(metrics) == {"Prime", "Near-Prime", "Subprime"}
    for metric in ("matured_default_rate", "lgd_pct"):
        assert (
            metrics["Prime"][metric]
            < metrics["Near-Prime"][metric]
            < metrics["Subprime"][metric]
        )


def test_vintage_cumulative_default_rate_is_monotonic(
    dw_engine: Engine,
) -> None:
    with dw_engine.connect() as connection:
        decreases = connection.execute(
            text(
                """
                WITH vintage_changes AS (
                    SELECT
                        year_quarter,
                        risk_segment,
                        term_months,
                        months_on_book,
                        cumulative_default_rate_pct,
                        LAG(cumulative_default_rate_pct) OVER (
                            PARTITION BY year_quarter, risk_segment, term_months
                            ORDER BY months_on_book
                        ) AS prior_rate
                    FROM dw.vw_vintage_cohort_matrix
                )
                SELECT COUNT(*)
                FROM vintage_changes
                WHERE cumulative_default_rate_pct < prior_rate
                """
            )
        ).scalar_one()

    assert decreases == 0


def _all_p_values(report: dict[str, Any]) -> list[float]:
    values = [report["chi_square_independence"]["p_value"]]
    for tests in report["outcome_comparisons"].values():
        values.extend(
            [
                tests["welch_t"]["p_value"],
                tests["mann_whitney_u"]["p_value"],
            ]
        )
    for tests in report["grade_tests"].values():
        values.extend(
            [
                tests["anova"]["p_value"],
                tests["kruskal_wallis"]["p_value"],
            ]
        )
    return values


def test_statistical_tests_are_significant_and_wilson_intervals_valid(
    statistical_report: dict[str, Any],
) -> None:
    p_values = _all_p_values(statistical_report)
    assert p_values
    assert all(0 <= p_value < 0.05 for p_value in p_values)

    intervals = statistical_report["wilson_intervals"]
    assert set(intervals) == {"Prime", "Near-Prime", "Subprime"}
    for interval in intervals.values():
        assert 0 <= interval["ci_low"] <= interval["ci_high"] <= 1
        assert interval["ci_low"] <= interval["matured_default_rate"] <= interval[
            "ci_high"
        ]
