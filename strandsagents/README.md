# AWS Bill Whisperer - Strandsagents Multi-Agent System

🤖 **Autonomous AWS cost optimization through specialized AI agents**

Built with [Strands Agents SDK](https://strandsagents.com) for production-ready multi-agent orchestration.

## 🎯 Overview

The AWS Bill Whisperer leverages multiple specialized AI agents to automatically identify, analyze, and recommend cost optimizations across your AWS infrastructure. Each agent is an expert in specific AWS services, working together through an intelligent orchestrator.

### Agent Ecosystem

| Agent | Specialty | Focus Areas | Typical Savings |
|-------|-----------|-------------|-----------------|
| **🗄️ StorageOptimizer** | EBS, S3, Snapshots | Unattached volumes, oversized storage, lifecycle policies | $200-800/month |
| **⚡ ComputeOptimizer** | EC2, Lambda, Reserved Instances | Idle instances, rightsizing, RI opportunities | $500-2000/month |
| **📊 MonitoringOptimizer** | CloudWatch, Logs, Metrics | Unused log groups, excessive retention | $100-500/month |
| **🎭 CostOrchestrator** | Cross-service coordination | Multi-agent analysis, priority planning | **$850-3500/month total** |

## 🚀 Quick Start

### Prerequisites

- Python 3.10 or higher
- AWS CLI configured with appropriate permissions
- pip package manager

### Installation

```bash
# Clone or download the strandsagents directory
cd aws-bill-whisperer/strandsagents

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\activate

# Install dependencies
pip install -r requirements.txt
```

### AWS Permissions Required

Your AWS credentials need the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstances",
                "ec2:DescribeVolumes",
                "ec2:DescribeReservedInstances",
                "s3:ListAllMyBuckets",
                "s3:GetBucketLifecycleConfiguration",
                "lambda:ListFunctions",
                "cloudwatch:GetMetricStatistics",
                "ce:GetUsageAndCosts",
                "pricing:GetProducts"
            ],
            "Resource": "*"
        }
    ]
}
```

### Configuration

Set your AWS credentials using any standard method:

```bash
# Option 1: AWS CLI
aws configure

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1

# Option 3: IAM Role (recommended for production)
```

## 🎬 Demo

Run the interactive demo to see the multi-agent system in action:

```bash
python demo_runner.py
```

The demo showcases:
- Individual agent capabilities
- Multi-agent orchestration
- Cross-domain optimization scenarios
- Automation recommendations

## 💬 Conversational Chat Interface

Ask natural-language questions ("show idle EC2 instances", "what storage waste do we have?") via the built-in chat CLI:

```bash
# From the strandsagents directory (with AWS creds configured)
python -m chat_interface.chat_cli
```

The chat interface:
- Routes queries to the right specialist agents automatically
- Returns human-readable summaries plus the exact AWS CLI commands to fix each issue
- Keeps a running conversation context (similar to OpenClaw’s orchestration chat)

### Customer Setup Checklist
1. **Clone + install** (see Quick Start above).  
2. **Provide AWS credentials** (env vars, `aws configure`, or an IAM role).  
3. **Run** `python -m chat_interface.chat_cli`.  
4. **Ask questions** like “show me Lambda savings” or “generate a full report”.  
5. **Copy/paste the suggested commands** straight into your ops tooling (SSM, Terraform, etc.).

> Tip: To expose this to your customers, wrap the CLI in a thin web socket server (FastAPI/Flask) and stream the `ChatResponse` payloads to a browser/chat UI. The conversational layer is already abstracted in `chat_interface/chatbot.py`, so you only need to swap the input/output transport.

Example session:
```
you> show me storage waste in us-east-1
agent> Storage scan found 7 unattached EBS volumes wasting $312/mo...
Suggested commands:
  - Cleanup vol-0abc123:
    aws ec2 create-snapshot --volume-id vol-0abc123 ...
    aws ec2 delete-volume --volume-id vol-0abc123
```

## 📖 Usage Examples

### Individual Agent Analysis

```python
from agents.storage_agent import StorageOptimizationAgent
from agents.compute_agent import ComputeOptimizationAgent

# Storage optimization
storage_agent = StorageOptimizationAgent()
storage_report = storage_agent.analyze()
print(storage_report)

# Quick wins only
quick_storage_wins = storage_agent.quick_wins()
print(quick_storage_wins)

# Compute optimization  
compute_agent = ComputeOptimizationAgent()
compute_report = compute_agent.analyze()
print(compute_report)
```

### Orchestrated Multi-Agent Analysis

```python
import asyncio
from orchestrator import AWSCostOrchestrator

async def run_analysis():
    orchestrator = AWSCostOrchestrator()
    
    # Quick wins across all services
    quick_wins = await orchestrator.quick_wins_analysis()
    print("Quick Wins:", quick_wins)
    
    # Comprehensive analysis
    full_report = await orchestrator.run_full_analysis()
    print("Full Report:", full_report)

asyncio.run(run_analysis())
```

### Custom Agent Queries

```python
from orchestrator import AWSCostOrchestrator

orchestrator = AWSCostOrchestrator()

# Specific optimization queries
result = orchestrator.orchestrator(
    "Find all EC2 instances that have been idle for more than 7 days "
    "and calculate the potential savings from automated scheduling"
)

