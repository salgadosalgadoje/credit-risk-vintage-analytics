# Credit Risk Data Dictionary

This dictionary describes the PostgreSQL 16 star schema in `dw`, the grain and keys of each object, and the business rules applied by the ETL and analytical views. Monetary values are denominated in US dollars. Rates in `fact_loans.int_rate` are stored as decimal fractions; columns explicitly named `*_pct` in the vintage view are on a 0–100 scale.

## Conformed dimensions

### `dw.dim_date`

**Grain:** One calendar date. `date_key` is the `YYYYMMDD` integer surrogate/business key; `-1` is the unknown-date member.

| Column | PostgreSQL type | Key / rule | Description |
| --- | --- | --- | --- |
| `date_key` | `integer` | PK | Date key in `YYYYMMDD` format, except the `-1` unknown member. |
| `full_date` | `date` | Not null | Calendar date represented by the row. |
| `year` | `integer` | Not null | Calendar year. |
| `quarter` | `integer` | Not null; 1–4 | Calendar quarter. |
| `year_quarter` | `varchar(7)` | Not null | Year-quarter label, such as `2018Q4`; `Unknown` for the unknown member. |
| `month` | `integer` | Not null; 1–12 | Calendar month number. |
| `month_name` | `varchar(15)` | Not null | Calendar month label; `Unknown` for the unknown member. |
| `year_month` | `varchar(7)` | Not null | Year-month label, such as `2018-12`; `Unknown` for the unknown member. |

### `dw.dim_risk_grade`

**Grain:** One contractual sub-grade and term combination. The ETL maps grades A–B to Prime, C–D to Near-Prime, and E–G to Subprime.

| Column | PostgreSQL type | Key / rule | Description |
| --- | --- | --- | --- |
| `risk_grade_key` | `integer` (`serial`) | PK | Warehouse surrogate key. |
| `grade` | `char(1)` | Not null; A–G | LendingClub letter grade. |
| `sub_grade` | `varchar(3)` | Not null; unique with `term_months`; A1–G5 and must start with `grade` | More granular letter-and-number credit grade. |
| `term_months` | `integer` | Not null; 36 or 60 | Contractual loan term in months. |
| `risk_segment` | `varchar(20)` | Not null; derived from `grade` | `Prime` for A/B, `Near-Prime` for C/D, and `Subprime` for E/F/G. |

### `dw.dim_borrower`

**Grain:** One distinct combination of the borrower attributes retained for analysis.

| Column | PostgreSQL type | Key / rule | Description |
| --- | --- | --- | --- |
| `borrower_key` | `integer` (`serial`) | PK | Warehouse surrogate key. |
| `emp_length` | `varchar(25)` | Not null | Employment-length category; missing values are normalized to `Unknown`. |
| `home_ownership` | `varchar(20)` | Not null | Home-ownership category. |
| `verification_status` | `varchar(30)` | Not null | Income verification category. |
| `addr_state` | `varchar(10)` | Not null | Borrower state/territory code; unknown values are normalized to `UNK`. |
| `income_tier` | `varchar(25)` | Not null; constrained to `<40K`, `40K-75K`, `75K-120K`, `120K+`, `Unknown` | Annual-income band derived from `annual_inc`. |

`(emp_length, home_ownership, verification_status, addr_state, income_tier)` is unique.

### `dw.dim_loan_purpose`

**Grain:** One distinct loan-purpose and application-type combination.

| Column | PostgreSQL type | Key / rule | Description |
| --- | --- | --- | --- |
| `purpose_key` | `integer` (`serial`) | PK | Warehouse surrogate key. |
| `purpose` | `varchar(50)` | Not null | Borrower-stated loan purpose. |
| `application_type` | `varchar(25)` | Not null | Individual/joint application category. |

`(purpose, application_type)` is unique.

## Loan performance fact

### `dw.fact_loans`

**Grain:** One originated loan (`loan_id`). Dimension keys are mandatory foreign keys; `last_pymnt_date_key = -1` represents an unknown last-payment date.

