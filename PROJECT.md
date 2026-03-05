# AWS Bill Whisperer — Pattern Detection Extension

> Adding automated waste pattern detection to existing Bill Whisperer

**Status:** 🟡 Planning  
**Priority:** High (job hunt portfolio piece + potential revenue)  
**Owner:** Forge (technical build)  
**Created:** 2026-03-01

**NOTE:** Extends existing project (Lambda + Bedrock AI analyzer). Adding CLI pattern scanner as new capability.

---

## 🎯 Vision

"The Data Transfer Detective" — the only CLI that analyzes VPC flow logs and NAT Gateway traffic to find architectural waste, not just billing data.

**Tagline:** "Most companies overspend on AWS by 25-40%. This tool tells you exactly what to fix."

---

## 📋 MVP Scope (Week 1)

| # | Pattern | Complexity | API |
|---|---------|------------|-----|
| 1 | Unattached EBS volumes | Easy | `ec2.describe_volumes` |
| 2 | Unattached Elastic IPs | Easy | `ec2.describe_addresses` |
| 3 | gp2 → gp3 migration | Easy | `ec2.describe_volumes` |
| 4 | Idle EC2 instances | Easy | CloudWatch metrics |
| 5 | Old EBS snapshots | Easy | `ec2.describe_snapshots` |
| 6 | **NAT Gateway waste** | Medium | CloudWatch + VPC flow logs |
| 7 | Idle RDS | Easy | CloudWatch metrics |

---

## 🏗️ Architecture

**Phase 1:** CLI-only (Python/boto3)
- Single script, AWS credentials only
- JSON/CSV/table output
- `--fix` flags for automated remediation

**Phase 2:** Polish
- pip installable package
- GitHub Actions for CI
- brew formula

---

## 📊 Go-to-Market

1. **Week 1-2:** Build MVP, open source on GitHub
2. **Week 3-4:** Run on 5 real accounts (friends/startups) for case studies
3. **Month 2:** LinkedIn content: "I saved $X with this tool I built"
4. **Month 3:** Optional SaaS layer ($49/account/month)

---

## 🔗 Resources

- Research: `~/.openclaw/workspace/aws-cost-optimization-patterns.md`
- Consul analysis: 2026-03-01 (Kimi + Sonnet + GPT-4o unanimous on CLI-first)

---

## 📝 Tasks

- [ ] Scaffold CLI structure (Click/Typer)
- [ ] Implement Pattern 1: Unattached EBS
- [ ] Implement Pattern 2: Unattached EIPs
- [ ] Implement Pattern 3: gp2→gp3
- [ ] Implement Pattern 4: Idle EC2
- [ ] Implement Pattern 5: Old Snapshots
- [ ] Implement Pattern 6: NAT Gateway waste (differentiator)
- [ ] Implement Pattern 7: Idle RDS
- [ ] Add `--json` output flag
- [ ] Add `--fix` dry-run mode
- [ ] Write README with demo GIF
- [ ] Publish to GitHub
- [ ] Write LinkedIn launch post

---

## 💡 Blind Spots to Add Later

- S3 Intelligent Tiering gaps
- CloudWatch Logs retention
- Lambda memory over-provisioning
- Cross-AZ data transfer
- Savings Plans coverage analysis

---

*Consul vote: 3/3 models agreed on CLI-first, open source, NAT as differentiator*
