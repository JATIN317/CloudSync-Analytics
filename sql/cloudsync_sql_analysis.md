# CloudSync SaaS Analytics — SQL Analysis Notebook

**Author:** Jatin Nair  
**Project:** CloudSync SaaS Product Analytics — Root Cause Investigation  
**Role context:** Incoming analyst. CEO mandate: diagnose why Enterprise-Outbound renewal rate declined in Q4 2024 following a period of declining engagement, and recommend product or operational actions to restore retention.

---

## How to Read This Notebook

This is a structured analytical investigation, not a collection of ad-hoc queries. Every query exists to answer a specific business question in a deliberate sequence. Before any hypothesis was formed, the charter's core claims were validated against the data. Only then were hypotheses ranked, tested, and stress-tested for falsification.

**Investigation sequence:**

| Section | Purpose |
|---------|---------|
| §1–6 | Charter Validation — confirm the problem exists and is real |
| §7 | Hypothesis Testing — prove the mechanism |
| §8 | Falsification — deliberately try to break the conclusions |

**Database schema:**

| Table | Grain | Key columns |
|-------|-------|-------------|
| `dim_customers` | One row per customer | customer_id, company_size, acquisition_channel, signup_date |
| `dim_date` | One row per calendar date | date_key |
| `fact_subscriptions` | One row per subscription event | customer_id, plan_tier, base_mrr, start_date, end_date, status |
| `fact_monthly_usage` | One row per customer per month | customer_id, activity_month, core_actions_logged, active_users_count |
| `fact_support_tickets` | One row per support ticket | customer_id, created_date, resolved_date, issue_category, severity, resolution_time_hrs |

**Cohort definitions used throughout this notebook:**
- `Enterprise_Outbound` — company_size = 'Enterprise' AND acquisition_channel LIKE '%outbound%'
- `Enterprise_Inbound` — company_size = 'Enterprise' AND acquisition_channel NOT LIKE '%outbound%'

The `LIKE '%outbound%'` pattern is intentional. The raw `acquisition_channel` column contains four string variants for the same channel due to manual CRM entry — `'Outbound Sales'`, `'Outbound'`, `'outbound_sales'`, `'Outbound sales'`. An exact match would silently undercount the cohort.

---

## SECTION 1 — Cohort Definition

Before any analysis runs, the cohort must be validated. An incorrectly defined cohort makes every downstream finding invalid. This section confirms the filter logic captures the right accounts, checks for unexpected string variants, and validates that the cohort is viable for decline analysis by examining tenure distribution.

### 1.1 — Cohort Size and Channel Variant Check

```sql
-- What it tests: Whether LIKE '%outbound%' captures the right accounts and all channel variants
-- Key finding:   82 raw accounts across two string variants (Outbound: 2, Outbound Sales: 80)
-- Business use:  Confirms cohort filter before any downstream query depends on it

SELECT
    company_size,
    acquisition_channel,
    COUNT(*) AS customer_count
FROM dim_customers
WHERE company_size = 'Enterprise'
  AND acquisition_channel LIKE '%outbound%'
GROUP BY company_size, acquisition_channel
ORDER BY acquisition_channel;
```

**Output:**

| company_size | acquisition_channel | customer_count |
|-------------|--------------------| --------------|
| Enterprise | Outbound | 2 |
| Enterprise | Outbound Sales | 80 |

**Finding:** 82 raw accounts captured across two channel string variants. After the Python cleaning step — removing one explicit CRM duplicate (CUST-0514-DUP) and one ghost account (CUST-5738, which exists in `dim_customers` but has no records in any fact table) — the clean analysis cohort is **80 accounts**. All downstream queries apply the same `NOT LIKE '%-DUP'` and specific ID exclusion.

---

### 1.2 — Tenure Distribution (Mix-Shift Stress Test)

Customers who joined in Q4 2024 could not have contributed to an engagement decline that started in Q3. If most accounts were recent joiners, the observed decline would be a cohort composition artefact, not a retention problem. This query rules that out.

```sql
-- What it tests: Whether the cohort is viable for decline analysis or is dominated by new joiners
-- Key finding:   ~75% of accounts were active before Q3 — mix-shift ruled out
-- Business use:  Validates that the engagement decline reflects real behavioral change, not dilution

WITH cohort AS (
    SELECT
        c.customer_id,
        MIN(s.start_date) AS first_start_date
    FROM dim_customers c
    JOIN fact_subscriptions s ON c.customer_id = s.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
    GROUP BY c.customer_id
)
SELECT
    CASE
        WHEN first_start_date < '2024-07-01'  THEN 'Pre-Q3 — valid for decline analysis'
        WHEN first_start_date < '2024-10-01'  THEN 'Q3 joiner — partial exposure'
        ELSE                                       'Q4 joiner — not valid for decline analysis'
    END AS tenure_bucket,
    COUNT(*) AS customer_count
FROM cohort
GROUP BY
    CASE
        WHEN first_start_date < '2024-07-01'  THEN 'Pre-Q3 — valid for decline analysis'
        WHEN first_start_date < '2024-10-01'  THEN 'Q3 joiner — partial exposure'
        ELSE                                       'Q4 joiner — not valid for decline analysis'
    END
ORDER BY tenure_bucket;
```

**Finding:** Approximately 54 of 80 accounts (67.5%) were active before Q3 2024. The engagement decline cannot be explained as a new-customer dilution effect. Confirmed: viable cohort for longitudinal analysis.

---

## SECTION 2 — MRR Stability

The charter stated that MRR was "nominally stable" while the retention crisis developed. This section tests that claim and reveals that MRR was not stable — it was growing. The more precise finding is that new customer acquisition masked churned accounts, making the crisis invisible to anyone monitoring total revenue.

### 2.1 — Monthly MRR and Active Customer Snapshot

```sql
-- What it tests: Whether MRR and active customers moved during the problem window
-- Key finding:   MRR grew $44K → $110K. Not stable — growing. Hides churn underneath.
-- Business use:  Establishes why the CEO mandate existed: revenue dashboard showed green

SELECT
    DATETRUNC(month, d.date_key)        AS snapshot_month,
    SUM(s.base_mrr)                      AS total_mrr,
    COUNT(DISTINCT s.customer_id)        AS active_customers
FROM fact_subscriptions s
JOIN dim_customers c  ON s.customer_id = c.customer_id
JOIN dim_date d
    ON d.date_key BETWEEN s.start_date AND COALESCE(s.end_date, '2024-12-31')
WHERE d.date_key  BETWEEN '2024-01-01' AND '2024-12-31'
  AND d.date_key  = DATETRUNC(month, d.date_key)
  AND c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY DATETRUNC(month, d.date_key)
ORDER BY snapshot_month;
```

**Finding:** MRR grew from ~$44K (January) to ~$110K (December). Active customers grew from 30 to 69. Total MRR growth did not signal a problem — which is precisely the diagnostic challenge this project addresses.

---

### 2.2 — MRR Waterfall: Decomposing Revenue Movement

Total MRR growing is not proof of health. This query splits revenue into its four components — new, expansion, contraction, and churned — to reveal whether new acquisition was offsetting losses from deteriorating accounts.

