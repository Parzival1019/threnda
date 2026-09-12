"""Manual asset updates — the Milestone 4 risk-scoring inputs.

No audit trail here (unlike ``Exposure``/``ExposureHistory``): the spec's
audit design (docs/community-v0.1-spec.md, "ExposureHistory") is scoped to
the exposure workflow, and these two fields aren't workflow state — they're
closer to the asset attributes an importer would set if any format carried
them. Extracted into its own service (rather than living inline in
``api/v1/assets.py``) purely to keep the web UI's asset-edit route and the
JSON API's ``PATCH`` calling the same code, matching every other write path
in this codebase.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from threnda.app.models.asset import Asset
from threnda.app.models.enums import AssetCriticality
from threnda.app.services.risk_scoring import recompute_scores_for_asset


def apply_asset_update(
    db: Session,
    asset: Asset,
    *,
    internet_facing: bool | None = None,
    internet_facing_set: bool = False,
    business_criticality: AssetCriticality | None = None,
    business_criticality_set: bool = False,
) -> Asset:
    """Apply a partial update and recompute every exposure it can affect.

    Uses explicit ``*_set`` flags rather than a dict (matching
    ``ExposureChanges``'s "only touch what the caller actually intends")
    because ``internet_facing``'s valid values include ``None`` itself
    (meaning "set to unknown"), so presence can't be inferred from the
    value alone the way it can for ``ExposureChanges``.
    """
    changed = False
    if internet_facing_set and asset.internet_facing != internet_facing:
        asset.internet_facing = internet_facing
        changed = True
    if (
        business_criticality_set
        and business_criticality is not None
        and asset.business_criticality != business_criticality
    ):
        asset.business_criticality = business_criticality
        changed = True

    db.flush()
    if changed:
        recompute_scores_for_asset(db, asset.id, asset.workspace_id)
    return asset
