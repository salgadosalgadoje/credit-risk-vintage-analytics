SET search_path TO dw, public;

CREATE OR REPLACE VIEW dw.vw_vintage_cohort_matrix AS
WITH loan_cohorts AS (
    SELECT
        d.year,
        d.year_quarter,
        rg.risk_segment,
        rg.term_months,
        f.loan_id,
        f.months_on_book,
        f.is_default,
        f.principal_loss,
        SUM(1) OVER (
            PARTITION BY d.year_quarter, rg.risk_segment, rg.term_months
        ) AS cohort_loans,
        SUM(f.funded_amnt) OVER (
            PARTITION BY d.year_quarter, rg.risk_segment, rg.term_months
        ) AS cohort_funded_amnt
    FROM dw.fact_loans AS f
    JOIN dw.dim_date AS d
        ON d.date_key = f.issue_date_key
    JOIN dw.dim_risk_grade AS rg
        ON rg.risk_grade_key = f.risk_grade_key
),
cohorts AS (
    SELECT DISTINCT
        year,
        year_quarter,
        risk_segment,
        term_months,
        cohort_loans,
        cohort_funded_amnt
    FROM loan_cohorts
    WHERE cohort_loans >= 1000
),
mob_spine AS (
    SELECT
        c.year,
        c.year_quarter,
        c.risk_segment,
        c.term_months,
        c.cohort_loans,
        c.cohort_funded_amnt,
        mob.months_on_book
    FROM cohorts AS c
    CROSS JOIN generate_series(0, 60) AS mob(months_on_book)
),
marginal_events AS (
    SELECT
        lc.year_quarter,
        lc.risk_segment,
        lc.term_months,
        lc.months_on_book,
        COUNT(*) FILTER (WHERE lc.is_default) AS marginal_defaults,
        SUM(lc.principal_loss) AS marginal_principal_loss
    FROM loan_cohorts AS lc
    JOIN cohorts AS c
        ON c.year_quarter = lc.year_quarter
        AND c.risk_segment = lc.risk_segment
        AND c.term_months = lc.term_months
    WHERE lc.months_on_book BETWEEN 0 AND 60
    GROUP BY
        lc.year_quarter,
        lc.risk_segment,
        lc.term_months,
        lc.months_on_book
),
cohort_mob AS (
    SELECT
        s.year,
        s.year_quarter,
        s.risk_segment,
        s.term_months,
        s.cohort_loans,
        s.cohort_funded_amnt,
        s.months_on_book,
        COALESCE(m.marginal_defaults, 0) AS marginal_defaults,
        COALESCE(m.marginal_principal_loss, 0) AS marginal_principal_loss
    FROM mob_spine AS s
    LEFT JOIN marginal_events AS m
        ON m.year_quarter = s.year_quarter
        AND m.risk_segment = s.risk_segment
        AND m.term_months = s.term_months
        AND m.months_on_book = s.months_on_book
),
cumulative_metrics AS (
    SELECT
        *,
        SUM(marginal_defaults) OVER (
            PARTITION BY year_quarter, risk_segment, term_months
            ORDER BY months_on_book
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_defaults,
        SUM(marginal_principal_loss) OVER (
            PARTITION BY year_quarter, risk_segment, term_months
            ORDER BY months_on_book
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_principal_loss
    FROM cohort_mob
)
SELECT
    year,
    year_quarter,
    risk_segment,
    term_months,
    cohort_loans,
    cohort_funded_amnt,
    months_on_book,
    marginal_defaults,
    marginal_principal_loss,
    cumulative_defaults,
    cumulative_defaults * 100.0 / NULLIF(cohort_loans, 0)
        AS cumulative_default_rate_pct,
    cumulative_principal_loss,
    cumulative_principal_loss * 100.0 / NULLIF(cohort_funded_amnt, 0)
        AS cumulative_loss_rate_pct
FROM cumulative_metrics;
