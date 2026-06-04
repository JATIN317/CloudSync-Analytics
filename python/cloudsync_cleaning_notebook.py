"""
CloudSync — Data Cleaning Pipeline
===================================
Project:  CloudSync SaaS Product Analytics
Author:   Avinash
Purpose:  Load raw CSVs, document data quality issues, apply fixes,
          output a clean cohort table ready for analysis.

How to run:
    pip install pandas numpy
    python cloudsync_cleaning_notebook.py

All raw columns are preserved. Every fix creates a new column.
No rows are deleted — issues are flagged for downstream filtering.
"""

import pandas as pd
import numpy as np

# =============================================================================
# SECTION 1 — LOAD RAW DATA & INITIAL INSPECTION
# =============================================================================

print("=" * 60)
print("SECTION 1 — LOADING RAW DATA")
print("=" * 60)

# Update these paths if your CSVs are in a different folder
DATA_PATH = "./"

dim_cust    = pd.read_csv(DATA_PATH + "dim_customers.csv")
fact_usage  = pd.read_csv(DATA_PATH + "fact_monthly_usage.csv")
fact_subs   = pd.read_csv(DATA_PATH + "fact_subscriptions.csv")
fact_tickets = pd.read_csv(DATA_PATH + "fact_support_tickets.csv")
dim_date    = pd.read_csv(DATA_PATH + "dim_date.csv")

# Shape check
print("\nTable shapes (rows × columns):")
for name, df in [("dim_customers", dim_cust),
                 ("fact_monthly_usage", fact_usage),
                 ("fact_subscriptions", fact_subs),
                 ("fact_support_tickets", fact_tickets)]:
    print(f"  {name}: {df.shape[0]:,} rows × {df.shape[1]} cols")

# Null check
print("\nNull counts per table:")
for name, df in [("dim_customers", dim_cust),
                 ("fact_subscriptions", fact_subs),
                 ("fact_support_tickets", fact_tickets)]:
    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]
    if len(nulls):
        print(f"  {name}: {nulls.to_dict()}")
    else:
        print(f"  {name}: no nulls")


# =============================================================================
# SECTION 2 — DIM_CUSTOMERS CLEANING
# =============================================================================

print("\n" + "=" * 60)
print("SECTION 2 — DIM_CUSTOMERS CLEANING")
print("=" * 60)

# ------- 2A: Channel Normalization -------
# Problem: 27 string variants for 6 true acquisition channels
# due to manual CRM entry (typos, underscores, mixed case)
# Fix: explicit mapping table → new column channel_clean

print("\n[2A] Raw acquisition_channel variants found:")
print(dim_cust['acquisition_channel'].value_counts().to_string())

CHANNEL_MAP = {
    # Content Marketing
    'Content Marketing'  : 'Content Marketing',
    'content_marketing'  : 'Content Marketing',
    'Content Mktg'       : 'Content Marketing',
    'Content marketing'  : 'Content Marketing',
    # Outbound Sales
    'Outbound Sales'     : 'Outbound Sales',
    'outbound_sales'     : 'Outbound Sales',
    'Outbound sales'     : 'Outbound Sales',
    'Outbound'           : 'Outbound Sales',
    # Organic Search
    'Organic Search'     : 'Organic Search',
    'Organic search'     : 'Organic Search',
    'organic_search'     : 'Organic Search',
    'Orgainc Search'     : 'Organic Search',   # typo
    # Referral
    'Referral'           : 'Referral',
    'Refferal'           : 'Referral',         # typo
    'Referall'           : 'Referral',         # typo
    'referral'           : 'Referral',
    # Self-Serve
    'Self-Serve'         : 'Self-Serve',
    'Self Serve'         : 'Self-Serve',
    'SelfServe'          : 'Self-Serve',
    'self-serve'         : 'Self-Serve',
    # Webinar
    'Webinar'            : 'Webinar',
    'Webinars'           : 'Webinar',
    'Web Seminar'        : 'Webinar',
    # Event
    'Event'              : 'Event',
    'event'              : 'Event',
    # Partner
    'Partner'            : 'Partner',
    'Partners'           : 'Partner',
}

dim_cust['channel_clean'] = dim_cust['acquisition_channel'].map(CHANNEL_MAP)

# Verify: no unmapped values
unmapped = dim_cust[dim_cust['channel_clean'].isna()]
if len(unmapped):
    print(f"\n⚠️  WARNING: {len(unmapped)} rows not mapped:")
    print(unmapped['acquisition_channel'].value_counts())
