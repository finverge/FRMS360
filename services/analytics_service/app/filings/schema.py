"""What a regulatory return has to contain, and how to tell when it does not.

**Read this before assuming the output is submission-ready.**

The Fraud Monitoring Return goes to RBI through its own portal, and an STR goes to FIU-IND
through FINnet, each in a prescribed technical format that the regulator publishes to
regulated entities. Those exact schemas are not reproduced here, and inventing something
that resembles them would be worse than useless: it would look authoritative, get filed,
and be rejected - or worse, accepted while carrying the wrong thing in the wrong field.

So this layer does the part that is genuinely ours to do:

* define the **information** each return needs, drawn from the Directions;
* prove a case actually carries it, and say precisely what is missing when it does not;
* emit a complete, versioned, hashed record in structured form and in a form a human can
  read and check.

Turning that record into the regulator's own file is a **mapping**, declared per entity
type in ``FORMAT_BINDINGS``. Until a bank supplies the current schema for its channel, the
platform is explicit that what it produces is a submission *pack*, not the submission.

The value that survives that caveat is not small: a bank finds out it cannot evidence a
fraud *before* the filing deadline, rather than at the portal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Version of the field set below. Stamped on every artefact so a return generated last
#: year can be explained by the definition in force when it was generated.
SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    #: Mandatory fields block generation. Optional ones are reported as gaps but do not.
    mandatory: bool = True
    #: Why the regulator wants it - shown to whoever has to go and find it.
    note: str = ""


#: Common to both returns: who is filing, about whom, for how much.
_CORE = (
    Field("entity_name", "Reporting entity", note="Legal name of the regulated entity"),
    Field("entity_type", "Entity category",
          note="Determines which Master Direction governs the filing"),
    Field("case_reference", "Internal case reference"),
    Field("detection_date", "Date of detection",
          note="When the entity first identified the fraud"),
    Field("occurrence_date", "Date of occurrence",
          note="When the earliest constituent transaction took place"),
    Field("amount_involved", "Amount involved",
          note="Sum of the transactions comprising the fraud, in rupees"),
    Field("amount_recovered", "Amount recovered", mandatory=False),
    Field("accounts_involved", "Accounts involved"),
    Field("modus_operandi", "Modus operandi",
          note="How the fraud was carried out, in narrative form"),
)

FMR_FIELDS = _CORE + (
    Field("fmr_category", "Fraud category",
          note="RBI classification the fraud is reported under"),
    Field("declaration_date", "Date of declaration",
          note="When the entity classified the case as fraud"),
    Field("reasoned_order_on_file", "Reasoned order",
          note="A speaking order must exist before a fraud is declared "
               "(SBI v. Rajesh Agarwal)"),
    Field("approved_by", "Approved by",
          note="The second authoriser under dual control"),
    Field("staff_involvement", "Staff involvement", mandatory=False,
          note="Whether staff accountability is being examined"),
    Field("recovery_status", "Recovery action", mandatory=False),
    Field("lea_referred", "Referred to law enforcement", mandatory=False,
          note="Required above the entity's board-approved referral threshold"),
)

STR_FIELDS = _CORE + (
    Field("suspicion_grounds", "Grounds for suspicion",
          note="Why the activity is believed to be suspicious, beyond the rule that fired"),
    Field("indicators_triggered", "Indicators triggered",
          note="Which early-warning indicators fired, with their observed values"),
    Field("transaction_count", "Number of transactions"),
    Field("period_covered", "Period covered"),
    Field("principal_officer", "Principal Officer",
          note="The officer designated under the PMLA rules"),
)

FIELDS = {"fmr": FMR_FIELDS, "str": STR_FIELDS}

RETURN_LABELS = {
    "fmr": "Fraud Monitoring Return",
    "str": "Suspicious Transaction Report",
}

RETURN_RECIPIENTS = {
    "fmr": "Reserve Bank of India",     # NABARD / NHB for some entity types; see policy
    "str": "FIU-IND",
}


#: Where the structured record has to be mapped to before it can actually be submitted.
#: Declared rather than implemented, so nobody mistakes the pack for the filing.
FORMAT_BINDINGS = {
    "fmr": {
        "channel": "RBI supervisory returns portal",
        "status": "not_bound",
        "needs": "The current FMR template and code lists for this entity's channel.",
    },
    "str": {
        "channel": "FIU-IND FINnet",
        "status": "not_bound",
        "needs": "The current FINnet STR schema and the entity's reporting credentials.",
    },
}


@dataclass
class Gap:
    key: str
    label: str
    mandatory: bool
    note: str

    def as_dict(self) -> dict:
        return {"field": self.key, "label": self.label,
                "mandatory": self.mandatory, "why": self.note}


@dataclass
class Validation:
    kind: str
    gaps: list[Gap] = field(default_factory=list)

    @property
    def blocking(self) -> list[Gap]:
        return [g for g in self.gaps if g.mandatory]

    @property
    def ready(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ready": self.ready,
            "blocking": [g.as_dict() for g in self.blocking],
            "advisory": [g.as_dict() for g in self.gaps if not g.mandatory],
        }


def validate(kind: str, payload: dict) -> Validation:
    """Check a built payload against the field set.

    A field counts as present when it holds something a reader could act on. Empty
    strings, empty lists and None are absences - a return that says "amount: " has not
    answered the question, and letting it through only moves the rejection to the
    regulator.
    """
    out = Validation(kind=kind)
    for f in FIELDS.get(kind, ()):
        value = payload.get(f.key)
        missing = value is None or value == "" or value == [] or value == {}
        # Explicit False is an answer for the yes/no fields, not an absence.
        if isinstance(value, bool):
            missing = False
        if missing:
            out.gaps.append(Gap(f.key, f.label, f.mandatory, f.note))
    return out
