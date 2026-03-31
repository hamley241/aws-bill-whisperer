"""
AWS Bill Whisperer - Compute Optimization Agent
Specialized agent for EC2, Lambda, and compute cost optimization using Strandsagents
"""

import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
from strands import Agent, tool
from strands_tools import memory, calculator


class ComputeOptimizationAgent:
    """Specialized agent for AWS compute cost optimization"""
    
    def __init__(self, aws_session=None):
        self.aws_session = aws_session or boto3.Session()
        self.ec2_client = self.aws_session.client('ec2')
        self.cloudwatch_client = self.aws_session.client('cloudwatch')
        self.lambda_client = self.aws_session.client('lambda')
        self.pricing_client = self.aws_session.client('pricing', region_name='us-east-1')
        
        # Instance pricing data (simplified)
        self.instance_pricing = {
            't3.nano': 0.0052, 't3.micro': 0.0104, 't3.small': 0.0208,
            't3.medium': 0.0416, 't3.large': 0.0832, 't3.xlarge': 0.1664,
            't3.2xlarge': 0.3328, 'm5.large': 0.096, 'm5.xlarge': 0.192,
            'm5.2xlarge': 0.384, 'm5.4xlarge': 0.768, 'c5.large': 0.085,
            'c5.xlarge': 0.17, 'c5.2xlarge': 0.34, 'c5.4xlarge': 0.68,
            'r5.large': 0.126, 'r5.xlarge': 0.252, 'r5.2xlarge': 0.504
        }
        
        # Initialize the agent with compute-specific tools
        self.agent = self._create_compute_agent()
    
    def _create_compute_agent(self):
        """Create compute optimization agent with specialized tools"""
        
        @tool
        def scan_idle_ec2_instances() -> str:
            """Find idle and underutilized EC2 instances"""
            try:
                # Get all running instances
                instances = self.ec2_client.describe_instances(
                    Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
                )
                
                idle_instances = []
                underutilized_instances = []
                total_idle_cost = 0
                total_rightsizing_savings = 0
                
                for reservation in instances['Reservations']:
                    for instance in reservation['Instances']:
                        instance_id = instance['InstanceId']
                        instance_type = instance['InstanceType']
                        launch_time = instance['LaunchTime']
                        
                        # Calculate running time
                        running_days = (datetime.now(launch_time.tzinfo) - launch_time).days
                        
                        # Get hourly cost
                        hourly_cost = self.instance_pricing.get(instance_type, 0.1)
                        monthly_cost = hourly_cost * 24 * 30
                        
                        # Mock utilization data - in production, use CloudWatch
                        cpu_avg = self._get_cpu_utilization(instance_id)
                        memory_avg = self._get_memory_utilization(instance_id)
                        network_avg = self._get_network_utilization(instance_id)
                        
                        tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                        
                        # Classify as idle (very low utilization)
                        if cpu_avg < 5 and memory_avg < 10:
                            idle_instances.append({
                                'InstanceId': instance_id,
                                'InstanceType': instance_type,
                                'CpuUtilization': round(cpu_avg, 1),
                                'MemoryUtilization': round(memory_avg, 1),
                                'RunningDays': running_days,
                                'MonthlyCost': round(monthly_cost, 2),
                                'AnnualWaste': round(monthly_cost * 12, 2),
                                'Tags': tags,
                                'Recommendation': 'STOP' if running_days > 7 else 'MONITOR',
                                'StopCommand': f"aws ec2 stop-instances --instance-ids {instance_id}",
                                'ScheduleCommand': f"aws events put-rule --name stop-{instance_id}-offhours --schedule-expression 'cron(0 4 * * ? *)'"
                            })
                            total_idle_cost += monthly_cost
                            
                        # Classify as underutilized (moderate usage, could be downsized)
                        elif cpu_avg < 20 and memory_avg < 30:
                            recommended_type = self._recommend_smaller_instance(instance_type, cpu_avg, memory_avg)
                            if recommended_type != instance_type:
                                new_cost = self.instance_pricing.get(recommended_type, hourly_cost) * 24 * 30
                                savings = monthly_cost - new_cost
                                
                                underutilized_instances.append({
                                    'InstanceId': instance_id,
                                    'CurrentType': instance_type,
                                    'RecommendedType': recommended_type,
                                    'CpuUtilization': round(cpu_avg, 1),
                                    'MemoryUtilization': round(memory_avg, 1),
                                    'CurrentMonthlyCost': round(monthly_cost, 2),
                                    'RecommendedMonthlyCost': round(new_cost, 2),
                                    'MonthlySavings': round(savings, 2),
                                    'AnnualSavings': round(savings * 12, 2),
                                    'Tags': tags,
                                    'RightsizeCommand': f"aws ec2 stop-instances --instance-ids {instance_id} && aws ec2 modify-instance-attribute --instance-id {instance_id} --instance-type '{recommended_type}' && aws ec2 start-instances --instance-ids {instance_id}"
                                })
                                total_rightsizing_savings += savings
                
                # Sort by highest cost/savings first
                idle_instances.sort(key=lambda x: x['MonthlyCost'], reverse=True)
                underutilized_instances.sort(key=lambda x: x['MonthlySavings'], reverse=True)
                
                return json.dumps({
                    'scan_timestamp': datetime.now().isoformat(),
                    'idle_instances': {
                        'count': len(idle_instances),
                        'total_monthly_waste': round(total_idle_cost, 2),
                        'total_annual_waste': round(total_idle_cost * 12, 2),
                        'instances': idle_instances[:10]
                    },
                    'underutilized_instances': {
                        'count': len(underutilized_instances),
                        'total_monthly_savings_potential': round(total_rightsizing_savings, 2),
                        'total_annual_savings_potential': round(total_rightsizing_savings * 12, 2),
                        'instances': underutilized_instances[:10]
                    },
                    'automation_recommendations': [
                        'Auto-stop idle instances during off-hours (8pm-8am)',
                        'Schedule weekend shutdowns for dev/test instances',
                        'Implement automated rightsizing based on 30-day utilization',
                        'Set up CloudWatch alarms for sustained low utilization'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to scan EC2 instances: {str(e)}"})
        
        @tool
        def analyze_reserved_instance_opportunities() -> str:
            """Identify Reserved Instance savings opportunities"""
            try:
                # Get current running instances
                instances = self.ec2_client.describe_instances(
                    Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
                )
                
                # Get current Reserved Instances
                reserved_instances = self.ec2_client.describe_reserved_instances(
                    Filters=[{'Name': 'state', 'Values': ['active']}]
                )
                
                # Count running instances by type
                instance_counts = {}
                total_on_demand_cost = 0
                
                for reservation in instances['Reservations']:
                    for instance in reservation['Instances']:
                        instance_type = instance['InstanceType']
                        launch_time = instance['LaunchTime']
                        running_days = (datetime.now(launch_time.tzinfo) - launch_time).days
                        
                        # Only consider instances running >30 days for RI recommendations
                        if running_days > 30:
                            instance_counts[instance_type] = instance_counts.get(instance_type, 0) + 1
                            hourly_cost = self.instance_pricing.get(instance_type, 0.1)
                            total_on_demand_cost += hourly_cost * 24 * 30
                
                # Count existing RIs by type
                ri_counts = {}
                for ri in reserved_instances['ReservedInstances']:
                    instance_type = ri['InstanceType']
                    ri_counts[instance_type] = ri_counts.get(instance_type, 0) + ri['InstanceCount']
                
                # Calculate RI opportunities
                ri_opportunities = []
                total_potential_savings = 0
                
                for instance_type, count in instance_counts.items():
                    reserved_count = ri_counts.get(instance_type, 0)
                    unreserved_count = max(0, count - reserved_count)
                    
                    if unreserved_count > 0:
                        hourly_on_demand = self.instance_pricing.get(instance_type, 0.1)
                        hourly_ri_1yr = hourly_on_demand * 0.62  # ~38% discount typical
                        hourly_ri_3yr = hourly_on_demand * 0.50  # ~50% discount typical
                        
                        monthly_savings_1yr = (hourly_on_demand - hourly_ri_1yr) * 24 * 30 * unreserved_count
                        monthly_savings_3yr = (hourly_on_demand - hourly_ri_3yr) * 24 * 30 * unreserved_count
                        
                        ri_opportunities.append({
                            'InstanceType': instance_type,
                            'UnreservedCount': unreserved_count,
                            'CurrentMonthlyCost': round(hourly_on_demand * 24 * 30 * unreserved_count, 2),
                            '1YearRI': {
                                'MonthlyCost': round(hourly_ri_1yr * 24 * 30 * unreserved_count, 2),
                                'MonthlySavings': round(monthly_savings_1yr, 2),
                                'AnnualSavings': round(monthly_savings_1yr * 12, 2),
                                'PercentSavings': '38%'
                            },
                            '3YearRI': {
                                'MonthlyCost': round(hourly_ri_3yr * 24 * 30 * unreserved_count, 2),
                                'MonthlySavings': round(monthly_savings_3yr, 2),
                                'AnnualSavings': round(monthly_savings_3yr * 12, 2),
                                'PercentSavings': '50%'
                            },
                            'SampleCommand': 'aws ce get-reservation-purchase-recommendation --service EC2 --term-in-years ONE_YEAR --lookback-period-in-days THIRTY_DAYS --payment-option ALL_UPFRONT'
                        })
                        
                        # Use 1-year RI savings for total calculation
                        total_potential_savings += monthly_savings_1yr
                
                ri_opportunities.sort(key=lambda x: x['1YearRI']['MonthlySavings'], reverse=True)
                
                return json.dumps({
                    'analysis_timestamp': datetime.now().isoformat(),
                    'total_running_instances': sum(instance_counts.values()),
                    'total_reserved_instances': sum(ri_counts.values()),
                    'coverage_percentage': round((sum(ri_counts.values()) / sum(instance_counts.values())) * 100, 1) if instance_counts else 0,
                    'monthly_savings_potential': round(total_potential_savings, 2),
                    'annual_savings_potential': round(total_potential_savings * 12, 2),
                    'opportunities': ri_opportunities[:10],
                    'recommendations': [
                        'Start with 1-year RIs for proven stable workloads',
                        'Consider Convertible RIs for flexibility with changing instance types',
                        'Use RI utilization reports to optimize existing reservations',
                        'Implement automated RI purchasing for predictable workloads'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to analyze RI opportunities: {str(e)}"})
        
        @tool
        def analyze_lambda_optimization() -> str:
            """Analyze Lambda functions for cost optimization"""
            try:
                functions = self.lambda_client.list_functions()
                
                optimization_opportunities = []
                total_potential_savings = 0
                
                for func in functions['Functions']:
                    func_name = func['FunctionName']
                    memory_mb = func['MemorySize']
                    timeout_sec = func['Timeout']
                    
                    # Mock performance data - in production, use CloudWatch insights
                    avg_duration_ms = self._get_lambda_avg_duration(func_name)
                    avg_memory_used_mb = self._get_lambda_avg_memory_used(func_name)
                    monthly_invocations = self._get_lambda_monthly_invocations(func_name)
                    
                    # Calculate current cost
                    gb_seconds = (memory_mb / 1024) * (avg_duration_ms / 1000) * monthly_invocations
                    current_cost = gb_seconds * 0.0000166667  # $0.0000166667 per GB-second
                    
                    # Identify optimization opportunities
                    memory_optimized = False
                    timeout_optimized = False
                    recommended_memory = memory_mb
                    recommended_timeout = timeout_sec
                    potential_savings = 0
                    
                    # Memory optimization
                    if avg_memory_used_mb < memory_mb * 0.7:  # Using <70% of allocated memory
                        recommended_memory = max(128, int(avg_memory_used_mb * 1.3))  # 30% buffer
                        memory_optimized = True
                    
                    # Timeout optimization  
                    if avg_duration_ms < timeout_sec * 1000 * 0.5:  # Using <50% of timeout
                        recommended_timeout = max(3, int((avg_duration_ms / 1000) * 2))  # 100% buffer
                        timeout_optimized = True
                    
                    if memory_optimized:
                        optimized_gb_seconds = (recommended_memory / 1024) * (avg_duration_ms / 1000) * monthly_invocations
                        optimized_cost = optimized_gb_seconds * 0.0000166667
                        potential_savings = current_cost - optimized_cost
                        total_potential_savings += potential_savings
                    
                    if memory_optimized or timeout_optimized:
                        optimization_opportunities.append({
                            'FunctionName': func_name,
                            'CurrentMemoryMB': memory_mb,
                            'RecommendedMemoryMB': recommended_memory,
                            'CurrentTimeoutSec': timeout_sec,
                            'RecommendedTimeoutSec': recommended_timeout,
                            'AvgDurationMs': round(avg_duration_ms, 0),
                            'AvgMemoryUsedMB': round(avg_memory_used_mb, 0),
                            'MonthlyInvocations': monthly_invocations,
                            'CurrentMonthlyCost': round(current_cost, 4),
                            'OptimizedMonthlyCost': round(current_cost - potential_savings, 4) if memory_optimized else round(current_cost, 4),
                            'MonthlySavings': round(potential_savings, 4),
                            'OptimizationTypes': [
                                'MEMORY' if memory_optimized else None,
                                'TIMEOUT' if timeout_optimized else None
                            ],
                            'FixCommand': f"aws lambda update-function-configuration --function-name {func_name} --memory-size {recommended_memory} --timeout {recommended_timeout}"
                        })
                
                optimization_opportunities.sort(key=lambda x: x['MonthlySavings'], reverse=True)
                
                return json.dumps({
                    'analysis_timestamp': datetime.now().isoformat(),
                    'total_functions_analyzed': len(functions['Functions']),
                    'optimization_opportunities': len(optimization_opportunities),
                    'total_monthly_savings_potential': round(total_potential_savings, 4),
                    'total_annual_savings_potential': round(total_potential_savings * 12, 2),
                    'functions': optimization_opportunities[:15],
                    'automation_recommendations': [
                        'Use AWS Lambda Power Tuning for automated memory optimization',
                        'Implement CloudWatch alarms for duration and memory utilization',
                        'Consider Provisioned Concurrency for consistent high-frequency functions',
                        'Use Lambda Insights for detailed performance monitoring'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to analyze Lambda optimization: {str(e)}"})
        
        @tool
        def generate_compute_action_plan() -> str:
            """Generate prioritized compute optimization action plan"""
            try:
                # Get all compute analysis data
                ec2_scan = json.loads(scan_idle_ec2_instances())
                ri_analysis = json.loads(analyze_reserved_instance_opportunities())
                lambda_analysis = json.loads(analyze_lambda_optimization())
                
                total_monthly_savings = (
                    ec2_scan['idle_instances']['total_monthly_waste'] +
                    ec2_scan['underutilized_instances']['total_monthly_savings_potential'] +
                    ri_analysis['monthly_savings_potential'] +
                    lambda_analysis['total_monthly_savings_potential']
                )
                
                action_plan = {
                    'immediate_actions': [
                        {
                            'action': 'Stop idle EC2 instances during off-hours',
                            'monthly_savings': ec2_scan['idle_instances']['total_monthly_waste'] * 0.5,  # 50% time savings
                            'effort': 'LOW',
                            'risk': 'LOW',
                            'timeline': '1 week',
                            'automation': 'AWS Instance Scheduler',
                            'sample_command': 'aws ec2 stop-instances --instance-ids <INSTANCE_IDS>'
                        },
                        {
                            'action': 'Optimize over-provisioned Lambda memory',
                            'monthly_savings': lambda_analysis['total_monthly_savings_potential'],
                            'effort': 'LOW',
                            'risk': 'LOW',
                            'timeline': '1 week',
                            'automation': 'Lambda Power Tuning',
                            'sample_command': 'aws lambda update-function-configuration --function-name <FUNC> --memory-size <MB>'
                        }
                    ],
                    'short_term_actions': [
                        {
                            'action': 'Purchase Reserved Instances for stable workloads',
                            'monthly_savings': ri_analysis['monthly_savings_potential'] * 0.7,  # Conservative estimate
                            'effort': 'MEDIUM',
                            'risk': 'LOW',
                            'timeline': '2-4 weeks',
                            'automation': 'RI utilization monitoring',
                            'sample_command': 'aws ce get-reservation-purchase-recommendation --service EC2'
                        },
                        {
                            'action': 'Rightsize underutilized EC2 instances',
                            'monthly_savings': ec2_scan['underutilized_instances']['total_monthly_savings_potential'] * 0.6,
                            'effort': 'HIGH',
                            'risk': 'MEDIUM',
                            'timeline': '4-8 weeks',
                            'automation': 'AWS Compute Optimizer',
                            'sample_command': 'aws ec2 modify-instance-attribute --instance-id <ID> --instance-type <TYPE>'
                        }
                    ],
                    'monitoring_and_alerts': [
                        'CloudWatch alarms for instance utilization <10%',
                        'RI utilization and coverage tracking',
                        'Lambda duration and memory alerts',
                        'Weekly cost anomaly detection'
                    ]
                }
                
                return json.dumps({
                    'plan_generated': datetime.now().isoformat(),
                    'total_monthly_savings_potential': round(total_monthly_savings, 2),
                    'total_annual_savings_potential': round(total_monthly_savings * 12, 2),
                    'breakdown': {
                        'idle_ec2_waste': ec2_scan['idle_instances']['total_monthly_waste'],
                        'rightsizing_savings': ec2_scan['underutilized_instances']['total_monthly_savings_potential'],
                        'reserved_instance_savings': ri_analysis['monthly_savings_potential'],
                        'lambda_optimization': lambda_analysis['total_monthly_savings_potential']
                    },
                    'action_plan': action_plan,
                    'recommended_execution_priority': [
                        '1. Optimize Lambda memory (quick wins, no downtime)',
                        '2. Implement EC2 off-hours scheduling (immediate savings)',
                        '3. Purchase RIs for proven stable workloads (long-term savings)',
                        '4. Rightsize instances (coordinate with application teams)'
                    ],
                    'success_metrics': [
                        'Monthly compute cost reduction %',
                        'EC2 instance utilization improvement',
                        'RI coverage percentage increase',
                        'Lambda cost per invocation reduction'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to generate action plan: {str(e)}"})
        
        # Helper methods
        def _get_cpu_utilization(self, instance_id: str) -> float:
            """Mock CPU utilization - in production, use CloudWatch API"""
            import random
            return random.uniform(2, 85)
        
        def _get_memory_utilization(self, instance_id: str) -> float:
            """Mock memory utilization - in production, use CloudWatch agent"""
            import random
            return random.uniform(5, 75)
        
        def _get_network_utilization(self, instance_id: str) -> float:
            """Mock network utilization"""
            import random
            return random.uniform(1, 30)
        
        def _recommend_smaller_instance(self, current_type: str, cpu_avg: float, memory_avg: float) -> str:
            """Recommend smaller instance type based on utilization"""
            # Simplified recommendation logic
            instance_families = {
                't3': ['t3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge', 't3.2xlarge'],
                'm5': ['m5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge'],
                'c5': ['c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge'],
                'r5': ['r5.large', 'r5.xlarge', 'r5.2xlarge']
            }
            
            family = current_type.split('.')[0]
            if family in instance_families:
                family_instances = instance_families[family]
                current_index = family_instances.index(current_type) if current_type in family_instances else 0
                
                # If very low utilization, recommend 1-2 sizes smaller
                if cpu_avg < 10 and memory_avg < 20:
                    return family_instances[max(0, current_index - 2)]
                elif cpu_avg < 20 and memory_avg < 30:
                    return family_instances[max(0, current_index - 1)]
            
            return current_type
        
        def _get_lambda_avg_duration(self, func_name: str) -> float:
            """Mock Lambda duration - in production, use CloudWatch Insights"""
            import random
            return random.uniform(100, 5000)  # ms
        
        def _get_lambda_avg_memory_used(self, func_name: str) -> float:
            """Mock Lambda memory usage"""
            import random
            return random.uniform(50, 400)  # MB
        
        def _get_lambda_monthly_invocations(self, func_name: str) -> int:
            """Mock Lambda invocation count"""
            import random
            return random.randint(1000, 100000)
        
        # Attach helper methods for tool access
        self._get_cpu_utilization = _get_cpu_utilization
        self._get_memory_utilization = _get_memory_utilization
        self._get_network_utilization = _get_network_utilization
        self._recommend_smaller_instance = _recommend_smaller_instance
        self._get_lambda_avg_duration = _get_lambda_avg_duration
        self._get_lambda_avg_memory_used = _get_lambda_avg_memory_used
        self._get_lambda_monthly_invocations = _get_lambda_monthly_invocations
        
        return Agent(
            name="ComputeOptimizationAgent",
            tools=[
                scan_idle_ec2_instances,
                analyze_reserved_instance_opportunities,
                analyze_lambda_optimization,
                generate_compute_action_plan,
                memory,
                calculator
            ],
            system_prompt="""You are an AWS compute cost optimization specialist.
            
            Your expertise covers:
            - EC2 instance idle detection and rightsizing
            - Reserved Instance opportunity analysis
            - Lambda function memory and timeout optimization
            - Automation strategies for continuous optimization
            
            Always provide:
            - Quantified savings calculations with timeframes
            - Risk assessment for each optimization
            - Implementation difficulty estimates
            - Automation recommendations
            
            Focus on identifying quick wins first, then structural optimizations.
            Consider workload patterns and business requirements in recommendations."""
        )
    
    def analyze(self, request: str = None) -> str:
        """Run compute optimization analysis"""
        if request is None:
            request = "Perform comprehensive compute cost optimization analysis and generate action plan"
        
        return str(self.agent(request))
    
    def quick_wins(self) -> str:
        """Get quick compute optimization wins"""
        return str(self.agent(
            "Identify the top 5 quick wins for compute cost optimization "
            "that can be implemented immediately with minimal risk and effort"
        ))


if __name__ == "__main__":
    # Example usage
    agent = ComputeOptimizationAgent()
    
    print("⚡ Compute Optimization Agent - Analysis Starting...")
    print("\n💡 Quick Wins:")
    print(agent.quick_wins())
    
    print("\n📊 Full Analysis:")
    print(agent.analyze())