"""Cost optimization recommendation rules.

This module contains rule-based recommendations for AWS cost optimization.
These are applied alongside LLM analysis to provide concrete, actionable advice.
"""

from typing import Any


def generate_recommendations(cost_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate cost optimization recommendations based on cost data.

    Args:
        cost_data: Dictionary containing usage, comparison, and regions data

    Returns:
        List of recommendation dictionaries
    """
    recommendations = []
    services = cost_data.get("usage", {}).get("services", [])
    service_names = {s["name"].lower() for s in services}

    # EC2 recommendations
    ec2_services = [s for s in services if "ec2" in s["name"].lower()]
    if ec2_services:
        ec2_cost = max(ec2_services, key=lambda x: x["cost"])["cost"]
        if ec2_cost > 200:
            recommendations.append({
                "service": "EC2",
                "recommendation": "Consider using Reserved Instances or Savings Plans",
                "potential_savings": f"~{ec2_cost * 0.3:.0f}/mo",
                "priority": "high"
            })
        recommendations.append({
            "service": "EC2",
            "recommendation": "Review and terminate stopped instances",
            "potential_savings": f"~{ec2_cost * 0.1:.0f}/mo",
            "priority": "medium"
        })

    # S3 recommendations
    s3_services = [s for s in services if "s3" in s["name"].lower()]
    if s3_services:
        s3_cost = max(s3_services, key=lambda x: x["cost"])["cost"]
        if s3_cost > 50:
            recommendations.append({
                "service": "S3",
                "recommendation": "Enable S3 Intelligent-Tiering for infrequently accessed data",
                "potential_savings": f"~{s3_cost * 0.2:.0f}/mo",
                "priority": "medium"
            })

    # RDS recommendations
    rds_services = [s for s in services if "rds" in s["name"].lower()]
    if rds_services:
        rds_cost = max(rds_services, key=lambda x: x["cost"])["cost"]
        if rds_cost > 100:
            recommendations.append({
                "service": "RDS",
                "recommendation": "Consider RDS Reserved Instances for production databases",
                "potential_savings": f"~{rds_cost * 0.4:.0f}/mo",
                "priority": "high"
            })
        recommendations.append({
            "service": "RDS",
            "recommendation": "Review snapshot retention periods and delete old manual snapshots",
            "potential_savings": "varies",
            "priority": "low"
        })

    # Lambda recommendations
    lambda_services = [s for s in services if "lambda" in s["name"].lower()]
    if lambda_services:
        lambda_cost = max(lambda_services, key=lambda x: x["cost"])["cost"]
        if lambda_cost > 10:
            recommendations.append({
                "service": "Lambda",
                "recommendation": "Review function memory allocation (often over-provisioned)",
                "potential_savings": f"~{lambda_cost * 0.2:.0f}/mo",
                "priority": "low"
            })

    # CloudFront recommendations
    cf_services = [s for s in services if "cloudfront" in s["name"].lower()]
    if not cf_services and "s3" in service_names:
        recommendations.append({
            "service": "CloudFront",
            "recommendation": "Consider using CloudFront for S3 data transfer to reduce costs",
            "potential_savings": "varies",
            "priority": "low"
        })

    return sorted(recommendations, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])


def calculate_total_potential_savings(recommendations: list[dict]) -> str:
    """Calculate total potential monthly savings from recommendations.

    Args:
        recommendations: List of recommendation dictionaries

    Returns:
        Formatted string of potential savings
    """
    total = 0
    varies_count = 0

    for rec in recommendations:
        savings = rec.get("potential_savings", "")
        if "mo" in savings:
            try:
                value = float(savings.replace("~", "").replace("/mo", "").strip())
                total += value
            except ValueError:
                varies_count += 1

    if total > 0:
        result = f"${total:.0f}/mo"
        if varies_count > 0:
            result += " + varies"
        return result
    return "varies"
