"""FHIR resources: one entity per resource type, whether in a Bundle, a single resource or a bulk export.

A Bundle mixes resource types, so describing it as one JSON document would blend a Patient with an
Observation. Here each `resourceType` gets its own entity and the fields are inferred from the resources
of that type, the same way a JSON file's are. Values are never kept: only types and field names.

The resource type comes from the data, so it shows only if FHIR defines it; anything else, such as a
profile's own type or a value that was never a type, is called `Resource_N`. `.fhir.json`, `.fhir.ndjson` and `.fhir` files are recognised by name; for any
other file, pass `--source fhir`.
"""

from __future__ import annotations

import json
import re

from ..inference import Observation, infer
from ..privacy import KeyPolicy
from ..schema import DataSchema, Entity
from .base import Source, SourceError
from .files import extension, open_text, records_observation, reject_namespace

_RESOURCE_TYPE = re.compile(r"[A-Z][A-Za-z]{2,40}")
_MAX_TYPES = 200
_LINE_FORMATS = (".ndjson", ".jsonl")
# Every resource type in FHIR R4 and R5.
_KNOWN_TYPES = frozenset(
    ["Account", "ActivityDefinition", "ActorDefinition", "AdministrableProductDefinition", "AdverseEvent", "AllergyIntolerance", "Appointment", "AppointmentResponse", "ArtifactAssessment", "AuditEvent", "Basic", "Binary", "BiologicallyDerivedProduct", "BiologicallyDerivedProductDispense", "BodyStructure", "Bundle", "CapabilityStatement", "CarePlan", "CareTeam", "CatalogEntry", "ChargeItem", "ChargeItemDefinition", "Citation", "Claim", "ClaimResponse", "ClinicalImpression", "ClinicalUseDefinition", "CodeSystem", "Communication", "CommunicationRequest", "CompartmentDefinition", "Composition", "ConceptMap", "Condition", "ConditionDefinition", "Consent", "Contract", "Coverage", "CoverageEligibilityRequest", "CoverageEligibilityResponse", "DetectedIssue", "Device", "DeviceAssociation", "DeviceDefinition", "DeviceDispense", "DeviceMetric", "DeviceRequest", "DeviceUsage", "DeviceUseStatement", "DiagnosticReport", "DocumentManifest", "DocumentReference", "DomainResource", "EffectEvidenceSynthesis", "Encounter", "EncounterHistory", "Endpoint", "EnrollmentRequest", "EnrollmentResponse", "EpisodeOfCare", "EventDefinition", "Evidence", "EvidenceReport", "EvidenceVariable", "ExampleScenario", "ExplanationOfBenefit", "FamilyMemberHistory", "Flag", "FormularyItem", "GenomicStudy", "Goal", "GraphDefinition", "Group", "GuidanceResponse", "HealthcareService", "ImagingSelection", "ImagingStudy", "Immunization", "ImmunizationEvaluation", "ImmunizationRecommendation", "ImplementationGuide", "Ingredient", "InsurancePlan", "InventoryItem", "InventoryReport", "Invoice", "Library", "Linkage", "List", "Location", "ManufacturedItemDefinition", "Measure", "MeasureReport", "Media", "Medication", "MedicationAdministration", "MedicationDispense", "MedicationKnowledge", "MedicationRequest", "MedicationStatement", "MedicinalProduct", "MedicinalProductAuthorization", "MedicinalProductContraindication", "MedicinalProductDefinition", "MedicinalProductIndication", "MedicinalProductIngredient", "MedicinalProductInteraction", "MedicinalProductManufactured", "MedicinalProductPackaged", "MedicinalProductPharmaceutical", "MedicinalProductUndesirableEffect", "MessageDefinition", "MessageHeader", "MolecularDefinition", "MolecularSequence", "NamingSystem", "NutritionIntake", "NutritionOrder", "NutritionProduct", "Observation", "ObservationDefinition", "OperationDefinition", "OperationOutcome", "Organization", "OrganizationAffiliation", "PackagedProductDefinition", "Parameters", "Patient", "PaymentNotice", "PaymentReconciliation", "Permission", "Person", "PlanDefinition", "Practitioner", "PractitionerRole", "Procedure", "Provenance", "Questionnaire", "QuestionnaireResponse", "RegulatedAuthorization", "RelatedPerson", "RequestGroup", "RequestOrchestration", "Requirements", "ResearchDefinition", "ResearchElementDefinition", "ResearchStudy", "ResearchSubject", "Resource", "RiskAssessment", "RiskEvidenceSynthesis", "Schedule", "SearchParameter", "ServiceRequest", "Slot", "Specimen", "SpecimenDefinition", "StructureDefinition", "StructureMap", "Subscription", "SubscriptionStatus", "SubscriptionTopic", "Substance", "SubstanceDefinition", "SubstanceNucleicAcid", "SubstancePolymer", "SubstanceProtein", "SubstanceReferenceInformation", "SubstanceSourceMaterial", "SubstanceSpecification", "SupplyDelivery", "SupplyRequest", "Task", "TerminologyCapabilities", "TestPlan", "TestReport", "TestScript", "Transport", "ValueSet", "VerificationResult", "VisionPrescription"]
)


class FhirSource(Source):
    kind = "fhir"

    def matches(self, target: str) -> bool:
        lowered = target.lower()
        return lowered.endswith((".fhir.json", ".fhir.ndjson", ".fhir.jsonl", ".fhir"))

    def extract(self, target: str, policy: KeyPolicy, namespace: str | None = None) -> DataSchema:
        reject_namespace(namespace)
        groups: dict[str, Observation] = {}
        notes: list[str] = []
        skipped = over_limit = 0
        for document in _documents(target):
            for resource in _resources(document):
                name = resource.get("resourceType") if isinstance(resource, dict) else None
                if not isinstance(name, str) or not _RESOURCE_TYPE.fullmatch(name):
                    skipped += 1
                    continue
                if name not in groups and len(groups) >= _MAX_TYPES:
                    over_limit += 1
                    continue
                groups.setdefault(name, records_observation()).observe_item(resource)

        if not groups:
            raise SourceError("input has no FHIR resources (objects with a resourceType)")
        entities = []
        for position, (name, root) in enumerate(sorted(groups.items()), start=1):
            shown = name if name in _KNOWN_TYPES and policy.shows_name(name) else f"Resource_{position}"
            node, inferred = infer(root, policy, f"$.{shown}")
            notes.extend(inferred)
            entities.append(Entity(shown, node))
        if skipped:
            notes.append(f"skipped {skipped} object(s) that aren't FHIR resources")
        if over_limit:
            notes.append(f"left out {over_limit} resource(s) past the limit of {_MAX_TYPES} resource types")
        return DataSchema(self.kind, tuple(entities), tuple(notes))


def _documents(target: str):
    """The JSON documents in the file: one, or one per line for NDJSON."""
    with open_text(target) as stream:
        try:
            if extension(target) in _LINE_FORMATS:
                for line in stream:
                    if line.strip():
                        yield json.loads(line)
            else:
                yield json.load(stream)
        except json.JSONDecodeError:
            raise SourceError("input isn't valid JSON") from None
        except UnicodeDecodeError:
            raise SourceError("input is not valid UTF-8") from None


def _resources(document):
    """The resources in a document: a Bundle's entries, or the document itself."""
    if isinstance(document, list):
        for item in document:
            yield from _resources(item)
    elif isinstance(document, dict):
        if document.get("resourceType") == "Bundle":
            for entry in document.get("entry") or []:
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict):
                    yield entry["resource"]
        else:
            yield document
