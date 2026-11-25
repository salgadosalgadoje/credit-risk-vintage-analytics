# Power BI DAX Measures and Security

## Semantic model assumptions

Import the five `dw` tables and four analytical views documented in the [data dictionary](../docs/data_dictionary.md). Use one-to-many, single-direction relationships from each dimension to `fact_loans`:

- `dim_date[date_key]` → `fact_loans[issue_date_key]` (active); optionally add an inactive relationship from `dim_date[date_key]` to `fact_loans[last_pymnt_date_key]`.
- `dim_risk_grade[risk_grade_key]` → `fact_loans[risk_grade_key]`.
- `dim_borrower[borrower_key]` → `fact_loans[borrower_key]`.
- `dim_loan_purpose[purpose_key]` → `fact_loans[purpose_key]`.

The measures use the fact table for the portfolio and the imported vintage view for cohort curves. `fact_loans[int_rate]` is a decimal (12% = 0.12); rate measures below should be formatted as Percentage. The vintage view's `*_pct` fields are 0–100, so those fields are divided by 100 in DAX.

The matured-status definition is shared by every measure: Fully Paid, Charged Off, Default, and both `Does not meet the credit policy. Status:...` Fully Paid/Charged Off statuses.

## Portfolio Volume & Exposure

```DAX
Total Loans =
    DISTINCTCOUNT ( 'fact_loans'[loan_id] )

Matured Loans =
    CALCULATE (
        [Total Loans],
        'fact_loans'[loan_status] IN {
            "Fully Paid",
            "Charged Off",
            "Default",
            "Does not meet the credit policy. Status:Fully Paid",
            "Does not meet the credit policy. Status:Charged Off"
        }
    )

Total Funded ($) =
    SUM ( 'fact_loans'[funded_amnt] )

Matured Funded ($) =
    CALCULATE (
        [Total Funded ($)],
        'fact_loans'[loan_status] IN {
            "Fully Paid",
            "Charged Off",
            "Default",
            "Does not meet the credit policy. Status:Fully Paid",
            "Does not meet the credit policy. Status:Charged Off"
        }
    )

Avg Loan Size ($) =
    AVERAGE ( 'fact_loans'[loan_amnt] )

Weighted Avg Interest Rate =
    DIVIDE (
        SUMX (
            'fact_loans',
            'fact_loans'[funded_amnt] * 'fact_loans'[int_rate]
        ),
        [Total Funded ($)]
    )

Weighted Avg FICO =
    VAR ScoredLoans =
        FILTER (
            'fact_loans',
            NOT ISBLANK ( 'fact_loans'[fico_score] )
        )
    RETURN
        DIVIDE (
            SUMX (
                ScoredLoans,
                'fact_loans'[funded_amnt] * 'fact_loans'[fico_score]
            ),
            SUMX ( ScoredLoans, 'fact_loans'[funded_amnt] )
        )
```

## Credit Risk & Losses

`Default Funded ($)` is a supporting measure for LGD. The portfolio KPI view defines LGD as principal loss divided by default funded amount (not by default count).

```DAX
Default Count =
    CALCULATE (
        [Total Loans],
        'fact_loans'[is_default] = TRUE ()
    )

Global Default Rate =
    DIVIDE ( [Default Count], [Total Loans] )

Matured Default Rate =
    DIVIDE (
        CALCULATE (
            [Default Count],
            'fact_loans'[loan_status] IN {
                "Fully Paid",
                "Charged Off",
                "Default",
                "Does not meet the credit policy. Status:Fully Paid",
                "Does not meet the credit policy. Status:Charged Off"
            }
        ),
        [Matured Loans]
    )

Principal Loss ($) =
    SUM ( 'fact_loans'[principal_loss] )

Default Funded ($) =
    CALCULATE (
        [Total Funded ($)],
        'fact_loans'[is_default] = TRUE ()
    )

LGD % =
    DIVIDE ( [Principal Loss ($)], [Default Funded ($)] )

Expected Loss ($) =
    [Total Funded ($)] * [Matured Default Rate] * [LGD %]
```

## Profitability

```DAX
Net Profit ($) =
    SUM ( 'fact_loans'[net_profit] )

Matured Net Profit ($) =
    CALCULATE (
        [Net Profit ($)],
        'fact_loans'[loan_status] IN {
            "Fully Paid",
            "Charged Off",
            "Default",
            "Does not meet the credit policy. Status:Fully Paid",
            "Does not meet the credit policy. Status:Charged Off"
        }
    )

Matured ROI % =
    DIVIDE ( [Matured Net Profit ($)], [Matured Funded ($)] )
```

## Vintage Analysis

Place `months_on_book` on the visual axis and filter the view to the selected cohort/segment/term. The measures aggregate the cohort values visible at each months-on-book point; cumulative rates are inherently time-point measures.

