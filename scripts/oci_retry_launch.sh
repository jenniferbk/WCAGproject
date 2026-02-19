#!/usr/bin/env bash
# Retry launching an OCI ARM instance until capacity is available.
# Tries all 3 Ashburn ADs in rotation, every 60 seconds.
# Run: bash scripts/oci_retry_launch.sh
# Stop: Ctrl-C or kill the process

set -uo pipefail
export SUPPRESS_LABEL_WARNING=True

TENANCY="ocid1.tenancy.oc1..aaaaaaaa7rfcqld5epuahpyp6khxzhrga62c3h3xgrgxwojlo7abop4kek4a"
SUBNET_ID="ocid1.subnet.oc1.iad.aaaaaaaazssdpfvoihusazamobbh6adeugu4durreffc2v5lgqsozsahslga"
IMAGE_ID="ocid1.image.oc1.iad.aaaaaaaa5hgxi6voge43kultiindj3cbcnsimyatvlmq7wt5sbm6voo2ln3a"
SSH_KEY="$HOME/.ssh/id_ed25519.pub"
ADS=("TRTc:US-ASHBURN-AD-1" "TRTc:US-ASHBURN-AD-2" "TRTc:US-ASHBURN-AD-3")

ATTEMPT=0
while true; do
    for AD in "${ADS[@]}"; do
        ATTEMPT=$((ATTEMPT + 1))
        echo "[$(date '+%H:%M:%S')] Attempt $ATTEMPT — $AD"

        RESULT=$(oci compute instance launch \
            --compartment-id "$TENANCY" \
            --availability-domain "$AD" \
            --shape "VM.Standard.A1.Flex" \
            --shape-config '{"ocpus":2,"memoryInGBs":12}' \
            --image-id "$IMAGE_ID" \
            --subnet-id "$SUBNET_ID" \
            --assign-public-ip true \
            --ssh-authorized-keys-file "$SSH_KEY" \
            --display-name "a11y-remediate" \
            --output json 2>&1)

        if echo "$RESULT" | grep -q '"lifecycle-state"'; then
            echo ""
            echo "=== SUCCESS! ==="
            INSTANCE_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
            echo "Instance ID: $INSTANCE_ID"
            echo "AD: $AD"
            echo ""
            echo "Waiting for public IP..."
            sleep 30
            oci compute instance list-vnics --instance-id "$INSTANCE_ID" \
                --query 'data[0]."public-ip"' --raw-output 2>&1
            exit 0
        fi

        # Don't spam the API — only sleep between ADs, not after last one in round
        sleep 5
    done

    echo "  All ADs full. Waiting 60s before next round..."
    sleep 60
done
