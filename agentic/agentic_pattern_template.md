# Agentic Pattern Template

## Agent Behavior Framework

This template transforms static AWS cost detection patterns into intelligent, autonomous agents capable of triggering, investigating, deciding, and acting with minimal human intervention.

### Pattern Structure

```markdown
# Pattern XXX: [Name] - AGENTIC VERSION

## Agent Behavior

### Objective
[Clear autonomous objective with measurable business impact and cost optimization goal]

### Trigger Conditions
The agent should run:
- **Scheduled**: [Regular intervals - daily/weekly/monthly]
- **Event-driven**: [AWS CloudTrail events, resource state changes]
- **Threshold-based**: [Cost thresholds, utilization metrics, growth rates]
- **Context-aware**: [Deployment events, maintenance windows, business hours]

### Investigation Steps
For each flagged resource, the agent should:
1. **Resource Discovery**: [How to identify candidate resources]
2. **Data Collection**: [Metrics, metadata, configuration details to gather]
3. **Context Analysis**: [Usage patterns, dependencies, business context]
4. **Impact Assessment**: [Cost calculation, risk evaluation, potential savings]
5. **Owner Identification**: [Service owner, team, contact information]
6. **Dependency Mapping**: [Related resources, downstream impacts]
7. **Risk Evaluation**: [Production impact, rollback complexity, safety checks]

### Decision Policy
- **Low**: [Criteria and thresholds for low-priority findings]
  - Cost impact: $X-Y/month
  - Risk level: [Low risk scenarios]
  - Action: [Notification only, documentation]

- **Medium**: [Criteria and thresholds for medium-priority findings]
  - Cost impact: $Y-Z/month
  - Risk level: [Medium risk scenarios]  
  - Action: [Owner notification + ticket creation]

- **High**: [Criteria and thresholds for high-priority findings]
  - Cost impact: $Z+/month
  - Risk level: [High risk but manageable]
  - Action: [Urgent notification + approval required for remediation]

- **Critical**: [Criteria and thresholds for critical findings]
  - Cost impact: $W+/month OR rapid cost growth
  - Risk level: [Service impacting or runaway costs]
  - Action: [Immediate escalation + emergency procedures]

### Autonomous Actions
The agent may execute without approval:
- **Safe Operations**:
  - [Non-destructive analysis and reporting]
  - [Notification and alerting]
  - [Documentation and ticket creation]
  - [Monitoring and trend analysis]

- **Low-Risk Changes**:
  - [Reversible configuration changes]
  - [Test environment modifications]
  - [Snapshot creation before changes]

The agent must require approval before:
- **Production Impact**:
  - [Resource deletion or shutdown]
  - [Service configuration changes]
  - [Access or permission modifications]

- **Cost Impact**:
  - [Changes that incur additional costs]
  - [Resource provisioning or upgrades]

- **Service Risk**:
  - [Operations affecting service availability]
  - [Changes to production workloads]
  - [Modifications affecting customer experience]

### Verification Protocol
After remediation is deployed, the agent should:
1. **Immediate Verification**: [0-1 hour checks]
   - [Resource state confirmation]
   - [Service health validation]
   - [Error rate monitoring]

2. **Short-term Monitoring**: [24-48 hour observation]
   - [Performance impact assessment]
   - [Cost impact measurement]
   - [Dependency health checks]

3. **Success Metrics**: [Measurable outcomes]
   - [Cost savings achieved]
   - [Performance improvements]
   - [Risk reduction measures]

4. **Rollback Criteria**: [When to undo changes]
   - [Performance degradation thresholds]
   - [Error rate increases]
   - [Customer impact indicators]

5. **Continuous Monitoring**: [Ongoing observation]
   - [Long-term cost trends]
   - [Recurrence prevention]
   - [Pattern effectiveness measurement]

### Integration Points
- **Notification Systems**: [Slack, email, PagerDuty]
- **Ticketing Systems**: [Jira, ServiceNow, GitHub Issues]
- **Monitoring Systems**: [CloudWatch, DataDog, Prometheus]
- **Approval Workflows**: [Manual approval, automated gates]
- **Cost Management**: [AWS Cost Explorer, billing alerts]

### State Management
- **Investigation State**: [Tracking current analysis progress]
- **Decision History**: [Previous decisions and outcomes]
- **Action Tracking**: [Current and pending operations]
- **Learning Integration**: [Pattern effectiveness feedback]

### Safety Mechanisms
- **Rate Limiting**: [Prevent API abuse and resource exhaustion]
- **Circuit Breakers**: [Stop operations if error rates spike]
- **Approval Gates**: [Human oversight for high-risk operations]
- **Rollback Procedures**: [Automated reversion capabilities]
- **Audit Logging**: [Complete operation traceability]
```