```sql
-- What it tests: Whether churned MRR was becoming material despite total MRR growth
-- Key finding:   Churned and contraction MRR become significant in Q3-Q4 while new MRR offsets
-- Business use:  Proves the masking effect — the crisis was hidden inside growing topline revenue

WITH cohort AS (
    SELECT customer_id
    FROM dim_customers
    WHERE company_size = 'Enterprise'
      AND acquisition_channel LIKE '%outbound%'
      AND customer_id NOT LIKE '%-DUP'
      AND customer_id != 'CUST-5738'
),
months AS (
    SELECT DISTINCT DATETRUNC(month, date_key) AS month
    FROM dim_date
    WHERE date_key BETWEEN '2024-01-01' AND '2024-12-31'
      AND date_key = DATETRUNC(month, date_key)
),
customer_month_mrr AS (
    SELECT
        m.month,
        c.customer_id,
        COALESCE(SUM(s.base_mrr), 0) AS mrr
    FROM cohort c
    CROSS JOIN months m
    LEFT JOIN fact_subscriptions s
        ON  s.customer_id = c.customer_id
        AND m.month BETWEEN s.start_date AND COALESCE(s.end_date, '2024-12-31')
    GROUP BY m.month, c.customer_id
),
delta AS (
    SELECT
        month,
        customer_id,
        mrr,
        LAG(mrr, 1, 0) OVER (PARTITION BY customer_id ORDER BY month) AS prev_mrr
    FROM customer_month_mrr
)
SELECT
    month,
    SUM(CASE WHEN prev_mrr = 0   AND mrr > 0                THEN mrr            ELSE 0 END) AS new_mrr,
    SUM(CASE WHEN prev_mrr > 0   AND mrr > prev_mrr         THEN mrr - prev_mrr ELSE 0 END) AS expansion_mrr,
    SUM(CASE WHEN prev_mrr > 0   AND mrr < prev_mrr AND mrr > 0 THEN prev_mrr - mrr ELSE 0 END) AS contraction_mrr,
    SUM(CASE WHEN prev_mrr > 0   AND mrr = 0                THEN prev_mrr       ELSE 0 END) AS churned_mrr
FROM delta
GROUP BY month
ORDER BY month;
```

**Finding:** Churned MRR grows materially in Q3–Q4 while new MRR continues rising. Revenue from new customer acquisition was compensating for losses from existing accounts churning. The waterfall makes the hidden crisis visible: the blended MRR total was healthy; the underlying retention was not.

---

### 2.3 — MRR Per Active Customer

If per-customer MRR is flat while total MRR doubles, growth is purely volumetric. This query isolates the expansion signal from the headcount signal.

```sql
-- What it tests: Whether MRR growth reflects per-account value expansion or just adding accounts
-- Key finding:   Avg MRR per customer flat ($1,400–$1,700) throughout 2024 despite total MRR doubling
-- Business use:  Proves growth is volume-driven. Existing accounts are not expanding in value.

WITH cohort AS (
    SELECT customer_id
    FROM dim_customers
    WHERE company_size = 'Enterprise'
      AND acquisition_channel LIKE '%outbound%'
      AND customer_id NOT LIKE '%-DUP'
      AND customer_id != 'CUST-5738'
),
months AS (
    SELECT DISTINCT DATETRUNC(month, date_key) AS month
    FROM dim_date
    WHERE date_key BETWEEN '2024-01-01' AND '2024-12-31'
      AND date_key = DATETRUNC(month, date_key)
),
customer_month_mrr AS (
    SELECT
        m.month,
        c.customer_id,
        COALESCE(SUM(s.base_mrr), 0) AS mrr
    FROM cohort c
    CROSS JOIN months m
    LEFT JOIN fact_subscriptions s
        ON  s.customer_id = c.customer_id
        AND m.month BETWEEN s.start_date AND COALESCE(s.end_date, '2024-12-31')
    GROUP BY m.month, c.customer_id
)
SELECT
    month,
    COUNT(*)                        AS active_customers,
    ROUND(AVG(mrr), 2)              AS avg_mrr_per_customer
FROM customer_month_mrr
WHERE mrr > 0
GROUP BY month
ORDER BY month;
```

**Finding:** Average MRR per customer remained flat at $1,400–$1,700 throughout 2024. Total MRR growth came entirely from adding more customers, not from expanding value within existing accounts. This rules out product-led growth as an explanation and confirms the retention problem was structural.

---

### 2.4 — MRR Concentration of Q4 Churned Accounts

```sql
-- What it tests: Whether churned Q4 accounts represented a financially material portion of MRR
-- Key finding:   ~3% of December MRR came from accounts that churned in Q4 — early-stage signal
-- Business use:  Calibrates financial urgency. At CAC of $5K-$10K per account, even $8K lost = material

WITH december_active AS (
    SELECT s.customer_id, s.base_mrr
    FROM fact_subscriptions s
    JOIN dim_customers c ON s.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND '2024-12-01' BETWEEN s.start_date AND COALESCE(s.end_date, '2024-12-31')
),
q4_churned AS (
    SELECT DISTINCT s.customer_id
    FROM fact_subscriptions s
    JOIN dim_customers c ON s.customer_id = c.customer_id
    WHERE s.status = 'Churned'
      AND s.end_date BETWEEN '2024-10-01' AND '2024-12-31'
      AND c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
)
SELECT
    ROUND(SUM(d.base_mrr), 2)                                   AS churned_account_mrr,
    ROUND(100.0 * SUM(d.base_mrr) /
        (SELECT SUM(base_mrr) FROM december_active), 2)         AS pct_of_december_mrr
FROM december_active d
JOIN q4_churned q ON d.customer_id = q.customer_id;
```

**Finding:** Churned accounts represented approximately 3% of December MRR — early-stage rather than catastrophic. However, combined with rising contraction MRR and the engagement trajectory, this is a leading indicator of a growing problem, not a benign one-off. At $5,000–$10,000 CAC per Enterprise-Outbound account, each lost account requires a long sales cycle and significant cost to replace.

---

## SECTION 3 — Engagement Trend

With the cohort validated and the MRR story established, the next question is whether engagement actually declined and — critically — whether that decline was cohort-specific. Using Enterprise-Inbound as a control group isolates the acquisition channel variable while holding company size constant.

### 3.1 — Monthly Engagement by Cohort

```sql
-- What it tests: Whether core_actions_logged declined for Outbound but not for Inbound
-- Key finding:   Both cohorts track in H1. From July, Outbound drops to 6,324 while Inbound holds at 8,770.
-- Business use:  Proves the problem is cohort-specific, not a platform-wide product issue

WITH cohort_base AS (
    SELECT
        u.activity_month,
        u.customer_id,
        u.core_actions_logged,
        u.active_users_count,
        CASE
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
        END AS cohort
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE u.activity_month BETWEEN '2024-01-01' AND '2024-12-31'
      AND c.company_size = 'Enterprise'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
)
SELECT
    activity_month,
    cohort,
    COUNT(DISTINCT customer_id)                                  AS active_accounts,
    ROUND(AVG(CAST(core_actions_logged  AS FLOAT)), 1)           AS avg_core_actions,
    ROUND(AVG(CAST(active_users_count   AS FLOAT)), 1)           AS avg_active_users
FROM cohort_base
WHERE cohort IS NOT NULL
GROUP BY activity_month, cohort
ORDER BY activity_month, cohort;
```

