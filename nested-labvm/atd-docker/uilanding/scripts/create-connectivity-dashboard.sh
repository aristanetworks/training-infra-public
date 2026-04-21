#!/bin/bash
# Create GCP log-based metrics and Cloud Monitoring dashboard for connectivity monitoring
# Run this on a machine with gcloud access to the target project
#
# Usage: ./create-connectivity-dashboard.sh [project-id]
# Default project: atd-testdrivetraining-dev

set -e

PROJECT="${1:-atd-testdrivetraining-dev}"

echo "=== Creating Connectivity Monitoring Dashboard ==="
echo "Project: ${PROJECT}"
echo ""

gcloud config set project "${PROJECT}" 2>/dev/null

# ============================================
# Step 1: Create Log-Based Metrics
# ============================================

echo "=== Creating Log-Based Metrics ==="

# Metric 1: Session Starts (counter)
echo "  Creating: connectivity_session_starts"
gcloud logging metrics create connectivity_session_starts \
  --project="${PROJECT}" \
  --description="Count of new WebSocket sessions" \
  --log-filter='labels.event="connectivity" AND labels.action="session_start"' \
  --label-keys="labels.lab_hostname,labels.client_ip" \
  2>/dev/null || \
gcloud logging metrics update connectivity_session_starts \
  --project="${PROJECT}" \
  --description="Count of new WebSocket sessions" \
  --log-filter='labels.event="connectivity" AND labels.action="session_start"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 2: Reconnects (counter)
echo "  Creating: connectivity_reconnects"
gcloud logging metrics create connectivity_reconnects \
  --project="${PROJECT}" \
  --description="Count of client reconnections within 5 min of disconnect" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect"' \
  --label-keys="labels.lab_hostname,labels.client_ip" \
  2>/dev/null || \
gcloud logging metrics update connectivity_reconnects \
  --project="${PROJECT}" \
  --description="Count of client reconnections within 5 min of disconnect" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 3: Session Duration (distribution)
echo "  Creating: connectivity_session_duration"
gcloud logging metrics create connectivity_session_duration \
  --project="${PROJECT}" \
  --description="Distribution of WebSocket session durations in seconds" \
  --log-filter='labels.event="connectivity" AND labels.action="session_end"' \
  --label-keys="labels.lab_hostname" \
  --field-name="labels.duration_seconds" \
  --type=distribution \
  --bucket-type=explicit \
  --bucket-boundaries="10,30,60,120,300,600,1800,3600,7200,14400" \
  2>/dev/null || \
gcloud logging metrics update connectivity_session_duration \
  --project="${PROJECT}" \
  --description="Distribution of WebSocket session durations in seconds" \
  --log-filter='labels.event="connectivity" AND labels.action="session_end"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 4: Missed Pongs (counter)
echo "  Creating: connectivity_missed_pongs"
gcloud logging metrics create connectivity_missed_pongs \
  --project="${PROJECT}" \
  --description="Count of missed pong warning events (3+ missed)" \
  --log-filter='labels.event="connectivity" AND labels.action="missed_pongs"' \
  --label-keys="labels.lab_hostname,labels.session_id" \
  2>/dev/null || \
gcloud logging metrics update connectivity_missed_pongs \
  --project="${PROJECT}" \
  --description="Count of missed pong warning events (3+ missed)" \
  --log-filter='labels.event="connectivity" AND labels.action="missed_pongs"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 5: Offline Duration from reconnect reports (distribution)
echo "  Creating: connectivity_offline_duration"
gcloud logging metrics create connectivity_offline_duration \
  --project="${PROJECT}" \
  --description="Distribution of client offline durations in milliseconds" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect_report"' \
  --label-keys="labels.lab_hostname" \
  --field-name="labels.offline_duration_ms" \
  --type=distribution \
  --bucket-type=explicit \
  --bucket-boundaries="1000,5000,10000,30000,60000,120000,300000,600000" \
  2>/dev/null || \
gcloud logging metrics update connectivity_offline_duration \
  --project="${PROJECT}" \
  --description="Distribution of client offline durations in milliseconds" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect_report"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 6: WebSocket Latency (distribution)
