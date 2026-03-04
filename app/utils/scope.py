"""
app/utils/scope.py
------------------
Customer-scoping utility for the Janitorial QC portal.

Provides a single entry-point — get_customer_scope(user) — that returns the
set of facility IDs a customer is authorised to view, derived from their
CustomerAssignment rows.

Usage (inside any route that serves customer users):

    from app.utils.scope import get_customer_scope

    facility_ids = get_customer_scope(current_user)
    inspections  = Inspection.query.filter(
        Inspection.facility_id.in_(facility_ids)
    ).all()

For non-customer roles the function returns None, signalling that no
facility-level scoping is required (full access applies).
"""

import logging
from app.models.project import CustomerAssignment
from app.models.facility import Facility

logger = logging.getLogger(__name__)


def get_customer_scope(user) -> list[int] | None:
    """Return the list of facility IDs accessible to a customer user.

    Parameters
    ----------
    user : User
        The currently authenticated user.

    Returns
    -------
    list[int]
        Facility IDs the customer may access.  May be empty if no assignments
        exist yet — callers should treat an empty list as "no access".
    None
        Returned for non-customer roles, indicating unrestricted access.
    """
    if user.role != 'customer':
        return None  # no scoping needed for internal staff

    assignments = CustomerAssignment.query.filter_by(user_id=user.id).all()

    facility_ids = set()

    for assignment in assignments:
        if assignment.facility_id:
            # Scoped to a specific facility
            facility_ids.add(assignment.facility_id)
        else:
            # Scoped to an entire project — include all facilities in that project
            project_facilities = (
                Facility.query
                .filter_by(project_id=assignment.project_id, active=True)
                .all()
            )
            for f in project_facilities:
                facility_ids.add(f.id)

    logger.debug(
        'SCOPE | customer_scope | user_id=%s username=%s facility_ids=%s',
        user.id, user.username, sorted(facility_ids),
    )

    return sorted(facility_ids)
