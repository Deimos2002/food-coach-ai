// Alert rules of spec §9, all wired to the bootstrap action group.
// Job outcomes come from the Container Apps system logs in Log Analytics.

param location string
param logWorkspaceId string
param actionGroupId string
param apiId string
param postgresId string

@description('Budget start: first day of a month (yyyy-MM-dd).')
param budgetStartDate string

var jobFailed = 'ContainerAppSystemLogs_CL | where isnotempty(JobName_s) | where Reason_s in~ ("Failed", "ExecutionFailed", "BackoffLimitExceeded", "DeadlineExceeded") or Log_s has "failed"'

resource jobFailedAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-job-failed'
  location: location
  properties: {
    displayName: 'Food Coach: a job execution failed'
    severity: 1
    enabled: true
    scopes: [
      logWorkspaceId
    ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: jobFailed
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroupId
      ]
    }
  }
}

resource backupMissingAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-backup-missing'
  location: location
  properties: {
    displayName: 'Food Coach: no successful backup in 26 h'
    severity: 1
    enabled: true
    scopes: [
      logWorkspaceId
    ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT26H'
    criteria: {
      allOf: [
        {
          query: 'ContainerAppSystemLogs_CL | where JobName_s == "job-backup" | where Reason_s in~ ("Completed", "ExecutionSucceeded", "Succeeded") or Log_s has "succeeded"'
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroupId
      ]
    }
  }
}

// retention_check.py exits 1 when a blob (live or soft-deleted) is older than 25 days.
resource retentionAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-retention-breach'
  location: location
  properties: {
    displayName: 'Food Coach: backup retention breach (blob older than 25 days)'
    severity: 1
    enabled: true
    scopes: [
      logWorkspaceId
    ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT1H'
    criteria: {
      allOf: [
        {
          query: '${jobFailed} | where JobName_s == "job-retention-check"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroupId
      ]
    }
  }
}

resource api5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-api-5xx'
  location: 'global'
  properties: {
    description: 'Food Coach: 5 or more HTTP 5xx responses in 15 min'
    severity: 2
    enabled: true
    scopes: [
      apiId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'http5xx'
          metricNamespace: 'Microsoft.App/containerApps'
          metricName: 'Requests'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
          timeAggregation: 'Total'
          operator: 'GreaterThanOrEqual'
          threshold: 5
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroupId
      }
    ]
  }
}

resource dbStorageAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-db-storage'
  location: 'global'
  properties: {
    description: 'Food Coach: Postgres storage used above 80 %'
    severity: 2
    enabled: true
    scopes: [
      postgresId
    ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'storagePercent'
          metricNamespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
          metricName: 'storage_percent'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 80
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroupId
      }
    ]
  }
}

// Notification only; the hard LLM caps stay in the app. Amount is in the billing currency (EUR).
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-foodcoach'
  properties: {
    category: 'Cost'
    amount: 10
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      actual50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [
          actionGroupId
        ]
      }
      actual90: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 90
        thresholdType: 'Actual'
        contactEmails: []
        contactGroups: [
          actionGroupId
        ]
      }
    }
  }
}
