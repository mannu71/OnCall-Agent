-- db:postgres-production
-- label: Profiles Updated
SELECT COUNT(DISTINCT p.id) AS total_count
FROM profiles p
JOIN profile_kyc_status_history h1 ON p.id = h1.profile_id
JOIN profile_kyc_status_history h2 ON p.id = h2.profile_id
WHERE p.deleted_by_id IS NULL
  AND h1.status = 'approved'
  AND h2.status = 'approved_review_due'
  AND h2.status_updated_at > h1.status_updated_at
  AND h2.status_updated_at >= {current_date}
  AND h2.status_updated_at < {next_date}
  AND p.kyc_review_on < h2.status_updated_at;

-- label:Previous Pending Monitoring Notifications Count
SELECT COUNT(*) AS pending_count 
FROM kyc_monitoring_notifications kmn 
WHERE kmn.status = 'pending' 
  AND kmn.created_at < {current_date};

-- label:Kyc Monitoring Updater Run
SELECT EXISTS (
    SELECT 1
    FROM kyc_monitoring_updater_runs kmur
    WHERE kmur.started_at::date = {current_date}
      AND kmur.status = 'completed'
) AS is_completed_today;


-- db:postgres-production
-- label:Profile KYC Alerts Count
SELECT COUNT(*) AS value 
FROM profile_kyc_alerts AS pka 
LEFT JOIN profiles AS p
    ON pka.profile_id = p.id
LEFT JOIN kyc_monitoring_notifications AS kmn
    ON pka.notification_id = kmn.id
WHERE pka.created_at::date = {current_date}::date;


-- db:postgres-production
-- label:Profiles Ran Count
SELECT COUNT(DISTINCT pka.profile_id) AS value
FROM profile_kyc_alerts AS pka
WHERE pka.created_at::date = {current_date}::date;


-- db:postgres-production
-- label:Monitoring Metrics Refreshes (Today)
SELECT EXISTS (
    SELECT 1
    FROM monitoring_metrics_refreshes mmr
    WHERE mmr.started_at::date = {current_date}
      AND mmr.status = 'completed'
) AS is_completed;

-- db:postgres-production
-- label:Latest Schedule Updater Run ID
SELECT sur.Id 
FROM schedule_updater_runs sur 
ORDER BY sur.finished_at DESC 
LIMIT 1;

-- db:postgres-production
-- label:customer email notifications
SELECT 
    COUNT(*) FILTER (WHERE aae.is_published = TRUE) AS published_count,
    COUNT(*) AS total_count
FROM aml_alert_emails aae
WHERE aae.scheduler_updater_run_id = {id};

-- db:postgres-production
-- label:Failed Schedules
 SELECT count(*) FROM schedule_updater_run_items suri
 WHERE suri.schedule_updater_run_id = {id} AND suri.error_code is NOT null;

  --label:Schedules Ran Count
 SELECT count(*) FROM schedule_updater_run_items suri
 WHERE suri.schedule_updater_run_id = {id};

 -- label:Search Results Count
 SELECT COUNT(*) AS SearchResultsCount
 FROM schedule_updater_run_items suri
 WHERE suri.schedule_updater_run_id = {id}
 AND suri.search_result_id IS NOT NULL;

-- label: profiles locked in last 3 days
SELECT 
  COUNT(*) AS "profiles locked in last 3 days"
FROM profiles p
WHERE p.profile_import_request_id IS NOT NULL
  AND p.is_locked = TRUE
  AND p.deleted_by_id IS NULL
  AND p.modified_at >= (CURRENT_DATE - INTERVAL '3 day')
  AND p.modified_at < CURRENT_DATE;

-- db:postgres-acuris-production
-- label:Worklist Run ID
SELECT id as WorklistRunId
FROM monitor_updater_runs mur
WHERE mur.started_at::date = {current_date}
ORDER BY mur.started_at DESC
LIMIT 1;

-- label:Worklists Processed Count
select count(*) from worklist_monitor_metrics wmm where monitor_updater_run_id = {WorklistRunId};

-- label:Failed Worklists Count
select count(*) from worklist_monitor_metrics wmm where wmm.process_records_job_id is null
and monitor_updater_run_id = {WorklistRunId}; 

--label:Open business monitor records count
select SUM(business_open_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId}; 
  
--label:Open individual monitor records count
select SUM(individual_open_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};

--label:Screened but no matches business monitor records count
select SUM(business_screened_no_matches_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};
  
--label:Screened but no matches individual monitor records count
select SUM(individual_screened_no_matches_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};
  
--label:Not screened business monitor records count
select SUM(business_not_screened_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};
  
--label:Not screened individual monitor records count
select SUM(individual_not_screened_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};
  
--label:Closed business monitor records count
select SUM(business_closed_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};
  
--label:Closed individual monitor records count
select SUM(individual_closed_monitor_records_count)
from worklist_monitor_metrics wmm
where monitor_updater_run_id = {WorklistRunId};

--label:Skipped monitor records from processing
SELECT
COALESCE(SUM(wmm.business_monitor_records_count + wmm.individual_monitor_records_count), 0)
- COALESCE((
SELECT COUNT(*)
FROM worklist_monitor_record_metrics wmr
JOIN worklist_monitor_metrics wmm2
ON wmr.worklist_monitor_metric_id = wmm2.id
WHERE wmm2.monitor_updater_run_id = {WorklistRunId}
), 0) AS monitor_records_omitted_count
FROM worklist_monitor_metrics wmm
WHERE wmm.monitor_updater_run_id = {WorklistRunId};