```DAX
Cohort Initial Loans =
    SUMX (
        SUMMARIZE (
            'vw_vintage_cohort_matrix',
            'vw_vintage_cohort_matrix'[year_quarter],
            'vw_vintage_cohort_matrix'[risk_segment],
            'vw_vintage_cohort_matrix'[term_months],
            "InitialLoans", MAX ( 'vw_vintage_cohort_matrix'[cohort_loans] )
        ),
        [InitialLoans]
    )

Cumulative Default Rate % =
    DIVIDE (
        SUM ( 'vw_vintage_cohort_matrix'[cumulative_defaults] ),
        SUM ( 'vw_vintage_cohort_matrix'[cohort_loans] )
    )

Cumulative Loss Rate % =
    DIVIDE (
        SUM ( 'vw_vintage_cohort_matrix'[cumulative_principal_loss] ),
        SUM ( 'vw_vintage_cohort_matrix'[cohort_funded_amnt] )
    )
```

Format both cumulative-rate measures as Percentage. If displaying multiple MOB values in a card or total, use a single MOB selection rather than summing cumulative snapshots.

## What-If Stress Testing

Create two Power BI numeric What-If parameters, both decimal values with a default of zero. The example ranges allow a 50% reduction through a 100% relative uplift in default rate, and a 50% relative reduction through a 100% uplift in LGD. Set the parameter tables to **What-if parameter** and use their generated `[Value]` columns in slicers.

```DAX
Default Rate Shock =
    GENERATESERIES ( -0.5, 1.0, 0.05 )

LGD Shock =
    GENERATESERIES ( -0.5, 1.0, 0.05 )
```

```DAX
Default Rate Shock % =
    SELECTEDVALUE ( 'Default Rate Shock'[Value], 0 )

LGD Shock % =
    SELECTEDVALUE ( 'LGD Shock'[Value], 0 )

Stressed Default Rate =
    MIN ( 1, MAX ( 0, [Matured Default Rate] * ( 1 + [Default Rate Shock %] ) ) )

Stressed LGD % =
    MIN ( 1, MAX ( 0, [LGD %] * ( 1 + [LGD Shock %] ) ) )

Stress Expected Loss ($) =
    [Matured Funded ($)] * [Stressed Default Rate] * [Stressed LGD %]

Projected ROI % =
    VAR BaselineExpectedLoss =
        [Matured Funded ($)] * [Matured Default Rate] * [LGD %]
    VAR IncrementalStressLoss =
        [Stress Expected Loss ($)] - BaselineExpectedLoss
    RETURN
        DIVIDE (
            [Matured Net Profit ($)] - IncrementalStressLoss,
            [Matured Funded ($)]
        )
```

`Projected ROI %` holds current matured net profit constant at zero shock, and adjusts it only by the change in expected loss under the selected relative shocks. This is a scenario sensitivity, not a forecast of future cash flows.

## Row-Level Security (RLS)

Create roles in **Manage roles**. Apply each expression to the named table. Replace the illustrative state codes with the approved entitlements.

**Regional analyst — filter on `dim_borrower`:**

```DAX
'dim_borrower'[addr_state] IN { "CA", "OR", "WA" }
```

For dynamic state entitlements, maintain a secured `UserStateAccess` table with `[UPN]` and `[addr_state]`. Use this role filter on `dim_borrower` (no many-to-many relationship is required):

```DAX
'dim_borrower'[addr_state]
    IN CALCULATETABLE (
        VALUES ( 'UserStateAccess'[addr_state] ),
        'UserStateAccess'[UPN] = USERPRINCIPALNAME ()
    )
```

**Prime portfolio manager — filter on `dim_risk_grade`:**

```DAX
'dim_risk_grade'[risk_segment] = "Prime"
```

Apply the matching direct filter to each imported risk-segment analytical view used by a manager's report, because those views are separate model tables:

```DAX
'vw_portfolio_kpis_by_grade'[risk_segment] = "Prime"
'vw_origination_trends_monthly'[risk_segment] = "Prime"
'vw_vintage_cohort_matrix'[risk_segment] = "Prime"
```

**Subprime portfolio manager — filter on `dim_risk_grade`:**

```DAX
'dim_risk_grade'[risk_segment] = "Subprime"
```

Apply `"Subprime"` instead of `"Prime"` in the same three view filters for the Subprime role. The borrower profile view has no `risk_segment` or `addr_state` column; exclude it from either restricted report or rebuild it at an appropriately secured grain. Similarly, the regional role must use fact-backed visuals: the monthly trends and vintage views do not contain state and cannot be safely state-filtered. Do not rely on dimension relationships to secure a pre-aggregated view unless those relationships and filter propagation have been explicitly modeled and tested.

Assign users to least-privilege roles in the Power BI service. RLS does not restrict workspace administrators, members, or contributors; validate access with **View as** and service test accounts before publication.
