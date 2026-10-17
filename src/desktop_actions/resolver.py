"""Semantic identity first; ambiguous controls are never guessed from coordinates."""
from .models import TargetResolution


class DesktopTargetResolver:
    def resolve(self, target, state):
        scored = []
        for element in state.elements:
            if target.process_name and element.process_name.casefold() != target.process_name.casefold():
                continue
            if element.control_type != target.control_type:
                continue
            # Exact semantic identity is mandatory; runtime IDs only rank identical matches.
            if target.automation_id and element.automation_id != target.automation_id:
                continue
            if target.name and element.name != target.name:
                continue
            if target.class_name and element.class_name != target.class_name:
                continue
            if target.ancestor_path and element.ancestor_path != target.ancestor_path:
                continue
            if not set(target.required_actions) <= set(element.capabilities.actions) or not set(target.required_patterns) <= set(element.capabilities.patterns):
                continue
            score = .35 + (.3 if target.automation_id else 0) + (.2 if target.name else 0)
            score += .1 if target.ancestor_path else 0
            score += .05 if target.class_name else 0
            score += .1 if target.required_actions and target.required_patterns else 0
            scored.append((min(1.0, score), element))
        candidates = tuple(e for _, e in sorted(scored, key=lambda pair: pair[0], reverse=True))
        if not candidates:
            return TargetResolution('target_not_found', 0, 0, reasons=('No semantic control matches.',))
        if len(candidates) > 1:
            return TargetResolution('target_ambiguous', 0, len(candidates),
                reasons=('Multiple controls match. Select a distinct ancestor or automation ID.',), candidates=candidates[:8])
        confidence, element = scored[0]
        return TargetResolution('resolved' if confidence >= .75 else 'low_confidence', confidence, 1,
            element, ('Exact semantic properties matched in the fenced window.',), candidates)