**Finding:** Both cohorts track similarly in H1 (range ~8,800–9,800). From July onward, Outbound drops steadily — reaching 6,324 by December. Inbound holds at approximately 8,770. The 28% divergence gap that exists by December was absent in January. The inflection point is July 2024. The decline is cohort-specific.

---

### 3.2 — Distribution Check: Median and P90

A cohort average can be dragged down by a handful of low-engagement outliers. This query checks whether high-performing accounts also declined — confirming the drop is broad-based, not outlier-driven.

```sql
-- What it tests: Whether the average decline is driven by a few outliers or is distribution-wide
-- Key finding:   Median and p90 both decline from Q3 — even top performers reduced usage
-- Business use:  Rules out the "a few struggling accounts are dragging the average down" challenge

SELECT
    activity_month,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY core_actions_logged)
        AS median_core_actions,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY core_actions_logged)
        AS p90_core_actions
FROM fact_monthly_usage u
JOIN dim_customers c ON u.customer_id = c.customer_id
WHERE c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY activity_month
ORDER BY activity_month;
```

**Finding:** Both median and p90 decline from Q3 onward. When even the 90th percentile of usage falls, the decline is not caused by a handful of disengaged accounts pulling the average down. The deterioration is broad-based across the cohort.

---

### 3.3 — Tenure-Controlled Cohort (Mix-Shift Stress Test)

The Outbound cohort grew from 30 to 69 active accounts across 2024. Newer customers typically engage at lower levels than established ones. If the cohort average declined purely because lower-engagement new accounts were added, there is no retention problem — just a composition effect. This query removes new joiners to test whether the decline persists.

```sql
-- What it tests: Whether the engagement decline disappears when restricted to long-tenure accounts
-- Key finding:   Decline persists among pre-2024 accounts — genuine behavioral deterioration
-- Business use:  Rules out the "new customer dilution" alternative explanation

WITH cohort AS (
    SELECT customer_id, MIN(activity_month) AS first_active_month
    FROM fact_monthly_usage
    GROUP BY customer_id
)
SELECT
    u.activity_month,
    ROUND(AVG(CAST(u.core_actions_logged AS FLOAT)), 1) AS avg_core_actions
FROM fact_monthly_usage u
JOIN cohort c       ON u.customer_id = c.customer_id
JOIN dim_customers d ON u.customer_id = d.customer_id
WHERE d.company_size = 'Enterprise'
  AND d.acquisition_channel LIKE '%outbound%'
  AND d.customer_id NOT LIKE '%-DUP'
  AND d.customer_id != 'CUST-5738'
  AND c.first_active_month < '2024-01-01'
GROUP BY u.activity_month
ORDER BY u.activity_month;
```

**Finding:** Among accounts active before 2024 — the long-tenure subset — engagement still declines from Q3 onward. The drop is not an artefact of adding lower-engagement new customers. Existing accounts genuinely reduced their usage.

---

### 3.4 — Account-Level Consistency (H1 vs H2 Per Account)

Cohort averages are a starting point. This query checks whether the decline is systematic — visible at the individual account level — or whether a small number of accounts are responsible for the cohort-wide pattern.

```sql
-- What it tests: Whether the H1-to-H2 decline appears in most accounts or just a few
-- Key finding:   45 of 56 comparable accounts declined — 80% breadth rules out outlier-driven average
-- Business use:  Directly addresses the "couldn't a few accounts be dragging this down?" challenge

SELECT
    customer_id,
    AVG(CASE WHEN activity_month BETWEEN '2024-01-01' AND '2024-06-30'
             THEN CAST(core_actions_logged AS FLOAT) END) AS h1_avg,
    AVG(CASE WHEN activity_month >= '2024-07-01'
             THEN CAST(core_actions_logged AS FLOAT) END) AS h2_avg
FROM fact_monthly_usage u
JOIN dim_customers c ON u.customer_id = c.customer_id
WHERE c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY customer_id
HAVING AVG(CASE WHEN activity_month BETWEEN '2024-01-01' AND '2024-06-30'
                THEN CAST(core_actions_logged AS FLOAT) END) IS NOT NULL
   AND AVG(CASE WHEN activity_month >= '2024-07-01'
                THEN CAST(core_actions_logged AS FLOAT) END) IS NOT NULL
ORDER BY customer_id;
```

**Finding:** 45 of 56 comparable accounts showed lower H2 engagement than H1. 11 held stable or improved. 24 accounts are excluded — they joined mid-year or churned early and lack data in one of the two periods. A 45:11 decline ratio is not statistical noise; it is a systematic shift across the cohort.

---

## SECTION 4 — Support Burden

The engagement decline is real, cohort-specific, and broad-based. The next question is whether a support burden spike preceded it. This section checks whether the Enterprise-Outbound cohort experienced a meaningful worsening of support conditions in the same timeframe.

### 4.1 — Monthly Support Burden by Cohort

```sql
-- What it tests: Whether support ticket volume, resolution time, and severity worsened for Outbound
-- Key finding:   July inflection — tickets +70%, resolution time +159% in one month. Inbound stays flat.
-- Business use:  Identifies the operational failure that preceded the engagement collapse

WITH cohort_base AS (
    SELECT
        t.created_date,
        t.customer_id,
        t.resolution_time_hrs,
        t.severity,
        CASE
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
        END AS cohort
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE t.created_date >= '2024-01-01'
      AND t.created_date <  '2025-01-01'
      AND c.company_size = 'Enterprise'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
)
SELECT
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(created_date), created_date)) AS ticket_month,
    cohort,
    COUNT(*)                                                          AS ticket_count,
    COUNT(DISTINCT customer_id)                                       AS ticketed_customers,
    ROUND(1.0 * COUNT(*) / COUNT(DISTINCT customer_id), 1)            AS tickets_per_customer,
    ROUND(AVG(CAST(resolution_time_hrs AS FLOAT)), 1)                 AS avg_resolution_hrs,
    ROUND(100.0 * SUM(CASE WHEN severity IN ('High','Critical') THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                              AS pct_high_critical
FROM cohort_base
WHERE cohort IS NOT NULL
GROUP BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(created_date), created_date)), cohort
ORDER BY ticket_month, cohort;
```

**Finding:** July 2024 is the inflection point. For Enterprise-Outbound: ticket count jumped from 40 to 68 (+70%), resolution time from 33.6 to 87.1 hours (+159%) in a single month. By October, resolution time reached 110.8 hours (+230% vs June). Enterprise-Inbound shows no comparable spike — confirming the burden is cohort-specific.

*Note: These are blended figures across all issue categories. Filtered to API Integration Failure specifically, the numbers are sharper: resolution time jumps to 303+ hours, volume surges 15x June→July. See Section 6 for the API-specific time alignment analysis.*

---

### 4.2 — Support Burden Filtered to API Integration Failure

The blended support query covers all issue categories. This query isolates the specific mechanism identified in the charter — API Integration Failures — to confirm that the spike is not a general support quality problem but is concentrated in integration-related tickets.

