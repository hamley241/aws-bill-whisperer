"""
Tests for Pattern 010: Idle Load Balancers
GIVEN-WHEN-THEN format for BDD-style testing
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, 'src')

from patterns.p010_idle_load_balancer import IdleLoadBalancerPattern
from patterns.base import Severity


class TestIdleLoadBalancerPattern:
    """Tests for P010: Idle Load Balancers"""

    def test_finds_alb_with_no_targets(self):
        """
        GIVEN: An ALB with no registered targets
        WHEN: The pattern scans for idle load balancers
        THEN: It returns a finding with monthly cost
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        created_time = datetime.now(timezone.utc) - timedelta(days=60)
        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/idle-alb/abc123',
                'LoadBalancerName': 'idle-alb',
                'Type': 'application',
                'State': {'Code': 'active'},
                'CreatedTime': created_time,
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {
            'TargetGroups': [{'TargetGroupArn': 'arn:aws:elasticloadbalancing:tg/idle-tg/xyz'}]
        }

        # No targets
        mock_elbv2.describe_target_health.return_value = {
            'TargetHealthDescriptions': []
        }

        mock_cloudwatch.get_metric_statistics.return_value = {'Datapoints': []}

        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'idle-alb'
        assert findings[0].resource_type == 'ALB Load Balancer'
        assert 'no targets registered' in findings[0].metadata['idle_reason']
        assert findings[0].monthly_cost > 0

    def test_finds_nlb_with_zero_traffic(self):
        """
        GIVEN: An NLB with zero request count over 30 days
        WHEN: The pattern scans
        THEN: It returns a finding
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        created_time = datetime.now(timezone.utc) - timedelta(days=45)
        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/net/no-traffic-nlb/def456',
                'LoadBalancerName': 'no-traffic-nlb',
                'Type': 'network',
                'State': {'Code': 'active'},
                'CreatedTime': created_time,
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {
            'TargetGroups': [{'TargetGroupArn': 'arn:aws:elasticloadbalancing:tg/active-tg/abc'}]
        }

        # Has targets but no traffic
        mock_elbv2.describe_target_health.return_value = {
            'TargetHealthDescriptions': [
                {'TargetHealth': {'State': 'healthy'}},
            ]
        }

        # Zero traffic
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Sum': 0}]
        }

        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'no-traffic-nlb'
        assert 'zero requests' in str(findings[0].metadata['idle_reason'])

    def test_finds_classic_elb_idle(self):
        """
        GIVEN: A Classic ELB with no instances
        WHEN: The pattern scans
        THEN: It returns a finding mentioning deprecation
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': []
        }

        created_time = datetime.now(timezone.utc) - timedelta(days=365)
        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': [{
                'LoadBalancerName': 'old-classic-elb',
                'CreatedTime': created_time,
                'Instances': [],
            }]
        }

        mock_cloudwatch.get_metric_statistics.return_value = {'Datapoints': []}

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].resource_id == 'old-classic-elb'
        assert findings[0].resource_type == 'Classic Load Balancer'
        assert 'deprecated' in findings[0].recommendation
        assert findings[0].metadata['is_deprecated'] is True

    def test_skips_active_load_balancer(self):
        """
        GIVEN: An ALB with healthy targets and traffic
        WHEN: The pattern scans
        THEN: It returns no findings
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/active-alb/ghi789',
                'LoadBalancerName': 'active-alb',
                'Type': 'application',
                'State': {'Code': 'active'},
                'CreatedTime': datetime.now(timezone.utc) - timedelta(days=30),
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {
            'TargetGroups': [{'TargetGroupArn': 'arn:aws:elasticloadbalancing:tg/prod-tg/xyz'}]
        }

        # Has healthy targets
        mock_elbv2.describe_target_health.return_value = {
            'TargetHealthDescriptions': [
                {'TargetHealth': {'State': 'healthy'}},
                {'TargetHealth': {'State': 'healthy'}},
            ]
        }

        # Has traffic
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Sum': 1000000}]
        }

        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_high_severity_for_old_idle_lb(self):
        """
        GIVEN: An idle LB older than 30 days with no targets
        WHEN: The pattern scans
        THEN: Finding has HIGH severity
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        created_time = datetime.now(timezone.utc) - timedelta(days=90)
        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/old-idle-alb/xyz',
                'LoadBalancerName': 'old-idle-alb',
                'Type': 'application',
                'State': {'Code': 'active'},
                'CreatedTime': created_time,
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {'TargetGroups': []}
        mock_cloudwatch.get_metric_statistics.return_value = {'Datapoints': []}
        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].metadata['age_days'] == 90

    def test_safe_to_fix_for_old_no_target_lb(self):
        """
        GIVEN: An idle LB with no targets older than 7 days
        WHEN: The pattern scans
        THEN: Finding is marked safe to fix
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        created_time = datetime.now(timezone.utc) - timedelta(days=15)
        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/orphan-alb/abc',
                'LoadBalancerName': 'orphan-alb',
                'Type': 'application',
                'State': {'Code': 'active'},
                'CreatedTime': created_time,
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {'TargetGroups': []}
        mock_cloudwatch.get_metric_statistics.return_value = {'Datapoints': []}
        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        assert findings[0].safe_to_fix is True
        assert findings[0].fix_command is not None

    def test_unsafe_to_fix_with_targets(self):
        """
        GIVEN: An LB with targets but zero traffic
        WHEN: The pattern scans
        THEN: Finding is NOT safe to fix (has targets)
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        created_time = datetime.now(timezone.utc) - timedelta(days=30)
        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/has-targets/xyz',
                'LoadBalancerName': 'has-targets',
                'Type': 'application',
                'State': {'Code': 'active'},
                'CreatedTime': created_time,
            }]
        }

        mock_elbv2.describe_target_groups.return_value = {
            'TargetGroups': [{'TargetGroupArn': 'arn:aws:elasticloadbalancing:tg/tg1/abc'}]
        }

        # Has unhealthy targets
        mock_elbv2.describe_target_health.return_value = {
            'TargetHealthDescriptions': [
                {'TargetHealth': {'State': 'unhealthy'}},
            ]
        }

        # Zero traffic
        mock_cloudwatch.get_metric_statistics.return_value = {
            'Datapoints': [{'Sum': 0}]
        }

        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 1
        # Has targets (even if unhealthy), so not safe to auto-delete
        assert findings[0].safe_to_fix is False

    def test_skips_provisioning_lb(self):
        """
        GIVEN: An LB in provisioning state
        WHEN: The pattern scans
        THEN: It skips the LB
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_elb = MagicMock()
        mock_cloudwatch = MagicMock()

        def client_factory(service, **kwargs):
            return {
                'elbv2': mock_elbv2,
                'elb': mock_elb,
                'cloudwatch': mock_cloudwatch,
            }[service]

        mock_session.client.side_effect = client_factory

        mock_elbv2.describe_load_balancers.return_value = {
            'LoadBalancers': [{
                'LoadBalancerArn': 'arn:aws:elasticloadbalancing:us-east-1:123456789:loadbalancer/app/new-alb/xyz',
                'LoadBalancerName': 'new-alb',
                'Type': 'application',
                'State': {'Code': 'provisioning'},  # Still provisioning
                'CreatedTime': datetime.now(timezone.utc),
            }]
        }

        mock_elb.describe_load_balancers.return_value = {
            'LoadBalancerDescriptions': []
        }

        pattern = IdleLoadBalancerPattern(session=mock_session)
        pattern.get_all_regions = lambda: ['us-east-1']

        # WHEN
        findings = pattern.scan()

        # THEN
        assert len(findings) == 0

    def test_fix_dry_run(self):
        """
        GIVEN: A safe-to-fix finding
        WHEN: Fix is called with dry_run=True
        THEN: It returns True without deleting
        """
        # GIVEN
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_session.client.return_value = mock_elbv2

        from patterns.base import Finding, Severity

        finding = Finding(
            resource_id='test-alb',
            resource_type='ALB Load Balancer',
            region='us-east-1',
            monthly_cost=16.20,
            recommendation='Delete idle ALB',
            severity=Severity.HIGH,
            safe_to_fix=True,
            fix_command='aws elbv2 delete-load-balancer ...',
            metadata={
                'lb_arn': 'arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/test-alb/abc',
                'lb_type': 'application',
                'has_targets': False,
            }
        )

        pattern = IdleLoadBalancerPattern(session=mock_session)

        # WHEN
        result = pattern.fix(finding, dry_run=True)

        # THEN
        assert result is True
        mock_elbv2.delete_load_balancer.assert_not_called()

    def test_fix_raises_for_unsafe(self):
        """
        GIVEN: A finding not marked safe to fix
        WHEN: Fix is called
        THEN: It raises ValueError
        """
        # GIVEN
        mock_session = MagicMock()

        from patterns.base import Finding, Severity

        finding = Finding(
            resource_id='test-alb',
            resource_type='ALB Load Balancer',
            region='us-east-1',
            monthly_cost=16.20,
            recommendation='Review before deleting',
            severity=Severity.MEDIUM,
            safe_to_fix=False,  # Not safe
            metadata={'has_targets': True}
        )

        pattern = IdleLoadBalancerPattern(session=mock_session)

        # WHEN / THEN
        with pytest.raises(ValueError) as exc_info:
            pattern.fix(finding, dry_run=False)

        assert 'has targets or is too new' in str(exc_info.value)
