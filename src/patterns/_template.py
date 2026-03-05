"""
Pattern XXX: [Pattern Name]
[Brief description of what this pattern detects]

COPY THIS FILE to create a new pattern:
1. Copy to pXXX_your_pattern_name.py
2. Update PATTERN_ID, NAME, DESCRIPTION
3. Implement scan() method
4. Optionally implement fix() method
"""


from .base import BasePattern, Complexity, Finding


class TemplatePattern(BasePattern):
    # Unique pattern ID (used for sorting and identification)
    PATTERN_ID = "999"

    # Human-readable name
    NAME = "Template Pattern"

    # Description shown in help/docs
    DESCRIPTION = "Template - copy this to create new patterns"

    # How hard to implement: EASY, MEDIUM, HARD
    COMPLEXITY = Complexity.EASY

    # AWS services this pattern checks
    SERVICES = ["ec2"]

    def scan(self, regions: list[str] = None) -> list[Finding]:
        """
        Implement your scanning logic here.
        
        1. Get regions to scan
        2. For each region, query AWS APIs
        3. Analyze results for waste patterns
        4. Create Finding objects for each issue
        5. Return list of findings
        """
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                # Create boto3 client
                # client = self.session.client('service', region_name=region)

                # Query AWS
                # response = client.describe_something()

                # Analyze results
                # for item in response['Items']:
                #     if is_waste(item):
                #         finding = Finding(
                #             resource_id=item['Id'],
                #             resource_type="Resource Type",
                #             region=region,
                #             monthly_cost=calculate_cost(item),
                #             recommendation="What to do",
                #             severity=Severity.MEDIUM,
                #             safe_to_fix=True,
                #             fix_command="aws ... command",
                #             metadata={"key": "value"}
                #         )
                #         self._findings.append(finding)

                pass  # Remove this when implementing

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        """
        Optional: Implement automated fix.
        
        Only implement if the fix is safe and reversible.
        Always check finding.safe_to_fix first.
        """
        if not finding.safe_to_fix:
            raise ValueError(f"Cannot safely fix {finding.resource_id}")

        if dry_run:
            print(f"[DRY RUN] Would fix {finding.resource_id}")
            return True

        # Implement actual fix here
        # client = self.session.client('service', region_name=finding.region)
        # client.delete_thing(Id=finding.resource_id)

        return True
