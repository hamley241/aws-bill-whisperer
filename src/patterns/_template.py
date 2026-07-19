"""
Pattern XXX: [Pattern Name]
[Brief description of what this pattern detects]

COPY THIS FILE to create a new pattern:
1. Copy to pXXX_your_pattern_name.py
2. Update PATTERN_ID, NAME, DESCRIPTION
3. Implement scan() method
4. Optionally override remediate(finding, mode) for the modes this pattern
   supports (dry_run, command, pr, api_call). The base class handles
   dry_run and command off finding.fix_command; override to add pr /
   api_call. See p001_unattached_ebs.py for the reference implementation.
"""


import logging

from .base import (
    BasePattern,
    Complexity,
    Finding,
    RemediationMode,
    RemediationResult,
)


logger = logging.getLogger(__name__)


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
                #             monthly_impact_usd=calculate_cost(item),
                #             summary="What to do",
                #             risk_tier=RiskTier.MEDIUM,
                #             safe_to_fix=True,
                #             fix_command="aws ... command",
                #             metadata={"key": "value"}
                #         )
                #         self._findings.append(finding)

                pass  # Remove this when implementing

            except Exception:
                logger.exception("template pattern error scanning region %s", region)
                continue

        return self._findings

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        """
        Optional: apply a fix in the requested mode (CLAUDE.md principle 4 —
        one entry point dispatching on mode).

        The base class already handles DRY_RUN and COMMAND off
        finding.fix_command. Override here to add PR (emit an IaC diff) and
        API_CALL (execute the AWS call, gated on finding.safe_to_fix). See
        p001_unattached_ebs.py for the reference bulletproof implementation.
        """
        # DRY_RUN / COMMAND are handled by the base class off fix_command.
        if mode in (RemediationMode.DRY_RUN, RemediationMode.COMMAND):
            return super().remediate(finding, mode)

        # if mode == RemediationMode.PR:
        #     return RemediationResult(
        #         finding_id=finding.id,
        #         pattern_id=self.PATTERN_ID,
        #         mode=mode,
        #         success=True,
        #         message="Terraform diff hint emitted",
        #         output=self._terraform_diff_hint(finding),
        #     )

        # if mode == RemediationMode.API_CALL:
        #     if not finding.safe_to_fix:
        #         return RemediationResult(
        #             finding_id=finding.id,
        #             pattern_id=self.PATTERN_ID,
        #             mode=mode,
        #             success=False,
        #             message=f"refusing to fix {finding.resource_id}: safety gate failed",
        #         )
        #     # client = self.session.client('service', region_name=finding.region)
        #     # client.delete_thing(Id=finding.resource_id)
        #     return RemediationResult(
        #         finding_id=finding.id,
        #         pattern_id=self.PATTERN_ID,
        #         mode=mode,
        #         success=True,
        #         message=f"fixed {finding.resource_id}",
        #     )

        # Modes this pattern doesn't support fall through to the base class,
        # which returns a "not supported" result (or raises on unknown modes).
        return super().remediate(finding, mode)
