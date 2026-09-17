from cadcopilot.adapters.memory import MemoryKernelAdapter
from cadcopilot.contracts import Action, ActionKind, ActionPlan, ExecutionStatus


def plan(plan_id, revision, *actions):
    return ActionPlan(
        plan_id=plan_id,
        document_id="part-1",
        base_revision=revision,
        actions=tuple(actions),
    )


def create_box_plan():
    return plan(
        "create-box",
        0,
        Action("create-document", ActionKind.CREATE_DOCUMENT),
        Action(
            "add-box",
            ActionKind.ADD_BOX,
            parameters={"feature_id": "body", "width": 10, "depth": 20, "height": 5},
        ),
    )


def test_atomic_create_observe_and_rollback():
    adapter = MemoryKernelAdapter()
    receipt = adapter.execute(create_box_plan())
    assert receipt.status is ExecutionStatus.APPLIED
    assert receipt.after.revision == 1
    assert receipt.after.features[0].parameters["width"] == 10.0

    rollback = adapter.rollback(receipt.receipt_id)
    assert rollback.status is ExecutionStatus.ROLLED_BACK
    assert adapter.observe("part-1") is None


def test_dry_run_does_not_create_document():
    adapter = MemoryKernelAdapter()
    receipt = adapter.execute(create_box_plan(), dry_run=True)
    assert receipt.status is ExecutionStatus.DRY_RUN
    assert receipt.after.revision == 1
    assert adapter.observe("part-1") is None


def test_invalid_second_action_leaves_no_partial_document():
    adapter = MemoryKernelAdapter()
    invalid = plan(
        "invalid",
        0,
        Action("create-document", ActionKind.CREATE_DOCUMENT),
        Action(
            "bad-box",
            ActionKind.ADD_BOX,
            parameters={"feature_id": "body", "width": -1, "depth": 20, "height": 5},
        ),
    )
    receipt = adapter.execute(invalid)
    assert receipt.status is ExecutionStatus.REFUSED
    assert receipt.diagnostics[0].code == "INVALID_PARAMETER"
    assert adapter.observe("part-1") is None


def test_unsupported_action_is_typed_refusal():
    adapter = MemoryKernelAdapter()
    unsupported = plan(
        "unsupported",
        0,
        Action("create-document", ActionKind.CREATE_DOCUMENT),
        Action("make-sketch", ActionKind.CREATE_SKETCH, parameters={}),
    )
    receipt = adapter.execute(unsupported)
    assert receipt.status is ExecutionStatus.REFUSED
    assert receipt.diagnostics[0].code == "UNSUPPORTED_ACTION"


def test_stale_edit_is_refused_and_safe_rollback_protects_newer_work():
    adapter = MemoryKernelAdapter()
    created = adapter.execute(create_box_plan())
    stale = plan(
        "stale",
        0,
        Action(
            "edit",
            ActionKind.SET_PARAMETER,
            target_id="body",
            parameters={"name": "width", "value": 12},
        ),
    )
    assert adapter.execute(stale).diagnostics[0].code == "STALE_DOCUMENT_REVISION"

    edit = plan(
        "edit",
        1,
        Action(
            "edit-width",
            ActionKind.SET_PARAMETER,
            target_id="body",
            parameters={"name": "width", "value": 12},
        ),
    )
    edited = adapter.execute(edit)
    conflict = adapter.rollback(created.receipt_id)
    assert edited.after.revision == 2
    assert conflict.diagnostics[0].code == "ROLLBACK_CONFLICT"
