from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class OmniPilotDataStack(Stack):
    """The app's private data bucket and the limited IAM user that reads and writes it."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name="omni-pilot-data",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(30),
                    expired_object_delete_marker=True,
                )
            ],
        )

        user = iam.User(self, "AppUser", user_name="omni-pilot-app")
        user.add_to_policy(
            iam.PolicyStatement(effect=iam.Effect.ALLOW, actions=["s3:ListBucket"], resources=[bucket.bucket_arn])
        )
        user.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject", "s3:PutObject"],
                resources=[bucket.arn_for_objects("*")],
            )
        )

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "UserName", value=user.user_name)
