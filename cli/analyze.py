#!/usr/bin/env python3
"""
AWS Bill Whisperer CLI

Analyze your AWS costs from the command line.
Uses your local AWS credentials.

Usage:
    python analyze.py --days 30
    python analyze.py --days 7 --output json
    python analyze.py --mock  # Test without AWS credentials
"""

import argparse
import json
import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from analyzer import cost_explorer, csv_parser, formatter, llm


def get_mock_data() -> dict:
    """Return mock cost data for testing without AWS credentials."""
    return {
        "usage": {
            "period": {"start": "2026-01-18", "end": "2026-02-18"},
            "total": 1247.32,
            "services": [
                {"name": "Amazon Elastic Compute Cloud - Compute", "cost": 523.41, "percent": 42.0},
                {"name": "Amazon Relational Database Service", "cost": 312.18, "percent": 25.0},
                {"name": "Amazon Simple Storage Service", "cost": 198.44, "percent": 15.9},
                {"name": "AWS Lambda", "cost": 87.22, "percent": 7.0},
                {"name": "Amazon CloudFront", "cost": 52.11, "percent": 4.2},
                {"name": "Amazon DynamoDB", "cost": 38.50, "percent": 3.1},
                {"name": "AWS Key Management Service", "cost": 18.23, "percent": 1.5},
                {"name": "Amazon Route 53", "cost": 12.00, "percent": 1.0},
                {"name": "Amazon Simple Queue Service", "cost": 5.23, "percent": 0.4},
            ]
        },
        "comparison": {
            "current": {"start": "2026-01-18", "end": "2026-02-18", "total": 1247.32},
            "previous": {"start": "2025-12-18", "end": "2026-01-18", "total": 1056.00},
            "change": 191.32,
            "change_percent": 18.1,
            "service_changes": [
                {
                    "name": "Amazon Elastic Compute Cloud - Compute",
                    "current": 523.41, "previous": 367.00,
                    "change": 156.41, "change_percent": 42.6
                },
                {
                    "name": "Amazon Simple Storage Service",
                    "current": 198.44, "previous": 109.00,
                    "change": 89.44, "change_percent": 82.1
                },
                {
                    "name": "Amazon Relational Database Service",
                    "current": 312.18, "previous": 270.00,
                    "change": 42.18, "change_percent": 15.6
                },
            ]
        },
        "regions": {
            "total": 1247.32,
            "regions": [
                {"name": "us-east-1", "cost": 798.29, "percent": 64.0},
                {"name": "us-west-2", "cost": 311.83, "percent": 25.0},
                {"name": "eu-west-1", "cost": 87.31, "percent": 7.0},
                {"name": "global", "cost": 49.89, "percent": 4.0},
            ]
        },
        "daily": [
            {"date": "2026-02-11", "cost": 41.58},
            {"date": "2026-02-12", "cost": 38.92},
            {"date": "2026-02-13", "cost": 42.11},
            {"date": "2026-02-14", "cost": 78.33},  # Spike
            {"date": "2026-02-15", "cost": 45.21},
            {"date": "2026-02-16", "cost": 40.87},
            {"date": "2026-02-17", "cost": 39.44},
            {"date": "2026-02-18", "cost": 41.22},
        ]
    }


def main():
    parser = argparse.ArgumentParser(
        description='AWS Bill Whisperer - Understand your AWS costs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --days 30                    # Analyze last 30 days
  %(prog)s --days 7 --output json       # Last week, JSON output
  %(prog)s --csv costs.csv              # Analyze from CUR CSV file
  %(prog)s --mock                       # Test with mock data
  %(prog)s --provider openai            # Use OpenAI instead of Bedrock

Environment variables:
  AWS_PROFILE         AWS profile to use
  AWS_REGION          AWS region (default: us-east-1)
  LLM_PROVIDER        Default LLM provider (bedrock/openai)
  OPENAI_API_KEY      Required for OpenAI provider
        """
    )

    parser.add_argument(
        '--days', '-d',
        type=int,
        default=30,
        help='Number of days to analyze (default: 30)'
    )
    parser.add_argument(
        '--output', '-o',
        choices=['markdown', 'json', 'slack', 'raw'],
        default='markdown',
        help='Output format (default: markdown)'
    )
    parser.add_argument(
        '--provider', '-p',
        choices=['bedrock', 'openai'],
        default=os.environ.get('LLM_PROVIDER', 'bedrock'),
        help='LLM provider (default: bedrock)'
    )
    parser.add_argument(
        '--model', '-m',
        help='Model override (e.g., gpt-4o, anthropic.claude-3-haiku...)'
    )
    parser.add_argument(
        '--csv', '-c',
        type=str,
        metavar='FILE',
        help='Analyze from CUR CSV file instead of Cost Explorer API'
    )
    parser.add_argument(
        '--mock',
        action='store_true',
        help='Use mock data (for testing without AWS credentials)'
    )
    parser.add_argument(
        '--no-llm',
        action='store_true',
        help='Skip LLM analysis, just show raw cost data'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )

    args = parser.parse_args()

    try:
        # Get cost data
        if args.mock:
            if args.verbose:
                print("Using mock data...", file=sys.stderr)
            cost_data = get_mock_data()
        elif args.csv:
            if args.verbose:
                print(f"Parsing CSV file: {args.csv}...", file=sys.stderr)
            cost_data = csv_parser.parse_cur_csv(args.csv)
        else:
            if args.verbose:
                print(f"Fetching cost data for last {args.days} days...", file=sys.stderr)
            cost_data = cost_explorer.get_full_analysis(days=args.days)

        if args.verbose:
            print(f"Total cost: ${cost_data['usage']['total']:,.2f}", file=sys.stderr)

        # Get LLM analysis (unless --no-llm or --mock)
        if args.no_llm:
            analysis = "Cost data retrieved. LLM analysis skipped (--no-llm flag)."
        elif args.mock:
            # Mock LLM response for fully offline testing
            analysis = """## Summary
Your AWS bill for the past month was $1,247.32, an **18% increase** from the previous period.

## Why the increase?
1. **EC2 in us-east-1: +$156** - 3 new instances were launched and are still running
2. **S3 Transfer: +$89** - Significant increase in data transfer, likely CloudFront cache misses
3. **RDS: +$42** - Storage auto-scaled from 100GB to 150GB

## Recommendations
- **Terminate unused EC2 instances** → Save ~$89/mo (3 stopped instances in us-east-1)
- **Enable S3 Intelligent Tiering** → Save ~$40/mo on infrequently accessed data
- **Consider Reserved Instances** for prod EC2 → Save ~$150/mo

💡 **Potential monthly savings: $279**

*Note: This is mock analysis for testing purposes.*"""
        else:
            if args.verbose:
                print(f"Analyzing with {args.provider}...", file=sys.stderr)
            analysis = llm.analyze_costs(
                cost_data,
                provider=args.provider,
                model=args.model
            )

        # Format and output
        if args.output == 'json':
            result = formatter.to_json(analysis, cost_data)
            print(json.dumps(result, indent=2))
        elif args.output == 'slack':
            result = formatter.to_slack(analysis, cost_data)
            print(json.dumps(result, indent=2))
        elif args.output == 'raw':
            print(json.dumps(cost_data, indent=2))
        else:
            result = formatter.to_markdown(analysis, cost_data)
            print(result)

    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
