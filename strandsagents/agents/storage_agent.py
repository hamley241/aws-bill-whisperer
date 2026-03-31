"""
AWS Bill Whisperer - Storage Optimization Agent
Specialized agent for EBS, S3, and storage cost optimization using Strandsagents
"""

import boto3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
from strands import Agent, tool
from strands_tools import memory, calculator


class StorageOptimizationAgent:
    """Specialized agent for AWS storage cost optimization"""
    
    def __init__(self, aws_session=None):
        self.aws_session = aws_session or boto3.Session()
        self.ec2_client = self.aws_session.client('ec2')
        self.s3_client = self.aws_session.client('s3')
        self.cloudwatch_client = self.aws_session.client('cloudwatch')
        # S3 CloudWatch metrics are only available in us-east-1
        self.cloudwatch_s3_client = self.aws_session.client('cloudwatch', region_name='us-east-1')
        
        # Initialize the agent with storage-specific tools
        self.agent = self._create_storage_agent()
    
    def _create_storage_agent(self):
        """Create storage optimization agent with specialized tools"""
        
        @tool
        def scan_unattached_ebs_volumes() -> str:
            """Scan for unattached EBS volumes and calculate waste"""
            try:
                volumes = self.ec2_client.describe_volumes(
                    Filters=[{'Name': 'status', 'Values': ['available']}]
                )
                
                total_wasted_cost = 0
                volume_details = []
                
                for volume in volumes['Volumes']:
                    volume_id = volume['VolumeId']
                    size_gb = volume['Size']
                    volume_type = volume['VolumeType']
                    create_time = volume['CreateTime']
                    
                    # Calculate age in days
                    age_days = (datetime.now(create_time.tzinfo) - create_time).days
                    
                    # Pricing per GB/month by volume type
                    pricing = {
                        'gp3': 0.08, 'gp2': 0.10, 'io1': 0.125, 'io2': 0.125,
                        'sc1': 0.045, 'st1': 0.025, 'standard': 0.045
                    }
                    
                    cost_per_gb = pricing.get(volume_type, 0.08)
                    monthly_cost = size_gb * cost_per_gb
                    total_wasted_cost += monthly_cost
                    
                    # Get tags for better identification
                    tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
                    
                    volume_details.append({
                        'VolumeId': volume_id,
                        'SizeGB': size_gb,
                        'VolumeType': volume_type,
                        'AgeDays': age_days,
                        'MonthlyCost': round(monthly_cost, 2),
                        'AnnualCost': round(monthly_cost * 12, 2),
                        'CreateTime': create_time.isoformat(),
                        'Tags': tags,
                        'Priority': 'HIGH' if age_days > 30 and monthly_cost > 10 else 'MEDIUM',
                        'SafeToDelete': age_days > 7,  # Conservative safety threshold
                        'FixCommand': f"aws ec2 delete-volume --volume-id {volume_id}",
                        'SafetyCommand': f"aws ec2 create-snapshot --volume-id {volume_id} --description 'pre-delete-backup-{volume_id}'"
                    })
                
                # Sort by highest cost first
                volume_details.sort(key=lambda x: x['MonthlyCost'], reverse=True)
                
                return json.dumps({
                    'scan_timestamp': datetime.now().isoformat(),
                    'total_unattached_volumes': len(volume_details),
                    'total_monthly_waste': round(total_wasted_cost, 2),
                    'total_annual_waste': round(total_wasted_cost * 12, 2),
                    'volumes': volume_details[:10],  # Top 10 by cost
                    'automation_recommendations': [
                        'Create snapshots before deletion for volumes >30 days old',
                        'Auto-delete volumes unattached >90 days with proper tags',
                        'Alert on new unattached volumes after 24 hours'
                    ],
                    'risk_assessment': 'LOW - Unattached volumes are safe to delete after backup verification'
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to scan EBS volumes: {str(e)}"})
        
        @tool
        def scan_oversized_volumes() -> str:
            """Find attached but oversized EBS volumes"""
            try:
                # Get all attached volumes
                volumes = self.ec2_client.describe_volumes(
                    Filters=[{'Name': 'status', 'Values': ['in-use']}]
                )
                
                oversized_volumes = []
                total_potential_savings = 0
                
                for volume in volumes['Volumes']:
                    volume_id = volume['VolumeId']
                    size_gb = volume['Size']
                    volume_type = volume['VolumeType']
                    
                    # Mock CloudWatch data - in production, get actual utilization
                    utilization_pct = self._get_volume_utilization(volume_id)
                    
                    if utilization_pct < 50:  # Less than 50% used
                        recommended_size = max(8, int(size_gb * (utilization_pct / 100) * 1.2))  # 20% buffer
                        size_reduction = size_gb - recommended_size
                        
                        cost_per_gb = {'gp3': 0.08, 'gp2': 0.10, 'io1': 0.125}.get(volume_type, 0.08)
                        monthly_savings = size_reduction * cost_per_gb
                        total_potential_savings += monthly_savings
                        
                        oversized_volumes.append({
                            'VolumeId': volume_id,
                            'CurrentSizeGB': size_gb,
                            'RecommendedSizeGB': recommended_size,
                            'UtilizationPercent': round(utilization_pct, 1),
                            'MonthlySavings': round(monthly_savings, 2),
                            'VolumeType': volume_type,
                            'AttachedInstance': volume.get('Attachments', [{}])[0].get('InstanceId', 'Unknown'),
                            'FixCommand': f"aws ec2 modify-volume --volume-id {volume_id} --size {recommended_size}",
                            'SafetyCommand': f"aws ec2 create-snapshot --volume-id {volume_id} --description 'pre-resize-{volume_id}'"
                        })
                
                oversized_volumes.sort(key=lambda x: x['MonthlySavings'], reverse=True)
                
                return json.dumps({
                    'scan_timestamp': datetime.now().isoformat(),
                    'oversized_volumes_found': len(oversized_volumes),
                    'total_monthly_savings_potential': round(total_potential_savings, 2),
                    'total_annual_savings_potential': round(total_potential_savings * 12, 2),
                    'volumes': oversized_volumes[:10],
                    'automation_recommendations': [
                        'Implement automated volume resizing for <50% utilization',
                        'Set up CloudWatch alarms for volume utilization tracking',
                        'Use gp3 for better cost per performance ratio'
                    ],
                    'risk_assessment': 'MEDIUM - Requires application-aware resizing'
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to analyze oversized volumes: {str(e)}"})
        
        @tool 
        def scan_s3_optimization_opportunities() -> str:
            """Identify S3 cost optimization opportunities"""
            try:
                buckets = self.s3_client.list_buckets()
                s3_findings = []
                total_potential_savings = 0
                
                for bucket in buckets['Buckets']:
                    bucket_name = bucket['Name']
                    
                    try:
                        # Check lifecycle configuration
                        lifecycle_config = None
                        try:
                            lifecycle_config = self.s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
                        except:
                            pass
                        
                        # Mock bucket analysis - in production, use S3 analytics
                        bucket_analysis = self._analyze_s3_bucket(bucket_name, lifecycle_config)
                        
                        if bucket_analysis['potential_savings'] > 0:
                            s3_findings.append(bucket_analysis)
                            total_potential_savings += bucket_analysis['potential_savings']
                    
                    except Exception as bucket_error:
                        continue  # Skip inaccessible buckets
                
                s3_findings.sort(key=lambda x: x['potential_savings'], reverse=True)
                
                return json.dumps({
                    'scan_timestamp': datetime.now().isoformat(),
                    'buckets_analyzed': len(buckets['Buckets']),
                    'optimization_opportunities': len(s3_findings),
                    'total_monthly_savings_potential': round(total_potential_savings, 2),
                    'findings': s3_findings[:10],
                    'automation_recommendations': [
                        'Implement intelligent tiering for automated cost optimization',
                        'Set up lifecycle rules for predictable access patterns',
                        'Enable compression for text-based objects',
                        'Review and optimize request patterns to reduce API costs'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to analyze S3 optimization: {str(e)}"})
        
        @tool
        def generate_storage_action_plan() -> str:
            """Generate prioritized action plan for storage optimization"""
            try:
                # Get data from all storage scans
                ebs_scan = json.loads(scan_unattached_ebs_volumes())
                oversized_scan = json.loads(scan_oversized_volumes())
                s3_scan = json.loads(scan_s3_optimization_opportunities())
                
                total_monthly_savings = (
                    ebs_scan.get('total_monthly_waste', 0) +
                    oversized_scan.get('total_monthly_savings_potential', 0) + 
                    s3_scan.get('total_monthly_savings_potential', 0)
                )
                
                # Prioritized action items
                action_plan = {
                    'immediate_actions': [
                        {
                            'action': 'Delete unattached EBS volumes >90 days old',
                            'monthly_savings': ebs_scan.get('total_monthly_waste', 0) * 0.6,  # Conservative estimate
                            'effort': 'LOW',
                            'risk': 'LOW',
                            'timeline': '1 week',
                            'sample_command': 'aws ec2 create-snapshot --volume-id <VOL> && aws ec2 delete-volume --volume-id <VOL>'
                        },
                        {
                            'action': 'Implement S3 Intelligent Tiering',
                            'monthly_savings': s3_scan.get('total_monthly_savings_potential', 0) * 0.3,
                            'effort': 'LOW', 
                            'risk': 'NONE',
                            'timeline': '1 day',
                            'sample_command': 'aws s3api put-bucket-lifecycle-configuration --bucket <BUCKET> --lifecycle-configuration file://intelligent-tiering.json'
                        }
                    ],
                    'short_term_actions': [
                        {
                            'action': 'Resize oversized EBS volumes',
                            'monthly_savings': oversized_scan.get('total_monthly_savings_potential', 0) * 0.4,
                            'effort': 'MEDIUM',
                            'risk': 'MEDIUM',
                            'timeline': '2-4 weeks',
                            'sample_command': 'aws ec2 modify-volume --volume-id <VOL> --size <NEW_SIZE>'
                        },
                        {
                            'action': 'Implement S3 lifecycle policies',
                            'monthly_savings': s3_scan.get('total_monthly_savings_potential', 0) * 0.5,
                            'effort': 'MEDIUM',
                            'risk': 'LOW',
                            'timeline': '2 weeks',
                            'sample_command': 'aws s3api put-bucket-lifecycle-configuration --bucket <BUCKET> --lifecycle-configuration file://lifecycle.json'
                        }
                    ],
                    'automation_opportunities': [
                        'Auto-delete unattached volumes after safety period',
                        'Automated EBS volume utilization monitoring',
                        'S3 intelligent tiering automation',
                        'Orphaned snapshot cleanup'
                    ]
                }
                
                return json.dumps({
                    'plan_generated': datetime.now().isoformat(),
                    'total_monthly_savings_potential': round(total_monthly_savings, 2),
                    'total_annual_savings_potential': round(total_monthly_savings * 12, 2),
                    'action_plan': action_plan,
                    'recommended_execution_order': [
                        '1. Enable S3 Intelligent Tiering (no risk, immediate savings)',
                        '2. Delete old unattached EBS volumes (after backup verification)',
                        '3. Implement S3 lifecycle policies (analyze access patterns first)', 
                        '4. Resize oversized EBS volumes (coordinate with application teams)'
                    ],
                    'success_metrics': [
                        'Monthly storage cost reduction %',
                        'Number of unattached volumes eliminated',
                        'S3 storage class distribution optimization',
                        'Average EBS volume utilization improvement'
                    ]
                })
                
            except Exception as e:
                return json.dumps({'error': f"Failed to generate action plan: {str(e)}"})
        
        # Helper methods for tool functions
        def _get_volume_utilization(self, volume_id: str) -> float:
            """Estimate EBS volume utilization using CloudWatch metrics"""
            try:
                end_time = datetime.utcnow()
                start_time = end_time - timedelta(days=7)
                metrics = self.cloudwatch_client.get_metric_statistics(
                    Namespace='AWS/EBS',
                    MetricName='VolumeIdleTime',
                    Dimensions=[{'Name': 'VolumeId', 'Value': volume_id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=['Average']
                )
                datapoints = metrics.get('Datapoints', [])
                if not datapoints:
                    return 50.0  # Neutral fallback when no metrics yet
                latest = sorted(datapoints, key=lambda d: d['Timestamp'])[-1]
                idle_pct = min(max(latest['Average'] / 3600 * 100, 0), 100)
                utilization = 100 - idle_pct
                return round(utilization, 2)
            except Exception:
                return 50.0
        
        def _get_bucket_size_gb(self, bucket_name: str) -> float:
            """Fetch actual S3 bucket size from CloudWatch BucketSizeBytes"""
            try:
                end_time = datetime.utcnow()
                start_time = end_time - timedelta(days=2)
                metrics = self.cloudwatch_s3_client.get_metric_statistics(
                    Namespace='AWS/S3',
                    MetricName='BucketSizeBytes',
                    Dimensions=[
                        {'Name': 'BucketName', 'Value': bucket_name},
                        {'Name': 'StorageType', 'Value': 'StandardStorage'}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=86400,
                    Statistics=['Average']
                )
                datapoints = metrics.get('Datapoints', [])
                if not datapoints:
                    return 0.0
                latest = sorted(datapoints, key=lambda d: d['Timestamp'])[-1]
                size_gb = latest['Average'] / (1024 ** 3)
                return round(size_gb, 2)
            except Exception:
                return 0.0
        
        def _analyze_s3_bucket(self, bucket_name: str, lifecycle_config: dict) -> dict:
            """Analyze S3 bucket using real metrics"""
            has_lifecycle = lifecycle_config is not None
            estimated_size_gb = self._get_bucket_size_gb(bucket_name)
            
            # Conservative savings estimate: 5% if lifecycle exists, 15% if not
            savings_rate = 0.05 if has_lifecycle else 0.15
            potential_savings = estimated_size_gb * savings_rate * 0.023  # $0.023/GB-month baseline
            
            return {
                'bucket_name': bucket_name,
                'estimated_size_gb': estimated_size_gb,
                'has_lifecycle_policy': has_lifecycle,
                'potential_savings': round(potential_savings, 2),
                'recommendations': [] if has_lifecycle else ['Enable Intelligent Tiering', 'Set up lifecycle rules'],
                'FixCommand': None if has_lifecycle else f"aws s3api put-bucket-lifecycle-configuration --bucket {bucket_name} --lifecycle-configuration file://lifecycle-{bucket_name}.json",
                'SampleLifecycleJson': None if has_lifecycle else '{"Rules":[{"ID":"intelligent-tiering","Status":"Enabled","Filter":{"Prefix":""},"Transitions":[{"Days":30,"StorageClass":"INTELLIGENT_TIERING"}]}]}'
            }
        
        # Attach helper methods to self for tool access
        self._get_volume_utilization = _get_volume_utilization
        self._analyze_s3_bucket = _analyze_s3_bucket
        
        return Agent(
            name="StorageOptimizationAgent",
            tools=[
                scan_unattached_ebs_volumes,
                scan_oversized_volumes, 
                scan_s3_optimization_opportunities,
                generate_storage_action_plan,
                memory,
                calculator
            ],
            system_prompt="""You are an AWS storage cost optimization specialist.
            
            Your expertise covers:
            - EBS volume waste identification and cleanup
            - S3 cost optimization through tiering and lifecycle policies
            - Storage utilization analysis and rightsizing
            - Risk assessment for storage modifications
            
            Always provide:
            - Quantified savings calculations
            - Risk assessments for each recommendation
            - Automation opportunities
            - Prioritized action plans
            
            Focus on high-impact, low-risk optimizations first.
            Consider data retention requirements and business continuity needs."""
        )
    
    def analyze(self, request: str = None) -> str:
        """Run storage optimization analysis"""
        if request is None:
            request = "Perform comprehensive storage cost optimization analysis and generate action plan"
        
        return str(self.agent(request))
    
    def quick_wins(self) -> str:
        """Get quick storage optimization wins"""
        return str(self.agent(
            "Identify the top 3 quick wins for storage cost optimization "
            "that can be implemented this week with minimal risk"
        ))


if __name__ == "__main__":
    # Example usage
    agent = StorageOptimizationAgent()
    
    print("🗄️ Storage Optimization Agent - Analysis Starting...")
    print("\n💡 Quick Wins:")
    print(agent.quick_wins())
    
    print("\n📊 Full Analysis:")
    print(agent.analyze())