echo "  Creating: connectivity_ws_latency"
gcloud logging metrics create connectivity_ws_latency \
  --project="${PROJECT}" \
  --description="Distribution of WebSocket round-trip latency in milliseconds" \
  --log-filter='labels.event="connectivity" AND labels.action="periodic_summary"' \
  --label-keys="labels.lab_hostname" \
  --field-name="labels.ws_latency_ms" \
  --type=distribution \
  --bucket-type=explicit \
  --bucket-boundaries="10,25,50,100,200,500,1000,2000,5000" \
  2>/dev/null || \
gcloud logging metrics update connectivity_ws_latency \
  --project="${PROJECT}" \
  --description="Distribution of WebSocket round-trip latency in milliseconds" \
  --log-filter='labels.event="connectivity" AND labels.action="periodic_summary"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 7: Internal gRPC Check (counter, by status)
echo "  Creating: connectivity_internal_grpc"
gcloud logging metrics create connectivity_internal_grpc \
  --project="${PROJECT}" \
  --description="Count of internal gRPC health checks to CVP by result status" \
  --log-filter='labels.event="connectivity" AND labels.action="grpc_check" AND labels.source="internal"' \
  --label-keys="labels.lab_hostname,labels.status" \
  2>/dev/null || \
gcloud logging metrics update connectivity_internal_grpc \
  --project="${PROJECT}" \
  --description="Count of internal gRPC health checks to CVP by result status" \
  --log-filter='labels.event="connectivity" AND labels.action="grpc_check" AND labels.source="internal"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 8: Client gRPC Check - individual results (counter, by status)
echo "  Creating: connectivity_client_grpc"
gcloud logging metrics create connectivity_client_grpc \
  --project="${PROJECT}" \
  --description="Count of client-side gRPC health check results by status" \
  --log-filter='labels.event="connectivity" AND labels.action="grpc_check" AND labels.source="client"' \
  --label-keys="labels.lab_hostname,labels.status" \
  2>/dev/null || \
gcloud logging metrics update connectivity_client_grpc \
  --project="${PROJECT}" \
  --description="Count of client-side gRPC health check results by status" \
  --log-filter='labels.event="connectivity" AND labels.action="grpc_check" AND labels.source="client"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 9: External Connectivity Check (counter, by result)
echo "  Creating: connectivity_external_check"
gcloud logging metrics create connectivity_external_check \
  --project="${PROJECT}" \
  --description="Count of external connectivity checks (arista.com) from periodic summaries" \
  --log-filter='labels.event="connectivity" AND labels.action="periodic_summary" AND labels.source="client"' \
  --label-keys="labels.lab_hostname,labels.external_check" \
  2>/dev/null || \
gcloud logging metrics update connectivity_external_check \
  --project="${PROJECT}" \
  --description="Count of external connectivity checks (arista.com) from periodic summaries" \
  --log-filter='labels.event="connectivity" AND labels.action="periodic_summary" AND labels.source="client"' \
  2>/dev/null || echo "    (failed to create or update)"

# Metric 10: Page Visibility Events (counter)
echo "  Creating: connectivity_visibility_reconnects"
gcloud logging metrics create connectivity_visibility_reconnects \
  --project="${PROJECT}" \
  --description="Count of reconnect events to identify visibility-triggered vs real disconnects" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect"' \
  --label-keys="labels.lab_hostname,labels.client_ip" \
  2>/dev/null || \
gcloud logging metrics update connectivity_visibility_reconnects \
  --project="${PROJECT}" \
  --description="Count of reconnect events to identify visibility-triggered vs real disconnects" \
  --log-filter='labels.event="connectivity" AND labels.action="reconnect"' \
  2>/dev/null || echo "    (failed to create or update)"

echo ""
echo "=== Log-Based Metrics Created ==="
echo ""

# ============================================
# Step 2: Create Dashboard
# ============================================

echo "=== Creating Cloud Monitoring Dashboard ==="