| Column | PostgreSQL type | Key / rule | Description |
| --- | --- | --- | --- |
| `loan_id` | `bigint` | PK | Source loan identifier. |
| `issue_date_key` | `integer` | FK → `dim_date.date_key`, not null | Origination date. |
| `last_pymnt_date_key` | `integer` | FK → `dim_date.date_key`, not null; defaults to `-1` | Last-payment date or unknown member. |
| `risk_grade_key` | `integer` | FK → `dim_risk_grade.risk_grade_key`, not null | Contractual grade, sub-grade, term, and segment. |
| `borrower_key` | `integer` | FK → `dim_borrower.borrower_key`, not null | Borrower profile. |
| `purpose_key` | `integer` | FK → `dim_loan_purpose.purpose_key`, not null | Purpose and application type. |
| `loan_status` | `varchar(60)` | Not null | Source performance/status category. |
| `is_default` | `boolean` | Not null | True for `Charged Off`, `Default`, and `Does not meet the credit policy. Status:Charged Off`. |
| `months_on_book` | `integer` | Not null; ≥ 0 | Whole months between issue date and last payment date; null/reversed intervals normalize to zero. |
| `annual_inc` | `numeric(14,2)` | Nullable; ETL contract ≥ 0 | Reported annual income in dollars. |
| `dti` | `numeric(8,2)` | Nullable | Debt-to-income ratio as supplied by the source. |
| `fico_score` | `integer` | Nullable; ETL contract 300–850 | Midpoint of source low and high FICO range. |
| `loan_amnt` | `numeric(12,2)` | Not null; ETL contract > 0 | Requested loan amount in dollars. |
| `funded_amnt` | `numeric(12,2)` | Not null; ETL contract > 0 | Funded principal in dollars. |
| `int_rate` | `numeric(6,4)` | Not null; ETL contract 0–1 | Annual interest rate as a decimal fraction (for example, 12% is `0.12`). |
| `installment` | `numeric(10,2)` | Not null; ETL contract ≥ 0 | Scheduled monthly installment in dollars. |
| `total_pymnt` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Total payments received in dollars. |
| `total_rec_prncp` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Principal recovered through payments in dollars. |
| `total_rec_int` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Interest received in dollars. |
| `recoveries` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Recoveries after default in dollars. |
| `collection_recovery_fee` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Collection recovery fees in dollars. |
| `principal_loss` | `numeric(14,2)` | Not null; ETL contract ≥ 0 | Default-only net principal loss: `max(0, funded_amnt - total_rec_prncp - recoveries)`, otherwise zero. |
| `net_profit` | `numeric(14,2)` | Not null | `total_pymnt - funded_amnt - collection_recovery_fee` in dollars. |

**Matured statuses:** `Fully Paid`, `Charged Off`, `Default`, `Does not meet the credit policy. Status:Fully Paid`, and `Does not meet the credit policy. Status:Charged Off`. Matured default rates use defaults divided by these matured loans. A global default rate includes the full originated population.

## Analytical views

### `dw.vw_portfolio_kpis_by_grade`

**Grain:** One row per `risk_segment`, `grade`, `sub_grade`, and `term_months`.

| Column | PostgreSQL type | Description / business rule |
| --- | --- | --- |
| `risk_segment` | `varchar(20)` | Prime, Near-Prime, or Subprime. |
| `grade` | `char(1)` | Letter grade A–G. |
| `sub_grade` | `varchar(3)` | Detailed grade A1–G5. |
| `term_months` | `integer` | Contractual term. |
| `total_loans` | `bigint` | All originated loans in the group. |
| `matured_loans` | `bigint` | Loans with a matured status. |
| `total_funded_amnt` | `numeric` | Total funded amount in dollars. |
| `matured_funded_amnt` | `numeric` | Funded amount for matured loans. |
| `default_funded_amnt` | `numeric` | Funded amount for loans flagged as default. |
| `avg_loan_amnt` | `numeric` | Average requested amount in dollars. |
| `avg_int_rate` | `numeric` | Mean decimal interest rate. |
| `avg_fico_score` | `numeric` | Mean FICO score, ignoring null scores. |
| `avg_dti` | `numeric` | Mean DTI, ignoring null values. |
| `default_count` | `bigint` | Number of loans flagged as default. |
| `global_default_rate` | `numeric` | `default_count / total_loans`. |
| `matured_default_rate` | `numeric` | Matured default count divided by matured loans. |
| `total_principal_loss` | `numeric` | Sum of net principal loss in dollars. |
| `lgd_pct` | `numeric` | `total_principal_loss / default_funded_amnt`; a decimal fraction, not multiplied by 100. |
| `matured_net_profit` | `numeric` | Sum of net profit for matured loans in dollars. |
| `matured_roi_pct` | `numeric` | Matured net profit divided by matured funded amount. |