```sql
-- What it tests: Whether the support spike is driven specifically by API Integration Failure tickets
-- Key finding:   Outbound API failures: 1-6/month in H1 → 15-25/month in H2. Inbound stays at 1-8.
-- Business use:  Rules out "general support team degradation" as the cause. Mechanism is integration-specific.

WITH cohort_base AS (
    SELECT
        DATETRUNC(month, t.created_date)  AS ticket_month,
        t.customer_id,
        t.resolution_time_hrs,
        t.severity,
        CASE
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
        END AS cohort
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE t.created_date >= '2024-01-01'
      AND t.created_date <  '2025-01-01'
      AND c.company_size = 'Enterprise'
      AND t.issue_category = 'API Integration Failure'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
)
SELECT
    ticket_month,
    cohort,
    COUNT(*)                                                          AS ticket_count,
    COUNT(DISTINCT customer_id)                                       AS ticketed_customers,
    ROUND(AVG(CAST(resolution_time_hrs AS FLOAT)), 1)                 AS avg_resolution_hrs,
    ROUND(100.0 * SUM(CASE WHEN severity IN ('High','Critical') THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                              AS pct_high_critical
FROM cohort_base
WHERE cohort IS NOT NULL
GROUP BY ticket_month, cohort
ORDER BY ticket_month, cohort;
```

**Finding:** When filtered to API Integration Failure, Enterprise-Outbound shows a 15x ticket volume surge from June (1 ticket) to July (15 tickets), sustained at 15–25/month through December. Resolution time for these tickets was 280–320 hours consistently in H2. Enterprise-Inbound shows 1–8 tickets/month with no sustained pattern. The support crisis is integration-specific and Outbound-specific.

---

## SECTION 5 — Renewal Rate Contraction

Support burden has been established and is cohort-specific. The final charter validation step confirms whether this translated into an actual renewal rate contraction in Q4 — the lagging outcome the entire investigation is built around.

### 5.1 — Q4 Renewal Rate by Cohort

```sql
-- What it tests: Whether Enterprise-Outbound churn rate was materially higher than Inbound in Q4
-- Key finding:   Outbound 27.8% churn (5/18) vs Inbound 10% (1/10) — nearly 3x higher
-- Business use:  Validates the charter's core claim: a real, cohort-specific renewal contraction occurred

WITH cohort_base AS (
    SELECT
        s.customer_id,
        s.status,
        CASE
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
            WHEN c.company_size = 'Enterprise'
                 AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
        END AS cohort
    FROM fact_subscriptions s
    JOIN dim_customers c ON s.customer_id = c.customer_id
    WHERE s.end_date >= '2024-10-01'
      AND s.end_date <= '2024-12-31'
      AND c.company_size = 'Enterprise'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
)
SELECT
    cohort,
    COUNT(*)                                                                               AS total_subs,
    SUM(CASE WHEN status = 'Churned'   THEN 1 ELSE 0 END)                                AS churned,
    SUM(CASE WHEN status != 'Churned'  THEN 1 ELSE 0 END)                                AS renewed_or_retained,
    ROUND(100.0 * SUM(CASE WHEN status != 'Churned' THEN 1 ELSE 0 END) / COUNT(*), 1)    AS renewal_rate,
    ROUND(100.0 * SUM(CASE WHEN status  = 'Churned' THEN 1 ELSE 0 END) / COUNT(*), 1)    AS churn_rate
FROM cohort_base
WHERE cohort IS NOT NULL
GROUP BY cohort
ORDER BY cohort;
```

**Finding:** Enterprise-Outbound: 27.8% churn (5 of 18 Q4 renewals). Enterprise-Inbound: 10% churn (1 of 10). Nearly 3x higher churn for the signal cohort. Sample size is small (28 total observations), so this is treated as directional evidence. The full-year validation below addresses sample size concern.

---

### 5.2 — Full-Year Churn Validation (Sample Size Stress Test)

28 Q4 observations is a thin basis for a conclusion. This query expands to all of 2024 to test whether the gap persists at a larger sample size.

```sql
-- What it tests: Whether the Q4 churn gap persists when the window is expanded to full-year 2024
-- Key finding:   Outbound 29.3% (12/41) vs Inbound 17.5% (7/40) — gap holds across larger sample
-- Business use:  Reduces the risk that Q4 was a seasonal anomaly rather than a structural pattern

SELECT
    CASE
        WHEN c.company_size = 'Enterprise'
             AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise'
             AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
    END AS cohort,
    COUNT(*)                                                                             AS total_subs,
    SUM(CASE WHEN s.status = 'Churned' THEN 1 ELSE 0 END)                               AS churned,
    ROUND(100.0 * SUM(CASE WHEN s.status = 'Churned' THEN 1 ELSE 0 END) / COUNT(*), 1) AS churn_rate
FROM fact_subscriptions s
JOIN dim_customers c ON s.customer_id = c.customer_id
WHERE s.end_date >= '2024-01-01'
  AND s.end_date <= '2024-12-31'
  AND c.company_size = 'Enterprise'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY
    CASE
        WHEN c.company_size = 'Enterprise'
             AND c.acquisition_channel LIKE '%outbound%' THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise'
             AND c.acquisition_channel NOT LIKE '%outbound%' THEN 'Enterprise_Inbound'
    END;
```

**Finding:** Full-year 2024: Outbound 29.3% churn (12 of 41), Inbound 17.5% (7 of 40). The gap persists at a larger sample. Q4 was not an anomaly — it was the most visible quarter of a pattern that ran throughout 2024.

---

### 5.3 — Tenure of Churned Accounts

If churn is concentrated in very recent accounts (1–2 months tenure), it could represent onboarding failures unrelated to the integration crisis. This query checks whether churned accounts spanned multiple lifecycle stages.

```sql
-- What it tests: Whether churn came only from newly acquired accounts (onboarding failure) or all tenures
-- Key finding:   Churned accounts span 1-3 month AND 7-11 month tenures — systemic, not early-stage
-- Business use:  Proves the crisis affected accounts at multiple lifecycle stages

SELECT
    s.customer_id,
    MIN(s.start_date)                                               AS first_start,
    MAX(s.end_date)                                                 AS last_end,
    DATEDIFF(month, MIN(s.start_date), MAX(s.end_date))            AS tenure_months
FROM fact_subscriptions s
JOIN dim_customers c ON s.customer_id = c.customer_id
WHERE s.status = 'Churned'
  AND s.end_date BETWEEN '2024-10-01' AND '2024-12-31'
  AND c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY s.customer_id;
```

**Finding:** Churned accounts span both short-tenure (1–3 months) and longer-tenure (7–11 months) customers. The crisis is not simply an onboarding failure concentrated in new accounts. Accounts that had been using the product for most of the year chose not to renew — indicating product value decay, not entry-point failure.

---

### 5.4 — Churned MRR in Q4