DASHBOARD_JSON=$(cat <<'ENDJSON'
{
  "displayName": "Connectivity Monitor",
  "dashboardFilters": [
    {
      "labelKey": "metric.labels.lab_hostname",
      "templateVariable": "lab_hostname",
      "stringValue": "",
      "filterType": "RESOURCE_LABEL"
    }
  ],
  "mosaicLayout": {
    "columns": 12,
    "tiles": [
      {
        "xPos": 0,
        "yPos": 0,
        "width": 12,
        "height": 1,
        "widget": {
          "title": "Connection Health",
          "text": {
            "content": "Session starts, reconnects, and reconnect ratio across lab instances",
            "format": "RAW"
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 1,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "Sessions Started",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_session_starts\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "LINE"
              }
            ],
            "timeshiftDuration": "0s",
            "yAxis": {
              "label": "sessions/s",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 4,
        "yPos": 1,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "Reconnects",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_reconnects\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "LINE"
              }
            ],
            "timeshiftDuration": "0s",
            "yAxis": {
              "label": "reconnects/s",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 8,
        "yPos": 1,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "Missed Pongs",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_missed_pongs\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "LINE"
              }
            ],
            "timeshiftDuration": "0s",
            "yAxis": {
              "label": "events/s",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 5,
        "width": 12,
        "height": 1,
        "widget": {
          "title": "Connection Quality",
          "text": {
            "content": "Session duration, offline gaps, WebSocket latency, and external connectivity",
            "format": "RAW"
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 6,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "Session Duration Distribution",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_session_duration\"",
                    "aggregation": {
                      "alignmentPeriod": "3600s",
                      "perSeriesAligner": "ALIGN_DELTA",
                      "crossSeriesReducer": "REDUCE_SUM"
                    }
                  }
                },
                "plotType": "HEATMAP"
              }
            ],
            "yAxis": {
              "label": "duration (seconds)",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 4,
        "yPos": 6,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "Offline Gap Duration Distribution",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_offline_duration\"",
                    "aggregation": {
                      "alignmentPeriod": "3600s",
                      "perSeriesAligner": "ALIGN_DELTA",
                      "crossSeriesReducer": "REDUCE_SUM"
                    }
                  }
                },
                "plotType": "HEATMAP"
              }
            ],
            "yAxis": {
              "label": "offline duration (ms)",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 8,
        "yPos": 6,
        "width": 4,
        "height": 4,
        "widget": {
          "title": "WebSocket Latency (p50 / p95)",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_ws_latency\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_PERCENTILE_50",
                      "crossSeriesReducer": "REDUCE_MEAN",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "LINE",
                "legendTemplate": "p50 - ${metric.labels.lab_hostname}"
              },
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_ws_latency\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_PERCENTILE_95",
                      "crossSeriesReducer": "REDUCE_MEAN",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "LINE",
                "legendTemplate": "p95 - ${metric.labels.lab_hostname}"
              }
            ],
            "yAxis": {
              "label": "latency (ms)",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 10,
        "width": 12,
        "height": 1,
        "widget": {
          "title": "Problem Indicators",
          "text": {
            "content": "Top reconnectors and short-lived sessions — use the hostname filter above to drill into specific instances",
            "format": "RAW"
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 11,
        "width": 6,
        "height": 4,
        "widget": {
          "title": "Top Reconnectors (by hostname + client IP)",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_reconnects\"",
                    "aggregation": {
                      "alignmentPeriod": "3600s",
                      "perSeriesAligner": "ALIGN_SUM",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.lab_hostname", "metric.labels.client_ip"]
                    }
                  }
                },
                "plotType": "STACKED_BAR"
              }
            ],
            "yAxis": {
              "label": "reconnect count",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 6,
        "yPos": 11,
        "width": 6,
        "height": 4,
        "widget": {
          "title": "Sessions Started (by hostname)",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_session_starts\"",
                    "aggregation": {
                      "alignmentPeriod": "3600s",
                      "perSeriesAligner": "ALIGN_SUM",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.lab_hostname"]
                    }
                  }
                },
                "plotType": "STACKED_BAR"
              }
            ],
            "yAxis": {
              "label": "session count",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 0,
        "yPos": 15,
        "width": 6,
        "height": 4,
        "widget": {
          "title": "Internal vs Client gRPC Health",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_internal_grpc\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.status"]
                    }
                  }
                },
                "plotType": "LINE",
                "legendTemplate": "Internal: ${metric.labels.status}"
              },
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_client_grpc\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.status"]
                    }
                  }
                },
                "plotType": "LINE",
                "legendTemplate": "Client: ${metric.labels.status}"
              }
            ],
            "yAxis": {
              "label": "checks/s",
              "scale": "LINEAR"
            }
          }
        }
      },
      {
        "xPos": 6,
        "yPos": 15,
        "width": 6,
        "height": 4,
        "widget": {
          "title": "External Connectivity (arista.com)",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "metric.type=\"logging.googleapis.com/user/connectivity_external_check\"",
                    "aggregation": {
                      "alignmentPeriod": "300s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.external_check"]
                    }
                  }
                },
                "plotType": "LINE",
                "legendTemplate": "${metric.labels.external_check}"
              }
            ],
            "yAxis": {
              "label": "checks/s",
              "scale": "LINEAR"
            }
          }
        }
      }
    ]
  }
}
ENDJSON
)

