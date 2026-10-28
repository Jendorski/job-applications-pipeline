import json
from datetime import date

from jap.answers import answers_context, answers_markdown, earliest_start, facts, generate_answers, validate_answers
from jap.config import Settings
from jap.llm.fake import FakeProvider
from jap.models import Answer, AnswerSet, FormQuestion, TailorPlan
from jap.scrape.ats import greenhouse_questions
from tests.test_pipeline import POSTING, ROOT

TODAY = date(2026, 10, 28)
WHY = FormQuestion(label="Why Company F?", required=True)
SPONSOR = FormQuestion(label="Will you require visa sponsorship?", required=True, options=["Yes", "No"])
USERNAME = FormQuestion(label="What is your GitHub username?", required=True)


def plan():
    return TailorPlan.model_validate_json((ROOT / "fixtures" / "sample_plan.json").read_text())


def ok_answers():
    return AnswerSet(answers=[
        Answer(question="Why Company F?", source="form", kind="motivation",
               answer="Your backend sits between software and payment devices; at Company A I write values back to running machines."),
        Answer(question="Will you require visa sponsorship?", source="form", kind="fact", answer="Yes"),
        Answer(question="What is your GitHub username?", source="form", kind="needs_input", answer="Your GitHub username"),
        Answer(question="What motivates you?", source="standard", kind="motivation",
               answer="My trading system: about 576 commits on 182 different days, rebuilt four times."),
    ])


def test_earliest_start_is_first_monday_of_next_month():
    assert earliest_start(date(2026, 10, 28)) == date(2026, 11, 2)
    assert earliest_start(date(2026, 12, 5)) == date(2027, 1, 4)


def test_facts_use_posted_salary_or_fallback(profile):
    lines = "\n".join(facts(profile, POSTING, TODAY))
    assert "John Doe" in lines and "Lisbon, Portugal" in lines and "Monday 2 November 2026" in lines
    assert "USD 80,000–120,000 per year (no range is posted)" in lines
    assert "Website/Portfolio: https://john-doe.example.com" in lines
    assert "LinkedIn: https://www.linkedin.com/in/john-doe-example/" in lines
    posted = POSTING.model_copy(update={"salary": "€70,000–€90,000"})
    assert "The posted range is: €70,000–€90,000" in "\n".join(facts(profile, posted, TODAY))


def test_valid_answers_pass_and_motivation_numbers_are_supported(profile):
    lines = facts(profile, POSTING, TODAY)
    assert validate_answers(profile, POSTING, ok_answers(), [WHY, SPONSOR, USERNAME], TODAY, lines) == []


def test_answer_rules(profile):
    lines = facts(profile, POSTING, TODAY)
    bad = ok_answers()
    bad.answers = [a for a in bad.answers if a.question != "Why Company F?"]
    bad.answers[0].answer = "Probably not"
    bad.answers[-1].answer = "I scaled Kafka to 9000 nodes and I am passionate."
    found = " ".join(validate_answers(profile, POSTING, bad, [WHY, SPONSOR, USERNAME], TODAY, lines))
    assert "not answered: Why Company F?" in found
    assert "must be exactly one of ['Yes', 'No']" in found
    assert "Kafka" in found and "9000" in found and "passionate" in found


def test_generate_reports_required_questions_needing_input(profile):
    provider = FakeProvider({AnswerSet: lambda _u: ok_answers()})
    answer_set, usages, problems, missing = generate_answers(provider, Settings(), profile, POSTING, plan(),
                                                             [WHY, SPONSOR, USERNAME], TODAY)
    assert problems == [] and missing == ["What is your GitHub username?"] and len(usages) == 1
    call = provider.calls[0]
    assert "<candidate>" in call["system"] and "Company F" not in call["system"]  # prefix stays cacheable
    assert "Why do you want to work at Company F?" in call["user"] and "Will you require visa sponsorship?" in call["user"]


def test_context_is_identical_across_jobs(profile):
    assert answers_context(profile) == answers_context(profile)


def test_markdown_groups_answers_and_lists_todos(profile):
    md = answers_markdown(POSTING, ok_answers(), [WHY, SPONSOR, USERNAME], facts(profile, POSTING, TODAY), [])
    assert md.index("## Needs your input") < md.index("## From the application form") < md.index("## Standard questions")
    assert "### Why Company F? *(required)*" in md and "**What is your GitHub username?**" in md
    assert "## Facts used" in md


def test_greenhouse_form_questions_are_extracted():
    payload = {"questions": [
        {"label": "Resume/CV", "required": True, "fields": [{"type": "input_file"}, {"type": "textarea"}]},
        {"label": "Why Company K?", "required": True, "fields": [{"type": "textarea"}]},
        {"label": "Will you need sponsorship?", "required": False,
         "fields": [{"type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "Upload portfolio", "required": False, "fields": [{"type": "input_file"}]},
    ]}
    qs = greenhouse_questions(payload)
    assert [(q.label, q.required, q.options) for q in qs] == [
        ("Why Company K?", True, []), ("Will you need sponsorship?", False, ["Yes", "No"])]
    assert json.loads(json.dumps([q.model_dump() for q in qs]))  # serialisable for form_questions.json
