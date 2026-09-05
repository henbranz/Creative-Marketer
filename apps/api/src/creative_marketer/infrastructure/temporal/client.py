from temporalio.client import Client


class TemporalWorkflowSignalClient:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def signal_approval_state_changed(self, workflow_id: str) -> None:
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal("approval_state_may_have_changed")
