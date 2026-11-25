"""Statistical inference for mature loans in the credit risk warehouse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from scipy import stats
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

from src.config import settings

MATURED_STATUSES = (
    "Fully Paid",
    "Charged Off",
    "Default",
    "Does not meet the credit policy. Status:Fully Paid",
    "Does not meet the credit policy. Status:Charged Off",
)
RISK_SEGMENTS = ("Prime", "Near-Prime", "Subprime")
GRADES = tuple("ABCDEFG")
COMPARISON_FEATURES = ("fico_score", "dti", "int_rate")
GRADE_FEATURES = ("int_rate", "dti")

def _numeric_values(
    rows: Sequence[Mapping[str, Any]], field: str, predicate: Any
) -> np.ndarray:
    return np.asarray(
        [
            float(row[field])
            for row in rows
            if predicate(row) and row[field] is not None
        ],
        dtype=np.float64,
    )


def _wilson_interval(successes: int, observations: int) -> tuple[float, float]:
    if observations <= 0:
        raise ValueError("Wilson interval requires at least one observation")
    z_score = float(stats.norm.ppf(0.975))
    proportion = successes / observations
    denominator = 1 + z_score**2 / observations
    center = (
        proportion + z_score**2 / (2 * observations)
    ) / denominator
    margin = (
        z_score
        * np.sqrt(
            proportion * (1 - proportion) / observations
            + z_score**2 / (4 * observations**2)
        )
        / denominator
    )
    return float(center - margin), float(center + margin)


def _test_result(result: Any, sample_sizes: Sequence[int]) -> dict[str, Any]:
    return {
        "statistic": float(result.statistic),
        "p_value": float(result.pvalue),
        "sample_sizes": list(sample_sizes),
    }


def analyze_matured_loans(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate hypothesis tests and Wilson intervals from matured loan rows."""
    if not rows:
        raise ValueError("No matured loan rows were returned by the warehouse")

    contingency = np.zeros((len(RISK_SEGMENTS), 2), dtype=np.int64)
    for row in rows:
        contingency[RISK_SEGMENTS.index(row["risk_segment"]), int(row["is_default"])] += 1
    chi_square, chi_square_p, degrees_freedom, _ = stats.chi2_contingency(
        contingency
    )
    total_observations = int(contingency.sum())
    cramers_v = float(
        np.sqrt(
            chi_square
            / (total_observations * min(contingency.shape[0] - 1, contingency.shape[1] - 1))
        )
    )

    outcome_comparisons: dict[str, Any] = {}
    for feature in COMPARISON_FEATURES:
        default_values = _numeric_values(
            rows, feature, lambda row: bool(row["is_default"])
        )
        paid_values = _numeric_values(
            rows,
            feature,
            lambda row: row["loan_status"]
            in {
                "Fully Paid",
                "Does not meet the credit policy. Status:Fully Paid",
            },
        )
        if min(default_values.size, paid_values.size) == 0:
            raise ValueError(f"Insufficient non-null {feature} observations by outcome")
        outcome_comparisons[feature] = {
            "default_n": int(default_values.size),
            "fully_paid_n": int(paid_values.size),
            "welch_t": _test_result(
                stats.ttest_ind(default_values, paid_values, equal_var=False),
                (default_values.size, paid_values.size),
            ),
            "mann_whitney_u": _test_result(
                stats.mannwhitneyu(
                    default_values, paid_values, alternative="two-sided"
                ),
                (default_values.size, paid_values.size),
            ),
        }

    grade_tests: dict[str, Any] = {}
    for feature in GRADE_FEATURES:
        groups = [
            _numeric_values(
                rows, feature, lambda row, grade=grade: row["grade"] == grade
            )
            for grade in GRADES
        ]
        if any(group.size == 0 for group in groups):
            raise ValueError(f"Insufficient {feature} observations across grades A-G")
        grade_tests[feature] = {
            "anova": _test_result(
                stats.f_oneway(*groups), tuple(group.size for group in groups)
            ),
            "kruskal_wallis": _test_result(
                stats.kruskal(*groups), tuple(group.size for group in groups)
            ),
        }

    wilson_intervals: dict[str, Any] = {}
    for segment in RISK_SEGMENTS:
        segment_rows = [row for row in rows if row["risk_segment"] == segment]
        successes = sum(bool(row["is_default"]) for row in segment_rows)
        observations = len(segment_rows)
        lower, upper = _wilson_interval(successes, observations)
        wilson_intervals[segment] = {
            "defaults": successes,
            "matured_loans": observations,
            "matured_default_rate": successes / observations,
            "confidence_level": 0.95,
            "ci_low": lower,
            "ci_high": upper,
        }

    return {
        "matured_loan_count": total_observations,
        "chi_square_independence": {
            "statistic": float(chi_square),
            "p_value": float(chi_square_p),
            "degrees_freedom": int(degrees_freedom),
            "cramers_v": cramers_v,
            "contingency_table": contingency.tolist(),
        },
        "outcome_comparisons": outcome_comparisons,
        "grade_tests": grade_tests,
        "wilson_intervals": wilson_intervals,
    }


