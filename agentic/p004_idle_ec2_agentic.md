# Pattern 004: Idle EC2 Instances - AGENTIC VERSION

## Agent Behavior

### Objective
Continuously detect, analyze, and optimize underutilized EC2 instances to reduce compute costs while maintaining service reliability and performance. Achieve 30-60% cost reduction on idle compute resources through intelligent rightsizing, scheduling, and consolidation with <0.1% service impact rate.

### Trigger Conditions
The agent should run:
- **Scheduled**: 
  - Daily CPU utilization analysis at 3 AM UTC
  - Weekly comprehensive analysis including network and memory patterns
  - Monthly rightsizing recommendations with cost projections
- **Event-driven**: 
  - Instance launch events (establish monitoring baselines)
  - Auto Scaling events (analyze pattern changes)
  - Application deployment events (reassess resource requirements)
  - Cost anomaly alerts (investigate sudden utilization changes)
- **Threshold-based**: 
  - CPU utilization <5% average over 14 days
  - Instance idle time >80% over 7 days
  - Compute cost per unit of work exceeds benchmark by >50%
  - Monthly compute waste exceeds $1000 per service
- **Context-aware**: 
  - Avoid analysis during known high-traffic periods (Black Friday, etc.)
  - Coordinate with deployment schedules and maintenance windows
  - Consider seasonal business patterns and growth trends

### Investigation Steps
For each flagged idle instance, the agent should:

1. **Resource Discovery & Utilization Analysis**
   - Collect 14-day CPU, memory, network, and disk utilization metrics
   - Analyze usage patterns: peak hours, weekdays vs weekends, seasonal trends
   - Identify burst patterns that might indicate actual workload requirements
   - Calculate cost per hour and monthly spend for current configuration

2. **Workload Classification & Context**
   - Determine application type from tags, AMI, and running processes
   - Identify if instance is part of Auto Scaling group or load balancer
   - Check for scheduled jobs, batch processing, or standby purposes
   - Assess criticality level (production, staging, development, testing)

3. **Business Impact Assessment**
   - Map instance to service ownership and business function
   - Evaluate SLA requirements and availability needs
   - Calculate potential cost savings from rightsizing or scheduling
   - Assess impact on dependent services and data flows

4. **Performance History Analysis**
   - Review historical performance during traffic spikes
   - Analyze response time and throughput trends
   - Identify resource bottlenecks beyond CPU (memory, I/O, network)
   - Compare current allocation vs actual peak usage

5. **Owner and Service Mapping**
   - Extract ownership from tags (Owner, Team, Project, Environment)
   - Correlate with service catalog and architecture documentation
   - Identify budget owner and cost center allocation
   - Determine technical contacts and on-call responsibilities

6. **Dependency and Risk Analysis**
   - Map network dependencies and service mesh connections
   - Identify data persistence and state management requirements
   - Assess disaster recovery and backup implications
   - Evaluate testing and deployment pipeline dependencies

7. **Optimization Opportunity Evaluation**
   - Calculate rightsizing options (smaller instance types)
   - Assess spot instance eligibility for cost savings
   - Evaluate containerization or serverless migration potential
   - Consider scheduled start/stop patterns for dev/test workloads

### Decision Policy

- **Low Priority** (<10% utilization, dev/test, <$100/month savings):
  - Development and testing environments with proper tagging
  - Recently launched instances still establishing patterns
  - Action: Owner notification with rightsizing recommendations and 30-day timeline

- **Medium Priority** (5-10% utilization, staging, $100-500/month savings):
  - Staging environments with low but consistent usage
  - Production instances with clear rightsizing opportunity
  - Action: Detailed analysis report to owner + performance impact assessment + 14-day review timeline

- **High Priority** (<5% utilization, production, $500-2000/month savings):
  - Production workloads significantly over-provisioned
  - Clear rightsizing opportunity without performance risk
  - Action: Engineering review required + architectural assessment + staged implementation plan

- **Critical Priority** (<2% utilization, >$2000/month savings, or rapid cost growth):
  - Severely oversized instances with massive waste
  - Abandoned or forgotten production workloads
  - Runaway auto-scaling causing cost explosion
  - Action: Immediate escalation + emergency cost control + architectural review

### Autonomous Actions

The agent may execute **without approval**:

**Safe Analysis and Reporting**:
- Generate detailed utilization reports with trend analysis
- Create cost optimization recommendations with impact projections
- Send educational notifications about rightsizing opportunities
- Tag instances with utilization metrics and optimization potential
- Create performance baseline documentation
- Generate monthly cost optimization dashboards

**Low-Risk Optimizations**:
- Schedule automatic stop/start for dev/test instances during off-hours
- Enable detailed monitoring for instances lacking performance data
- Create snapshots before any optimization attempts
- Apply cost allocation tags based on usage patterns

**Development Environment Actions** (specific conditions):
- Auto-stop dev/test instances after 12 hours of <1% CPU usage
- Automatically resize dev instances that are >5x oversized
- Implement instance scheduling for known development hours
- (Only if tagged as "Environment: Development" AND "AutoOptimize: Enabled")

The agent must require **approval before**:

**Production Impact**:
- Modifying production instance sizes or configurations
- Stopping or starting production workloads
- Changing Auto Scaling group configurations
- Implementing any optimization affecting SLA-bound services

**Architecture Changes**:
- Recommending container or serverless migrations
- Suggesting major architectural refactoring
- Proposing cross-service optimization strategies

**Cost Impact**:
- Optimizations requiring instance replacement (storage, networking changes)
- Spot instance migrations with availability trade-offs
- Reserved Instance purchase recommendations >$10,000

### Verification Protocol

After optimization is deployed, the agent should:

1. **Immediate Verification (0-2 hours)**:
   - Confirm instance state changes completed successfully
   - Verify application health checks pass post-optimization
   - Monitor error rates and response times for degradation
   - Check that dependent services remain operational

2. **Performance Monitoring (24-72 hours)**:
   - Track key performance metrics: response time, throughput, error rate
   - Monitor CPU, memory, and I/O utilization on resized instances
   - Verify Auto Scaling behaviors adapt properly to new instance sizes
   - Check for resource constraint warnings or throttling

3. **Success Metrics (Weekly)**:
   - Calculate actual cost savings achieved vs projected
   - Measure performance impact: latency, availability, user satisfaction
   - Track optimization success rate and rollback frequency
   - Monitor long-term utilization patterns post-optimization

4. **Rollback Criteria**:
   - Response time degrades >20% from baseline
   - Error rate increases >2% above normal levels
   - CPU utilization consistently exceeds 80% post-optimization
   - Customer complaints or SLA violations related to performance
   - **Rollback Method**: Restore previous instance configuration from snapshot/AMI

5. **Continuous Learning (Monthly)**:
   - Analyze optimization effectiveness across different workload types
   - Refine utilization thresholds based on false positive/negative rates
   - Update cost savings models with actual results
   - Improve workload classification algorithms

### Integration Points

**Notification Systems**:
- Slack notifications to #cost-optimization and service owner channels
- Email alerts to engineering leads with detailed recommendations
- PagerDuty integration for critical cost overruns or optimization failures

**Ticketing Systems**:
- Auto-create Jira epics for significant optimization opportunities
- Link to AWS Cost Explorer showing projected savings
- Include performance analysis and suggested implementation timeline

**Monitoring Systems**:
- CloudWatch dashboard integration for optimization tracking
- Custom metrics for agent effectiveness and cost impact
- Application Performance Monitoring (APM) integration for impact assessment

**Configuration Management**:
- Integration with Infrastructure-as-Code (Terraform, CloudFormation)
- Git-based change tracking for optimization implementations
- Configuration drift detection post-optimization

**Cost Management**:
- AWS Cost Explorer integration with optimization attribution
- Budget forecasting with projected savings integration
- Chargeback reporting with optimization impact

### State Management

**Investigation State**:
```json
{
  "instance_id": "i-1234567890abcdef0",
  "discovery_date": "2026-03-30",
  "avg_cpu_utilization": 3.2,
  "current_cost_monthly": 876.48,
  "optimization_potential": 65,
  "workload_type": "web_application",
  "environment": "production",
  "risk_level": "high",
  "owner": "platform-team",
  "recommendations": [
    {
      "type": "rightsize",
      "from": "m5.2xlarge",
      "to": "m5.large",
      "monthly_savings": 584.32,
      "performance_impact": "minimal"
    }
  ],
  "optimization_scheduled": "2026-04-15"
}
```

**Performance Baseline**:
- Historical performance metrics for rollback reference
- Workload characterization and resource usage patterns
- Dependency mapping and service interaction patterns

### Safety Mechanisms

**Rate Limiting**:
- Maximum 5 production instance optimizations per day per service
- Maximum $5000/day in optimization operations
- Gradual rollout: optimize 10% of similar instances first

**Circuit Breakers**:
- Stop optimizations if service degradation detected across >2 instances
- Pause operations if rollback rate exceeds 10% in 24 hours
- Emergency stop if customer-impacting incidents linked to optimizations

**Approval Gates**:
- Engineering approval for production instances >$1000/month
- Architecture review for optimizations affecting >10% of service capacity
- Security approval for instances with sensitive data or compliance requirements

**Gradual Rollout**:
- Blue/green deployment patterns for critical service optimization
- Canary analysis: optimize 1 instance, monitor, then proceed
- Automatic rollback triggers based on performance thresholds

**Performance Guards**:
- Pre-optimization performance profiling and baseline establishment
- Real-time monitoring during optimization window
- Automated rollback triggers for performance regression

### Expected Outcomes

**Monthly Cost Savings**: $25,000-100,000 across typical enterprise AWS account
**Optimization Rate**: 85% of truly idle instances optimized within 30 days
**Safety Record**: <0.1% service impact rate, <5% optimization rollback rate
**Performance Impact**: <5% average performance degradation, >90% user satisfaction maintained

**ROI Metrics**:
- 300-500% ROI on agent development and operational costs
- 40-60% reduction in compute waste across dev/test environments
- 15-25% overall compute cost reduction while maintaining service levels

---

*This agentic pattern transforms reactive instance management into proactive, intelligent compute optimization while maintaining enterprise-grade reliability and performance standards.*