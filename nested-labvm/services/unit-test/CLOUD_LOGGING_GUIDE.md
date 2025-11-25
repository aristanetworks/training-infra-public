# Cloud Logging Guide for ATD Unit Tests

## Architecture Overview

```
Multiple Lab VMs → Google Cloud Logging → Centralized Dashboard
```

All ATD unit test logs are sent to Google Cloud Logging with structured labels for easy filtering and debugging across multiple servers.

## Prerequisites

1. **Service Account Permissions**: Each VM needs the `Logs Writer` role
   - Already configured if using GCE with default service account
   - For custom service accounts, add: `roles/logging.logWriter`

2. **Python Dependencies**: Install via requirements.txt
   ```bash
   pip install -r /opt/atd/nested-labvm/services/unit-test/requirements.txt
   ```

## Log Labels

Each log entry includes these labels for filtering:

| Label | Description | Example |
|-------|-------------|---------|
| `service` | Service name (constant) | `atd-unit-tests` |
| `lab_name` | Hostname of the lab VM | `elan-test-lab-l4-v2-2-cecd11ea` |
| `log_type` | Type of log | `unit_test` |
| `environment` | Environment | `production` |

## Querying Logs in Cloud Console

### View All ATD Unit Test Logs (All Labs)
```
resource.type="global"
labels.service="atd-unit-tests"
```

### View Logs for a Specific Lab
```
resource.type="global"
labels.service="atd-unit-tests"
labels.lab_name="elan-test-lab-l4-v2-2-cecd11ea"
```

### View Only Errors Across All Labs
```
resource.type="global"
labels.service="atd-unit-tests"
severity>=ERROR
```

### View Logs from Last Hour for All Labs
```
resource.type="global"
labels.service="atd-unit-tests"
timestamp>="2024-01-01T10:00:00Z"
```

### Search for Specific Test Failures
```
resource.type="global"
labels.service="atd-unit-tests"
jsonPayload.message=~"FAILED|ERROR"
```

### View Logs for Multiple Specific Labs
```
resource.type="global"
labels.service="atd-unit-tests"
(labels.lab_name="lab-001" OR labels.lab_name="lab-002")
```

## Using gcloud CLI

### Tail logs in real-time (all labs)
```bash
gcloud logging tail "labels.service=atd-unit-tests" --format=json
```

### Tail logs for specific lab
```bash
gcloud logging tail "labels.service=atd-unit-tests AND labels.lab_name=elan-test-lab-l4-v2-2" --format=json
```

### Export last 1 hour of logs to file
```bash
gcloud logging read "labels.service=atd-unit-tests AND timestamp>=\"$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ')\"" \
  --limit=1000 \
  --format=json > atd_test_logs.json
```

### Count errors by lab
```bash
gcloud logging read "labels.service=atd-unit-tests AND severity>=ERROR" \
  --format="value(labels.lab_name)" | sort | uniq -c
```

## Creating Log-Based Metrics

Create custom metrics to monitor test failures:

1. Go to **Logging → Logs-based Metrics**
2. Click **Create Metric**
3. Use this filter:
   ```
   resource.type="global"
   labels.service="atd-unit-tests"
   jsonPayload.message=~"Test.*FAILED"
   ```
4. Name: `atd_test_failures`
5. Metric Type: Counter
6. Labels: Extract `lab_name`

## Setting Up Alerts

Create alerts for test failures:

1. Go to **Monitoring → Alerting → Create Policy**
2. Select metric: `logging/user/atd_test_failures`
3. Condition: Any time series violates > 0 for 1 minute
4. Notification: Email, Slack, PagerDuty, etc.
5. Documentation:
   ```
   ATD Unit Tests Failed on ${resource.labels.lab_name}

   View logs: https://console.cloud.google.com/logs/query;query=labels.service%3D%22atd-unit-tests%22%20labels.lab_name%3D%22${resource.labels.lab_name}%22
   ```

## Log Retention

- **Default**: 30 days
- **To extend**: Create a log sink to export to Cloud Storage or BigQuery
  ```bash
  gcloud logging sinks create atd-test-logs-archive \
    storage.googleapis.com/atd-test-logs-archive \
    --log-filter='labels.service="atd-unit-tests"'
  ```

## Cost Optimization

**Free Tier**: First 50 GB/month
**Pricing**: ~$0.50 per GB after free tier

**Tips to reduce costs**:
1. Only log at INFO level or above (already configured)
2. Don't log every request/response (avoid DEBUG logs)
3. Use log exclusion filters for noisy logs
4. Export old logs to Cloud Storage ($0.026/GB/month)

## Troubleshooting

### Logs not appearing in Cloud Logging?

1. **Check VM service account permissions**:
   ```bash
   gcloud compute instances describe $(hostname) --format="value(serviceAccounts[0].email)"
   gcloud projects get-iam-policy $(gcloud config get-value project) \
     --flatten="bindings[].members" \
     --filter="bindings.members:serviceAccount:EMAIL"
   ```

2. **Verify Cloud Logging library is installed**:
   ```bash
   python3 -c "import google.cloud.logging; print('✓ Cloud Logging available')"
   ```

3. **Check local logs for errors**:
   ```bash
   journalctl -u atd-unit-test.service -n 50
   ```

4. **Test Cloud Logging manually**:
   ```python
   from google.cloud import logging
   client = logging.Client()
   logger = client.logger('test')
   logger.log_text('Test message', severity='INFO')
   ```

## Dashboard Example

Create a custom dashboard in Cloud Monitoring:

1. Go to **Monitoring → Dashboards → Create Dashboard**
2. Add widgets:
   - **Line Chart**: Test execution count over time
   - **Pie Chart**: Test results by lab
   - **Table**: Recent failures with lab names
   - **Logs Panel**: Live log stream with filter `labels.service="atd-unit-tests"`

## Benefits of This Architecture

✅ **Centralized**: All lab logs in one place
✅ **Searchable**: Filter by lab name, severity, time range
✅ **Scalable**: Works with 1 or 1000 labs
✅ **Real-time**: Stream logs as they happen
✅ **Alerting**: Get notified of failures automatically
✅ **No Infrastructure**: No log server to maintain
✅ **Failsafe**: Local logs still work if Cloud Logging fails
✅ **Cost-effective**: ~$5/month for 100 labs (typical usage)

## Example Queries for Common Scenarios

### Find all labs that failed CVP SSH test
```
labels.service="atd-unit-tests"
jsonPayload.message=~"CVP SSH.*FAILED"
```

### Compare test results across labs
```
labels.service="atd-unit-tests"
jsonPayload.message=~"Test Summary"
```

### Debug a specific lab's test run
```
labels.service="atd-unit-tests"
labels.lab_name="YOUR-LAB-NAME"
timestamp>="2024-01-01T10:00:00Z"
```

### Find labs with consistent failures
```
labels.service="atd-unit-tests"
severity>=ERROR
| group_by(labels.lab_name)
```