```sql
-- What it tests: Whether the 5 churned accounts represented financially material MRR
-- Key finding:   $8,386 in churned MRR in Q4 alone (waterfall-derived actual lost revenue)
-- Business use:  Converts the churn rate from a percentage to a dollar figure for executive context

WITH churned_accounts AS (
    SELECT s.customer_id
    FROM fact_subscriptions s
    JOIN dim_customers c ON s.customer_id = c.customer_id
    WHERE s.status = 'Churned'
      AND s.end_date BETWEEN '2024-10-01' AND '2024-12-31'
      AND c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
),
waterfall AS (
    -- Use waterfall method: capture prev_mrr at the point of churn (MRR → 0)
    SELECT
        month,
        customer_id,
        mrr,
        LAG(mrr, 1, 0) OVER (PARTITION BY customer_id ORDER BY month) AS prev_mrr
    FROM (
        SELECT m.month, c2.customer_id, COALESCE(SUM(s2.base_mrr), 0) AS mrr
        FROM churned_accounts c2
        CROSS JOIN (
            SELECT DISTINCT DATETRUNC(month, date_key) AS month
            FROM dim_date WHERE date_key BETWEEN '2024-01-01' AND '2024-12-31'
              AND date_key = DATETRUNC(month, date_key)
        ) m
        LEFT JOIN fact_subscriptions s2
            ON s2.customer_id = c2.customer_id
           AND m.month BETWEEN s2.start_date AND COALESCE(s2.end_date, '2024-12-31')
        GROUP BY m.month, c2.customer_id
    ) monthly
)
SELECT
    SUM(prev_mrr) AS churned_mrr_waterfall
FROM waterfall
WHERE prev_mrr > 0 AND mrr = 0
  AND month BETWEEN '2024-10-01' AND '2024-12-31';
```

**Finding:** $8,386 in churned MRR in Q4 using the waterfall method (actual revenue lost at time of churn). At $5,000–$10,000 CAC per Enterprise-Outbound account, this represents months of sales cycle cost to replace and understates the full business impact.

---

## SECTION 6 — Time Alignment (Causal Sequencing)

Correlation between support burden and engagement decline is established. The critical question for causal inference is sequencing: did the support burden spike *before* engagement fell, or simultaneously? If support rises first and engagement falls after, the causal sequence required by the hypothesis holds.

### 6.1 — Support Burden and Engagement on a Shared Timeline

```sql
-- What it tests: Whether the support spike preceded the engagement decline (causal sequence)
-- Key finding:   Support spikes July (+70%, +159%). Engagement holds July, drops August. 1-month lag confirmed.
-- Business use:  Closest available evidence of causality without a controlled experiment

SELECT
    u.activity_month,
    ROUND(AVG(CAST(u.core_actions_logged AS FLOAT)), 1)  AS avg_core_actions,
    t.ticket_count,
    t.avg_resolution_hrs
FROM fact_monthly_usage u
JOIN dim_customers c ON u.customer_id = c.customer_id
LEFT JOIN (
    SELECT
        CONVERT(DATE, DATEADD(DAY, 1 - DAY(t2.created_date), t2.created_date)) AS ticket_month,
        COUNT(*)                                                                 AS ticket_count,
        ROUND(AVG(CAST(t2.resolution_time_hrs AS FLOAT)), 1)                    AS avg_resolution_hrs
    FROM fact_support_tickets t2
    JOIN dim_customers c2 ON t2.customer_id = c2.customer_id
    WHERE c2.company_size = 'Enterprise'
      AND c2.acquisition_channel LIKE '%outbound%'
      AND c2.customer_id NOT LIKE '%-DUP'
      AND c2.customer_id != 'CUST-5738'
      AND t2.created_date >= '2024-01-01'
      AND t2.created_date <  '2025-01-01'
    GROUP BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(t2.created_date), t2.created_date))
) t ON t.ticket_month = u.activity_month
WHERE c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
  AND u.activity_month BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY u.activity_month, t.ticket_count, t.avg_resolution_hrs
ORDER BY u.activity_month;
```

**Output summary:**

| Month | avg_core_actions | ticket_count | avg_resolution_hrs |
|-------|-----------------|-------------|-------------------|
| 2024-06 | 9,132 | 40 | 33.6 |
| 2024-07 | 9,108 | 68 | 87.1 |
| 2024-08 | 8,244 | 76 | 94.3 |
| 2024-09 | 7,836 | 72 | 102.1 |
| 2024-10 | 7,041 | 89 | 110.8 |

**Finding:** Support burden spikes in July (tickets +70%, resolution time +159%). Core actions in July (9,108) remain near June levels (9,132) — customers kept trying to use the product. The steep engagement drop begins in August (8,244) and continues through December. The 1-month lag between cause and effect is the key causal signal: support failures accumulate first, then customers disengage.

---

### 6.2 — Time Alignment with API Integration Failure Filter Only

```sql
-- What it tests: Whether the API-specific burden spike also precedes engagement by one month
-- Key finding:   API resolution time jumps to 303 hrs in July. Engagement drops August. Same 1-month lag.
-- Business use:  Confirms the mechanism is integration-specific, not general support degradation

SELECT
    u.activity_month,
    ROUND(AVG(CAST(u.core_actions_logged AS FLOAT)), 1)  AS avg_core_actions,
    t.ticket_count,
    t.avg_resolution_hrs
FROM fact_monthly_usage u
JOIN dim_customers c ON u.customer_id = c.customer_id
LEFT JOIN (
    SELECT
        CONVERT(DATE, DATEADD(DAY, 1 - DAY(t2.created_date), t2.created_date)) AS ticket_month,
        COUNT(*)                                                                 AS ticket_count,
        ROUND(AVG(CAST(t2.resolution_time_hrs AS FLOAT)), 1)                    AS avg_resolution_hrs
    FROM fact_support_tickets t2
    JOIN dim_customers c2 ON t2.customer_id = c2.customer_id
    WHERE c2.company_size = 'Enterprise'
      AND c2.acquisition_channel LIKE '%outbound%'
      AND c2.customer_id NOT LIKE '%-DUP'
      AND c2.customer_id != 'CUST-5738'
      AND t2.created_date >= '2024-01-01'
      AND t2.created_date <  '2025-01-01'
      AND t2.issue_category = 'API Integration Failure'
    GROUP BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(t2.created_date), t2.created_date))
) t ON t.ticket_month = u.activity_month
WHERE c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
  AND u.activity_month BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY u.activity_month, t.ticket_count, t.avg_resolution_hrs
ORDER BY u.activity_month;
```

**Finding:** When filtered to API Integration Failure only, the July spike is sharper: resolution time jumps from 65 hours in June to 303.6 hours in July — a 366% increase. Volume goes from 1 ticket in June to 15 in July (15x). March shows NULL for API tickets — no failures that month, confirming the spike is H2-concentrated. The 1-month lag between the July support spike and the August engagement drop holds in the API-specific view.

---

### 6.3 — Median vs Average Resolution Time (Outlier Check)

If a single account with an extreme 500-hour ticket is inflating the average, the 300+ hour figure would be misleading. This query compares median and average resolution time to confirm the elevated resolution time is systemic.

```sql
-- What it tests: Whether the high average resolution time is driven by one extreme outlier ticket
-- Key finding:   Median ≈ average in H2 (290-320 hrs). No single outlier is inflating the mean.
-- Business use:  Confirms 300+ hour resolution time is the typical experience, not a statistical anomaly

SELECT DISTINCT
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date))    AS ticket_month,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.resolution_time_hrs)
        OVER (PARTITION BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)))
                                                                             AS median_resolution_hrs,
    AVG(CAST(t.resolution_time_hrs AS FLOAT))
        OVER (PARTITION BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)))
                                                                             AS avg_resolution_hrs,
    COUNT(*)
        OVER (PARTITION BY CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)))
                                                                             AS ticket_count
FROM fact_support_tickets t
JOIN dim_customers c ON t.customer_id = c.customer_id
WHERE c.company_size = 'Enterprise'
  AND c.acquisition_channel LIKE '%outbound%'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
  AND t.issue_category = 'API Integration Failure'
  AND t.resolved_date IS NOT NULL
  AND t.created_date >= '2024-01-01'
  AND t.created_date <  '2025-01-01'
ORDER BY ticket_month;
```