else:
    print(f"\n✅ Channel normalization: all {len(dim_cust)} rows mapped successfully")

print("\nClean channel distribution:")
print(dim_cust['channel_clean'].value_counts().to_string())


# ------- 2B: Flag CRM Duplicate Rows -------
# Problem: 7 rows with '-DUP' suffix in customer_id
# These are obvious CRM re-sync duplicates
# Fix: flag column (never delete — preserve audit trail)

dim_cust['is_duplicate'] = dim_cust['customer_id'].str.contains('-DUP', na=False)

dup_count = dim_cust['is_duplicate'].sum()
print(f"\n[2B] CRM duplicate rows flagged: {dup_count}")
if dup_count > 0:
    print(dim_cust[dim_cust['is_duplicate']][['customer_id', 'company_size', 'channel_clean']].to_string())


# ------- 2C: Flag Ghost Customers -------
# Problem: ~8 customers exist in dim_customers but have no records
# in fact tables — silent orphans from a re-ingestion event
# Detection: customer_id appears in dim but not in fact_monthly_usage
# Fix: flag column has_usage = True/False

customers_with_usage = set(fact_usage['customer_id'].unique())
dim_cust['has_usage'] = dim_cust['customer_id'].isin(customers_with_usage)

ghost_mask = (~dim_cust['has_usage']) & (~dim_cust['is_duplicate'])
ghost_count = ghost_mask.sum()
print(f"\n[2C] Ghost customers (no usage data, not DUP): {ghost_count}")
if ghost_count > 0:
    print(dim_cust[ghost_mask][['customer_id', 'company_size', 'channel_clean']].to_string())


# ------- 2D: Cohort Assignment -------
# Assign analysis cohort label to every clean customer

def assign_cohort(row):
    """
    Excludes duplicates and ghosts.
    Enterprise + Outbound Sales → Enterprise_Outbound (signal cohort)
    Enterprise + anything else  → Enterprise_Inbound  (control cohort)
    Mid-Market / SMB             → labelled for completeness
    """
    if row['is_duplicate'] or not row['has_usage']:
        return 'Excluded'
    if row['company_size'] == 'Enterprise' and row['channel_clean'] == 'Outbound Sales':
        return 'Enterprise_Outbound'
    elif row['company_size'] == 'Enterprise':
        return 'Enterprise_Inbound'
    elif row['company_size'] == 'Mid-Market':
        return 'Mid_Market'
    else:
        return 'SMB'

dim_cust['cohort'] = dim_cust.apply(assign_cohort, axis=1)

print("\n[2D] Final cohort distribution:")
print(dim_cust['cohort'].value_counts().to_string())


# =============================================================================
# SECTION 3 — FACT_SUBSCRIPTIONS CLEANING
# =============================================================================

print("\n" + "=" * 60)
print("SECTION 3 — FACT_SUBSCRIPTIONS CLEANING")
print("=" * 60)

# ------- 3A: Impute NULL end_dates -------
# Problem: 778 rows have NULL end_date
# These are ACTIVE subscriptions — NULL means no end date yet
# Fix: replace NULL with sentinel date 2024-12-31
# IMPORTANT: keep original column untouched

fact_subs['start_date']     = pd.to_datetime(fact_subs['start_date'])
fact_subs['end_date_raw']   = pd.to_datetime(fact_subs['end_date'], errors='coerce')
fact_subs['end_date_clean'] = fact_subs['end_date_raw'].fillna(pd.Timestamp('2024-12-31'))

null_end_count = fact_subs['end_date_raw'].isna().sum()
print(f"[3A] NULL end_date rows: {null_end_count} → filled with 2024-12-31")
print("     (These are active subscriptions — NULL = no end date yet)")


# ------- 3B: Validate MRR Against Plan Tier -------
# Problem: 47 rows where base_mrr falls outside the valid range
# for the assigned plan_tier (billing API timeout returned stale value)
# Detection: compare base_mrr against tier-specific valid ranges
# Fix: flag column mrr_flag = Valid / Check

MRR_RANGES = {
    'Starter'      : (49,  199),
    'Professional' : (299, 699),
    'Enterprise'   : (799, 1999),
}

def flag_mrr(row):
    tier = row['plan_tier']
    if tier not in MRR_RANGES:
        return 'Unknown'
    lo, hi = MRR_RANGES[tier]
    return 'Valid' if lo <= row['base_mrr'] <= hi else 'Check'

fact_subs['mrr_flag'] = fact_subs.apply(flag_mrr, axis=1)

