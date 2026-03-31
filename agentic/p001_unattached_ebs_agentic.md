# Pattern 001: Unattached EBS Volumes - AGENTIC VERSION

## Agent Behavior

### Objective
Continuously detect, assess, and remediate unattached EBS volumes to eliminate wasteful spending while maintaining data safety and business continuity. Achieve 90%+ reduction in orphaned volume costs through intelligent automation with zero data loss incidents.

### Trigger Conditions
The agent should run:
- **Scheduled**: 
  - Daily scans at 2 AM UTC (low-impact time)
  - Weekly deep analysis with trending on Sundays
- **Event-driven**: 
  - EC2 instance termination events (check for orphaned volumes)
  - Volume detachment events (start tracking for potential cleanup)
  - Deployment completion events (identify temporary volumes left behind)
- **Threshold-based**: 
  - When unattached volume costs exceed $100/month per region
  - When >50 unattached volumes exist in any single region
  - When volume has been unattached for >7 days (early warning)
- **Context-aware**: 
  - Coordinate with maintenance windows for safe cleanup
  - Skip during known migration periods or disaster recovery operations
  - Accelerate during month-end cost optimization drives

### Investigation Steps
For each flagged unattached volume, the agent should:

1. **Volume Discovery & Classification**
   - Enumerate all volumes in `available` state across all regions
   - Group by volume type (gp2, gp3, io1, io2, st1, sc1)
   - Calculate current monthly cost using regional pricing

2. **Historical Context Analysis**
   - Determine how long volume has been unattached (CloudTrail analysis)
   - Identify last attached instance and termination reason
   - Check if volume was part of recent migration or deployment

3. **Data Protection Assessment**
   - Verify if recent snapshots exist (<30 days old)
   - Check snapshot retention policies and backup compliance
   - Assess volume importance based on size and previous instance type

4. **Business Impact Evaluation**
   - Calculate monthly waste cost (volume size × regional pricing)
   - Estimate annual cost impact if left unchecked
   - Determine cost savings opportunity vs. snapshot storage cost

5. **Owner and Service Identification**
   - Extract owner information from volume tags (Owner, Team, Project)
   - Map to service using instance metadata from last attachment
   - Identify cost center and budget allocation

6. **Dependency and Risk Mapping**
   - Check for any automation or scripts referencing the volume ID
   - Verify no active mount points or attachment plans
   - Assess rollback complexity if volume needed later

7. **Safety and Compliance Verification**
   - Ensure compliance with data retention policies
   - Check for regulatory requirements (SOX, HIPAA, etc.)
   - Validate no recent I/O activity through CloudWatch metrics

### Decision Policy

- **Low Priority** ($0-50/month total waste, 30-90 days unattached):
  - Has recent snapshots and low business value
  - Test/development environments with proper tagging
  - Action: Owner notification and 30-day cleanup timeline

- **Medium Priority** ($50-200/month total waste, 7-30 days unattached):
  - Production volumes but with recent backup
  - No clear owner identification but snapshot exists
  - Action: Urgent owner notification + ticket creation + 7-day cleanup timeline

- **High Priority** ($200-500/month total waste, >90 days unattached):
  - Large volumes with significant cost impact
  - No recent snapshots requiring data assessment
  - Action: Immediate owner escalation + approval required for snapshot-and-delete

- **Critical Priority** ($500+/month total waste OR rapidly growing inventory):
  - Runaway volume creation from failed automation
  - Multiple large volumes from botched migrations
  - Action: Immediate escalation to SRE/FinOps + emergency cleanup procedures

### Autonomous Actions

The agent may execute **without approval**:

**Safe Operations**:
- Create comprehensive cost analysis reports
- Send owner notifications via Slack/email
- Create Jira tickets with cleanup recommendations
- Generate monthly waste trending reports
- Create snapshots of volumes before any cleanup (under certain conditions)
- Tag volumes with cleanup schedules and owner notification dates

**Low-Risk Remediation** (specific conditions):
- Delete unattached volumes that are:
  - <10GB in size AND
  - Unattached >90 days AND  
  - Have snapshots <7 days old AND
  - Tagged as "test" or "dev" environment AND
  - Monthly cost <$5

The agent must require **approval before**:

**Production Impact**:
- Deleting any volume >10GB without explicit owner consent
- Deleting volumes without recent snapshots
- Removing volumes tagged as "production" or "critical"
- Modifying volumes with unknown or missing owner tags

