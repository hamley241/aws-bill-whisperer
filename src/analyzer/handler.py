"""AWS Lambda handler for Bill Whisperer."""

import json
import logging
import os
import urllib.request
from typing import Any

from .cost_explorer import get_full_analysis
from .formatter import to_json, to_markdown, to_slack
from .llm import analyze_costs

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context) -> dict[str, Any]:
    """
    Lambda entry point for AWS Bill Whisperer.

    Event parameters:
        days (int): Number of days to analyze (default: 30)
        output (str): Output format - markdown, json, slack (default: markdown)
        provider (str): LLM provider - bedrock, openai (default: bedrock)
        slack_webhook (str): Slack webhook URL (optional, sends to Slack if provided)

    Environment variables:
        LLM_PROVIDER: Default LLM provider
        SLACK_WEBHOOK: Default Slack webhook URL
        OPENAI_API_KEY: Required if using OpenAI provider

    Returns:
        Analysis result in requested format
    """
    logger.info(f"Received event: {json.dumps(event)}")

    # Get parameters from event or environment
    days = event.get('days', int(os.environ.get('ANALYSIS_DAYS', 30)))
    output_format = event.get('output', 'markdown')
    provider = event.get('provider', os.environ.get('LLM_PROVIDER', 'bedrock'))
    slack_webhook = event.get('slack_webhook', os.environ.get('SLACK_WEBHOOK'))

    try:
        # Fetch comprehensive cost data
        logger.info(f"Fetching cost data for last {days} days...")
        cost_data = get_full_analysis(days)
        logger.info(f"Total cost: ${cost_data['usage']['total']:,.2f}")

        # Analyze with LLM
        logger.info(f"Analyzing with {provider}...")
        analysis = analyze_costs(cost_data, provider=provider)
        logger.info("Analysis complete")

        # Format output
        if output_format == 'slack':
            result = to_slack(analysis, cost_data)
        elif output_format == 'json':
            result = to_json(analysis, cost_data)
        else:
            result = to_markdown(analysis, cost_data)

        # Send to Slack if webhook provided
        if slack_webhook:
            _send_to_slack(slack_webhook, analysis, cost_data)
            logger.info("Sent to Slack")

        return {
            'statusCode': 200,
            'body': result if isinstance(result, str) else json.dumps(result)
        }

    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Cost analysis failed. Check CloudWatch logs for details.'
            })
        }


def _send_to_slack(webhook_url: str, analysis: str, cost_data: dict) -> None:
    """Send analysis to Slack webhook."""
    slack_payload = to_slack(analysis, cost_data)

    data = json.dumps(slack_payload).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                logger.warning(f"Slack webhook returned status {response.status}")
    except Exception as e:
        logger.error(f"Failed to send to Slack: {e}")
        # Don't raise - Slack failure shouldn't fail the whole analysis
