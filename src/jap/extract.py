from importlib.resources import files

from jap.config import Settings
from jap.llm import LLMProvider, Usage
from jap.models import JobPosting

PROMPT = files("jap.prompts").joinpath("extract.md").read_text()
CONTEXT_MAX_CHARS = 8000  # company pages can be whole websites; the posting itself is never cut


def extract_posting(provider: LLMProvider, settings: Settings, posting_text: str, url: str | None,
                    company_context: str | None = None) -> tuple[JobPosting, Usage, list[str]]:
    notes = []
    user = f'<job_posting source="{url or "pasted"}">\n{posting_text}\n</job_posting>'
    if company_context:
        if len(company_context) > CONTEXT_MAX_CHARS:
            notes.append(f"company context trimmed from {len(company_context)} to {CONTEXT_MAX_CHARS} characters")
            company_context = company_context[:CONTEXT_MAX_CHARS]
        user += f"\n<company_context>\n{company_context}\n</company_context>"
    posting, usage = provider.complete(system=PROMPT, user=user, schema=JobPosting,
                                       model=settings.model_extract, effort=settings.effort_extract)
    return posting, usage, notes
