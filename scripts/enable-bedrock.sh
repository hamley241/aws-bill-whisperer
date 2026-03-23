#!/bin/bash
# Enable Bedrock model access for AWS Bill Whisperer
# Run this once per AWS account before first deployment

set -e

MODEL_ID="${1:-anthropic.claude-sonnet-4-6:0}"

echo "Enabling Bedrock model: $MODEL_ID"

# Check if already enabled
STATUS=$(aws bedrock get-foundation-model-model --model-identifier "$MODEL_ID" --query 'modelSummary.modelAccessStatus' --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$STATUS" = "ENABLED" ]; then
    echo "✓ Model already enabled"
    exit 0
fi

# Enable the model
echo "Enabling model..."
aws bedrock update-model-access \
    --model-identifier "$MODEL_ID" \
    --model-access-status ENABLED

echo "✓ Model enabled successfully"
