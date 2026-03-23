#!/bin/bash
# Enable Bedrock model access for AWS Bill Whisperer
# Most models auto-enable on first use.
#
# For Anthropic models, you may need to:
# 1. Go to AWS Console → Amazon Bedrock → Model access
# 2. Enable "Claude Sonnet 4.6"
#
# Or simply deploy and run - the API call will auto-enable.

set -e

MODEL_ID="${1:-anthropic.claude-sonnet-4-6:0}"

echo "Model: $MODEL_ID"
echo ""
echo "To enable model access:"
echo "1. Go to: https://console.aws.amazon.com/bedrock#/modelaccess"
echo "2. Find 'Claude Sonnet 4.6' and click 'Edit'"
echo "3. Enable the model and save"
echo ""
echo "Or simply run 'sam deploy' - it will auto-enable on first invocation."
