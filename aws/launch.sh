#!/usr/bin/env bash
# Launch one GPU experiment box. Run from your laptop with AWS CLI configured.
# Usage: ./launch.sh [on-demand|spot] [instance-type]
# Before the first launch: request a quota increase for "Running On-Demand G and VT instances"
# (and "All G and VT Spot Instance Requests") to at least 4 vCPUs in the region. New accounts start at 0.
set -euo pipefail

MARKET="${1:-on-demand}"
ITYPE="${2:-g6.xlarge}"            # 1x NVIDIA L4 24 GB, 4 vCPU, 16 GiB RAM. g6.2xlarge = same GPU, 8 vCPU, 32 GiB.
REGION="${REGION:-us-west-2}"
BUCKET="${BUCKET:?set BUCKET=<your-s3-bucket>}"
PROFILE_NAME="${PROFILE_NAME:-mas-runner}"   # AmazonSSMManagedInstanceCore + read/write on $BUCKET
SUBNET="${SUBNET:?set SUBNET=subnet-...}"
SG="${SG:?set SG=sg-... (no inbound rules; connect via SSM)}"

# AWS Deep Learning Base AMI (NVIDIA driver + CUDA toolkit preinstalled). Resolve once, then PIN via AMI=.
AMI="${AMI:-$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)}"
echo "AMI=$AMI  (pin this)"

MARKET_OPTS=()
if [[ "$MARKET" == "spot" ]]; then
  MARKET_OPTS=(--instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}')
fi

# user-data needs the bucket name; substitute it in
UD=$(mktemp); sed "s|__BUCKET__|$BUCKET|" "$(dirname "$0")/user-data.sh" > "$UD"

aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type "$ITYPE" \
  --subnet-id "$SUBNET" --security-group-ids "$SG" \
  --iam-instance-profile Name="$PROFILE_NAME" \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3,DeleteOnTermination=true}' \
  --metadata-options HttpTokens=required \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=mas-gpu},{Key=project,Value=memory-aware-agent-serving}]" \
  --user-data "file://$UD" \
  "${MARKET_OPTS[@]}" \
  --query 'Instances[0].InstanceId' --output text