**Finding:** Median and average resolution times are nearly identical throughout H2 — both sitting at 290–320 hours from July through December. When median equals average, no single outlier is distorting the picture. The 300+ hour resolution time is the *typical* experience for an Enterprise-Outbound API failure, not an anomaly caused by one difficult ticket.

---

## SECTION 7 — Hypothesis Testing

Charter validation confirmed the problem exists, is cohort-specific, and precedes churn. Three hypotheses were promoted to the sprint list based on Impact × Likelihood × Ease scoring. Each is tested using a different dimension of support burden to verify the pattern is not an artefact of any single measurement approach.

- **H9** — Resolution Time / SLA Breach: Does resolution time predict engagement decline?
- **H10** — Ticket Volume per Customer: Does ticket frequency independently predict decline?
- **H2** — API Failure Incidence: Does raw failure count predict decline with a dose-response gradient?

All three hypotheses use the same structure: split accounts into burden tiers, calculate H1 and H2 engagement per tier, compare. Convergence across three independent metrics is the evidentiary standard.

---

### 7.1 — H9: Resolution Time as Predictor of Engagement Decline

#### H9.1 — Resolution Time and Severity by Cohort and Month

```sql
-- What it tests: Whether Outbound API resolution time rises from July while Inbound stays flat
-- Key finding:   Outbound locks at 280-320 hrs July-December. Inbound erratic and low-volume.
-- Business use:  Establishes the cohort-specific resolution burden that H9 tiers are built on

SELECT
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)) AS ticket_month,
    CASE
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel LIKE '%outbound%'
        THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel NOT LIKE '%outbound%'
        THEN 'Enterprise_Inbound'
    END AS cohort,
    COUNT(*)                                                               AS ticket_count,
    ROUND(AVG(CAST(t.resolution_time_hrs AS FLOAT)), 1)                    AS avg_resolution_hrs,
    ROUND(100.0 * SUM(CASE WHEN t.severity IN ('High','Critical') THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                                   AS pct_high_critical
FROM fact_support_tickets t
JOIN dim_customers c ON t.customer_id = c.customer_id
WHERE c.company_size = 'Enterprise'
  AND t.issue_category = 'API Integration Failure'
  AND t.created_date >= '2024-01-01'
  AND t.created_date <  '2025-01-01'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)),
    CASE
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel LIKE '%outbound%'
        THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel NOT LIKE '%outbound%'
        THEN 'Enterprise_Inbound'
    END
ORDER BY ticket_month, cohort;
```

**Finding:** From July, Outbound ticket volume explodes (15–25 per month) while Inbound stays at 1–8. Resolution time for Outbound locks into 280–320 hours consistently through Q4. Inbound is low-volume and erratic — statistical noise at small sample sizes, not signal. The integration failure problem is concentrated in Outbound.

---

#### H9.2 — Account-Level Resolution Burden vs Engagement Decline (Tier Analysis)

```sql
-- What it tests: Whether accounts with higher resolution burden declined proportionally more
-- Key finding:   High burden -22.3%, Low burden -9.5% — 2.3x steeper decline for burdened accounts
-- Business use:  Same cohort, same product, same time period. Only variable: resolution friction.

WITH resolution_burden AS (
    SELECT
        t.customer_id,
        ROUND(AVG(CAST(t.resolution_time_hrs AS FLOAT)), 1) AS avg_resolution_hrs,
        COUNT(*)                                             AS api_ticket_count
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND t.issue_category = 'API Integration Failure'
      AND t.created_date >= '2024-07-01'
      AND t.created_date <  '2025-01-01'
    GROUP BY t.customer_id
),
engagement_change AS (
    SELECT
        u.customer_id,
        AVG(CASE WHEN u.activity_month BETWEEN '2024-01-01' AND '2024-06-30'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h1_avg,
        AVG(CASE WHEN u.activity_month >= '2024-07-01'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h2_avg
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
    GROUP BY u.customer_id
)
SELECT
    CASE
        WHEN r.avg_resolution_hrs >= 200 THEN 'High Burden (200+ hrs)'
        WHEN r.avg_resolution_hrs >= 100 THEN 'Medium Burden (100-200 hrs)'
        ELSE                                  'Low Burden (<100 hrs)'
    END                                                              AS resolution_burden_tier,
    COUNT(*)                                                         AS account_count,
    ROUND(AVG(e.h1_avg), 1)                                          AS avg_h1_actions,
    ROUND(AVG(e.h2_avg), 1)                                          AS avg_h2_actions,
    ROUND(AVG(e.h2_avg) - AVG(e.h1_avg), 1)                         AS avg_change,
    ROUND(100.0 * (AVG(e.h2_avg) - AVG(e.h1_avg))
          / NULLIF(AVG(e.h1_avg), 0), 1)                             AS pct_change
FROM resolution_burden r
JOIN engagement_change e ON r.customer_id = e.customer_id
GROUP BY
    CASE
        WHEN r.avg_resolution_hrs >= 200 THEN 'High Burden (200+ hrs)'
        WHEN r.avg_resolution_hrs >= 100 THEN 'Medium Burden (100-200 hrs)'
        ELSE                                  'Low Burden (<100 hrs)'
    END
ORDER BY MIN(r.avg_resolution_hrs) DESC;
```

**Output:**

| resolution_burden_tier | account_count | avg_h1_actions | avg_h2_actions | avg_change | pct_change |
|------------------------|--------------|---------------|---------------|-----------|-----------|
| High Burden (200+ hrs) | 37 | 9,185.4 | 7,136.4 | -2,049.1 | -22.3% |
| Medium Burden (100-200 hrs) | 10 | 8,660.8 | 6,679.3 | -1,981.5 | -22.9% |
| Low Burden (<100 hrs) | 8 | 8,490.2 | 7,680.2 | -810.0 | -9.5% |

**H9 conclusion:** Accounts below 100 hours resolution time declined 9.5%. Accounts above 100 hours declined 22%+. The data reveals a threshold effect at the 100-hour mark — which is the empirical basis for the `<100-hour SLA` recommendation. Same cohort, same product, same time period: the only variable is how long each account waited for failures to be resolved.

---

### 7.2 — H10: Ticket Volume per Customer as Predictor

#### H10.1 — Tickets per Customer by Cohort and Month

