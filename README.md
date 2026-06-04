# CloudSync SaaS Analytics — Root Cause Investigation

> **Role context:** Incoming data analyst. CEO mandate: diagnose why Enterprise-Outbound renewal rate declined in Q4 2024 following a period of declining engagement — and recommend actions.

---

## The Problem

CloudSync's revenue dashboard showed healthy growth throughout 2024 — MRR grew from $44K to $110K. But underneath, a specific segment was quietly deteriorating: Enterprise accounts acquired through Outbound Sales were disengaging from the product and not renewing.

The investigation was invisible from the top line. New customer acquisition was offsetting churned accounts, making the problem invisible to anyone monitoring total revenue. The goal was to find the root cause before more renewals were lost.

---

## Key Findings

| Finding | Value |
|---------|-------|
| Engagement decline (H1 → H2 2024) | -15.7% for Enterprise-Outbound |
| Comparable accounts declined | 45 of 56 |
| Q4 churn rate — Outbound vs Inbound | 27.78% vs 10% |
| H2 avg API resolution time | 304.33 hours (13 days) |
| High-critical severity (H2, API tickets) | 79.44% |
| Churned MRR — Q4 2024 | $8,386 |

**Root cause:** API Integration Failure tickets for Enterprise-Outbound accounts began taking 300+ hours to resolve from July 2024 onward — up from 65 hours in June. Accounts waiting the longest declined 22% in engagement versus 9.5% for low-burden accounts. The support burden spike preceded the engagement collapse by one month. A dose-response gradient confirmed across three independent burden metrics rules out coincidence.

---

## Methodology

This project follows the **C.H.E.S.S. framework** — Context, Hypothesis, Engine, Scrutiny, Solution.

```
Context     → Define the business problem and scoping questions before touching data
Hypothesis  → Build a MECE RCA tree, rank 12 hypotheses by Impact × Likelihood × Ease
Engine      → Charter validation (Q0–Q5) + Sprint hypothesis testing (H9, H10, H2)
Scrutiny    → Simpson's Paradox checks, statistical confidence assessment, falsification
Solution    → Dose-response-derived SLA recommendation + A/B experiment design
```

**Cohort design:** Enterprise-Inbound used as control group — matched on company size and integration complexity, isolating the acquisition channel variable.

**Causal evidence approach:** Three independent burden metrics (resolution time, ticket frequency, failure count) all produced the same dose-response gradient. Zero-ticket accounts maintained stable engagement, eliminating platform-wide causes.

---

## Artifacts

### Power BI Dashboard
6-page dashboard covering Executive Overview, Engagement Collapse, Support Burden, Causal Chain, and Recommendation.

🔗 [View live dashboard →](https://app.powerbi.com/reportEmbed?reportId=5dc4512f-dcb0-4ce2-a6e2-73dc4c241109&autoAuth=true&ctid=931ed907-4be0-494c-8f96-17c8a5af1736)

### SQL Analysis Notebook
Charter validation (Q0–Q5) + hypothesis testing (H9, H10, H2) + falsification queries. Every query has a 3-line annotation explaining what it tests, what it found, and how it is used in the investigation.

📄 [sql/cloudsync_sql_analysis.md](./sql/cloudsync_sql_analysis.md)

### Python Data Cleaning Notebook
End-to-end cleaning pipeline for all five tables — handling 27 channel typos, 7 CRM duplicates, 1 ghost account, 59 MRR/tier mismatches, and 156 open ticket records.

📄 [Cleaning notebook Link](https://github.com/JATIN317/CloudSync-Analytics/blob/main/python/cloudsync_cleaning_notebook.py)

### Experiment Design Document
A/B test design to validate whether a dedicated `<100-hour SLA queue` for API Integration Failure tickets recovers engagement. Covers MDE (697 actions), power (80%), sample size (40 accounts per group), and 16-week timeline.

📄 [docs/experiment_design.md](https://github.com/JATIN317/CloudSync-Analytics/blob/main/docs/experiment_design)

---

## Data Model

Star schema — 5 tables, all relationships single-direction from dim to fact.

```
dim_customers          ←── fact_subscriptions
dim_customers          ←── fact_monthly_usage
dim_customers          ←── fact_support_tickets
dim_date               ←── fact_subscriptions
dim_date               ←── fact_monthly_usage
```

**Known data quality issues handled:**

| Issue | Table | Scale | Resolution |
|-------|-------|-------|-----------|
| Channel typos | dim_customers | 27 variants | LIKE filter + channel_clean column |
| CRM duplicates | dim_customers | 7 rows | is_duplicate flag |
| Ghost account | dim_customers | 1 (CUST-5738) | has_usage flag |
| MRR/tier mismatch | fact_subscriptions | 59 rows | mrr_flag = 'Check' |
| Open tickets | fact_support_tickets | 156 rows | ticket_status filter |

---

## Tech Stack

| Tool | Use |
|------|-----|
| Python (Pandas) | Data cleaning and cohort preparation |
| SQL (SQL-Server) | Charter validation and hypothesis testing |
| Power BI | Dashboard, DAX measures, RLS, data model |
| Power BI Service | Publishing and sharing |

---

## Recommendation

**Immediate (no engineering required):** Establish a dedicated support queue for API Integration Failure tickets in Enterprise-Outbound accounts with a `<100-hour` SLA target. This threshold is empirically derived — accounts below 100 hours declined 9.5% in engagement; accounts above it declined 22%+.

**Before full rollout:** Run a 16-week A/B experiment (40 accounts treatment, 40 control) to validate that reducing resolution time causes engagement recovery. MDE: +697 core actions per account per month (50% recovery of H2 decline). Power: 80%, α = 0.05.

---

*Dataset: Simulated. Designed as a portfolio project to demonstrate end-to-end analytical investigation — from raw messy data through structured hypothesis testing to an executive recommendation.*
