"""Deterministic guardrails on a TailorPlan. Every rule here is a reason to reject a plan.

The core idea: prose the LLM writes (summary, rephrased bullets, letter) may only contain
technologies, numbers, dates and employers that the verified profile already states.
"""

import re
from datetime import date

from jap.models import JobPosting, Profile, TailorPlan, Track
from jap.profile import find_never_claim
from jap.render.context import plan_problems, years_since

SUMMARY_MAX_WORDS = 120
LETTER_WORDS = (200, 420)
MAX_BULLETS_PER_ROLE = 8
REPHRASE_MAX_GROWTH = 1.25  # rephrased bullet may be at most 25% longer than the original

BANNED_PHRASES = [
    "i am writing to express", "passionate", "fast-paced", "team player", "hit the ground running",
    "synergy", "perfect fit", "dynamic team", "dynamic environment", "think outside the box",
]

# Technologies a CV is likely to name. Any of these in LLM-written prose must be backed by
# the profile. Not exhaustive: the allow-list check on skill groups covers the structured part.
TECH_TERMS = [
    # languages
    "Java", "Scala", "Golang", "Ruby", "PHP", "Elixir", "Erlang", "Haskell", "Clojure", "Swift",
    "Objective-C", "Dart", "Perl", "MATLAB", "Solidity", "Zig", "F#", "VB.NET",
    # frameworks / runtimes
    "Django", "Flask", "Rails", "Ruby on Rails", "Spring", "Spring Boot", "Laravel", "Symfony",
    "ASP.NET", ".NET", "Next.js", "Nuxt", "Angular", "Vue", "Svelte", "Remix", "Gin", "Phoenix",
    "Ktor", "Quarkus", "Micronaut", "Koa", "Hapi", "Deno", "Bun", "GraphQL", "gRPC", "tRPC",
    "Apollo", "Prisma", "Drizzle", "Hibernate", "Celery", "Pandas", "NumPy", "React Native",
    "Flutter", "Electron", "Tauri",
    # data / messaging
    "Kafka", "RabbitMQ", "NATS", "Pulsar", "Kinesis", "SQS", "EventBridge", "DynamoDB", "Aurora",
    "Redshift", "BigQuery", "Snowflake", "Databricks", "Cassandra", "ScyllaDB", "CockroachDB",
    "ClickHouse", "Elasticsearch", "OpenSearch", "Neo4j", "InfluxDB", "TimescaleDB", "Supabase",
    "PlanetScale", "Memcached", "Spark", "Airflow", "dbt", "Flink", "Hadoop", "Oracle", "DB2",
    # infrastructure
    "Kubernetes", "K8s", "Helm", "Terraform", "Pulumi", "Ansible", "Chef", "Puppet",
    "CloudFormation", "AWS CDK", "Docker Swarm", "Nomad", "Consul", "Istio", "Linkerd", "Envoy",
    "Nginx", "HAProxy", "Jenkins", "GitLab CI", "CircleCI", "Travis CI", "ArgoCD", "Argo CD",
    "Spinnaker", "Prometheus", "Grafana", "Datadog", "New Relic", "Splunk", "Kibana", "Logstash",
    "OpenTelemetry", "Jaeger", "PagerDuty", "Azure", "Lambda", "ECS", "EKS", "Fargate",
    "CloudFront", "Vercel", "Netlify", "Cloudflare Workers", "DigitalOcean", "GKE", "OpenShift",
    # AI
    "PyTorch", "TensorFlow", "Keras", "scikit-learn", "Hugging Face", "LlamaIndex", "OpenAI",
    "GPT", "Pinecone", "Weaviate", "Milvus", "pgvector",
    # industrial / embedded
    "CAN bus", "Zigbee", "LoRaWAN", "Bluetooth", "BLE", "PLC", "SCADA", "Profinet", "EtherCAT",
    "Node-RED", "Kepware", "OSIsoft", "Siemens S7", "embedded C", "RTOS", "FreeRTOS",
    # payments / identity / compliance
    "Stripe", "Plaid", "Adyen", "Braintree", "PayPal", "Paystack", "Flutterwave", "Keycloak",
    "Auth0", "Okta", "OIDC", "SAML", "SOC 2", "ISO 27001", "PCI DSS", "HIPAA", "GDPR",
    # mobile
    "iOS", "Android", "Kotlin Multiplatform", "SwiftUI", "Jetpack Compose",
]
# Ordinary English words that are also tech names: match only with their exact capitalisation.
CASE_SENSITIVE = {"Swift", "Spark", "Rails", "Chef", "Puppet", "Gin", "Phoenix", "Envoy", "Consul",
                  "Nomad", "Dart", "Lambda", "Aurora", "Bun", "Koa", "Hapi", "Apollo", "Oracle", "Vue"}
COMMON_WORDS = {"service", "services", "inc", "ltd", "group", "labs", "company"}

NUMBER = re.compile(r"(?<![\w.])~?(\d+(?:[.,]\d+)*)(?:\+|%|x)?(?!\w)")
YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _has(term: str, text: str, case_sensitive: bool = False) -> bool:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(rf"(?<![\w.]){re.escape(term)}(?![\w])", text, flags) is not None


def _numbers(text: str) -> set[str]:
    return {m.group(1).replace(",", "") for m in NUMBER.finditer(text)}


