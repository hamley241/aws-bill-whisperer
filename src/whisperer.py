#!/usr/bin/env python3
"""
AWS Bill Whisperer - Detect cloud cost waste patterns
"""

import click

# Pattern implementations will go here

@click.group()
def cli():
    """AWS Bill Whisperer - Find and fix cloud cost waste"""
    pass

@cli.command()
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.option('--fix', is_flag=True, help='Apply fixes (dry-run by default)')
def scan(output_json, fix):
    """Scan AWS account for cost waste patterns"""
    click.echo("🔍 Scanning AWS account for waste patterns...")
    # TODO: Implement scan logic
    pass

@cli.command()
def patterns():
    """List all detectable waste patterns"""
    patterns = [
        ("1", "Unattached EBS Volumes", "EASY", "✅"),
        ("2", "Unattached Elastic IPs", "EASY", "✅"),
        ("3", "gp2 → gp3 Migration", "EASY", "✅"),
        ("4", "Idle EC2 Instances", "EASY", "✅"),
        ("5", "Old EBS Snapshots", "EASY", "✅"),
        ("6", "NAT Gateway Waste", "MEDIUM", "⭐"),
        ("7", "Idle RDS Instances", "EASY", "✅"),
    ]
    click.echo("\n📋 Detectable Patterns:\n")
    for num, name, complexity, status in patterns:
        click.echo(f"  {num}. {name} [{complexity}] {status}")
    click.echo("\n⭐ = Differentiator (unique to this tool)\n")

if __name__ == '__main__':
    cli()
