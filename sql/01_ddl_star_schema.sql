-- Esquema Estrella Kimball del data warehouse de riesgo crediticio.
-- Los DROP permiten reconstruir el modelo completo en entornos de desarrollo.
CREATE SCHEMA IF NOT EXISTS dw;
SET search_path TO dw, public;

-- Eliminar primero la tabla de hechos y después las dimensiones referenciadas.
DROP TABLE IF EXISTS dw.fact_loans CASCADE;
DROP TABLE IF EXISTS dw.dim_date CASCADE;
DROP TABLE IF EXISTS dw.dim_risk_grade CASCADE;
DROP TABLE IF EXISTS dw.dim_borrower CASCADE;
DROP TABLE IF EXISTS dw.dim_loan_purpose CASCADE;

-- Dimensión calendario para fechas de originación y último pago.
CREATE TABLE dw.dim_date (
    date_key INT PRIMARY KEY,
    full_date DATE NOT NULL,
    year INT NOT NULL,
    quarter INT NOT NULL,
    year_quarter VARCHAR(7) NOT NULL,
    month INT NOT NULL,
    month_name VARCHAR(15) NOT NULL,
    year_month VARCHAR(7) NOT NULL
);

-- Dimensión de clasificación de riesgo y plazo contractual.
CREATE TABLE dw.dim_risk_grade (
    risk_grade_key SERIAL PRIMARY KEY,
    grade CHAR(1) NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')),
    sub_grade VARCHAR(3) NOT NULL,
    term_months INT NOT NULL CHECK (term_months IN (36, 60)),
    risk_segment VARCHAR(20) NOT NULL,
    CONSTRAINT uq_dim_risk_grade_sub_grade_term
        UNIQUE (sub_grade, term_months),
    CONSTRAINT ck_dim_risk_grade_sub_grade
        CHECK (sub_grade ~ '^[A-G][1-5]$' AND LEFT(sub_grade, 1) = grade),
    CONSTRAINT ck_dim_risk_grade_segment
        CHECK (
            (grade IN ('A', 'B') AND risk_segment = 'Prime')
            OR (grade IN ('C', 'D') AND risk_segment = 'Near-Prime')
            OR (grade IN ('E', 'F', 'G') AND risk_segment = 'Subprime')
        )
);

-- Mini-dimensión con atributos demográficos y socioeconómicos del prestatario.
CREATE TABLE dw.dim_borrower (
    borrower_key SERIAL PRIMARY KEY,
    emp_length VARCHAR(25) NOT NULL,
    home_ownership VARCHAR(20) NOT NULL,
    verification_status VARCHAR(30) NOT NULL,
    addr_state VARCHAR(10) NOT NULL,
    income_tier VARCHAR(25) NOT NULL
        CHECK (income_tier IN ('<40K', '40K-75K', '75K-120K', '120K+', 'Unknown')),
    CONSTRAINT uq_dim_borrower_attributes
        UNIQUE (emp_length, home_ownership, verification_status, addr_state, income_tier)
);

-- Dimensión de propósito y tipo de solicitud.
CREATE TABLE dw.dim_loan_purpose (
    purpose_key SERIAL PRIMARY KEY,
    purpose VARCHAR(50) NOT NULL,
    application_type VARCHAR(25) NOT NULL,
    CONSTRAINT uq_dim_loan_purpose_attributes
        UNIQUE (purpose, application_type)
);

-- Hecho con grano de un préstamo originado y sus métricas de desempeño.
CREATE TABLE dw.fact_loans (
    loan_id BIGINT PRIMARY KEY,
    issue_date_key INT NOT NULL REFERENCES dw.dim_date(date_key),
    last_pymnt_date_key INT NOT NULL DEFAULT -1 REFERENCES dw.dim_date(date_key),
    risk_grade_key INT NOT NULL REFERENCES dw.dim_risk_grade(risk_grade_key),
    borrower_key INT NOT NULL REFERENCES dw.dim_borrower(borrower_key),
    purpose_key INT NOT NULL REFERENCES dw.dim_loan_purpose(purpose_key),
    loan_status VARCHAR(60) NOT NULL,
    is_default BOOLEAN NOT NULL,
    months_on_book INT NOT NULL CHECK (months_on_book >= 0),
    annual_inc NUMERIC(14, 2),
    dti NUMERIC(8, 2),
    fico_score INT,
    loan_amnt NUMERIC(12, 2) NOT NULL,
    funded_amnt NUMERIC(12, 2) NOT NULL,
    int_rate NUMERIC(6, 4) NOT NULL,
    installment NUMERIC(10, 2) NOT NULL,
    total_pymnt NUMERIC(14, 2) NOT NULL,
    total_rec_prncp NUMERIC(14, 2) NOT NULL,
    total_rec_int NUMERIC(14, 2) NOT NULL,
    recoveries NUMERIC(14, 2) NOT NULL,
    collection_recovery_fee NUMERIC(14, 2) NOT NULL,
    principal_loss NUMERIC(14, 2) NOT NULL,
    net_profit NUMERIC(14, 2) NOT NULL
);

-- Índices para joins dimensional, filtros de estado y análisis de cosechas.
CREATE INDEX idx_fact_loans_issue_date_key
    ON dw.fact_loans USING BTREE (issue_date_key);
CREATE INDEX idx_fact_loans_last_pymnt_date_key
    ON dw.fact_loans USING BTREE (last_pymnt_date_key);
CREATE INDEX idx_fact_loans_risk_grade_key
    ON dw.fact_loans USING BTREE (risk_grade_key);
CREATE INDEX idx_fact_loans_borrower_key
    ON dw.fact_loans USING BTREE (borrower_key);
CREATE INDEX idx_fact_loans_purpose_key
    ON dw.fact_loans USING BTREE (purpose_key);
CREATE INDEX idx_fact_loans_loan_status
    ON dw.fact_loans USING BTREE (loan_status);
CREATE INDEX idx_fact_loans_is_default
    ON dw.fact_loans USING BTREE (is_default);
CREATE INDEX idx_fact_loans_issue_date_mob
    ON dw.fact_loans USING BTREE (issue_date_key, months_on_book);

-- Definiciones de negocio para medidas críticas de riesgo y retorno.
COMMENT ON TABLE dw.fact_loans IS
    'Hecho al grano de un préstamo originado, con estado y flujos financieros para análisis de riesgo y cosechas.';
COMMENT ON COLUMN dw.fact_loans.is_default IS
    'Indica incumplimiento; TRUE para Charged Off, Default y Does not meet the credit policy. Status:Charged Off.';
COMMENT ON COLUMN dw.fact_loans.months_on_book IS
    'Meses transcurridos entre issue_d y last_pymnt_d, usados para medir la maduración de cada cosecha.';
COMMENT ON COLUMN dw.fact_loans.principal_loss IS
    'Pérdida neta de principal después de recuperaciones; importe monetario para el cálculo de LGD.';
COMMENT ON COLUMN dw.fact_loans.net_profit IS
    'Retorno neto monetario calculado como total_pymnt - funded_amnt - collection_recovery_fee, base del ROI.';

-- Miembro desconocido para conservar la integridad referencial ante fechas nulas.
INSERT INTO dw.dim_date (
    date_key,
    full_date,
    year,
    quarter,
    year_quarter,
    month,
    month_name,
    year_month
)
VALUES (-1, '1900-01-01', 1900, 1, 'Unknown', 1, 'Unknown', 'Unknown')
ON CONFLICT (date_key) DO NOTHING;