def _words(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def verified_text(profile: Profile) -> str:
    """Everything the profile states as fact and may appear on a CV."""
    parts = [s for group in profile.skills.values() for s in group]
    for role in profile.experience:
        if role.show_on_cv:
            parts += [role.company, role.title, role.location or ""]
            parts += [b.text for b in role.bullets if b.status == "verified"]
    parts += [f"{p.name} {p.text} {' '.join(p.stack)}" for p in profile.projects if p.status == "verified"]
    parts += [f"{e.institution} {e.degree} {e.location or ''}" for e in profile.education if e.status == "verified"]
    if c := profile.candidate:
        parts += [c.motivation.get("text", ""), c.why_looking, c.based_in, c.work_authorisation,
                  " ".join(c.relocation.get("open_to", [])), c.salary.get("fallback", "")]
    return "\n".join(parts)


def allowed_numbers(profile: Profile, posting: JobPosting | None, today: date, extra_text: str = "") -> set[str]:
    nums = _numbers(verified_text(profile)) | _numbers(extra_text)
    nums |= {str(years_since(profile.person.career_start, today)), str(today.year), str(today.year + 1)}
    for role in profile.experience:
        nums |= {d.split("-")[0] for d in (role.start, role.end) if d}
    nums |= {e.end.split("-")[0] for e in profile.education}
    if posting:
        nums |= _numbers(posting.model_dump_json())
    return nums


def prose_problems(profile: Profile, prose: list[tuple[str, str]], numbers: set[str]) -> list[str]:
    """Technologies, employers, numbers and stock phrases in free text written by the LLM."""
    problems = []
    facts = verified_text(profile)
    terms = TECH_TERMS + [s for s in unverified_skills(profile) if s not in TECH_TERMS]
    for where, text in prose:
        for term in terms:
            cs = term in CASE_SENSITIVE
            if _has(term, text, cs) and not _has(term, facts, cs):
                problems.append(f"{where}: names '{term}', which the profile does not support")
        for name in forbidden_employers(profile):
            if _has(name, text, case_sensitive=True):
                problems.append(f"{where}: mentions '{name}', an employer that must not appear on CVs")
        problems += [f"{where}: never_claim term '{t}'" for t in find_never_claim(text, profile.never_claim)]
        if extra := _numbers(text.replace("{years}", "")) - numbers:
            problems.append(f"{where}: numbers {sorted(extra)} are not supported by the profile or posting")
        problems += [f"{where}: stock phrase '{p}'" for p in BANNED_PHRASES if p in text.lower()]
    return problems


def forbidden_employers(profile: Profile) -> list[str]:
    names = []
    for role in profile.experience:
        if not role.show_on_cv:
            # The full name too, so a name made of common words ("Company C") is still caught.
            names += [role.company] + [w for w in re.findall(r"[A-Z][\w-]{3,}", role.company) if w.lower() not in COMMON_WORDS]
    names += (getattr(profile, "unverified", None) or {}).get("devops_cv", {}).get("employers", [])
    return [n for n in names if isinstance(n, str) and n]


def unverified_skills(profile: Profile) -> list[str]:
    devops = (getattr(profile, "unverified", None) or {}).get("devops_cv", {})
    return [s for s in devops.get("skills", []) if isinstance(s, str)]


def validate(profile: Profile, track: Track, plan: TailorPlan, posting: JobPosting | None = None,
             today: date | None = None) -> list[str]:
    today = today or date.today()
    problems = plan_problems(profile, track, plan)

    bullets = {b.id: b for r in profile.experience for b in r.bullets}
    rephrased = [(c.bullet_id, c.rephrased) for cs in plan.experience.values() for c in cs if c.rephrased]
    letter = plan.cover_letter.paragraphs if plan.cover_letter else []
    free = [("summary", plan.summary)] + [(f"letter ¶{i + 1}", p) for i, p in enumerate(letter)]

    # Free prose: only technologies, employers and numbers the profile (or posting) supports.
    # never_claim is already covered by plan_problems.
    numbers = allowed_numbers(profile, posting, today, track.summary)
    problems += [p for p in prose_problems(profile, free, numbers) if "never_claim" not in p]

    # Rephrased bullets: same checks, but numbers must come from that bullet, and only light edits.
    for bid, text in rephrased:
        original = bullets[bid].text if bid in bullets else ""
        problems += [p for p in prose_problems(profile, [(f"rephrased {bid}", text)], _numbers(original))
                     if "never_claim" not in p and "numbers" not in p and "stock phrase" not in p]
        if extra := _numbers(text) - _numbers(original):
            problems.append(f"rephrased {bid}: numbers {sorted(extra)} are not in the original bullet")
        if original and len(text) > len(original) * REPHRASE_MAX_GROWTH:
            problems.append(f"rephrased {bid}: {len(text)} chars vs {len(original)} original; light rephrasing only")

    # Shape.
    if (n := _words(plan.summary)) > SUMMARY_MAX_WORDS:
        problems.append(f"summary: {n} words (max {SUMMARY_MAX_WORDS})")
    for role in profile.experience:
        if role.show_on_cv and not plan.experience.get(role.id):
            problems.append(f"experience: role '{role.id}' is missing; every CV role needs at least one bullet")
    for role_id, choices in plan.experience.items():
        if len(choices) > MAX_BULLETS_PER_ROLE:
            problems.append(f"experience.{role_id}: {len(choices)} bullets (max {MAX_BULLETS_PER_ROLE})")
        if len({c.bullet_id for c in choices}) != len(choices):
            problems.append(f"experience.{role_id}: repeats a bullet")

    if plan.cover_letter:
        body = " ".join(letter)
        lo, hi = LETTER_WORDS
        if not lo <= (n := _words(body)) <= hi:
            problems.append(f"cover letter: {n} words (expected {lo}–{hi})")
        company = posting.company if posting else plan.company
        if company and company.lower() not in (plan.cover_letter.greeting + " " + body).lower():
            problems.append(f"cover letter: never names {company}")

    return problems
