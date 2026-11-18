# CRM Integrity Report

_Generated 2026-11-18T16:35:52+00:00_

## Overall: 77.5 / 100 — Grade B

25 records audited.

**Record mean 82.5** − dupe penalty 5.0 (20% of records sit in duplicate clusters) · **8% dead** (unreachable) · **16% at risk** (record score < 70)

## Dimension averages (worst first)

| Dimension | Avg score |
|---|---|
| freshness | 55.0 |
| ownership | 88.0 |
| validity | 91.9 |
| consistency | 93.6 |
| completeness | 94.6 |

## Score distribution

```
  0-9   |  0
 10-19  |  0
 20-29  |  0
 30-39  |  0
 40-49  | ######## 2
 50-59  |  0
 60-69  | ######## 2
 70-79  | ######################## 6
 80-89  | #################### 5
 90-99  | ######################################## 10
100-100 |  0
```

## Top 20 worst records

| Email | Overall | Worst dimension |
|---|---|---|
| test@test.com | 45.0 | validity |
| asdf@asdf | 45.0 | validity |
| arjun.desai@ferrotechalloys.com | 69.5 | freshness |
| nisha.pillai@granitewaveexports.com | 69.5 | freshness |
| (missing) | 73.5 | freshness |
| zoya.ansari@bluecrestenergy.com | 74.2 | ownership |
| notanemailnodomain | 76.2 | validity |
| manoj.iyengar@haldiaportservices.com | 77.5 | freshness |
| reema.chowdhury@vantagepolymers.com | 77.5 | freshness |
| sanjay.oberoi@duneshieldcement.com | 77.5 | freshness |
| tanvi.saxena@orionvalveworks.com | 80.5 | ownership |
| harish.menon@sterlingcastparts.com | 80.5 | ownership |
| sales@brightpathlogistics.com | 88.8 | freshness |
| sales@brightpathlogistics.com | 88.8 | freshness |
| leela.subramaniam@crestlinepackaging.com | 88.8 | freshness |
| sales@brightpathlogistics.com | 95.0 | freshness |
| priya.kapoor@meridiansteelworks.com | 95.0 | freshness |
| p.kapoor@meridiansteelworks.com | 95.0 | freshness |
| kiran.vora@northstarfabrication.com | 95.0 | freshness |
| vivaan.chauhan@pinnacleautoparts.com | 95.0 | freshness |

## Duplicate clusters

| Type | Key | Records |
|---|---|---|
| exact_email | sales@brightpathlogistics.com | sales@brightpathlogistics.com, sales@brightpathlogistics.com, sales@brightpathlogistics.com |
| fuzzy_name_company | priya kapoor / meridian steelworks | priya.kapoor@meridiansteelworks.com, p.kapoor@meridiansteelworks.com |

## $-leak estimate

7 of 25 records (28.0%) are unreachable (validity < 40 or freshness <= 10).

Estimated leak: **$1,260** (unreachable_pct × total_records × ACV × 2%)

_Assumption: ACV = $9,000, baseline conversion rate = 2%. Both are editable inputs — pass --acv to change the contract value assumption._
