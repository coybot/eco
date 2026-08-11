# Certificates (AWS mode only)

GCS mode needs no certificates at all — mosquitto authenticates with a
generated username/password (see [`control-plane.md`](control-plane.md)).
This page is only for `control_plane: "aws"`.

There is no Presidio-hosted claim certificate. Fleet provisioning is
AWS-only and uses **your own** AWS account — mint your own claim
certificate, IoT policy, and provisioning template.

## What you need, and why

[`drone/common/fleet_provisioning.py`](../drone/common/fleet_provisioning.py)
expects, under `drone/common/certs/`:

- `claim-cert.pem` / `claim-private.key` — a shared certificate every new
  drone starts with. It can **only** be used to request a unique certificate
  via Fleet Provisioning, never to publish/subscribe on real MQTT topics —
  that's what makes it safe to bake into an installer image.
- `root-ca.pem` — the AWS IoT root CA (download from
  [AWS's own root CA page](https://docs.aws.amazon.com/iot/latest/developerguide/managing-device-certs.html#device-certificate-authentication)).

Once a drone provisions, it gets its own `device.pem`/`private.key` and never
touches the claim cert again.

## 1. Create the claim certificate

```bash
aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile claim-cert.pem \
  --public-key-outfile claim-public.key \
  --private-key-outfile claim-private.key
```

Note the returned `certificateArn` — you'll attach a policy to it next.

## 2. Create a claim-only IoT policy

This policy must allow *only* the provisioning topics — not general
publish/subscribe:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "iot:Connect", "Resource": "*"},
    {"Effect": "Allow", "Action": "iot:Publish",
     "Resource": [
       "arn:aws:iot:REGION:ACCOUNT_ID:topic/$aws/certificates/create/*",
       "arn:aws:iot:REGION:ACCOUNT_ID:topic/$aws/provisioning-templates/DroneFleetProvisioning/provision/*"
     ]},
    {"Effect": "Allow", "Action": "iot:Subscribe",
     "Resource": [
       "arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/$aws/certificates/create/*",
       "arn:aws:iot:REGION:ACCOUNT_ID:topicfilter/$aws/provisioning-templates/DroneFleetProvisioning/provision/*"
     ]},
    {"Effect": "Allow", "Action": "iot:Receive", "Resource": "*"}
  ]
}
```

```bash
aws iot create-policy --policy-name DroneClaimPolicy --policy-document file://claim-policy.json
aws iot attach-policy --policy-name DroneClaimPolicy --target <certificateArn from step 1>
```

## 3. Create the provisioning template

The template name **must** be `DroneFleetProvisioning` — it's hardcoded in
`fleet_provisioning.py`'s `TEMPLATE_NAME`. This needs an IAM role that lets
IoT create Things on the template's behalf
(`AWSIoTThingsRegistration` managed policy is the standard starting point),
and a template body that creates a Thing and attaches your drones' real
IoT policy (the one `control/drones.py`'s registered devices actually use —
see `aws/template.yaml`'s `DroneIoTPolicy`, kept private since it's tied to
your specific deployment):

```bash
aws iot create-provisioning-template \
  --template-name DroneFleetProvisioning \
  --provisioning-role-arn arn:aws:iam::ACCOUNT_ID:role/DroneFleetProvisioningRole \
  --template-body file://provisioning-template.json \
  --enabled
```

## 4. Ship the claim cert

Place `claim-cert.pem`, `claim-private.key`, and `root-ca.pem` under
`drone/common/certs/` before running the installer (see
[`install-companion.md`](install-companion.md)) — or copy them onto an
already-installed device's `~/drone-api/certs/` and re-run
`fleet_provisioning.py`.

## Verify

```bash
python3 drone/common/fleet_provisioning.py
```

should exchange the claim cert for a unique `device.pem`/`private.key` and
report success — that's the same call `install.sh` makes automatically.