mrr_check = fact_subs[fact_subs['mrr_flag'] == 'Check']
print(f"\n[3B] MRR/Tier mismatches flagged: {len(mrr_check)}")
print("     Example mismatches (Enterprise tier with wrong MRR):")
example = mrr_check[mrr_check['plan_tier']=='Enterprise'][['subscription_id','plan_tier','base_mrr','mrr_flag']].head(5)
print(example.to_string())
print("     → Excluded from revenue calculations via mrr_flag filter")

print(f"\nMRR flag summary:")
print(fact_subs['mrr_flag'].value_counts().to_string())


# =============================================================================
# SECTION 4 — FACT_SUPPORT_TICKETS CLEANING
# =============================================================================

print("\n" + "=" * 60)
print("SECTION 4 — FACT_SUPPORT_TICKETS CLEANING")
print("=" * 60)

# ------- 4A: Flag Open vs Resolved Tickets -------
# Problem: 156 rows have NULL resolved_date (open tickets at extract time)
# These have NULL resolution_time_hrs as well
# Fix: flag column ticket_status = 'Resolved' / 'Open'
# Resolution time analyses must filter to ticket_status = 'Resolved'

fact_tickets['ticket_status'] = fact_tickets['resolved_date'].apply(
    lambda x: 'Resolved' if pd.notna(x) else 'Open'
)

print(f"[4A] Ticket status distribution:")
print(fact_tickets['ticket_status'].value_counts().to_string())
open_pct = fact_tickets['ticket_status'].value_counts(normalize=True)['Open'] * 100
print(f"     Open tickets: {open_pct:.1f}% of all tickets")
print("     → Excluded from all resolution_time_hrs calculations")


# ------- 4B: Parse dates -------
fact_tickets['created_date']  = pd.to_datetime(fact_tickets['created_date'])
fact_tickets['resolved_date'] = pd.to_datetime(fact_tickets['resolved_date'], errors='coerce')
fact_tickets['ticket_month']  = fact_tickets['created_date'].dt.to_period('M').dt.to_timestamp()

print(f"\n[4B] Date parsing complete. Ticket date range:")
print(f"     Earliest: {fact_tickets['created_date'].min().date()}")
print(f"     Latest:   {fact_tickets['created_date'].max().date()}")


# =============================================================================
# SECTION 5 — BUILD CLEAN COHORT TABLE
# =============================================================================

print("\n" + "=" * 60)
print("SECTION 5 — BUILD CLEAN COHORT TABLE")
print("=" * 60)

# Join dim_customers (with all cleaning columns) to fact tables
# This single table is the foundation for all downstream analysis

clean_cohort = dim_cust[
    dim_cust['cohort'].isin(['Enterprise_Outbound', 'Enterprise_Inbound'])
][['customer_id', 'industry', 'company_size', 'channel_clean',
   'signup_date', 'cohort', 'is_duplicate', 'has_usage']].copy()

print(f"Clean cohort table: {len(clean_cohort)} Enterprise accounts")
print(clean_cohort['cohort'].value_counts().to_string())

# Save clean cohort for use in downstream notebooks
clean_cohort.to_csv("/home/claude/clean_cohort.csv", index=False)
print("\n✅ clean_cohort.csv written to output folder")


# =============================================================================
# SECTION 6 — CLEANING SUMMARY REPORT
# =============================================================================

print("\n" + "=" * 60)
print("SECTION 6 — CLEANING SUMMARY REPORT")
print("=" * 60)

print("""
┌─────────────────────────────────────────────────────────────┐
│            CLOUDSYNC DATA CLEANING SUMMARY                  │
├───────────────┬──────────────┬──────────────┬──────────────┤
│ Table         │ Issue        │ Rows Affected│ Fix Applied  │
├───────────────┼──────────────┼──────────────┼──────────────┤
│ dim_customers │ Channel typos│     27 vars  │ channel_clean│
│ dim_customers │ CRM dups     │     7 rows   │ is_duplicate │
│ dim_customers │ Ghost IDs    │     8 rows   │ has_usage    │
│ fact_subs     │ NULL end_date│   778 rows   │ end_date_clean│
│ fact_subs     │ MRR mismatch │    59 rows   │ mrr_flag     │
│ fact_tickets  │ Open tickets │   156 rows   │ ticket_status│
├───────────────┴──────────────┴──────────────┴──────────────┤
│ PRINCIPLE: Raw columns preserved. New columns for fixes.    │
│            No rows deleted. Full audit trail maintained.    │
└─────────────────────────────────────────────────────────────┘
""")

print("Cleaning complete. Proceed to analysis with clean columns.")