def run_analysis(engine: Engine | None = None) -> dict[str, Any]:
    """Query the configured warehouse and return the statistical report."""
    owns_engine = engine is None
    active_engine = engine or create_engine(settings.sqlalchemy_database_uri)
    try:
        with active_engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT
                            rg.risk_segment,
                            rg.grade,
                            f.loan_status,
                            f.is_default,
                            f.fico_score,
                            f.dti,
                            f.int_rate
                        FROM dw.fact_loans AS f
                        JOIN dw.dim_risk_grade AS rg
                            ON rg.risk_grade_key = f.risk_grade_key
                        WHERE f.loan_status IN :matured_statuses
                        """
                    ).bindparams(bindparam("matured_statuses", expanding=True)),
                    {"matured_statuses": MATURED_STATUSES},
                )
                .mappings()
                .all()
            )
        return analyze_matured_loans(rows)
    finally:
        if owns_engine:
            active_engine.dispose()


def _format_p_value(value: float) -> str:
    return "< 0.001" if value < 0.001 else f"= {value:.3f}"


def print_report(report: Mapping[str, Any]) -> None:
    """Print an executive-oriented summary of the statistical results."""
    print("CREDIT RISK STATISTICAL REPORT")
    print(f"Matured loans analyzed: {report['matured_loan_count']:,}")

    chi = report["chi_square_independence"]
    print("\n1. Default independence by risk segment")
    print(
        f"   Chi-square({chi['degrees_freedom']}) = {chi['statistic']:.2f}, "
        f"p {_format_p_value(chi['p_value'])}; "
        f"Cramer's V = {chi['cramers_v']:.3f}"
    )

    print("\n2. Default versus fully paid")
    for feature, tests in report["outcome_comparisons"].items():
        welch = tests["welch_t"]
        mann_whitney = tests["mann_whitney_u"]
        print(
            f"   {feature}: Welch t = {welch['statistic']:.2f}, "
            f"p {_format_p_value(welch['p_value'])}; "
            f"Mann-Whitney U = {mann_whitney['statistic']:.2f}, "
            f"p {_format_p_value(mann_whitney['p_value'])}"
        )

    print("\n3. Interest rate and DTI across grades A-G")
    for feature, tests in report["grade_tests"].items():
        anova = tests["anova"]
        kruskal = tests["kruskal_wallis"]
        print(
            f"   {feature}: one-way ANOVA F = {anova['statistic']:.2f}, "
            f"p {_format_p_value(anova['p_value'])}; "
            f"Kruskal-Wallis H = {kruskal['statistic']:.2f}, "
            f"p {_format_p_value(kruskal['p_value'])}"
        )

    print("\n4. Matured default rate (95% Wilson score interval)")
    for segment, interval in report["wilson_intervals"].items():
        print(
            f"   {segment}: {interval['matured_default_rate']:.2%} "
            f"[{interval['ci_low']:.2%}, {interval['ci_high']:.2%}] "
            f"(n={interval['matured_loans']:,})"
        )


def main() -> None:
    print_report(run_analysis())


if __name__ == "__main__":
    main()