**Data Protection**:
- Deleting volumes that lack recent snapshots
- Removing volumes from compliance-regulated workloads
- Changing retention policies or backup schedules

**Cost Impact**:
- Creating snapshots that would cost >$50/month in storage
- Implementing automated cleanup policies affecting >$1000/month

### Verification Protocol

After remediation is deployed, the agent should:

1. **Immediate Verification (0-4 hours)**:
   - Confirm volume deletion completed successfully
   - Verify snapshots are accessible and complete
   - Check no service alerts or incidents triggered
   - Validate cost reduction appears in billing data

2. **Short-term Monitoring (24-72 hours)**:
   - Monitor for any service degradation in related applications
   - Track customer support tickets for data access issues
   - Verify no rollback requests from service owners
   - Confirm no unexpected volume recreation

3. **Success Metrics (Weekly/Monthly)**:
   - Calculate actual cost savings achieved vs. projected
   - Measure reduction in total unattached volume count
   - Track time from detection to resolution
   - Monitor false positive rate (volumes incorrectly flagged)

4. **Rollback Criteria (if needed)**:
   - Service owner requests data recovery within 30 days
   - Critical business process requires deleted volume data
   - Compliance audit identifies retention policy violation
   - **Rollback Method**: Restore from snapshot to new volume

5. **Continuous Learning (Monthly)**:
   - Analyze patterns in volume abandonment (which teams, which deployments)
   - Update detection thresholds based on false positive rates
   - Refine owner identification algorithms
   - Improve cost impact prediction accuracy

### Integration Points

**Notification Systems**:
- Slack notifications to #finops and service owner channels
- Email alerts to budget owners and technical leads
- PagerDuty for critical cost overruns (>$1000/month waste)

**Ticketing Systems**:
- Auto-create Jira tickets with cost analysis and cleanup timeline
- Link to AWS Cost Explorer with specific volume costs
- Include snapshot verification and rollback procedures

**Monitoring Systems**:
- CloudWatch dashboards for volume lifecycle tracking
- Cost anomaly detection integration
- Custom metrics for agent effectiveness and safety

**Approval Workflows**:
- ServiceNow approval for high-value volume deletions
- Slack approval workflow for medium-risk operations
- Auto-approval for low-risk operations meeting strict criteria

**Cost Management**:
- Integration with AWS Cost Explorer for impact tracking
- Budget alert integration for cost threshold monitoring
- Monthly reporting to FinOps team with savings attribution

### State Management

**Investigation State**:
```json
{
  "volume_id": "vol-12345",
  "discovery_date": "2026-03-30",
  "last_attached": "2026-02-15",
  "investigation_phase": "owner_identification",
  "cost_impact": 25.60,
  "risk_level": "medium",
  "owner_contacted": true,
  "snapshot_status": "recent_available",
  "cleanup_scheduled": "2026-04-15"
}
```

**Decision History**:
- Track all decisions made and rationale
- Record approval/rejection patterns for machine learning
- Maintain audit trail for compliance requirements

**Action Tracking**:
- Current operations in progress
- Pending approvals and timelines
- Scheduled cleanup operations

### Safety Mechanisms

**Rate Limiting**:
- Maximum 10 volume deletions per day per region
- Maximum $500/day in cleanup operations
- Pause operations if error rate exceeds 2%

**Circuit Breakers**:
- Stop all operations if >1 service incident linked to agent actions
- Pause if >3 rollback requests received in 24 hours
- Emergency stop if compliance violation detected

**Approval Gates**:
- Human approval required for >$100/month individual volume cleanup
- FinOps approval required for >$1000/month total monthly cleanup
- Security approval required for volumes with PII/sensitive data tags

**Rollback Procedures**:
- Maintain snapshot references for 90 days minimum
- Document complete restoration procedures
- Test rollback procedures monthly

**Audit Logging**:
- Complete CloudTrail integration for all operations
- Detailed logging of decision rationale and data sources
- Compliance reporting for regulatory requirements

### Expected Outcomes

**Monthly Cost Savings**: $5,000-15,000 across typical enterprise AWS account
**Automation Rate**: 70% of unattached volumes cleaned up without human intervention
**Safety Record**: Zero data loss incidents, <1% false positive rate
**Time to Resolution**: Average 3 days from detection to cleanup (vs. 30+ days manual)

---

*This agentic pattern transforms reactive volume cleanup into proactive, intelligent cost optimization while maintaining enterprise-grade safety and compliance standards.*