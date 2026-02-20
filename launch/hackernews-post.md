# AWS Bill Whisperer — Hacker News Launch

---

## Title Options (pick one)

1. **Show HN: Self-hosted AWS cost analyzer – your billing data never leaves your account**

2. **Show HN: Privacy-first AWS bill explainer (runs entirely in your account via Bedrock)**

3. **Show HN: I built an open-source alternative to Vantage that keeps your billing data in your AWS account**

---

## Post Text

I spent 5 years at AWS (Lambda, BigMac team). One thing I saw constantly: engineers confused by their AWS bills.

Existing tools (Vantage, CloudHealth, Finout) are good, but they're SaaS — your billing data flows through their infrastructure. For some teams, that's a dealbreaker.

**AWS Bill Whisperer** is different:

- Deploys 100% in your AWS account (CloudFormation one-click)
- Uses Bedrock (Claude) for AI analysis — runs in YOUR account
- Explains cost spikes in plain English
- Suggests optimizations
- ~$1-5/mo in AWS costs, no SaaS subscription
- MIT licensed, fork it and customize

**Who it's for:**
- Security-conscious teams
- Regulated industries (can't send billing data externally)
- OSS purists who want to self-host

**Who it's NOT for:**
- If you need 26 cloud providers and enterprise features, use Vantage
- If data leaving your account isn't a concern, existing tools work fine

This is deliberately simple. Deploy in 5 minutes, get plain English explanations of your AWS costs.

GitHub: [link]

Feedback welcome — what's broken, what's missing, what would make you actually use this?

---

## Potential HN Comments to Prepare For

**"Why not just use Cost Explorer?"**
> You can. But Cost Explorer shows you numbers, not explanations. Bill Whisperer tells you *why* your bill changed and what to do about it.

**"Bedrock still sends data to AWS"**
> Bedrock runs in your account, in your region. It's the same trust boundary as any AWS service. No third-party ever sees your data.

**"Vantage has a free tier"**
> True. But Vantage still connects to your account and processes data on their infra. For some security policies, that's not allowed.

**"This is just a wrapper around Bedrock"**
> Mostly, yes. The value is in the prompts, the cost parsing logic, and the CloudFormation deployment. It's simple by design.

**"Will you add [feature X]?"**
> Maybe! Open an issue. If it fits the "simple, self-hosted" philosophy, happy to consider it.
