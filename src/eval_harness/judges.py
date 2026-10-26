"""Pick the judge by model name: claude-* goes to the Claude judge, everything else to OpenAI."""
from . import claude_judge, openai_judge


def _module(model):
    return claude_judge if claude_judge.is_claude_model(model) else openai_judge


def preview(packet, model):
    return _module(model).preview(packet, model)


def evaluate(packet, model, approval_sha256, output_root, *, client_factory=None):
    return _module(model).evaluate(packet, model, approval_sha256, output_root, client_factory=client_factory)