print(result)
```

## 🏗️ Architecture

### Multi-Agent Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    Cost Orchestrator                        │
│  • Cross-service coordination                               │
│  • Priority planning                                        │  
│  • ROI calculation                                          │
└─────────────────┬───────────────┬───────────────┬───────────┘
                  │               │               │
          ┌───────▼─────┐  ┌──────▼──────┐ ┌─────▼──────┐
          │   Storage   │  │   Compute   │ │ Monitoring │
          │   Agent     │  │   Agent     │ │   Agent    │
          │             │  │             │ │            │
          │ • EBS       │  │ • EC2       │ │ • CloudWatch│
          │ • S3        │  │ • Lambda    │ │ • Logs     │
          │ • Snapshots │  │ • Reserved  │ │ • Metrics  │
          └─────────────┘  └─────────────┘ └────────────┘
```

### Strandsagents Integration

Each agent is built using the Strands Agents SDK:

- **Agent-as-Tool Pattern**: Specialist agents expose their capabilities as tools
- **Shared Memory**: Context exchange between agents
- **Concurrent Execution**: Agents can run in parallel for faster analysis
- **Built-in Telemetry**: Automatic monitoring and observability

## 🎯 Optimization Capabilities

### Storage Optimization

- **Unattached EBS Volumes**: Identify and cleanup orphaned volumes
- **Oversized Volumes**: Recommend rightsizing based on utilization
- **S3 Lifecycle Management**: Optimize storage classes and retention
- **Snapshot Cleanup**: Remove orphaned and unnecessary snapshots

### Compute Optimization

- **Idle Instance Detection**: Find underutilized EC2 instances
- **Rightsizing Recommendations**: Match instance types to workload needs
- **Reserved Instance Planning**: Optimize RI coverage for stable workloads
- **Lambda Optimization**: Memory and timeout tuning for cost efficiency

### Monitoring Optimization

- **Log Group Cleanup**: Remove unused CloudWatch log groups
- **Retention Optimization**: Adjust retention periods to business needs
- **Metric Resolution**: Optimize high-resolution metrics usage
- **Alert Rationalization**: Reduce alert noise and associated costs

## 🔧 Automation Features

### Built-in Safety Mechanisms

- **Whitelist Protection**: Prevent deletion of critical resources
- **Backup Verification**: Ensure data safety before cleanup actions
- **Gradual Rollout**: Test optimizations on non-critical resources first
- **Rollback Capabilities**: Quick reversion if issues detected

### Automation Triggers

```python
# Example automation configuration
automation_config = {
    "unattached_ebs_cleanup": {
        "trigger": "age > 90 days",
        "safety": "snapshot_before_delete",
        "approval": "required"
    },
    "idle_instance_scheduling": {
        "trigger": "cpu < 5% for 7 days",
        "action": "stop_during_off_hours",
        "approval": "automatic"
    },
    "lambda_memory_optimization": {
        "trigger": "memory_usage < 70% allocated",
        "action": "reduce_memory_allocation",
        "approval": "automatic"
    }
}
```

## 📊 Monitoring & Reporting

### Cost Savings Tracking

- **Monthly Savings Reports**: Track actual vs projected savings
- **ROI Analysis**: Measure optimization program effectiveness
- **Trend Analysis**: Identify new waste patterns over time
- **Exception Reporting**: Alert on unusual cost spikes

### Integration Options

- **AWS Cost Explorer**: Enhanced with agent insights
- **Custom Dashboards**: Grafana, Tableau, or AWS QuickSight
- **Slack/Teams Notifications**: Regular savings updates
- **Email Reports**: Executive summaries and technical details

## 🔒 Security & Compliance

### Read-Only by Default

All agents operate in read-only mode by default. Modifications require:

- Explicit configuration
- Appropriate IAM permissions
- Approval workflows (when configured)

### Audit Trail

- All recommendations logged with timestamps
- Action history maintained
- Approval chain documentation
- Compliance reporting capabilities

## 🚀 Production Deployment

### Scalability Considerations

- **Multi-Account Support**: Analyze costs across AWS Organizations
- **Parallel Processing**: Concurrent analysis of large environments
- **Incremental Updates**: Process only changed resources
- **Caching**: Reduce API calls through intelligent caching

### Deployment Options

1. **AWS Lambda**: Serverless execution for cost efficiency
2. **Amazon ECS**: Containerized deployment for scalability
3. **EC2 Instance**: Traditional server deployment
4. **AWS Fargate**: Container deployment without server management

### Example Lambda Deployment

```python
import json
import asyncio
from orchestrator import AWSCostOrchestrator

def lambda_handler(event, context):
    async def analyze():
        orchestrator = AWSCostOrchestrator()
        return await orchestrator.run_full_analysis()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(analyze())
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    finally:
        loop.close()
```

## 🔄 Continuous Improvement

### Machine Learning Integration

Future versions will include:

- **Anomaly Detection**: ML-powered cost spike identification
- **Predictive Analysis**: Forecast future optimization opportunities
- **Pattern Recognition**: Learn from optimization success/failure patterns
- **Automated Tuning**: Self-improving recommendation algorithms

### Feedback Loops

- **Outcome Tracking**: Monitor actual savings from implementations
- **Model Refinement**: Improve accuracy based on real-world results
- **Business Context**: Incorporate business priorities and constraints

## 📞 Support & Contributing

### Getting Help

- **Issues**: Report bugs or request features via GitHub Issues
- **Documentation**: Comprehensive guides in `/docs` directory
- **Examples**: Additional usage examples in `/examples` directory

### Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make changes and add tests
4. Submit a pull request

### Roadmap

- ✅ Core multi-agent architecture
- ✅ Storage and compute optimization
- ⏳ Network and database optimization agents
- ⏳ Real-time cost anomaly detection
- ⏳ ML-powered predictive analysis
- ⏳ Multi-cloud support (Azure, GCP)

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Built with ❤️ using [Strands Agents SDK](https://strandsagents.com)**

*Autonomous cost optimization for modern cloud infrastructure*