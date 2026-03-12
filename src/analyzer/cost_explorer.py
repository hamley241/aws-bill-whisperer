"""AWS Cost Explorer client for fetching and parsing cost data."""

def format_service_name(service_name: str) -> str:
    """Format AWS service name for display."""
    if service_name.startswith("Amazon "):
        return service_name.replace("Amazon ", "", 1)
    if service_name.startswith("AWS "):
        return service_name.replace("AWS ", "", 1)
    return service_name

import logging
from datetime import datetime, timedelta

import boto3

logger = logging.getLogger(__name__)


def get_cost_and_usage(days: int = 30) -> dict:
    """
    Get costs grouped by service for the last N days.

    Returns:
        {
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "total": 1247.32,
            "services": [
                {"name": "Amazon EC2", "cost": 523.41, "percent": 42.0},
                {"name": "Amazon RDS", "cost": 312.18, "percent": 25.0},
                ...
            ]
        }
    """
    client = boto3.client('ce')
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start, 'End': end},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
        )
    except Exception as e:
        logger.error(f"Failed to fetch cost data: {e}")
        raise

    # Aggregate costs by service across all time periods
    service_costs = {}
    for period in response.get('ResultsByTime', []):
        for group in period.get('Groups', []):
            service_name = group['Keys'][0]
            cost = float(group['Metrics']['UnblendedCost']['Amount'])
            service_costs[service_name] = service_costs.get(service_name, 0) + cost

    total = sum(service_costs.values())

    # Build sorted list with percentages
    services = []
    for name, cost in sorted(service_costs.items(), key=lambda x: x[1], reverse=True):
        if cost > 0.01:  # Filter out near-zero costs
            services.append({
                "name": name,
                "cost": round(cost, 2),
                "percent": round((cost / total * 100) if total > 0 else 0, 1)
            })

    return {
        "period": {"start": start, "end": end},
        "total": round(total, 2),
        "services": services
    }


def get_daily_costs(days: int = 30) -> list[dict]:
    """
    Get daily cost trend.

    Returns:
        [
            {"date": "2026-01-15", "cost": 41.23},
            {"date": "2026-01-16", "cost": 38.91},
            ...
        ]
    """
    client = boto3.client('ce')
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start, 'End': end},
            Granularity='DAILY',
            Metrics=['UnblendedCost']
        )
    except Exception as e:
        logger.error(f"Failed to fetch daily costs: {e}")
        raise

    daily_costs = []
    for period in response.get('ResultsByTime', []):
        date = period['TimePeriod']['Start']
        cost = float(period.get('Total', {}).get('UnblendedCost', {}).get('Amount', 0))
        daily_costs.append({
            "date": date,
            "cost": round(cost, 2)
        })

    return daily_costs


def get_cost_by_region(days: int = 30) -> dict:
    """
    Get costs grouped by region.

    Returns:
        {
            "total": 1247.32,
            "regions": [
                {"name": "us-east-1", "cost": 800.00, "percent": 64.1},
                {"name": "us-west-2", "cost": 400.00, "percent": 32.1},
                ...
            ]
        }
    """
    client = boto3.client('ce')
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start, 'End': end},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'REGION'}]
        )
    except Exception as e:
        logger.error(f"Failed to fetch regional costs: {e}")
        raise

    region_costs = {}
    for period in response.get('ResultsByTime', []):
        for group in period.get('Groups', []):
            region = group['Keys'][0] or 'global'
            cost = float(group['Metrics']['UnblendedCost']['Amount'])
            region_costs[region] = region_costs.get(region, 0) + cost

    total = sum(region_costs.values())

    regions = []
    for name, cost in sorted(region_costs.items(), key=lambda x: x[1], reverse=True):
        if cost > 0.01:
            regions.append({
                "name": name,
                "cost": round(cost, 2),
                "percent": round((cost / total * 100) if total > 0 else 0, 1)
            })

    return {"total": round(total, 2), "regions": regions}


def get_comparison(days: int = 30) -> dict:
    """
    Compare current period to previous period of same length.

    Returns:
        {
            "current": {"start": "2026-01-01", "end": "2026-01-31", "total": 1247.32},
            "previous": {"start": "2025-12-01", "end": "2025-12-31", "total": 1056.00},
            "change": 191.32,
            "change_percent": 18.1,
            "service_changes": [
                {"name": "EC2", "current": 523, "previous": 367, ...},
            ]
        }
    """
    client = boto3.client('ce')

    # Current period
    current_end = datetime.now()
    current_start = current_end - timedelta(days=days)

    # Previous period (same length, immediately before)
    previous_end = current_start
    previous_start = previous_end - timedelta(days=days)

    def fetch_period(start: datetime, end: datetime) -> dict:
        response = client.get_cost_and_usage(
            TimePeriod={
                'Start': start.strftime('%Y-%m-%d'),
                'End': end.strftime('%Y-%m-%d')
            },
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
        )

        service_costs = {}
        for period in response.get('ResultsByTime', []):
            for group in period.get('Groups', []):
                service_name = group['Keys'][0]
                cost = float(group['Metrics']['UnblendedCost']['Amount'])
                service_costs[service_name] = service_costs.get(service_name, 0) + cost

        return service_costs

    try:
        current_costs = fetch_period(current_start, current_end)
        previous_costs = fetch_period(previous_start, previous_end)
    except Exception as e:
        logger.error(f"Failed to fetch comparison data: {e}")
        raise

    current_total = sum(current_costs.values())
    previous_total = sum(previous_costs.values())

    change = current_total - previous_total
    change_percent = ((change / previous_total) * 100) if previous_total > 0 else 0

    # Calculate per-service changes
    all_services = set(current_costs.keys()) | set(previous_costs.keys())
    service_changes = []

    for service in all_services:
        curr = current_costs.get(service, 0)
        prev = previous_costs.get(service, 0)
        svc_change = curr - prev
        svc_change_pct = ((svc_change / prev) * 100) if prev > 0 else (100 if curr > 0 else 0)

        if abs(svc_change) > 1:  # Only include meaningful changes
            service_changes.append({
                "name": service,
                "current": round(curr, 2),
                "previous": round(prev, 2),
                "change": round(svc_change, 2),
                "change_percent": round(svc_change_pct, 1)
            })

    # Sort by absolute change
    service_changes.sort(key=lambda x: abs(x['change']), reverse=True)

    return {
        "current": {
            "start": current_start.strftime('%Y-%m-%d'),
            "end": current_end.strftime('%Y-%m-%d'),
            "total": round(current_total, 2)
        },
        "previous": {
            "start": previous_start.strftime('%Y-%m-%d'),
            "end": previous_end.strftime('%Y-%m-%d'),
            "total": round(previous_total, 2)
        },
        "change": round(change, 2),
        "change_percent": round(change_percent, 1),
        "service_changes": service_changes[:10]  # Top 10 changes
    }


def get_full_analysis(days: int = 30) -> dict:
    """
    Get comprehensive cost data for LLM analysis.
    Combines all metrics into one structure.
    """
    return {
        "usage": get_cost_and_usage(days),
        "daily": get_daily_costs(days),
        "regions": get_cost_by_region(days),
        "comparison": get_comparison(days)
    }
