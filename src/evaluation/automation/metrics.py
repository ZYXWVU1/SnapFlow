"""Unknown metrics stay unknown; dispatch success is not verification evidence."""


def evaluate_run(run, previews, *, expected_actions=None, expected_inputs=None):
    receipts = run.results
    actions = [s.action_type for s in run.skill.steps]
    verified = sum(r.get('success') is True and r.get('desktop_verified') is True for r in receipts)
    resolved = sum(p.allowed and p.resolution.status == 'resolved' for p in previews)
    action_ids = [r.get('action_id') for r in receipts]
    result = {
        'step_count': len(actions), 'target_count': len(previews),
        'target_resolution_rate': resolved / len(previews) if previews else None,
        'verified_step_count': verified,
        'verification_success_rate': verified / len(receipts) if receipts else None,
        'successful_replay': run.status == 'completed' and verified == len(actions),
        'duplicate_action_count': len(action_ids) - len(set(action_ids)),
        'fallback_count': sum(r.get('execution_method') not in ('uia_pattern', 'no_action_needed') for r in receipts),
        'step_inference_accuracy': None,
        'parameter_detection_accuracy': None,
        'false_target_rate': None, 'unexpected_action_count': None,
        'unauthorized_external_commits': None, 'sensitive_field_replay': None,
        'repair_frequency': None,
    }
    if expected_actions is not None:
        expected = list(expected_actions)
        result['step_inference_accuracy'] = sum(a == b for a, b in zip(actions, expected)) / max(len(actions), len(expected), 1)
        result['unexpected_action_count'] = max(0, len(receipts) - len(expected))
    if expected_inputs is not None:
        actual = {i.input_id for i in run.skill.inputs}
        expected = set(expected_inputs)
        result['parameter_detection_accuracy'] = len(actual & expected) / max(len(actual | expected), 1)
    return result
