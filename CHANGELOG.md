# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-02-20

### Added
- Initial release of AWS Bill Whisperer
- Lambda function for automated cost analysis
- CLI tool for local cost analysis (`--mock` flag for testing)
- AWS Cost Explorer integration for fetching usage data
- Bedrock Claude integration for AI-powered analysis
- Plain English explanations of cost changes
- Top cost drivers identification
- Simple cost optimization recommendations
- Multiple output formats (markdown, JSON, Slack)
- CloudFormation/SAM template for one-click deployment
- Support for custom analysis periods
- Service-level and region-level cost breakdowns
- Cost comparison with previous periods

### Infrastructure
- SAM/CloudFormation template with IAM policies
- Lambda function with configurable memory/timeout
- Environment variable configuration support

### Documentation
- Comprehensive README with architecture diagram
- Sample output examples
- Configuration guide
- Quick start guides for all deployment options

[Unreleased]: https://github.com/hamley241/aws-bill-whisperer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hamley241/aws-bill-whisperer/releases/tag/v0.1.0
