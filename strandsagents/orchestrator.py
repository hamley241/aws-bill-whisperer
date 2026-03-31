"""
AWS Bill Whisperer - Strandsagents Multi-Agent Cost Optimization
Central Orchestrator for autonomous cost optimization agents
"""

import asyncio
import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
from strands import Agent, tool
from strands_tools import memory
import pandas as pd


class AWSCostOrchestrator:
    """Central orchestrator for AWS cost optimization agents"""
    
    def __init__(self, aws_session=None):
        self.aws_session = aws_session or boto3.Session()
        self.ce_client = self.aws_session.client('ce')  # Cost Explorer
        self.ec2_client = self.aws_session.client('ec2')
        self.ebs_client = self.aws_session.client('ec2')
        
        # Initialize specialist agents
        self.storage_agent = self._create_storage_agent()
        self.compute_agent = self._create_compute_agent()
        self.monitoring_agent = self._create_monitoring_agent()
        
        # Create orchestrator agent with access to specialist agents
        self.orchestrator = self._create_orchestrator_agent()
    
    def _create_storage_agent(self):
        """Create specialized storage optimization agent"""
        
        @tool
        def analyze_unattached_ebs_volumes() -> str:
            """Find and analyze unattached EBS volumes for potential deletion"""
            try:
                volumes = self.ebs_client.describe_volumes(
                    Filters=[{'Name': 'status', 'Values': ['available']}]
                )
                
                total_wasted_cost = 0
                volume_details = []
                
                for volume in volumes['Volumes']:
                    size_gb = volume['Size']
                    volume_type = volume['VolumeType']
                    
                    # Estimate monthly cost based on volume type
                    cost_per_gb_month = {
                        'gp3': 0.08, 'gp2': 0.10, 'io1': 0.125, 
                        'io2': 0.125, 'sc1': 0.045, 'st1': 0.025
                    }.get(volume_type, 0.08)
                    
                    monthly_cost = size_gb * cost_per_gb_month
                    total_wasted_cost += monthly_cost
                    
                    volume_details.append({
                        'VolumeId': volume['VolumeId'],
                        'Size': size_gb,
                        'Type': volume_type,
                        'MonthlyCost': monthly_cost,
                        'CreationTime': volume['CreateTime'].isoformat()
                    })
                
                return json.dumps({
                    'total_unattached_volumes': len(volume_details),
                    'total_wasted_monthly_cost': round(total_wasted_cost, 2),
                    'volumes': volume_details,
                    'recommendation': 'Delete volumes unused for >30 days after backup verification'
                })
                
            except Exception as e:
                return f"Error analyzing EBS volumes: {str(e)}"
        
        return Agent(
            name="StorageOptimizer",
            tools=[analyze_unattached_ebs_volumes, memory],
            system_prompt="""You are a storage cost optimization specialist. 
            Analyze AWS storage resources for cost savings opportunities.
            Focus on unattached EBS volumes, oversized volumes, and unused snapshots.
            Always calculate potential savings and provide safe automation recommendations."""
        )
    
    def _create_compute_agent(self):
        """Create specialized compute optimization agent"""
        
        @tool
        def analyze_idle_ec2_instances() -> str:
            """Find and analyze low-utilization EC2 instances"""
            try:
                # Get running instances
                instances = self.ec2_client.describe_instances(
                    Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
                )
                
                idle_instances = []
                total_potential_savings = 0
                
                for reservation in instances['Reservations']:
                    for instance in reservation['Instances']:
                        instance_id = instance['InstanceId']
                        instance_type = instance['InstanceType']
                        
                        # Estimate monthly cost (simplified)
                        hourly_cost = self._estimate_instance_hourly_cost(instance_type)
                        monthly_cost = hourly_cost * 24 * 30
                        
                        # Check CloudWatch metrics for CPU utilization
                        # (In production, integrate with CloudWatch API)
                        avg_cpu = self._get_avg_cpu_utilization(instance_id)
                        
                        if avg_cpu < 10:  # Consider <10% CPU as idle
                            idle_instances.append({
                                'InstanceId': instance_id,
                                'InstanceType': instance_type,
                                'AvgCpuUtilization': avg_cpu,
                                'MonthlyCost': round(monthly_cost, 2),
                                'LaunchTime': instance['LaunchTime'].isoformat()
                            })
                            total_potential_savings += monthly_cost
                
                return json.dumps({
                    'total_idle_instances': len(idle_instances),
                    'total_potential_monthly_savings': round(total_potential_savings, 2),
                    'instances': idle_instances,
                    'recommendations': [
                        'Stop instances during off-hours',
                        'Downsize to smaller instance types',
                        'Consider Reserved Instance discounts for predictable workloads'
                    ]
                })
                
            except Exception as e:
                return f"Error analyzing EC2 instances: {str(e)}"
        
        def _estimate_instance_hourly_cost(self, instance_type: str) -> float:
            """Estimate hourly cost for instance type (simplified pricing)"""
            cost_map = {
                't3.nano': 0.0052, 't3.micro': 0.0104, 't3.small': 0.0208,
                't3.medium': 0.0416, 't3.large': 0.0832, 't3.xlarge': 0.1664,
                'm5.large': 0.096, 'm5.xlarge': 0.192, 'm5.2xlarge': 0.384,
                'c5.large': 0.085, 'c5.xlarge': 0.17, 'c5.2xlarge': 0.34
            }
            return cost_map.get(instance_type, 0.1)  # Default estimate
        
        def _get_avg_cpu_utilization(self, instance_id: str) -> float:
            """Mock CPU utilization - in production, use CloudWatch API"""
            import random
            return random.uniform(5, 95)  # Simulate CPU usage
        
        # Attach methods to instance for tool access
        self._estimate_instance_hourly_cost = _estimate_instance_hourly_cost
        self._get_avg_cpu_utilization = _get_avg_cpu_utilization
        
        return Agent(
            name="ComputeOptimizer", 
            tools=[analyze_idle_ec2_instances, memory],
            system_prompt="""You are a compute cost optimization specialist.
            Analyze AWS EC2 instances for rightsizing opportunities.
            Focus on idle instances, over-provisioned resources, and scheduling optimizations.
            Calculate potential savings and suggest automation strategies."""
        )
    
    def _create_monitoring_agent(self):
        """Create specialized monitoring optimization agent"""
        
        @tool
        def analyze_cloudwatch_waste() -> str:
            """Analyze CloudWatch logs and metrics for cost optimization"""
            try:
                # Simulate CloudWatch metrics analysis
                # In production, integrate with CloudWatch API
                
                findings = {
                    'unused_log_groups': {
                        'count': 23,
                        'monthly_cost': 847.50,
                        'recommendation': 'Delete log groups with no ingestion for >90 days'
                    },
                    'excessive_metric_resolution': {
                        'high_resolution_metrics': 156,
                        'potential_savings': 234.80,
                        'recommendation': 'Change to standard resolution for non-critical metrics'
                    },
                    'long_retention_periods': {
                        'over_retained_logs': 34,
                        'wasted_storage_cost': 123.40,
                        'recommendation': 'Reduce retention to business requirements (7-30 days)'
                    }
                }
                
                total_savings = (findings['unused_log_groups']['monthly_cost'] + 
                               findings['excessive_metric_resolution']['potential_savings'] +
                               findings['long_retention_periods']['wasted_storage_cost'])
                
                findings['total_monthly_savings_potential'] = round(total_savings, 2)
                
                return json.dumps(findings)
                
            except Exception as e:
                return f"Error analyzing CloudWatch costs: {str(e)}"
        
        return Agent(
            name="MonitoringOptimizer",
            tools=[analyze_cloudwatch_waste, memory],
            system_prompt="""You are a monitoring cost optimization specialist.
            Analyze AWS CloudWatch logs, metrics, and alarms for cost savings.
            Focus on unused resources, excessive retention, and over-monitoring.
            Provide actionable recommendations with clear ROI calculations."""
        )
    
    def _create_orchestrator_agent(self):
        """Create main orchestrator that coordinates specialist agents"""
        
        @tool  
        def storage_analysis_tool(query: str) -> str:
            """Get storage cost optimization analysis"""
            return str(self.storage_agent(query))
        
        @tool
        def compute_analysis_tool(query: str) -> str:
            """Get compute cost optimization analysis"""
            return str(self.compute_agent(query))
        
        @tool
        def monitoring_analysis_tool(query: str) -> str:
            """Get monitoring cost optimization analysis"""  
            return str(self.monitoring_agent(query))
        
        @tool
        def generate_cost_report() -> str:
            """Generate comprehensive cost optimization report"""
            try:
                # Get analysis from all specialist agents
                storage_analysis = self.storage_agent("Analyze all storage waste and calculate savings")
                compute_analysis = self.compute_agent("Find all idle and oversized compute resources")
                monitoring_analysis = self.monitoring_agent("Identify monitoring cost optimization opportunities")
                
                # Combine results
                report = {
                    'report_timestamp': datetime.now().isoformat(),
                    'summary': 'AWS Cost Optimization Analysis',
                    'storage_optimization': json.loads(storage_analysis),
                    'compute_optimization': json.loads(compute_analysis), 
                    'monitoring_optimization': json.loads(monitoring_analysis)
                }
                
                # Calculate total potential savings
                storage_savings = report['storage_optimization'].get('total_wasted_monthly_cost', 0)
                compute_savings = report['compute_optimization'].get('total_potential_monthly_savings', 0)
                monitoring_savings = report['monitoring_optimization'].get('total_monthly_savings_potential', 0)
                
                total_monthly_savings = storage_savings + compute_savings + monitoring_savings
                annual_savings = total_monthly_savings * 12
                
                report['total_optimization_potential'] = {
                    'monthly_savings': round(total_monthly_savings, 2),
                    'annual_savings': round(annual_savings, 2),
                    'percentage_improvement': '15-25%'  # Estimated
                }
                
                return json.dumps(report, indent=2)
                
            except Exception as e:
                return f"Error generating cost report: {str(e)}"
        
        return Agent(
            name="AWSCostOrchestrator",
            tools=[
                storage_analysis_tool, 
                compute_analysis_tool, 
                monitoring_analysis_tool,
                generate_cost_report,
                memory
            ],
            system_prompt="""You are the AWS Cost Optimization Orchestrator.
            Coordinate specialist agents to identify cost savings across AWS services.
            
            Your capabilities:
            - Storage optimization via StorageOptimizer agent
            - Compute optimization via ComputeOptimizer agent  
            - Monitoring optimization via MonitoringOptimizer agent
            - Comprehensive cost reporting and ROI analysis
            
            Always provide actionable recommendations with clear savings calculations.
            Prioritize high-impact, low-risk optimizations first.
            Consider automation opportunities for continuous cost optimization."""
        )
    
    async def run_full_analysis(self) -> Dict[str, Any]:
        """Run complete cost optimization analysis"""
        try:
            result = self.orchestrator(
                "Run a comprehensive AWS cost optimization analysis. "
                "Use all specialist agents to identify savings opportunities "
                "and generate a detailed report with total potential savings."
            )
            return json.loads(str(result))
        except Exception as e:
            return {'error': f"Analysis failed: {str(e)}"}
    
    async def quick_wins_analysis(self) -> Dict[str, Any]:
        """Identify quick win cost optimizations"""
        try:
            result = self.orchestrator(
                "Focus on quick wins - identify the top 5 highest-impact, "
                "lowest-risk cost optimizations that can be implemented this week. "
                "Calculate potential monthly savings for each."
            )
            return json.loads(str(result))
        except Exception as e:
            return {'error': f"Quick wins analysis failed: {str(e)}"}


if __name__ == "__main__":
    # Example usage
    async def main():
        orchestrator = AWSCostOrchestrator()
        
        print("🚀 Starting AWS Bill Whisperer - Strandsagents Multi-Agent Analysis...")
        
        # Run quick wins analysis
        print("\n💡 Analyzing Quick Wins...")
        quick_wins = await orchestrator.quick_wins_analysis()
        print(json.dumps(quick_wins, indent=2))
        
        # Run full analysis
        print("\n📊 Running Full Cost Optimization Analysis...")
        full_report = await orchestrator.run_full_analysis()
        print(json.dumps(full_report, indent=2))
    
    asyncio.run(main())