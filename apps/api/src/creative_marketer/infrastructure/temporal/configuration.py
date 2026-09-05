from datetime import timedelta

from temporalio.common import RetryPolicy

WORKFLOW_TASK_QUEUE = "creative-marketer-workflows"

STATE_ACTIVITY_TIMEOUT = timedelta(seconds=15)
TOOL_ACTIVITY_TIMEOUT = timedelta(seconds=60)
GENERATION_ACTIVITY_TIMEOUT = timedelta(seconds=30)

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
