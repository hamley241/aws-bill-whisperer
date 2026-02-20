# Slack Integration Guide

AWS Bill Whisperer can post cost analysis reports directly to Slack channels.

## Setup

### 1. Create a Slack Incoming Webhook

1. Go to your Slack workspace's [App Management](https://api.slack.com/apps)
2. Create a New App → From scratch
3. Add functionality → Incoming Webhooks
4. Activate Incoming Webhooks
5. Add New Webhook to Workspace
6. Choose the channel where reports should go
7. Copy the Webhook URL

### 2. Configure AWS Bill Whisperer

#### Via Environment Variable

```bash
export SLACK_WEBHOOK="https://hooks.slack.com/services/T00/B00/XXXX"
```

#### Via SAM Deployment

During `sam deploy --guided`, the template will prompt for the Slack webhook URL.

#### Via CLI

```bash
python cli/analyze.py --output slack --days 30
```

### 3. Test

```bash
# Test with mock data
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"AWS Bill Whisperer is configured!"}' \
  YOUR_WEBHOOK_URL
```

## Customization

### Custom Formatting

Edit `src/analyzer/formatter.py` to customize Slack message formatting:

```python
def to_slack(analysis: str, cost_data: dict) -> dict:
    return {
        "text": "AWS Bill Summary",
        "blocks": [
            # Customize your Slack blocks here
        ]
    }
```

### Scheduled Reports

Set up a CloudWatch Event to trigger the Lambda on a schedule:

```yaml
ScheduledReport:
  Type: AWS::Events::Rule
  Properties:
    ScheduleExpression: "rate(7 days)"  # Weekly
    Targets:
      - Arn: !GetAtt AWSBillWhispererFunction.Arn
        Id: BillWhispererScheduled
```

### Multi-Channel Support

To post to multiple Slack channels:

```python
webhooks = [
    "https://hooks.slack.com/services/.../CHANNEL1",
    "https://hooks.slack.com/services/.../CHANNEL2",
]
for webhook in webhooks:
    post_to_slack(webhook, message)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "invalid_auth" | Check webhook URL is complete |
| "channel_not_found" | Re-add webhook to workspace |
| No messages received | Check Lambda logs in CloudWatch |
| Malformed messages | Verify `formatter.to_slack()` returns valid JSON |

## Security

⚠️ **Never commit webhook URLs to version control!**

- Store them in AWS Secrets Manager
- Or use AWS Systems Manager Parameter Store
- Use environment variables for local testing

## Sample Output

```
📊 AWS Bill Summary: February 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 Total: $1,247.32 (+18% from January)
🔥 Biggest change: EC2 (+$156)
💡 Potential savings: $279/mo
```

---

*For more advanced integrations, consider using the Slack Bolt SDK for error handling and interactive messages.*