## Trigger Condition Types

### 1. Scheduled Triggers
- **Daily**: High-frequency resource scans (EC2, RDS utilization)
- **Weekly**: Medium-frequency analysis (EBS snapshots, unused resources)
- **Monthly**: Low-frequency audits (historical trends, capacity planning)
- **Quarterly**: Strategic reviews (architecture optimization, major cleanups)

### 2. Event-Driven Triggers
- **Resource Creation**: New resource provisioning
- **Resource Termination**: Resource cleanup opportunities
- **Configuration Changes**: Resource modification events
- **Deployment Events**: Application deployments affecting infrastructure
- **Cost Anomalies**: Unusual spending patterns detected

### 3. Threshold-Based Triggers
- **Cost Thresholds**: Spending exceeds configured limits
- **Utilization Thresholds**: Resource utilization falls below/above targets
- **Growth Rate Thresholds**: Rapid resource or cost growth detected
- **Efficiency Thresholds**: Cost-per-unit metrics exceed benchmarks

### 4. Context-Aware Triggers
- **Business Hours**: Different policies for business vs off-hours
- **Maintenance Windows**: Safe times for potentially disruptive changes
- **Deployment Cycles**: Coordinate with regular deployment schedules
- **Seasonal Patterns**: Adjust for known business seasonality

## Decision Policy Framework

### Cost Impact Tiers
- **Tier 1 ($0-50/month)**: Notification and documentation
- **Tier 2 ($50-250/month)**: Owner notification and ticket creation
- **Tier 3 ($250-1000/month)**: Urgent notification and approval required
- **Tier 4 ($1000+/month)**: Immediate escalation and emergency procedures

### Risk Assessment Levels
- **Low Risk**: Non-production, easily reversible, no service impact
- **Medium Risk**: Test environments, reversible with effort, minimal service impact  
- **High Risk**: Production resources, complex rollback, potential service impact
- **Critical Risk**: Core production, difficult rollback, customer-facing impact

### Action Authorization Matrix

| Risk Level | Cost Tier 1 | Cost Tier 2 | Cost Tier 3 | Cost Tier 4 |
|------------|-------------|-------------|-------------|-------------|
| **Low** | Auto | Auto | Approval | Approval |
| **Medium** | Auto | Approval | Approval | Escalation |
| **High** | Approval | Approval | Escalation | Emergency |
| **Critical** | Approval | Escalation | Emergency | Emergency |

## Implementation Guidelines

### Phase 1: Framework Setup
1. Create base agent class with common functionality
2. Implement trigger system (scheduled, event, threshold, context)
3. Build decision policy engine
4. Create approval workflow integration
5. Implement state management and audit logging

### Phase 2: Pattern Migration
1. Start with low-risk, high-impact patterns
2. Implement agentic versions incrementally
3. Test thoroughly in non-production environments
4. Gradual rollout with monitoring and feedback

### Phase 3: Advanced Features
1. Machine learning for pattern recognition
2. Predictive cost modeling
3. Cross-pattern coordination
4. Advanced safety mechanisms

## Success Metrics

### Agent Effectiveness
- **Cost Savings Achieved**: Monthly cost reductions from agent actions
- **Detection Accuracy**: True positive rate for resource identification
- **False Positive Rate**: Incorrect recommendations requiring rollback
- **Time to Resolution**: Average time from detection to remediation

### Operational Excellence
- **Safety Record**: Number of incidents caused by agent actions
- **Approval Rate**: Percentage of recommendations approved by humans
- **Automation Rate**: Percentage of issues resolved without human intervention
- **Pattern Coverage**: Percentage of cost optimization patterns automated

### Business Impact
- **Total Cost Optimization**: Overall AWS spending reduction
- **Operational Efficiency**: Reduction in manual cost management effort
- **Risk Reduction**: Decrease in cost overruns and budget surprises
- **Team Productivity**: Time saved from automated cost optimization

---

*This template provides the foundation for transforming static detection patterns into intelligent, autonomous cost optimization agents.*