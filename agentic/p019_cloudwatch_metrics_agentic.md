# Pattern 019: High-Cardinality CloudWatch Custom Metrics - AGENTIC VERSION

## Agent Behavior

### Objective
Continuously detect and reduce high-cardinality CloudWatch custom metrics that create unnecessary cost or observability risk. Prevent metric explosion scenarios while maintaining essential monitoring coverage. Achieve 50-90% reduction in custom metric costs without compromising service observability.

### Trigger Conditions
The agent should run:
- **Scheduled**: 
  - Weekly cost scans every Sunday at 4 AM UTC
  - Daily cardinality growth monitoring
  - Monthly comprehensive observability cost review
- **Event-driven**: 
  - After deployments affecting application observability or metric emission
  - New application deployments that introduce custom metrics
  - CloudWatch billing anomaly alerts
- **Threshold-based**: 
  - When a namespace shows >20% week-over-week stream growth
  - When projected monthly custom metric spend exceeds configured threshold
  - When namespace exceeds 1000 unique metric streams
  - When individual metric dimension count exceeds 5
- **Context-aware**: 
  - Post-deployment metric explosion detection
  - Coordinate with observability team maintenance windows
  - Align with monthly cost optimization reviews

### Investigation Steps
For each flagged namespace, the agent should:

1. **Metric Stream Enumeration and Analysis**
   - Enumerate all metric streams and group by metric name
   - Calculate unique dimension combinations per metric
   - Identify dimension names and their cardinality patterns
   - Map metric publication frequency and data point volume

2. **Cardinality Pattern Detection**
   - Identify dimension names likely to be unbounded (user_id, request_id, timestamp, token, trace_id)
   - Detect rapidly growing dimensions indicating runaway cardinality
   - Analyze dimension value patterns for uniqueness vs categorical data
   - Compare current cardinality to historical trends

3. **Cost Impact Assessment**
   - Estimate monthly metric storage cost ($0.30 per metric per month)
   - Calculate PutMetricData ingestion cost ($0.01 per 1,000 data points)
   - Project annual cost trajectory based on growth trends
   - Compare findings to previous scans to detect acceleration

4. **Service Ownership and Context Mapping**
   - Map namespace to likely service owners using naming conventions
   - Correlate with repository metadata and deployment information
   - Identify service team and on-call contacts
   - Determine service criticality and observability requirements

5. **Instrumentation Code Analysis**
   - Search codebase for metric emission patterns causing high cardinality
   - Identify specific code paths that emit the offending metrics
   - Analyze metric emission logic for optimization opportunities
   - Review metric collection frameworks and libraries used

6. **Dependency and Impact Assessment**
   - Determine whether dashboards depend on the high-cardinality metrics
   - Check if alarms are configured using these metrics
   - Assess SLA monitoring dependencies
   - Evaluate impact on existing monitoring workflows

7. **Alternative Architecture Evaluation**
   - Assess feasibility of EMF (Embedded Metric Format) migration
   - Evaluate metric aggregation opportunities
   - Consider sampling strategies for high-volume metrics
   - Review CloudWatch Logs Insights as alternative for detailed analysis

### Decision Policy

- **Low Priority** (>100 streams OR >5 dimensions, slow growth, <$250/month):
  - Gradual cardinality increase within acceptable bounds
  - Development environments with bounded test data
  - Metrics with clear business value and controlled growth
  - Action: Owner notification with optimization recommendations

- **Medium Priority** (>1000 streams OR projected cost >$250/month):
  - Moderate cardinality explosion with cost impact
  - Production metrics with optimization opportunities
  - Clear inefficiencies in metric emission patterns
  - Action: Technical review with service team + optimization plan within 14 days

- **High Priority** (Unbounded dimensions OR projected cost >$1000/month):
  - Clear presence of unique-ID dimensions causing explosion
  - Production workloads with significant cost impact
  - Rapid growth trajectory indicating architectural issues
  - Action: Immediate engineering escalation + architectural review + mitigation plan within 7 days

- **Critical Priority** (Runaway growth OR >$5000/month, risk of service impact):
  - Rapidly growing cardinality with exponential cost increase
  - Risk of CloudWatch API throttling affecting service monitoring
  - Potential service availability impact from metric system overload
  - Action: Emergency escalation + immediate cost control + emergency architectural changes

### Autonomous Actions

The agent may execute **without approval**:

**Safe Analysis and Documentation**:
- Create comprehensive cardinality analysis reports
- Generate cost impact projections and trend analysis
- Notify service owners with detailed optimization recommendations
- Open tracking tickets with specific remediation steps
- Tag namespaces with cardinality risk levels and optimization potential
- Create alternative monitoring strategy recommendations (EMF, aggregation, sampling)

**Educational and Preventive Actions**:
- Generate EMF migration examples for identified anti-patterns
- Create metric optimization best practices documentation
- Provide code-level remediation suggestions and examples
- Recommend alternative observability approaches for specific use cases

**Low-Risk Monitoring Changes** (development environments only):
- Enable enhanced monitoring to better understand metric usage patterns
- Create test metric aggregation configurations
- Implement metric sampling in non-production environments
- (Only if Environment: Development AND AutoOptimize: Enabled tags present)

The agent must require **approval before**:

**Production Observability Changes**:
- Disabling or modifying metric publication in production
- Removing dimensions from production metric streams
- Changing metric emission frequency or sampling rates
- Modifying metric aggregation or filtering logic

