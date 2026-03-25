#!/usr/bin/env python3
"""
AWS Bill Whisperer - Modular Pattern Scanner

Scan your AWS infrastructure for cost waste and optimization opportunities.

Usage:
    python whisper.py scan                    # Scan all patterns
    python whisper.py scan --pattern 001      # Scan specific pattern
    python whisper.py scan --json             # Output JSON
    python whisper.py fix 001 vol-12345678    # Apply fix for a finding
    python whisper.py fix 001 vol-12345678 --dry-run  # Preview fix
    python whisper.py patterns                # List available patterns
"""

import argparse
import json
import sys

from patterns import discover_patterns
from patterns.base import Finding


def get_pattern_by_id(pattern_id: str) -> type | None:
    """Get a pattern class by its ID."""
    patterns = discover_patterns()
    for pattern in patterns:
        if pattern_id.zfill(3) == pattern.PATTERN_ID:
            return pattern
    return None


def get_all_patterns() -> list[type]:
    """Get all discovered pattern classes."""
    return discover_patterns()


def format_finding(finding: Finding, verbose: bool = False) -> str:
    """Format a finding for display."""
    lines = [
        f"\n  📍 {finding.resource_type}: {finding.resource_id}",
        f"     Region: {finding.region}",
        f"     Monthly Cost: ${finding.monthly_cost:.2f}",
        f"     Severity: {finding.severity.value.upper()}",
        f"     Recommendation: {finding.recommendation}",
    ]
    if finding.safe_to_fix:
        lines.append("     ✅ Safe to auto-fix")
        if finding.fix_command:
            lines.append(f"     Fix: {finding.fix_command}")
    else:
        lines.append("     ⚠️  Manual review required")
    if verbose and finding.metadata:
        lines.append(f"     Metadata: {json.dumps(finding.metadata, indent=2)}")
    return "\n".join(lines)


def cmd_scan(args):
    """Execute scan command."""
    patterns_to_run = []

    if args.pattern:
        pattern_class = get_pattern_by_id(args.pattern)
        if not pattern_class:
            print(f"Error: Pattern '{args.pattern}' not found", file=sys.stderr)
            print("Run 'python whisper.py patterns' to see available patterns", file=sys.stderr)
            sys.exit(1)
        patterns_to_run = [pattern_class]
    else:
        patterns_to_run = get_all_patterns()

    if not patterns_to_run:
        print("No patterns found. Check the src/patterns/ directory.", file=sys.stderr)
        sys.exit(1)

    # Collect results
    all_findings = []
    pattern_results = []

    for pattern_class in patterns_to_run:
        pattern = pattern_class()
        if args.verbose:
            print(f"\n🔍 Scanning with {pattern.NAME} (ID: {pattern.PATTERN_ID})...")

        try:
            findings = pattern.scan(regions=args.regions)
            pattern_results.append({
                "pattern_id": pattern.PATTERN_ID,
                "name": pattern.NAME,
                "description": pattern.DESCRIPTION,
                "findings": [f.to_dict() for f in findings],
                "total_monthly_waste": sum(f.monthly_cost for f in findings),
                "finding_count": len(findings)
            })
            all_findings.extend(findings)
        except Exception as e:
            if args.verbose:
                import traceback
                traceback.print_exc()
            pattern_results.append({
                "pattern_id": pattern.PATTERN_ID,
                "name": pattern.NAME,
                "error": str(e),
                "findings": [],
                "total_monthly_waste": 0,
                "finding_count": 0
            })

    # Output results
    total_waste = sum(f.monthly_cost for f in all_findings)

    if args.json:
        output = {
            "patterns_scanned": len(patterns_to_run),
            "total_findings": len(all_findings),
            "total_monthly_waste": round(total_waste, 2),
            "patterns": pattern_results
        }
        print(json.dumps(output, indent=2))
    else:
        # Human readable output
        print("\n" + "=" * 60)
        print("AWS Bill Whisperer - Scan Results")
        print("=" * 60)

        for pattern_data in pattern_results:
            print(f"\n🔹 {pattern_data['name']} (ID: {pattern_data['pattern_id']})")
            print(f"   {pattern_data['description']}")

            if "error" in pattern_data:
                print(f"   ❌ ERROR: {pattern_data['error']}")
                continue

            findings = pattern_data['findings']
            if not findings:
                print("   ✅ No issues found")
                continue

            print(f"   Found {len(findings)} issue(s), monthly waste: ${pattern_data['total_monthly_waste']:.2f}")
            for f in findings:
                finding_obj = Finding(
                    resource_id=f['resource_id'],
                    resource_type=f['resource_type'],
                    region=f['region'],
                    monthly_cost=f['monthly_cost'],
                    recommendation=f['recommendation'],
                    severity=f['severity'],
                    safe_to_fix=f['safe_to_fix'],
                    fix_command=f.get('fix_command'),
                    metadata=f.get('metadata', {})
                )
                print(format_finding(finding_obj, args.verbose))

        print("\n" + "=" * 60)
        print(f"TOTAL MONTHLY WASTE: ${total_waste:.2f}")
        print(f"TOTAL ANNUAL WASTE: ${total_waste * 12:.2f}")
        print("=" * 60)


