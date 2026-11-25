SET search_path TO dw, public;

CREATE OR REPLACE VIEW dw.vw_portfolio_kpis_by_grade AS
WITH loan_metrics AS (
    SELECT
        rg.risk_segment,
        rg.grade,
        rg.sub_grade,
        rg.term_months,
        f.loan_id,
        f.loan_status,
        f.is_default,
        f.funded_amnt,
        f.loan_amnt,
        f.int_rate,
        f.fico_score,
        f.dti,
        f.principal_loss,
        f.net_profit,
        f.loan_status IN (
            'Fully Paid',
            'Charged Off',
            'Default',
            'Does not meet the credit policy. Status:Fully Paid',
            'Does not meet the credit policy. Status:Charged Off'
        ) AS is_matured
    FROM dw.fact_loans AS f
    JOIN dw.dim_risk_grade AS rg
        ON rg.risk_grade_key = f.risk_grade_key
)
SELECT
    risk_segment,
    grade,
    sub_grade,
    term_months,
    COUNT(*) AS total_loans,
    COUNT(*) FILTER (WHERE is_matured) AS matured_loans,
    SUM(funded_amnt) AS total_funded_amnt,
    SUM(CASE WHEN is_matured THEN funded_amnt ELSE 0 END) AS matured_funded_amnt,
    SUM(CASE WHEN is_default THEN funded_amnt ELSE 0 END) AS default_funded_amnt,
    AVG(loan_amnt) AS avg_loan_amnt,
    AVG(int_rate) AS avg_int_rate,
    AVG(fico_score) AS avg_fico_score,
    AVG(dti) AS avg_dti,
    COUNT(*) FILTER (WHERE is_default) AS default_count,
    COUNT(*) FILTER (WHERE is_default)::NUMERIC / NULLIF(COUNT(*), 0)
        AS global_default_rate,
    COUNT(*) FILTER (WHERE is_default)::NUMERIC
        / NULLIF(COUNT(*) FILTER (WHERE is_matured), 0) AS matured_default_rate,
    SUM(principal_loss) AS total_principal_loss,
    SUM(principal_loss)
        / NULLIF(SUM(CASE WHEN is_default THEN funded_amnt ELSE 0 END), 0) AS lgd_pct,
    SUM(CASE WHEN is_matured THEN net_profit ELSE 0 END) AS matured_net_profit,
    SUM(CASE WHEN is_matured THEN net_profit ELSE 0 END)
        / NULLIF(SUM(CASE WHEN is_matured THEN funded_amnt ELSE 0 END), 0)
        AS matured_roi_pct
FROM loan_metrics
GROUP BY risk_segment, grade, sub_grade, term_months;

CREATE OR REPLACE VIEW dw.vw_origination_trends_monthly AS
WITH monthly_metrics AS (
    SELECT
        d.year,
        d.quarter,
        d.year_quarter,
        d.year_month,
        rg.risk_segment,
        COUNT(*) AS total_loans,
        SUM(f.funded_amnt) AS funded_amnt,
        AVG(f.int_rate) AS avg_int_rate,
        AVG(f.fico_score) AS avg_fico_score,
        COUNT(*) FILTER (WHERE f.is_default)::NUMERIC / NULLIF(COUNT(*), 0)
            AS default_rate
    FROM dw.fact_loans AS f
    JOIN dw.dim_date AS d
        ON d.date_key = f.issue_date_key
    JOIN dw.dim_risk_grade AS rg
        ON rg.risk_grade_key = f.risk_grade_key
    GROUP BY d.year, d.quarter, d.year_quarter, d.year_month, rg.risk_segment
),
monthly_lags AS (
    SELECT
        *,
        LAG(total_loans) OVER (
            PARTITION BY risk_segment
            ORDER BY year_month
        ) AS prior_month_loans,
        LAG(funded_amnt) OVER (
            PARTITION BY risk_segment
            ORDER BY year_month
        ) AS prior_month_funded_amnt
    FROM monthly_metrics
)
SELECT
    year,
    quarter,
    year_quarter,
    year_month,
    risk_segment,
    total_loans,
    funded_amnt,
    avg_int_rate,
    avg_fico_score,
    default_rate,
    (total_loans - prior_month_loans)::NUMERIC
        / NULLIF(prior_month_loans, 0) * 100 AS loan_count_mom_growth_pct,
    (funded_amnt - prior_month_funded_amnt)
        / NULLIF(prior_month_funded_amnt, 0) * 100 AS funded_amnt_mom_growth_pct
FROM monthly_lags;

CREATE OR REPLACE VIEW dw.vw_borrower_risk_profile AS
SELECT
    b.income_tier,
    b.home_ownership,
    b.verification_status,
    lp.purpose,
    lp.application_type,
    COUNT(*) AS total_loans,
    SUM(f.funded_amnt) AS total_funded_amnt,
    AVG(f.dti) AS avg_dti,
    AVG(f.fico_score) AS avg_fico_score,
    COUNT(*) FILTER (WHERE f.is_default)::NUMERIC / NULLIF(COUNT(*), 0)
        AS default_rate,
    SUM(f.principal_loss)
        / NULLIF(SUM(CASE WHEN f.is_default THEN f.funded_amnt ELSE 0 END), 0)
        AS lgd_pct
FROM dw.fact_loans AS f
JOIN dw.dim_borrower AS b
    ON b.borrower_key = f.borrower_key
JOIN dw.dim_loan_purpose AS lp
    ON lp.purpose_key = f.purpose_key
GROUP BY
    b.income_tier,
    b.home_ownership,
    b.verification_status,
    lp.purpose,
    lp.application_type;
