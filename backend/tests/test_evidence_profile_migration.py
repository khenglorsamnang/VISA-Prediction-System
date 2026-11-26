from copy import deepcopy
from datetime import datetime, timezone

from backend.scripts.migrate_evidence_profiles import plan_drafts, rollback_eligible
from backend.app.services.policy_profiles import content_hash


def legacy_requirements(total=90):
    return [{"_id":"a","visaTypeId":"visa","code":"passport","weight":total,"required":True,"status":"ACTIVE"}]


def test_migration_preserves_legacy_allocation_and_creates_only_unpublished_drafts():
    source=legacy_requirements()
    original=deepcopy(source)
    drafts=plan_drafts([{"_id":"visa","countryId":"country"}],source,"upgrade-1")
    assert source==original
    assert len(drafts)==1
    draft=drafts[0]
    assert draft["status"]=="DRAFT"
    assert draft["requirements"][0]["weight"]==90
    assert draft["migrationWarnings"] == []
    assert draft["migrationSourceHash"]==content_hash(draft)
    assert draft["_id"]==plan_drafts([{"_id":"visa"}],source,"upgrade-1")[0]["_id"]


def test_rollback_refuses_published_edited_or_foreign_run_drafts():
    draft=plan_drafts([{"_id":"visa"}],legacy_requirements(100),"upgrade-1")[0]
    assert rollback_eligible(draft,"upgrade-1")
    assert not rollback_eligible(draft,"other-run")
    assert not rollback_eligible(draft,"upgrade-1",{draft["_id"]})
    assert not rollback_eligible({**draft,"status":"PUBLISHED"},"upgrade-1")
    assert not rollback_eligible({**draft,"revision":2},"upgrade-1")
    edited=deepcopy(draft);edited["requirements"][0]["weight"]=99
    assert not rollback_eligible(edited,"upgrade-1")


def test_migration_isolates_visa_types_and_excludes_archived_requirements():
    reqs=legacy_requirements(100)+[{"_id":"b","visaTypeId":"other","weight":40},{"_id":"old","visaTypeId":"visa","weight":20,"status":"ARCHIVED"}]
    drafts=plan_drafts([{"_id":"visa"},{"_id":"other"}],reqs,"upgrade-1")
    assert len(drafts)==2
    assert len(drafts[0]["requirements"])==1
    assert drafts[0]["requirements"][0]["requirementId"]=="a"
    assert drafts[1]["requirements"][0]["requirementId"]=="b"
