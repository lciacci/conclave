"""Stop every running project=conclave EC2 instance. Fired by SNS on budget breach."""
import boto3


def handler(event, context):
    ec2 = boto3.client("ec2")
    reservations = ec2.describe_instances(
        Filters=[
            {"Name": "tag:project", "Values": ["conclave"]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]},
        ]
    )["Reservations"]
    ids = [i["InstanceId"] for r in reservations for i in r["Instances"]]
    if ids:
        ec2.stop_instances(InstanceIds=ids)
    print(f"hardstop: stopped {ids or 'nothing'}")
    return {"stopped": ids}