def cmd_fix(args):
    """Execute fix command."""
    if not args.pattern or not args.resource_id:
        print("Error: --pattern and --resource-id are required for fix", file=sys.stderr)
        sys.exit(1)

    pattern_class = get_pattern_by_id(args.pattern)
    if not pattern_class:
        print(f"Error: Pattern '{args.pattern}' not found", file=sys.stderr)
        sys.exit(1)

    pattern = pattern_class()

    # First scan to find the specific resource
    if args.verbose:
        print(f"Scanning with {pattern.NAME} to find {args.resource_id}...")

    findings = pattern.scan()
    target_finding = None
    for f in findings:
        if f.resource_id == args.resource_id:
            target_finding = f
            break

    if not target_finding:
        print(f"Error: Resource '{args.resource_id}' not found in pattern '{args.pattern}'", file=sys.stderr)
        sys.exit(1)

    # Apply fix
    try:
        result = pattern.fix(target_finding, dry_run=args.dry_run)
        if result:
            if args.dry_run:
                print(f"✅ Dry-run successful for {args.resource_id}")
            else:
                print(f"✅ Fixed {args.resource_id}")
        else:
            print(f"❌ Fix failed for {args.resource_id}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error fixing {args.resource_id}: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def cmd_patterns(args):
    """List available patterns."""
    patterns = get_all_patterns()

    if args.json:
        output = [
            {
                "id": p.PATTERN_ID,
                "name": p.NAME,
                "description": p.DESCRIPTION,
                "complexity": p.COMPLEXITY.value,
                "services": p.SERVICES
            }
            for p in patterns
        ]
        print(json.dumps(output, indent=2))
    else:
        print("\nAvailable patterns:\n")
        for pattern in patterns:
            print(f"  [{pattern.PATTERN_ID}] {pattern.NAME}")
            print(f"       {pattern.DESCRIPTION}")
            print(f"       Services: {', '.join(pattern.SERVICES)} | Complexity: {pattern.COMPLEXITY.value}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description='AWS Bill Whisperer - Find and fix AWS cost waste',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s scan                          # Scan all patterns
  %(prog)s scan --pattern 001            # Scan only pattern 001
  %(prog)s scan --json                   # JSON output
  %(prog)s fix 001 vol-12345 --dry-run   # Preview fix
  %(prog)s fix 001 vol-12345             # Apply fix
  %(prog)s patterns                      # List available patterns
"""
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Scan command
    scan_parser = subparsers.add_parser('scan', help='Scan for waste')
    scan_parser.add_argument('--pattern', '-p', type=str, help='Pattern ID to scan (e.g., 001)')
    scan_parser.add_argument('--json', '-j', action='store_true', help='Output JSON')
    scan_parser.add_argument('--dry-run', '-d', action='store_true', help='Dry run mode')
    scan_parser.add_argument('--regions', '-r', nargs='+', help='Specific regions to scan')
    scan_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    # Fix command
    fix_parser = subparsers.add_parser('fix', help='Fix a specific finding')
    fix_parser.add_argument('pattern_id', help='Pattern ID')
    fix_parser.add_argument('resource_id', help='Resource ID to fix')
    fix_parser.add_argument('--dry-run', '-d', action='store_true', help='Preview fix without applying')
    fix_parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    # Patterns command
    patterns_parser = subparsers.add_parser('patterns', help='List available patterns')
    patterns_parser.add_argument('--json', '-j', action='store_true', help='Output JSON')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == 'scan':
            cmd_scan(args)
        elif args.command == 'fix':
            # Convert positional args to match scan style
            class Args:
                pass
            fix_args = Args()
            fix_args.pattern = args.pattern_id
            fix_args.resource_id = args.resource_id
            fix_args.dry_run = args.dry_run
            fix_args.verbose = args.verbose
            cmd_fix(fix_args)
        elif args.command == 'patterns':
            cmd_patterns(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if hasattr(args, 'verbose') and args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
