import os

from aws_cdk import App, Environment
from omni_pilot_infra.data_stack import OmniPilotDataStack

app = App()
OmniPilotDataStack(
    app,
    "OmniPilotData",
    env=Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="eu-central-1"),
)
app.synth()