echo "${DASHBOARD_JSON}" > /tmp/connectivity-dashboard.json
gcloud monitoring dashboards create \
  --project="${PROJECT}" \
  --config-from-file=/tmp/connectivity-dashboard.json
rm /tmp/connectivity-dashboard.json

echo ""
echo "=== Dashboard Created ==="
echo ""

# ============================================
# Step 3: Create Alerting Policies
# ============================================

echo "=== Creating Alerting Policies ==="

# Alert 1: Excessive reconnections (>10 in 1 hour)
echo "  Creating alert: Excessive Reconnections"
cat > /tmp/reconnect-alert.json << 'ALERTJSON'
{
  "displayName": "Connectivity: Excessive Reconnections",
  "conditions": [
    {
      "displayName": "Reconnect rate > 10/hour",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/connectivity_reconnects\"",
        "aggregations": [
          {
            "alignmentPeriod": "3600s",
            "perSeriesAligner": "ALIGN_SUM",
            "crossSeriesReducer": "REDUCE_SUM",
            "groupByFields": ["metric.labels.lab_hostname"]
          }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 10,
        "duration": "0s",
        "trigger": {
          "count": 1
        }
      }
    }
  ],
  "combiner": "OR",
  "enabled": true,
  "notificationChannels": []
}
ALERTJSON
gcloud alpha monitoring policies create \
  --project="${PROJECT}" \
  --policy-from-file=/tmp/reconnect-alert.json \
  2>/dev/null || echo "    (alert may already exist or alpha API unavailable)"
rm -f /tmp/reconnect-alert.json

# Alert 2: Client gRPC failing (firewall/VPN detection)
echo "  Creating alert: Client gRPC Failures"
cat > /tmp/grpc-divergence-alert.json << 'ALERTJSON'
{
  "displayName": "Connectivity: Client gRPC Check Failing",
  "conditions": [
    {
      "displayName": "Client gRPC checks failing for 5+ minutes",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/connectivity_client_grpc\" AND metric.labels.status!=\"ok\"",
        "aggregations": [
          {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_SUM",
            "crossSeriesReducer": "REDUCE_SUM",
            "groupByFields": ["metric.labels.lab_hostname"]
          }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 5,
        "duration": "300s",
        "trigger": {
          "count": 1
        }
      }
    }
  ],
  "combiner": "OR",
  "enabled": true,
  "notificationChannels": []
}
ALERTJSON
gcloud alpha monitoring policies create \
  --project="${PROJECT}" \
  --policy-from-file=/tmp/grpc-divergence-alert.json \
  2>/dev/null || echo "    (alert may already exist or alpha API unavailable)"
rm -f /tmp/grpc-divergence-alert.json

echo ""
echo "=== Alerting Policies Created ==="
echo "Note: Notification channels are empty. Add email/Slack/PagerDuty channels in the GCP Console."

echo ""
echo "View at: https://console.cloud.google.com/monitoring/dashboards?project=${PROJECT}"
echo ""
echo "Metrics may take a few minutes to start collecting data."
echo "Use the 'lab_hostname' filter at the top of the dashboard to drill into specific instances."
