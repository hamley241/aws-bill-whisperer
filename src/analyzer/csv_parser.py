"""Parse AWS Cost & Usage Report (CUR) CSV files."""

import csv
import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_cur_csv(file_path: str | Path) -> dict:
    """
    Parse an AWS Cost & Usage Report CSV file.

    Args:
        file_path: Path to the CUR CSV file

    Returns:
        Cost data dict compatible with get_full_analysis() output
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"CUR file not found: {file_path}")

    service_costs: dict[str, float] = defaultdict(float)
    region_costs: dict[str, float] = defaultdict(float)
    daily_costs: dict[str, float] = defaultdict(float)
    total_cost = 0.0
    start_date = None
    end_date = None

    with open(file_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Handle different CUR column naming conventions
            cost = _extract_cost(row)
            service = _extract_service(row)
            region = _extract_region(row)
            date = _extract_date(row)

            if cost and cost > 0:
                total_cost += cost
                if service:
                    service_costs[service] += cost
                if region:
                    region_costs[region] += cost
                if date:
                    daily_costs[date] += cost
                    if start_date is None or date < start_date:
                        start_date = date
                    if end_date is None or date > end_date:
                        end_date = date

    # Build output structure
    services_list = []
    for name, cost in sorted(service_costs.items(), key=lambda x: x[1], reverse=True):
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        services_list.append({
            "name": _format_service_name(name),
            "cost": round(cost, 2),
            "percent": round(pct, 1)
        })

    regions_list = []
    for name, cost in sorted(region_costs.items(), key=lambda x: x[1], reverse=True):
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        regions_list.append({
            "name": name or "global",
            "cost": round(cost, 2),
            "percent": round(pct, 1)
        })

    daily_list = []
    for date, cost in sorted(daily_costs.items()):
        daily_list.append({"date": date, "cost": round(cost, 2)})

    return {
        "usage": {
            "period": {
                "start": start_date or "unknown",
                "end": end_date or "unknown"
            },
            "total": round(total_cost, 2),
            "services": services_list[:20]  # Top 20
        },
        "regions": {
            "total": round(total_cost, 2),
            "regions": regions_list[:10]  # Top 10
        },
        "daily": daily_list,
        "comparison": None  # CSV doesn't have comparison data
    }


def _extract_cost(row: dict) -> float | None:
    """Extract cost from various possible column names."""
    cost_columns = [
        'lineItem/UnblendedCost',
        'lineItem/BlendedCost',
        'UnblendedCost',
        'BlendedCost',
        'Cost',
        'cost'
    ]
    for col in cost_columns:
        if col in row and row[col]:
            try:
                return float(row[col])
            except ValueError:
                continue
    return None


def _extract_service(row: dict) -> str | None:
    """Extract service name from various possible column names."""
    service_columns = [
        'lineItem/ProductCode',
        'product/ProductName',
        'ProductCode',
        'Service',
        'service'
    ]
    for col in service_columns:
        if col in row and row[col]:
            return row[col]
    return None


def _extract_region(row: dict) -> str | None:
    """Extract region from various possible column names."""
    region_columns = [
        'product/region',
        'product/Region',
        'Region',
        'region'
    ]
    for col in region_columns:
        if col in row and row[col]:
            return row[col]
    return None


def _extract_date(row: dict) -> str | None:
    """Extract date from various possible column names."""
    date_columns = [
        'identity/TimeInterval',
        'bill/BillingPeriodStartDate',
        'UsageStartDate',
        'Date',
        'date'
    ]
    for col in date_columns:
        if col in row and row[col]:
            date_str = row[col]
            # Handle TimeInterval format: 2026-01-18T00:00:00Z/2026-01-19T00:00:00Z
            if '/' in date_str and 'T' in date_str:
                date_str = date_str.split('/')[0].split('T')[0]
            # Handle ISO format
            elif 'T' in date_str:
                date_str = date_str.split('T')[0]
            return date_str[:10]  # YYYY-MM-DD
    return None


def _format_service_name(name: str) -> str:
    """Clean up AWS service names for readability."""
    replacements = {
        'AmazonEC2': 'EC2',
        'AmazonS3': 'S3',
        'AmazonRDS': 'RDS',
        'AWSLambda': 'Lambda',
        'AmazonCloudFront': 'CloudFront',
        'AmazonDynamoDB': 'DynamoDB',
        'AmazonSNS': 'SNS',
        'AmazonSQS': 'SQS',
        'AWSCloudTrail': 'CloudTrail',
        'AmazonRoute53': 'Route 53',
        'AmazonECR': 'ECR',
        'AmazonECS': 'ECS',
        'AmazonEKS': 'EKS',
        'Amazon Elastic Compute Cloud': 'EC2',
        'Amazon Simple Storage Service': 'S3',
        'Amazon Relational Database Service': 'RDS',
        'AWS Lambda': 'Lambda',
    }
    for old, new in replacements.items():
        if old in name:
            return new
    return name
