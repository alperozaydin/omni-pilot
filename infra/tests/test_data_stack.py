import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template
from omni_pilot_infra.data_stack import OmniPilotDataStack


@pytest.fixture(scope="module")
def template():
    stack = OmniPilotDataStack(
        App(), "OmniPilotData", env=Environment(account="123456789012", region="eu-central-1")
    )
    return Template.from_stack(stack)


def _statements(policies):
    return [s for p in policies.values() for s in p["Properties"]["PolicyDocument"]["Statement"]]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def test_bucket_is_private_versioned_encrypted_and_named(template):
    template.has_resource_properties("AWS::S3::Bucket", {
        "BucketName": "omni-pilot-data",
        "VersioningConfiguration": {"Status": "Enabled"},
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        },
        "BucketEncryption": Match.object_like({"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}),
    })


def test_old_versions_expire_after_30_days(template):
    template.has_resource_properties("AWS::S3::Bucket", {"LifecycleConfiguration": {"Rules": Match.array_with([
        Match.object_like({"Status": "Enabled", "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                           "ExpiredObjectDeleteMarker": True})])}})


def test_bucket_survives_stack_deletion(template):
    template.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"})
    template.resource_count_is("Custom::S3AutoDeleteObjects", 0)


def test_bucket_requires_tls(template):
    template.has_resource_properties("AWS::S3::BucketPolicy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}})])}})


def test_app_user_can_only_list_get_put_this_bucket(template):
    template.has_resource_properties("AWS::IAM::User", {"UserName": "omni-pilot-app"})
    policies = template.find_resources("AWS::IAM::Policy")
    assert len(policies) == 1
    statements = _statements(policies)
    actions = {a for s in statements for a in _as_list(s["Action"])}
    assert actions == {"s3:ListBucket", "s3:GetObject", "s3:PutObject"}
    assert all(s["Effect"] == "Allow" for s in statements)

    bucket_id = next(iter(template.find_resources("AWS::S3::Bucket")))
    bucket_arn = {"Fn::GetAtt": [bucket_id, "Arn"]}
    by_action = {a: s["Resource"] for s in statements for a in _as_list(s["Action"])}
    assert by_action["s3:ListBucket"] == bucket_arn
    assert by_action["s3:GetObject"] == {"Fn::Join": ["", [bucket_arn, "/*"]]}
    assert by_action["s3:PutObject"] == by_action["s3:GetObject"]


def test_no_access_key_and_no_console_login(template):
    template.resource_count_is("AWS::IAM::AccessKey", 0)
    user = next(iter(template.find_resources("AWS::IAM::User").values()))
    assert "LoginProfile" not in user["Properties"]


def test_outputs_bucket_and_user_names_only(template):
    assert set(template.find_outputs("*")) == {"BucketName", "UserName"}
