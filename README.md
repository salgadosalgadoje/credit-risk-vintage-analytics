# Credit Risk & Vintage Analysis Pipeline

End-to-end credit risk analytics for consumer origination: a Kimball star schema, quarterly cohort vintage modeling, and an executive BI layer that turns historical loan performance into origination, loss, and return decisions.

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Polars](https://img.shields.io/badge/Polars-2.2-CD7925?logo=polars&logoColor=white)
![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-DAX-F2C811?logo=powerbi&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

## Executive Overview & Business Problem

Consumer credit portfolios fail quietly when origination quality is judged on volume and coupon, and loss is measured only after the fact. This pipeline converts more than 2.2 million historical LendingClub loans (`2007–2018Q4`) into a governed warehouse so risk and finance can answer four questions on the same grain:

- **Origination quality.** Which grades, terms, and vintages entered the book, and at what exposure.
- **Default Rate / Charge-Off.** How often a cohort reaches default or charge-off as months on book increase.
- **Loss given default (LGD).** How much of the defaulted balance is not recovered.
- **Net return (ROI).** Whether the yield collected still covers credit loss for that cohort.
- **Vintage deterioration.** How quarterly origination cohorts age, so a weakening book is visible before it dominates the portfolio.

The LendingClub extract stays local. It is larger than 1.5 GB and is excluded from Git. Only the dimensional model, the KPI contract, and the BI definitions are versioned.

## Architecture & Tech Stack

| Layer | Responsibility | Stack |
| --- | --- | --- |
| Data warehouse | Conformed dimensions and a loan-performance fact on PostgreSQL, isolated per environment | PostgreSQL 16 on Docker, database `credit_risk_dw` |
| Ingestion and data contracts | Columnar read of the raw extract, row-level validation, and an idempotent load path | Polars, Pydantic v2, SQLAlchemy 2, psycopg 3 |
| Statistical analytics | Cohort curves, default and charge-off rates, and tests that the vintage matrix is internally consistent | SciPy, NumPy |
| Executive BI | Measures, scenario levers, and row-level access for risk and finance audiences | Power BI with DAX, What-If parameters, and RLS |

```text
accepted_2007_to_2018Q4.csv          local only, not in Git
        │
        ▼
Polars + Pydantic v2                contract checks before any write
        │
        ▼
PostgreSQL 16 (Docker)              Kimball star schema
        ├── KPI views               Default Rate, Charge-Off, LGD, ROI
        └── Vintage matrix          quarterly cohort × months on book
                │
                ├── SciPy           statistical tests on cohort curves
                └── Power BI        DAX measures, What-If, RLS
```

`src/config.py` is the only configuration entry point. Pydantic Settings reads `.env` and builds a `postgresql+psycopg` SQLAlchemy URI.

## Repository Structure

```text
credit-risk-vintage-analytics/
├── data/
│   └── raw/
│       └── .gitkeep                 # keeps the folder; the LendingClub CSV is gitignored
├── sql/
│   ├── 01_ddl_star_schema.sql       # Kimball star schema DDL
│   ├── 02_kpi_views.sql             # portfolio KPI views
│   └── 03_vintage_analysis.sql      # quarterly vintage / months-on-book matrix
├── src/
│   ├── __init__.py
│   ├── config.py                    # Pydantic v2 settings and SQLAlchemy URI
│   ├── download_data.py             # idempotent LendingClub download via kagglehub
│   ├── etl_load.py                  # idempotent load with Pydantic contracts
│   └── risk_stats.py                # cohort loss and vintage statistics
├── powerbi/
│   └── dax_measures.md              # DAX, What-If, and RLS semantic layer
├── docs/
│   ├── data_dictionary.md           # grain, keys, and KPI definitions
│   └── screenshots/
│       └── .gitkeep
├── tests/
│   ├── __init__.py
│   └── test_data_integrity.py       # grain, contracts, and vintage consistency
├── .env.example                     # PostgreSQL and raw-path contract
├── .gitignore                       # blocks .env, the raw extract, and local caches
├── docker-compose.yml               # PostgreSQL 16 with a persistent volume
├── requirements.txt
└── README.md
```

## Quickstart Guide (Local Setup)

Requirements: Git, Docker, and Python 3.11 or newer.

1. Clone the repository and enter it.

   ```powershell
   git clone https://github.com/salgadosalgadoje/credit-risk-vintage-analytics.git
   cd credit-risk-vintage-analytics
   ```

2. Create the local environment file from the template. Set a real PostgreSQL password and `KAGGLE_API_TOKEN` (from [Kaggle API settings](https://www.kaggle.com/settings/api)) before the first download.

   ```powershell
   Copy-Item .env.example .env
   ```

3. Start PostgreSQL 16. The service listens on `localhost:5432`, creates `credit_risk_dw`, and stores data in the `postgres_data` volume. It is ready when `pg_isready` reports healthy.

   ```powershell
   docker compose up -d
   docker compose ps
   ```

4. Create the virtual environment and install dependencies.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

5. Download the LendingClub extract. The command is idempotent: when `data/raw/accepted_2007_to_2018Q4.csv` already exists and is larger than 100 MB, it exits without calling Kaggle.

   ```powershell
   python -m src.download_data
   ```

   The CSV stays on disk only. `.gitignore` ignores `data/raw/*` and keeps `data/raw/.gitkeep`.

On macOS or Linux, replace the copy and activation lines with `cp .env.example .env` and `source .venv/bin/activate`.

## Implementation Roadmap

- [x] **Phase 1: Infrastructure & Data Governance [Completed]** — Dockerized PostgreSQL 16, secret and raw-data git rules, Pydantic settings, and the project scaffold.
- [x] **Phase 2: Kimball Star Schema DDL [Completed]** — conformed dimensions and the loan-performance fact in `sql/01_ddl_star_schema.sql`.
- [ ] **Phase 3: Idempotent ETL & Pydantic Validation** — Polars extract, loan-level contracts, and a repeatable load into `credit_risk_dw`.
- [ ] **Phase 4: KPI Views & Vintage Cohort SQL** — Default Rate, Charge-Off, LGD, ROI, and the quarterly vintage matrix.
- [ ] **Phase 5: Statistical Testing & Power BI Dashboard** — SciPy checks on cohort curves, plus DAX measures, What-If scenarios, and RLS.
