from datetime import timedelta

from temporalio.common import RetryPolicy

WORKFLOW_TASK_QUEUE = "creative-marketer-workflows"
PRODUCTION_TASK_QUEUE = "creative-marketer-production"
COMMERCE_TASK_QUEUE = "creative-marketer-commerce"
ASSEMBLY_TASK_QUEUE = "creative-marketer-assembly"
ORCHESTRATION_TASK_QUEUE = "creative-marketer-orchestration"

STATE_ACTIVITY_TIMEOUT = timedelta(seconds=15)
TOOL_ACTIVITY_TIMEOUT = timedelta(seconds=60)
GENERATION_ACTIVITY_TIMEOUT = timedelta(seconds=30)
RESEARCHER_ACTIVITY_TIMEOUT = timedelta(minutes=10)

STATE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=250),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=5,
)
TOOL_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)
GENERATION_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=500),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
RESEARCHER_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=15),
    maximum_attempts=2,
    non_retryable_error_types=["AGENT_RECOVERY_REQUIRED"],
)