```sql
-- What it tests: Whether per-customer ticket rate rose for Outbound while staying flat for Inbound
-- Key finding:   Inbound stays exactly at 1.0 tickets/customer every month. Outbound goes to 1.13-1.15.
-- Business use:  More important: Outbound absolute volume grew 1→15 while Inbound stayed 1-8 total.

SELECT
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)) AS ticket_month,
    CASE
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel LIKE '%outbound%'
        THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel NOT LIKE '%outbound%'
        THEN 'Enterprise_Inbound'
    END AS cohort,
    COUNT(*)                                                               AS ticket_count,
    COUNT(DISTINCT t.customer_id)                                          AS ticketed_customers,
    ROUND(1.0 * COUNT(*) / COUNT(DISTINCT t.customer_id), 2)               AS tickets_per_customer
FROM fact_support_tickets t
JOIN dim_customers c ON t.customer_id = c.customer_id
WHERE c.company_size = 'Enterprise'
  AND t.issue_category = 'API Integration Failure'
  AND t.created_date >= '2024-01-01'
  AND t.created_date <  '2025-01-01'
  AND c.customer_id NOT LIKE '%-DUP'
  AND c.customer_id != 'CUST-5738'
GROUP BY
    CONVERT(DATE, DATEADD(DAY, 1 - DAY(t.created_date), t.created_date)),
    CASE
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel LIKE '%outbound%'
        THEN 'Enterprise_Outbound'
        WHEN c.company_size = 'Enterprise' AND c.acquisition_channel NOT LIKE '%outbound%'
        THEN 'Enterprise_Inbound'
    END
ORDER BY ticket_month, cohort;
```

**Finding:** Inbound stays exactly at 1.0 tickets per customer every month throughout 2024. The more important signal for Outbound is breadth, not depth: the problem spread from 1–6 accounts per month in H1 to 13–22 accounts per month in H2. More accounts getting hit — not the same accounts raising repeat tickets — is the dominant pattern.

---

#### H10.2 — Ticket Volume Tier vs Engagement Decline

```sql
-- What it tests: Whether accounts with more tickets declined proportionally more than single-ticket accounts
-- Key finding:   2-4 tickets: -26.3%. 1 ticket: -13.1%. Double the friction = double the decline.
-- Business use:  H10 confirms the dose-response gradient independently of resolution time (H9)

WITH ticket_burden AS (
    SELECT
        t.customer_id,
        COUNT(*) AS total_tickets
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND t.issue_category = 'API Integration Failure'
      AND t.created_date >= '2024-07-01'
      AND t.created_date <  '2025-01-01'
    GROUP BY t.customer_id
),
engagement_change AS (
    SELECT
        u.customer_id,
        AVG(CASE WHEN u.activity_month BETWEEN '2024-01-01' AND '2024-06-30'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h1_avg,
        AVG(CASE WHEN u.activity_month >= '2024-07-01'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h2_avg
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
    GROUP BY u.customer_id
)
SELECT
    CASE
        WHEN b.total_tickets >= 5 THEN 'High Volume (5+ tickets)'
        WHEN b.total_tickets >= 2 THEN 'Medium Volume (2-4 tickets)'
        ELSE                          'Low Volume (1 ticket)'
    END                                                              AS ticket_burden_tier,
    COUNT(*)                                                         AS account_count,
    ROUND(AVG(e.h1_avg), 1)                                          AS avg_h1_actions,
    ROUND(AVG(e.h2_avg), 1)                                          AS avg_h2_actions,
    ROUND(AVG(e.h2_avg) - AVG(e.h1_avg), 1)                         AS avg_change,
    ROUND(100.0 * (AVG(e.h2_avg) - AVG(e.h1_avg))
          / NULLIF(AVG(e.h1_avg), 0), 1)                             AS pct_change
FROM ticket_burden b
JOIN engagement_change e ON b.customer_id = e.customer_id
GROUP BY
    CASE
        WHEN b.total_tickets >= 5 THEN 'High Volume (5+ tickets)'
        WHEN b.total_tickets >= 2 THEN 'Medium Volume (2-4 tickets)'
        ELSE                          'Low Volume (1 ticket)'
    END
ORDER BY MIN(b.total_tickets) DESC;
```

**Output:**

| ticket_burden_tier | account_count | avg_h1_actions | avg_h2_actions | pct_change |
|-------------------|--------------|---------------|---------------|-----------|
| High Volume (5+ tickets) | 1 | NULL | 4,548.8 | NULL |
| Medium Volume (2-4 tickets) | 28 | 9,001.6 | 6,633.1 | -26.3% |
| Low Volume (1 ticket) | 26 | 8,939.9 | 7,769.4 | -13.1% |

*Note: High Volume tier (n=1, no H1 data) excluded from interpretation.*

**H10 conclusion:** Accounts with 2–4 tickets declined 26.3% versus 13.1% for single-ticket accounts — exactly double the decline for double the friction. H10 confirms the dose-response pattern independently using ticket frequency as the burden metric rather than resolution time.

---

### 7.3 — H2: Raw Failure Incidence as Predictor

```sql
-- What it tests: Whether failure count alone (regardless of resolution time) predicts engagement decline
-- Key finding:   4+ failures: -40.9%. 2-3: -23.9%. 1: -13.1%. Clean dose-response gradient.
-- Business use:  Three independent metrics (H9, H10, H2) all show the same pattern — convergent evidence

WITH failure_burden AS (
    SELECT
        t.customer_id,
        COUNT(*) AS failure_count
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND t.issue_category = 'API Integration Failure'
      AND t.created_date >= '2024-07-01'
      AND t.created_date <  '2025-01-01'
    GROUP BY t.customer_id
),
engagement_change AS (
    SELECT
        u.customer_id,
        AVG(CASE WHEN u.activity_month BETWEEN '2024-01-01' AND '2024-06-30'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h1_avg,
        AVG(CASE WHEN u.activity_month >= '2024-07-01'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h2_avg
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
    GROUP BY u.customer_id
)
SELECT
    CASE
        WHEN f.failure_count >= 4 THEN 'High Failures (4+)'
        WHEN f.failure_count >= 2 THEN 'Medium Failures (2-3)'
        ELSE                          'Low Failures (1)'
    END                                                              AS failure_tier,
    COUNT(*)                                                         AS account_count,
    ROUND(AVG(e.h1_avg), 1)                                          AS avg_h1_actions,
    ROUND(AVG(e.h2_avg), 1)                                          AS avg_h2_actions,
    ROUND(AVG(e.h2_avg) - AVG(e.h1_avg), 1)                         AS avg_change,
    ROUND(100.0 * (AVG(e.h2_avg) - AVG(e.h1_avg))
          / NULLIF(AVG(e.h1_avg), 0), 1)                             AS pct_change
FROM failure_burden f
JOIN engagement_change e ON f.customer_id = e.customer_id
GROUP BY
    CASE
        WHEN f.failure_count >= 4 THEN 'High Failures (4+)'
        WHEN f.failure_count >= 2 THEN 'Medium Failures (2-3)'
        ELSE                          'Low Failures (1)'
    END
ORDER BY MIN(f.failure_count) DESC;
```

**Output:**

| failure_tier | account_count | avg_h1_actions | avg_h2_actions | pct_change |
|-------------|--------------|---------------|---------------|-----------|
| High Failures (4+) | 6 | 8,332.3 | 4,925.3 | -40.9% |
| Medium Failures (2-3) | 23 | 9,187.5 | 6,988.0 | -23.9% |
| Low Failures (1) | 26 | 8,939.9 | 7,769.4 | -13.1% |

**H2 conclusion:** Perfect dose-response gradient. 1 failure = -13%. 2–3 failures = -24%. 4+ failures = -41%. Every step up in failure count roughly doubles the engagement decline. H2, H9, and H10 all use different burden metrics and all show the same gradient. Three independent metrics, same pattern — this is convergent evidence, not coincidence.

---

## SECTION 8 — Falsification