**Infrastructure Changes**:
- Modifying alarms or dashboards that depend on high-cardinality metrics
- Changing observability contracts used by other teams or services
- Implementing architectural changes affecting metric collection

**Service Risk Operations**:
- Any changes that could impact SLA monitoring or alerting
- Modifications to critical path monitoring or business metrics
- Changes affecting compliance or audit monitoring requirements

### Verification Protocol

After remediation is deployed, the agent should:

1. **Immediate Verification (0-4 hours)**:
   - Confirm metric stream count reduction as expected
   - Verify critical alarms and dashboards continue functioning
   - Check for any monitoring gaps or blind spots introduced
   - Monitor for service degradation related to observability changes

2. **Short-term Monitoring (24-48 hours)**:
   - Re-scan the namespace to measure actual cardinality reduction
   - Compare metric stream counts before and after optimization
   - Verify no increase in application error rates or performance issues
   - Check that essential business metrics remain available

3. **Cost Impact Assessment (Weekly)**:
   - Recalculate projected monthly cost savings
   - Track actual CloudWatch billing reduction
   - Measure optimization effectiveness vs. projections
   - Verify no unexpected cost increases in related services

4. **Observability Quality Verification (Monthly)**:
   - Confirm service monitoring effectiveness maintained
   - Verify incident detection capabilities preserved
   - Check that performance monitoring remains comprehensive
   - Validate business metrics and SLA tracking continue functioning

5. **Rollback Criteria**:
   - Critical service monitoring gaps identified
   - SLA violations due to insufficient observability
   - Incident detection degradation >10 minutes average
   - Business stakeholder requirements not met
   - **Rollback Method**: Restore previous metric emission patterns from configuration backup

6. **Continuous Learning and Prevention**:
   - Mark the finding resolved only if cardinality remains reduced across consecutive scans
   - Analyze root causes that led to metric explosion
   - Update detection algorithms based on new anti-patterns discovered
   - Share learnings across service teams for prevention

### Integration Points

**Notification Systems**:
- Slack notifications to #observability and service owner channels
- Email alerts to SRE and FinOps teams with cost impact analysis
- PagerDuty integration for critical metric cost explosions

**Ticketing Systems**:
- Auto-create Jira tickets with detailed cardinality analysis
- Link to CloudWatch console with specific metric stream details
- Include code-level remediation suggestions and EMF migration examples

**Monitoring Systems**:
- CloudWatch dashboard for metric cardinality trends
- Custom metrics tracking agent effectiveness and cost savings
- Integration with cost monitoring tools for savings attribution

**Development Workflow**:
- Integration with CI/CD pipelines to detect metric anti-patterns
- Code review automation for metric emission pattern analysis
- Documentation integration with observability runbooks

**Cost Management**:
- AWS Cost Explorer integration with metric cost attribution
- Budget forecasting with cardinality growth projections
- FinOps reporting with optimization impact tracking

### State Management

**Investigation State**:
```json
{
  "namespace": "MyApp/CustomMetrics",
  "discovery_date": "2026-03-30",
  "metric_streams": 2847,
  "growth_rate_weekly": 23.5,
  "current_monthly_cost": 854.10,
  "projected_annual_cost": 12450.00,
  "risk_level": "high",
  "unbounded_dimensions": ["user_id", "request_id", "trace_id"],
  "service_owner": "platform-engineering",
  "dashboards_dependent": ["service-health", "user-analytics"],
  "alarms_dependent": ["high-latency", "error-rate"],
  "optimization_scheduled": "2026-04-10",
  "remediation_plan": {
    "approach": "EMF_migration",
    "estimated_savings": 675.25,
    "timeline": "14_days",
    "risk_assessment": "low"
  }
}
```

**Pattern Library**:
- Common anti-patterns and their solutions
- EMF migration templates for different use cases
- Metric aggregation strategies by service type

### Safety Mechanisms

**Rate Limiting**:
- Maximum 3 production namespace optimizations per week
- Maximum $2000/month in optimization operations
- Gradual rollout: optimize test environments first

**Circuit Breakers**:
- Stop optimizations if >2 critical monitoring alerts triggered
- Pause operations if service incidents linked to metric changes
- Emergency halt if SLA violations detected post-optimization

**Approval Gates**:
- SRE approval required for production observability changes
- Architecture review for optimizations affecting >$1000/month
- Security approval for metrics containing sensitive data dimensions

**Monitoring Preservation**:
- Maintain critical business and SLA metrics regardless of cost
- Preserve incident detection capabilities during optimization
- Ensure compliance monitoring requirements remain met

**Rollback Procedures**:
- Maintain metric emission configuration backups
- Document complete restoration procedures for each optimization
- Test rollback procedures in staging environments

### Expected Outcomes

**Monthly Cost Savings**: $15,000-50,000 across typical enterprise AWS account
**Optimization Rate**: 80% of high-cardinality namespaces optimized within 30 days
**Safety Record**: Zero monitoring gaps, <2% rollback rate for observability issues
**Cardinality Reduction**: 50-90% reduction in metric streams while maintaining coverage

**Observability Quality Metrics**:
- <5% increase in incident detection time
- >95% critical metric coverage maintained
- <1% degradation in service monitoring effectiveness
- 100% SLA monitoring capability preserved

**Prevention Effectiveness**:
- 70% reduction in new high-cardinality anti-pattern introduction
- 50% improvement in metric efficiency across new services
- 80% adoption rate of recommended EMF patterns

---

*This agentic pattern transforms reactive metric cost management into proactive, intelligent observability optimization while maintaining enterprise-grade monitoring quality and service reliability.*