### `dw.vw_origination_trends_monthly`

**Grain:** One origination month and risk segment.

| Column | PostgreSQL type | Description / business rule |
| --- | --- | --- |
| `year` | `integer` | Origination calendar year. |
| `quarter` | `integer` | Origination calendar quarter, 1–4. |
| `year_quarter` | `varchar(7)` | Origination cohort label. |
| `year_month` | `varchar(7)` | Origination month label; orders the trend. |
| `risk_segment` | `varchar(20)` | Prime, Near-Prime, or Subprime. |
| `total_loans` | `bigint` | Originated loan count. |
| `funded_amnt` | `numeric` | Funded amount in dollars. |
| `avg_int_rate` | `numeric` | Average decimal interest rate. |
| `avg_fico_score` | `numeric` | Average FICO score. |
| `default_rate` | `numeric` | All-originations default count divided by loans. |
| `loan_count_mom_growth_pct` | `numeric` | Month-over-month loan-count growth multiplied by 100; null without a prior nonzero month. |
| `funded_amnt_mom_growth_pct` | `numeric` | Month-over-month funded-dollar growth multiplied by 100; null without a prior nonzero month. |

### `dw.vw_borrower_risk_profile`

**Grain:** One income tier, home-ownership, verification, purpose, and application-type combination.

| Column | PostgreSQL type | Description / business rule |
| --- | --- | --- |
| `income_tier` | `varchar(25)` | Borrower annual-income band. |
| `home_ownership` | `varchar(20)` | Home-ownership category. |
| `verification_status` | `varchar(30)` | Income verification category. |
| `purpose` | `varchar(50)` | Loan purpose. |
| `application_type` | `varchar(25)` | Application type. |
| `total_loans` | `bigint` | Loan count in the group. |
| `total_funded_amnt` | `numeric` | Funded dollars in the group. |
| `avg_dti` | `numeric` | Average DTI. |
| `avg_fico_score` | `numeric` | Average FICO score. |
| `default_rate` | `numeric` | Default count divided by all loans in the group. |
| `lgd_pct` | `numeric` | Principal loss divided by default funded amount; decimal fraction. |

### `dw.vw_vintage_cohort_matrix`

**Grain:** One quarterly origination cohort, risk segment, term, and months-on-book value (0–60). Cohorts with fewer than 1,000 loans are excluded; the view generates a complete monthly spine, including months with no observed events.

| Column | PostgreSQL type | Description / business rule |
| --- | --- | --- |
| `year` | `integer` | Cohort calendar year. |
| `year_quarter` | `varchar(7)` | Quarterly origination cohort. |
| `risk_segment` | `varchar(20)` | Prime, Near-Prime, or Subprime. |
| `term_months` | `integer` | Contractual term. |
| `cohort_loans` | `bigint` | Initial number of originated loans in the cohort. |
| `cohort_funded_amnt` | `numeric` | Initial funded dollars in the cohort. |
| `months_on_book` | `integer` | Months-on-book point, from 0 to 60. |
| `marginal_defaults` | `bigint` | Default events assigned to this months-on-book value. |
| `marginal_principal_loss` | `numeric` | Principal loss assigned to this months-on-book value in dollars. |
| `cumulative_defaults` | `bigint` | Running total of marginal defaults through this month. |
| `cumulative_default_rate_pct` | `numeric` | Cumulative defaults divided by cohort loans × 100; 0–100 scale. |
| `cumulative_principal_loss` | `numeric` | Running total of marginal principal loss through this month in dollars. |
| `cumulative_loss_rate_pct` | `numeric` | Cumulative principal loss divided by cohort funded amount × 100; 0–100 scale. |

## Statistical reporting conventions

`src.risk_stats` restricts inference to the matured-status set above. It tests default independence by segment using Pearson's chi-square and reports Cramér's V; compares defaulted with Fully Paid loans using Welch's t-test and two-sided Mann–Whitney U; compares interest rate and DTI across grades A–G using one-way ANOVA and Kruskal–Wallis; and reports 95% Wilson score intervals for matured default rates by risk segment. Null FICO and DTI observations are excluded only from the test that uses that field.