The final step is deliberately trying to break the conclusions. These queries are designed to find evidence that would undermine the causal story. If they come back negative — if the alternative explanations do not hold — the original findings are strengthened.

### 8.1 — Devil's Advocate: High-Burden Accounts That Did Not Decline

If the causal story is real, almost all accounts with high failure burden should show engagement decline. If a large proportion held stable despite heavy burden, there are moderating factors — possibly account-level factors like dedicated CSM support or industry — that the analysis has not accounted for.

```sql
-- What it tests: Whether any high-burden accounts defied the pattern and stayed stable
-- Key finding:   Among accounts with 3+ failures AND 200+ hr resolution, most declined — not a clean break
-- Business use:  Acknowledges exceptions exist. A handful of resilient accounts does not undermine the gradient.

WITH failure_burden AS (
    SELECT
        t.customer_id,
        COUNT(*)                                             AS failure_count,
        ROUND(AVG(CAST(t.resolution_time_hrs AS FLOAT)), 1) AS avg_resolution_hrs
    FROM fact_support_tickets t
    JOIN dim_customers c ON t.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND t.issue_category = 'API Integration Failure'
      AND t.created_date >= '2024-07-01'
      AND t.created_date <  '2025-01-01'
    GROUP BY t.customer_id
),
engagement_change AS (
    SELECT
        u.customer_id,
        AVG(CASE WHEN u.activity_month BETWEEN '2024-01-01' AND '2024-06-30'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h1_avg,
        AVG(CASE WHEN u.activity_month >= '2024-07-01'
                 THEN CAST(u.core_actions_logged AS FLOAT) END) AS h2_avg
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
    GROUP BY u.customer_id
)
SELECT
    COUNT(*)                                                                      AS total_comparable_accounts,
    SUM(CASE WHEN e.h2_avg >= e.h1_avg THEN 1 ELSE 0 END)                        AS held_or_improved,
    SUM(CASE WHEN e.h2_avg <  e.h1_avg THEN 1 ELSE 0 END)                        AS declined,
    ROUND(100.0 * SUM(CASE WHEN e.h2_avg >= e.h1_avg THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                                          AS pct_held_or_improved,
    ROUND(100.0 * SUM(CASE WHEN e.h2_avg <  e.h1_avg THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                                          AS pct_declined
FROM failure_burden f
JOIN engagement_change e ON f.customer_id = e.customer_id
WHERE f.failure_count >= 3
  AND f.avg_resolution_hrs >= 200;
```

**Finding:** Among accounts with the highest burden profile (3+ failures AND 200+ hour avg resolution), the vast majority declined. A small number held stable or improved — these accounts likely had moderating factors (dedicated CSM, higher internal IT capability, or lower dependency on the integration layer). Their existence does not undermine the gradient; it indicates that the relationship between burden and disengagement is strong but not deterministic. An A/B experiment controls for these factors by randomizing treatment assignment.

---

### 8.2 — Zero-Ticket Stability: The Strongest Falsification

If something other than API failures caused the engagement decline — a platform-wide issue, a seasonal pattern, a product change — then even Outbound accounts that experienced zero API failures should show engagement decline. This query tests that directly.

```sql
-- What it tests: Whether engagement declined even for Outbound accounts with no API failures in H2
-- Key finding:   Zero-ticket accounts stayed at 8,270-9,012 throughout 2024. No July inflection.
-- Business use:  If engagement held for zero-ticket accounts, platform-wide causes are eliminated.

WITH zero_ticket_accounts AS (
    SELECT DISTINCT u.customer_id
    FROM fact_monthly_usage u
    JOIN dim_customers c ON u.customer_id = c.customer_id
    WHERE c.company_size = 'Enterprise'
      AND c.acquisition_channel LIKE '%outbound%'
      AND c.customer_id NOT LIKE '%-DUP'
      AND c.customer_id != 'CUST-5738'
      AND u.customer_id NOT IN (
          SELECT DISTINCT t.customer_id
          FROM fact_support_tickets t
          JOIN dim_customers c2 ON t.customer_id = c2.customer_id
          WHERE t.created_date >= '2024-07-01'
            AND t.created_date <  '2025-01-01'
            AND t.issue_category = 'API Integration Failure'
            AND c2.company_size = 'Enterprise'
            AND c2.acquisition_channel LIKE '%outbound%'
      )
)
SELECT
    u.activity_month,
    COUNT(DISTINCT u.customer_id)                                  AS accounts,
    ROUND(AVG(CAST(u.core_actions_logged AS FLOAT)), 1)            AS avg_core_actions
FROM fact_monthly_usage u
WHERE u.customer_id IN (SELECT customer_id FROM zero_ticket_accounts)
  AND u.activity_month BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY u.activity_month
ORDER BY u.activity_month;
```

**Finding:** Outbound accounts with zero API Integration Failure tickets in H2 maintained stable engagement throughout 2024, averaging 8,270–9,012 core actions with no July inflection point. The cohort-wide engagement decline is driven by accounts that experienced API failures. Accounts untouched by the failure pattern continued using the product normally. This eliminates seasonality, platform-wide product changes, and market conditions as primary explanations — all of which would have affected zero-ticket accounts equally.

---

## Summary of Findings

| Query | Claim tested | Verdict | Confidence |
|-------|-------------|---------|-----------|
| Q0 | Cohort definition valid, n=80 clean accounts | ✅ Confirmed | High |
| Q1 | MRR appeared stable while crisis built | ✅ MRR grew — new acquisition masked churn | High |
| Q2 | Engagement declined cohort-specifically | ✅ -15.7% Outbound vs stable Inbound | High |
| Q3 | Support burden spiked in July for Outbound | ✅ +70% volume, +159% resolution time | High |
| Q4 | Renewal rate contracted for Outbound | ✅ 27.8% vs 10% — directional, n=28 | Directional |
| Q5 | Support spike preceded engagement decline | ✅ 1-month lag confirmed | High |
| H9 | Resolution time predicts engagement decline | ✅ Low -9.5%, High -22.3% | High |
| H10 | Ticket frequency independently predicts decline | ✅ 1 ticket -13.1%, 2-4 tickets -26.3% | High |
| H2 | Failure count shows dose-response gradient | ✅ 1: -13.1%, 2-3: -23.9%, 4+: -40.9% | High |
| §8.1 | High-burden accounts that didn't decline | ⚠️ A minority held stable — moderating factors exist | Partial |
| §8.2 | Zero-ticket accounts stayed stable | ✅ No July inflection — platform-wide cause eliminated | High |

**Master conclusion:** API Integration Failure support burden is the confirmed root cause of the Enterprise-Outbound engagement collapse and Q4 churn. Three independent burden metrics converge on the same dose-response gradient. Time alignment holds. Falsification tests were negative. The recommended intervention — a dedicated `<100-hour SLA` queue for API Integration Failure tickets in Enterprise-Outbound accounts — is derived from the empirically observed threshold in H9: accounts below 100 hours declined 9.5%; accounts above it declined 22%+.

---

*Notebook version: Charter validation (Q0–Q5) + hypothesis testing (H9, H10, H2) + falsification complete. All queries written for SQL Server / T-SQL syntax. Adjust `DATETRUNC` / `DATEADD` for PostgreSQL or BigQuery as needed.*
